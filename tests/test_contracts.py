"""M2: contract archaeology, and how it feeds task packages and verification."""
import difflib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import (HYPOTHESES, PACKAGES, PROFILE, ROOT, VALID, codes, guard_id, reason,
                      referenced_files)
from unwritten.adapters import detect_adapter
from unwritten.cli import main
from unwritten.contracts import attach_contracts, extract_contracts
from unwritten.errors import HarnessError
from unwritten.profile import load_profile
from unwritten.runners import make_runner
from unwritten.verify import verify_package


def by_id(contract_map):
    return {c["id"]: c for c in contract_map["contracts"]}


# ---------------------------------------------------------------- extraction


def test_extraction_is_deterministic_and_makes_no_model_calls(contract_map):
    first, second = contract_map(), contract_map(fresh=True)
    assert first["fingerprint"] == second["fingerprint"]
    assert first["contracts"] == second["contracts"]
    assert first["process"]["llm_calls"] == 0
    assert first["repository"]["commit"] == "667512bac0534ea742a7a7c3e3262142ac914c30"
    assert first["adapter"] == "python"


def test_every_contract_carries_the_required_fields(contract_map):
    for contract in contract_map()["contracts"]:
        assert contract["id"] and contract["description"] and contract["category"]
        assert contract["status"] in ("verified", "hypothesis", "refuted")
        assert contract["evidence_type"] in ("existing_test", "guard_clause", "supplied_hypothesis")
        assert contract["confidence"] and contract["origin"]
        assert set(contract["source"]) == {"file", "symbol", "line"}
        assert isinstance(contract["related"], list)
        assert contract["validation"]["method"] and contract["validation"]["result"]


def test_every_verified_contract_has_probes_that_pass_at_the_pinned_commit(contract_map, runner_name):
    result = contract_map(runner_name)
    logs = Path(result["map_dir"]) / "logs"
    passed = []
    for run in ("baseline1", "baseline2"):
        outcome = {}
        for report in (logs / run).glob("*.json"):
            if report.stem in ("repo_tests", "probes"):
                for test in json.loads(report.read_text())["tests"]:
                    outcome[report.stem, test["nodeid"]] = test["phases"].get("call", {}).get("outcome")
        passed.append(outcome)
    verified = [c for c in result["contracts"] if c["status"] == "verified"]
    assert len(verified) >= 20
    for contract in verified:
        probes = contract["validation"]["probes"]
        assert probes, contract["id"]
        for probe in probes:
            key = (probe["suite"], probe["nodeid"])
            assert passed[0][key] == "passed" and passed[1][key] == "passed", (contract["id"], key)
    # Nothing that is not verified may look verified.
    for contract in result["contracts"]:
        if contract["status"] != "verified":
            assert contract["confidence"] != "high"


def test_guard_is_verified_only_when_a_test_notices_its_removal(contract_map):
    without, with_probes = contract_map(hypotheses=False), contract_map()
    quantity = by_id(without)[guard_id(without["contracts"], "_check_quantity", "quantity < 1")]
    assert quantity["status"] == "verified"
    assert quantity["validation"]["result"] == "guard_removal_detected"
    assert quantity["validation"]["fault_status"] == "killed"
    assert all("test_invalid_quantity_is_rejected" in p["nodeid"]
               for p in quantity["validation"]["probes"])

    # The pilot's own tests never try a negative price: the rule is in the code but unwritten.
    price = guard_id(without["contracts"], "Product.__post_init__", "price < 0")
    assert by_id(without)[price]["status"] == "hypothesis"
    assert by_id(without)[price]["validation"]["result"] == "guard_removal_undetected"
    assert by_id(without)[price]["validation"]["probes"] == []
    # A supplied probe that passes turns it into a verified contract, with that probe attached.
    assert by_id(with_probes)[price]["status"] == "verified"
    assert [p["suite"] for p in by_id(with_probes)[price]["validation"]["probes"]] == ["probes"]


