"""The three shipped packages, verified end to end on every available runner."""
from pathlib import Path

from conftest import PACKAGES, VALID, codes, reason
from unwritten.cli import main
from unwritten.verify import EVIDENCE_FILE


def _worlds(evidence, run=0):
    return evidence["runs"][run]["worlds"]


def test_valid_package_is_accepted_with_full_evidence(verified, runner_name):
    evidence = verified(VALID, runner_name)
    assert evidence["verdict"] == "accepted" and evidence["reasons"] == []
    assert evidence["environment"]["runner"] == runner_name
    assert evidence["process"]["llm_calls"] == 0
    assert evidence["repository"]["commit"] == "667512bac0534ea742a7a7c3e3262142ac914c30"

    for run in range(2):
        worlds = _worlds(evidence, run)
        # World 1: every feature test fails, and fails through an assertion.
        feature = worlds["world1"]["suites"]["feature"]
        assert len(feature["tests"]) == 25 and feature["collection_errors"] == []
        assert {t["outcome"] for t in feature["tests"]} == {"failed"}
        assert {t["category"] for t in feature["tests"]} == {"assertion"}
        assert all(t["exc_type"] and t["detail"] for t in feature["tests"])
        for suite in ("regression_package", "regression_repo"):
            tests = worlds["world1"]["suites"][suite]["tests"]
            assert tests and {t["outcome"] for t in tests} == {"passed"}
        # World 2: everything passes.
        for suite in worlds["world2"]["suites"].values():
            assert suite["tests"] and {t["outcome"] for t in suite["tests"]} == {"passed"}
        # World 3: every mutant is killed and the killing tests are named.
        mutants = worlds["world3"]["mutants"]
        assert sum(m["origin"] == "declared" for m in mutants) == 4
        assert sum(m["origin"] == "generated" for m in mutants) >= 20
        for mutant in mutants:
            assert mutant["status"] == "killed", mutant["id"]
            assert mutant["killed_by"] and all(k["test"] and k["suite"] for k in mutant["killed_by"])
        assert worlds["world3"]["summary"]["mutation_score"] == 1.0

    # A backward-compatibility mutant must be caught by the repository's own tests.
    compat = next(m for m in _worlds(evidence)["world3"]["mutants"]
                  if m["id"] == "declared:eligibility-required")
    assert "regression_repo" in {k["suite"] for k in compat["killed_by"]}


def test_evidence_on_disk_is_complete_and_inspectable(verified, runner_name):
    evidence = verified(VALID, runner_name)
    root = Path(evidence["evidence_dir"])
    assert (root / EVIDENCE_FILE).is_file()
    worlds = _worlds(evidence)
    for world in ("world1", "world2"):
        for suite in worlds[world]["suites"].values():
            assert (root / suite["log"]).read_text().startswith("$ pytest")
    for mutant in worlds["world3"]["mutants"]:
        diff = (root / mutant["diff"]).read_text()
        assert diff.count("\n+") >= 1
        assert (root / mutant["logs"] / "feature.log").is_file()


def test_repeated_runs_are_equivalent(verified, runner_name):
    for package in (VALID, "invalid-baseline-passes", "invalid-surviving-mutant"):
        repeatability = verified(package, runner_name)["repeatability"]
        assert repeatability["runs"] == 2 and repeatability["equivalent"]
        assert len(set(repeatability["fingerprints"])) == 1


def test_package_whose_feature_test_passes_at_base_is_rejected(verified, runner_name):
    evidence = verified("invalid-baseline-passes", runner_name)
    assert evidence["verdict"] == "rejected"
    assert codes(evidence) == {"W1_FEATURE_PASSES_AT_BASE"}
    assert reason(evidence, "W1_FEATURE_PASSES_AT_BASE")["items"] == [
        "_unwritten/feature/test_discount_happy_path.py::"
        "test_discount_applies_when_cart_meets_the_minimum"]
    worlds = _worlds(evidence)
    assert worlds["world1"]["status"] == "failed"
    # The defect is in the tests, not the implementation: the other worlds still pass.
    assert worlds["world2"]["status"] == "passed" and worlds["world3"]["status"] == "passed"


def test_package_with_surviving_mutant_is_rejected(verified, runner_name):
    evidence = verified("invalid-surviving-mutant", runner_name)
    assert evidence["verdict"] == "rejected"
    assert codes(evidence) == {"W3_MUTANT_SURVIVED"}
    worlds = _worlds(evidence)
    assert worlds["world1"]["status"] == "passed" and worlds["world2"]["status"] == "passed"
    survivors = reason(evidence, "W3_MUTANT_SURVIVED")["items"]
    assert "declared:min-subtotal-checks-whole-cart" in survivors          # required, declared
    assert "generated:CMP_BOUNDARY:shopcart/discounts.py:53:20" in survivors  # boundary untested
    recorded = {m["id"]: m for m in worlds["world3"]["mutants"]}
    assert all(recorded[s]["status"] == "survived" and recorded[s]["killed_by"] == []
               for s in survivors)
    assert worlds["world3"]["summary"]["mutation_score"] < 1.0


def test_separate_invocations_agree(verified, pilot_repo, tmp_path, capsys):
    """Two independent command-line runs reach the same verdict and fingerprint."""
    first = verified(VALID, "local")
    exit_code = main(["verify", str(PACKAGES / VALID), "--repo", str(pilot_repo),
                      "--runner", "local", "--out", str(tmp_path / "second")])
    output = capsys.readouterr().out
    assert exit_code == 0 and "VERDICT           ACCEPTED" in output
    assert first["repeatability"]["fingerprints"][0][:12] in output
