"""M2: contract archaeology, and how it feeds task packages and verification."""
import difflib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import HYPOTHESES, PACKAGES, PROFILE, ROOT, VALID, codes, reason
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
    without, with_probes = by_id(contract_map(hypotheses=False)), by_id(contract_map())
    quantity = without["guard:shopcart/cart.py:_check_quantity:2"]
    assert quantity["status"] == "verified"
    assert quantity["validation"]["result"] == "guard_removal_detected"
    assert all("test_invalid_quantity_is_rejected" in p["nodeid"]
               for p in quantity["validation"]["probes"])

    # The pilot's own tests never try a negative price: the rule is in the code but unwritten.
    price = "guard:shopcart/catalog.py:Product.__post_init__:1"
    assert without[price]["status"] == "hypothesis"
    assert without[price]["validation"]["result"] == "unprotected"
    assert without[price]["validation"]["probes"] == []
    # A supplied probe that passes turns it into a verified contract, with that probe attached.
    assert with_probes[price]["status"] == "verified"
    assert [p["suite"] for p in with_probes[price]["validation"]["probes"]] == ["probes"]

    # Removing this check changes nothing observable (the next line raises the same error),
    # so no probe can verify it and it must stay a hypothesis.
    redundant = with_probes["guard:shopcart/cart.py:Cart.remove:1"]
    assert (redundant["status"], redundant["validation"]["result"]) == ("hypothesis", "unprotected")


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
    assert contracts["hyp:negative-price-rejected"]["related"] == [
        {"file": "shopcart/catalog.py", "symbol": "Product.__post_init__"}]
    assert "ten" in contracts["hyp:discount-codes-case-sensitive"]["validation"]["note"]


def test_undetected_faults_are_reported(contract_map):
    result = contract_map(hypotheses=False)
    summary = result["summary"]
    assert summary["faults_injected"] == 45
    assert summary["faults_detected"] + summary["faults_undetected"] + summary["faults_timed_out"] == 45
    assert len(result["unprotected_behaviour"]) == summary["faults_undetected"] >= 2
    assert {u["symbol"] for u in result["unprotected_behaviour"]} >= {"Product.__post_init__", "Cart.remove"}


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
    assert "guard:shopcart/discounts.py:_parse_percent:2" in attached
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
    guard = "guard:shopcart/discounts.py:_parse_percent:2"
    assert mutants[f"contract:{guard}"]["status"] == "killed"
    assert f"contract:{guard}" in next(c for c in evidence["contracts"] if c["id"] == guard)["mutants_killed"]
    assert evidence["summary"]["world3"]["contract"] == 4


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
    violated = " | ".join(reason(evidence, "W2_CONTRACT_VIOLATED")["items"])
    assert "guard:shopcart/catalog.py:Product.__post_init__:1" in violated
    assert "hyp:negative-price-rejected" in violated
    assert evidence["runs"][0]["worlds"]["world1"]["status"] == "passed"
    report = {c["id"]: c for c in evidence["contracts"]}
    assert report["hyp:negative-price-rejected"]["holds_at_base"]
    assert not report["hyp:negative-price-rejected"]["preserved_by_reference"]


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