def test_surviving_fault_is_not_called_equivalent_or_weak(contract_map):
    """No test notices the removal of this guard. That is all the evidence says."""
    result = contract_map()
    contract = by_id(result)[guard_id(result["contracts"], "Cart.remove", "sku not in")]
    assert (contract["status"], contract["validation"]["result"]) == (
        "hypothesis", "guard_removal_undetected")
    assert contract["validation"]["fault_status"] == "survived"
    assert "has not been established" in contract["validation"]["note"]
    (survivor,) = [f for f in result["faults"] if f["status"] == "survived"]
    assert survivor["id"] == contract["validation"]["fault"]
    assert "equivalence_evidence" not in survivor


def test_every_fault_has_exactly_one_state(contract_map):
    result = contract_map(hypotheses=False)
    summary, faults = result["summary"], result["faults"]
    assert set(result["fault_states"]) == {"killed", "survived", "equivalent", "unexecutable"}
    assert {f["status"] for f in faults} <= set(result["fault_states"])
    assert len(faults) == summary["faults_injected"] == 45
    assert (summary["faults_killed"] + summary["faults_survived"] + summary["faults_equivalent"]
            + summary["faults_unexecutable"]) == 45
    assert all((f["detected_by"] > 0) == (f["status"] == "killed") for f in faults)
    # Without the supplied probes, two faults survive: the price guard and the Cart.remove guard.
    # Without the supplied probes, the pilot's own tests miss three guards.
    assert {f["symbol"] for f in faults if f["status"] == "survived"} == {
        "Cart.remove", "Product.__post_init__", "to_money"}
    assert summary["faults_equivalent"] == 0   # none is demonstrably equivalent, so none is claimed


def test_contract_ids_are_stable_unique_and_referenceable(contract_map, runner_name):
    import re
    result, again = contract_map(runner_name), contract_map("local", fresh=True)
    ids = [c["id"] for c in result["contracts"]]
    assert len(ids) == len(set(ids)) == 34
    assert all(re.fullmatch(r"(test|guard|hyp):[A-Za-z0-9_.:/<>\-]+", i) for i in ids)
    assert ids == [c["id"] for c in again["contracts"]]
    # Guard ids name the file, the symbol and a digest of what the guard says.
    assert guard_id(result["contracts"], "_parse_percent", "percent <= 0").startswith(
        "guard:shopcart/discounts.py:_parse_percent:")
    # The shipped package refers to contracts by exactly these ids.
    shipped = json.loads((PACKAGES / VALID / "contracts.json").read_text())
    assert {c["id"] for c in shipped["contracts"]} <= set(ids)


def test_fingerprint_is_identical_on_both_runners(contract_map):
    from conftest import docker_available
    if not docker_available():
        pytest.skip("Docker daemon not reachable: cross-runner fingerprint was NOT compared")
    local, docker = contract_map("local"), contract_map("docker")
    assert local["environment"]["runner"] == "local" and docker["environment"]["runner"] == "docker"
    assert local["fingerprint"] == docker["fingerprint"]
    assert local["faults"] == docker["faults"]

    def comparable(contract):
        # Notes quote pytest's own failure message, whose wording differs between the
        # pytest versions of the two environments. Everything else must be identical.
        validation = {k: v for k, v in contract["validation"].items() if k != "note"}
        return {**contract, "validation": validation}

    assert [comparable(c) for c in local["contracts"]] == [comparable(c) for c in docker["contracts"]]
    assert local["environment"]["pytest_version"] != docker["environment"]["pytest_version"]


def test_verified_contracts_carry_sensitivity_evidence_that_checks_out(contract_map, runner_name):
    """For every verified contract, re-read the raw logs: a named fault really fails a probe."""
    result = contract_map(runner_name)
    root = Path(result["map_dir"])
    faults = {f["id"]: f for f in result["faults"]}
    checked = 0
    for contract in result["contracts"]:
        validation = contract["validation"]
        if contract["status"] != "verified":
            continue
        assert validation["faults_detected"] >= 1 and validation["detected_faults"], contract["id"]
        fault = faults[validation["detected_faults"][0]]
        assert fault["status"] == "killed"
        passing = set()
        for report in (root / fault["logs"]).glob("*.json"):
            if report.stem in ("repo_tests", "probes"):
                for test in json.loads(report.read_text())["tests"]:
                    if test["phases"].get("call", {}).get("outcome") == "passed":
                        passing.add((report.stem, test["nodeid"]))
        # A probe detects the fault when it no longer passes: it fails, or its file can
        # no longer even be collected (for example, a module-level Product(...) now raises).
        probes = {(p["suite"], p["nodeid"]) for p in validation["probes"]}
        assert probes - passing, (contract["id"], fault["id"])
        checked += 1
    assert checked == 28


def test_every_reference_in_the_contract_map_exists(contract_map, runner_name):
    result = contract_map(runner_name)
    root = Path(result["map_dir"])
    references = referenced_files(json.loads((root / "contract-map.json").read_text()))
    assert len(references) >= 45
    assert [r for r in references if not (root / r).exists()] == []


def test_supplied_hypotheses_are_judged_only_by_execution(contract_map):
    contracts = by_id(contract_map())
    expected = {
        "hyp:negative-price-rejected": ("verified", "passes_and_detects_faults"),
        "hyp:non-finite-money-rejected": ("verified", "passes_and_detects_faults"),
        "hyp:discount-codes-case-sensitive": ("refuted", "probe_assertion_failed"),
        "hyp:quantity-upper-limit": ("refuted", "probe_assertion_failed"),      # DID NOT RAISE
        "hyp:stock-is-checked": ("hypothesis", "probe_collection_error"),
        "hyp:errors-share-a-base-class": ("hypothesis", "passes_but_insensitive"),
        "hyp:totals-never-negative": ("hypothesis", "no_probe"),
    }
    for contract_id, (status, result) in expected.items():
        contract = contracts[contract_id]
        assert (contract["status"], contract["validation"]["result"]) == (status, result), contract_id
        # Where a claim came from is never erased, verified or not.
        assert contract["origin"].startswith("fixture:")
        assert contract["evidence_type"] == "supplied_hypothesis"
    # The probe constructs a Product, which converts its price: both symbols are related.
    assert contracts["hyp:negative-price-rejected"]["related"] == [
        {"file": "shopcart/catalog.py", "symbol": "Product.__post_init__"},
        {"file": "shopcart/money.py", "symbol": "to_money"}]
    assert {c["origin"] for c in contracts.values()} == {
        "repository", "static_analysis",
        "fixture: hand-written stand-in for model output (no model was called)"}
    assert "ten" in contracts["hyp:discount-codes-case-sensitive"]["validation"]["note"]


def test_repository_without_a_supported_language_is_a_harness_error(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "README.md").write_text("no code here")
    with pytest.raises(HarnessError, match="no language adapter"):
        detect_adapter(tmp_path)


def test_bad_hypotheses_file_is_refused(pilot_repo, tmp_path):
    bad = tmp_path / "h.json"
    bad.write_text(json.dumps({"hypotheses": [{"id": "x", "description": "claim", "probe": None}]}))
    with pytest.raises(HarnessError, match="proposed_by"):
        extract_contracts(pilot_repo, load_profile(PROFILE), make_runner("local"),
                          tmp_path / "out", bad)


def test_command_line_extraction(pilot_repo, tmp_path, capsys):
    exit_code = main(["contracts", "extract", "--repo", str(pilot_repo), "--profile", str(PROFILE),
                      "--hypotheses", str(HYPOTHESES), "--runner", "local",
                      "--out", str(tmp_path / "map")])
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "28 verified, 4 hypotheses, 2 refuted" in output
    assert (tmp_path / "map" / "probes" / "test_hyp_negative_price_rejected.py").is_file()


# ---------------------------------------------------------------- feeding packages and verification


def _package(tmp_path, reference_patch=None):
    package = tmp_path / "package"
    shutil.copytree(PACKAGES / VALID, package)
    (package / "contracts.json").unlink()
    manifest = json.loads((package / "task.json").read_text())
    manifest["mutation"]["generate"] = False
    manifest["mutation"]["declared"] = manifest["mutation"]["declared"][:1]
    (package / "task.json").write_text(json.dumps(manifest))
    if reference_patch:
        (package / "reference.patch").write_text(reference_patch)
    return package


def test_attached_contracts_are_enforced_and_reported(contract_map, pilot_repo, tmp_path, runner_name):
    result = contract_map(runner_name)
    package = _package(tmp_path)
    document = attach_contracts(package, result["map_dir"])
    attached = {c["id"] for c in document["contracts"]}
    # Only verified contracts about the touched file are attached.
    assert document["touched_files"] == ["shopcart/discounts.py"]
    guard = guard_id(result["contracts"], "_parse_percent", "percent <= 0")
    assert guard in attached
    assert "test:tests/test_discounts.py::test_unknown_config_keys_are_ignored" in attached
    assert not any(c.startswith("guard:shopcart/cart.py") for c in attached)
    statuses = by_id(result)
    assert all(statuses[c]["status"] == "verified" for c in attached)

    evidence = verify_package(package, pilot_repo, make_runner(runner_name), tmp_path / "ev", jobs=4)
    assert evidence["verdict"] == "accepted"
    assert {c["id"] for c in evidence["contracts"]} == attached
    for contract in evidence["contracts"]:
        assert contract["holds_at_base"] and contract["preserved_by_reference"]
        assert all(p["world1"] == p["world2"] == "passed" for p in contract["probes"])
    # Each guard contract contributes a mutant that removes the guard, and its probes kill it.
    mutants = {m["id"]: m for m in evidence["runs"][0]["worlds"]["world3"]["mutants"]}
    assert mutants[f"contract:{guard}"]["status"] == "killed"
    assert f"contract:{guard}" in next(c for c in evidence["contracts"] if c["id"] == guard)["mutants_killed"]
    assert evidence["summary"]["world3"]["contract"] == 4
    # Origin and evidence type travel with the contract into the package and the evidence.
    assert {c["origin"] for c in document["contracts"]} == {"repository", "static_analysis"}
    assert all(c["origin"] == statuses[c["id"]]["origin"] for c in evidence["contracts"])


def _reference_that_also_drops_the_price_check():
    """The valid reference patch plus a hunk deleting the negative-price guard."""
    source = (ROOT / "pilot" / "unwritten-pilot" / "shopcart" / "catalog.py").read_text()
    damaged = source.replace(
        '        if price < 0:\n            raise ValueError("price must not be negative")\n', "")
    assert damaged != source
    hunk = "".join(difflib.unified_diff(
        source.splitlines(keepends=True), damaged.splitlines(keepends=True),
        "a/shopcart/catalog.py", "b/shopcart/catalog.py"))
    return (PACKAGES / VALID / "reference.patch").read_text() + hunk


def test_contracts_catch_a_regression_the_existing_tests_miss(contract_map, pilot_repo, tmp_path):
    patch = _reference_that_also_drops_the_price_check()

    # Without contract archaeology the harness has no way to notice: accepted.
    (tmp_path / "plain").mkdir()
    plain = _package(tmp_path / "plain", patch)
    blind = verify_package(plain, pilot_repo, make_runner("local"), tmp_path / "ev-plain")
    assert blind["verdict"] == "accepted"

    # With the verified contracts attached, the same reference is rejected, by name.
    (tmp_path / "guarded").mkdir()
    guarded = _package(tmp_path / "guarded", patch)
    document = attach_contracts(guarded, contract_map()["map_dir"])
    assert "shopcart/catalog.py" in document["touched_files"]
    assert (guarded / "tests" / "contracts" / "test_hyp_negative_price_rejected.py").is_file()
    evidence = verify_package(guarded, pilot_repo, make_runner("local"), tmp_path / "ev-guarded")
    assert evidence["verdict"] == "rejected"
    assert codes(evidence) == {"W2_REGRESSION_BROKEN", "W2_CONTRACT_VIOLATED"}
    price_guard = guard_id(contract_map()["contracts"], "Product.__post_init__", "price < 0")
    violated = {item.split(": ", 1)[0] for item in reason(evidence, "W2_CONTRACT_VIOLATED")["items"]}
    assert violated == {price_guard, "hyp:negative-price-rejected"}     # exactly these, no others
    assert reason(evidence, "W2_REGRESSION_BROKEN")["items"] == [
        "_unwritten/contracts/test_hyp_negative_price_rejected.py::test_negative_price_is_rejected [failed]"]
    origins = {c["id"]: c["origin"] for c in evidence["contracts"]}
    assert origins["hyp:negative-price-rejected"].startswith("fixture:")
    assert origins[price_guard] == "static_analysis"
    assert evidence["runs"][0]["worlds"]["world1"]["status"] == "passed"
    report = {c["id"]: c for c in evidence["contracts"]}
    assert report["hyp:negative-price-rejected"]["holds_at_base"]
    assert not report["hyp:negative-price-rejected"]["preserved_by_reference"]


REPLACEMENT_TEST = """
from decimal import Decimal

import pytest

from shopcart import Product


def test_negative_price_is_allowed_for_refund_lines():
    try:
        product = Product("RF-1", "Refund", "-5.00", "refunds")
    except ValueError:
        pytest.fail("a negative price must be accepted")
    assert product.price == Decimal("-5.00")
"""
REPLACEMENT_ID = "tests/feature/test_refund_lines.py::test_negative_price_is_allowed_for_refund_lines"


def _override_package(tmp_path, contract_map, overrides):
    """A task that deliberately removes the negative-price rule, with declared overrides."""
    package = _package(tmp_path, _reference_that_also_drops_the_price_check())
    (package / "tests" / "feature" / "test_refund_lines.py").write_text(REPLACEMENT_TEST)
    attach_contracts(package, contract_map()["map_dir"])
    manifest = json.loads((package / "task.json").read_text())
    manifest["contract_overrides"] = [
        {"contract_id": contract_id, "reason": "refund lines carry negative prices",
         "new_behaviour": "a product may have a negative price", "replacement_tests": tests}
        for contract_id, tests in overrides]
    (package / "task.json").write_text(json.dumps(manifest))
    return package


def test_declared_override_lets_a_task_change_a_contract_with_evidence(contract_map, pilot_repo, tmp_path):
    price_guard = guard_id(contract_map()["contracts"], "Product.__post_init__", "price < 0")
    package = _override_package(tmp_path, contract_map, [
        (price_guard, [REPLACEMENT_ID]), ("hyp:negative-price-rejected", [REPLACEMENT_ID])])
    evidence = verify_package(package, pilot_repo, make_runner("local"), tmp_path / "ev")
    assert evidence["verdict"] == "accepted"

    report = {c["id"]: c for c in evidence["contracts"]}
    for contract_id in (price_guard, "hyp:negative-price-rejected"):
        entry = report[contract_id]
        assert entry["holds_at_base"] and not entry["preserved_by_reference"]   # really changed
        override = entry["override"]
        assert override["reason"] and override["new_behaviour"]
        # The replacement test fails before the change and passes after it.
        assert override["replacement_tests"] == [
            {"test": REPLACEMENT_ID, "world1": "failed", "world2": "passed"}]
        # Evidence that every other attached contract is still protected.
        others = override["other_contracts"]
        assert others["attached"] == len(report) - 2 >= 10
        assert others["preserved_by_reference"] == others["attached"] and others["not_preserved"] == []
    assert all(c["preserved_by_reference"] for c in report.values() if "override" not in c)
    recorded = [m for m in evidence["process"]["manual_interventions"] if m["type"] == "contract_override"]
    assert {m["contract_id"] for m in recorded} == {price_guard, "hyp:negative-price-rejected"}
    # The overridden guard contributes no guard-removal mutant; the untouched ones still do.
    contract_mutants = [m["id"] for m in evidence["runs"][0]["worlds"]["world3"]["mutants"]
                        if m["origin"] == "contract"]
    assert f"contract:{price_guard}" not in contract_mutants and len(contract_mutants) >= 4


def test_override_covers_only_the_contract_it_names(contract_map, pilot_repo, tmp_path):
    price_guard = guard_id(contract_map()["contracts"], "Product.__post_init__", "price < 0")
    package = _override_package(tmp_path, contract_map, [
        ("hyp:negative-price-rejected", [REPLACEMENT_ID])])
    evidence = verify_package(package, pilot_repo, make_runner("local"), tmp_path / "ev")
    assert codes(evidence) == {"W2_CONTRACT_VIOLATED"}
    assert [i.split(": ", 1)[0] for i in reason(evidence, "W2_CONTRACT_VIOLATED")["items"]] == [price_guard]


def test_unjustified_overrides_are_rejected(contract_map, pilot_repo, tmp_path):
    contracts = contract_map()["contracts"]
    price_guard = guard_id(contracts, "Product.__post_init__", "price < 0")
    untouched = guard_id(contracts, "_parse_percent", "percent <= 0")
    package = _override_package(tmp_path, contract_map, [
        (price_guard, [REPLACEMENT_ID]), ("hyp:negative-price-rejected", [REPLACEMENT_ID]),
        (untouched, ["tests/feature/test_refund_lines.py::test_that_does_not_exist"]),
        ("guard:shopcart/nowhere.py:f:00000000", [REPLACEMENT_ID])])
    evidence = verify_package(package, pilot_repo, make_runner("local"), tmp_path / "ev")
    assert codes(evidence) == {"OVERRIDE_NOT_NEEDED", "OVERRIDE_REPLACEMENT_MISSING",
                               "OVERRIDE_UNKNOWN_CONTRACT"}
    assert reason(evidence, "OVERRIDE_NOT_NEEDED")["items"] == [untouched]
    assert reason(evidence, "OVERRIDE_UNKNOWN_CONTRACT")["items"] == ["guard:shopcart/nowhere.py:f:00000000"]
    assert untouched in reason(evidence, "OVERRIDE_REPLACEMENT_MISSING")["items"][0]


def test_malformed_override_is_a_package_error(variant):
    def edit(package, manifest):
        manifest["contract_overrides"] = [{"contract_id": "hyp:x", "reason": "because"}]
    evidence = variant(edit)
    assert codes(evidence) == {"PACKAGE_INVALID"}
    assert "new_behaviour" in reason(evidence, "PACKAGE_INVALID")["items"][0]


def test_contract_that_does_not_hold_at_base_is_not_accepted_as_protection(variant):
    def edit(package, manifest):
        document = json.loads((package / "contracts.json").read_text())
        document["contracts"][0]["probes"][0]["nodeid"] = "tests/test_discounts.py::test_gone"
        (package / "contracts.json").write_text(json.dumps(document))
    evidence = variant(edit, keep_contracts=True)
    assert "W1_CONTRACT_PROBE_NOT_PASSING" in codes(evidence)


def test_attach_refuses_a_map_from_another_commit(contract_map, tmp_path):
    map_dir = tmp_path / "map"
    shutil.copytree(contract_map()["map_dir"], map_dir)
    document = json.loads((map_dir / "contract-map.json").read_text())
    document["repository"]["commit"] = "0" * 40
    (map_dir / "contract-map.json").write_text(json.dumps(document))
    with pytest.raises(HarnessError, match="but the package pins"):
        attach_contracts(_package(tmp_path), map_dir)


def test_shipped_package_carries_the_committed_contract_map(contract_map):
    """The contracts.json in the repository matches what extraction produces today."""
    shipped = json.loads((PACKAGES / VALID / "contracts.json").read_text())
    assert shipped["contract_map_fingerprint"] == contract_map()["fingerprint"]
    committed = json.loads((ROOT / "evidence" / "contract-map-unwritten-pilot" / "contract-map.json").read_text())
    assert committed["fingerprint"] == contract_map()["fingerprint"]
