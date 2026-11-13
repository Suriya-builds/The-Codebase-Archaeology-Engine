"""Damaged copies of the valid package: each defect must be named correctly."""
import textwrap

import pytest

from conftest import PACKAGES, VALID, codes, reason
from unwritten.cli import main
from unwritten.errors import HarnessError
from unwritten.runners import make_runner
from unwritten.verify import verify_package


def _feature_test(package, body):
    (package / "tests" / "feature" / "test_extra.py").write_text(textwrap.dedent(body))


def test_feature_test_that_cannot_be_imported_at_base(variant):
    def edit(package, manifest):
        _feature_test(package, """
            from shopcart.discounts import Eligibility

            def test_eligibility_default():
                assert Eligibility().categories is None
        """)
    evidence = variant(edit)
    assert codes(evidence) == {"W1_FEATURE_FAILS_WITHOUT_ASSERTION"}
    assert reason(evidence, "W1_FEATURE_FAILS_WITHOUT_ASSERTION")["items"] == [
        "_unwritten/feature/test_extra.py"]


def test_feature_test_that_fails_with_an_error_instead_of_an_assertion(variant):
    def edit(package, manifest):
        _feature_test(package, """
            from shopcart import Discount

            def test_parsed_discount_exposes_eligibility():
                discount = Discount.from_config(
                    {"code": "T", "percent": 5, "eligibility": {"min_subtotal": "1"}})
                assert discount.eligibility is not None
        """)
    evidence = variant(edit)
    assert codes(evidence) == {"W1_FEATURE_FAILS_WITHOUT_ASSERTION"}
    (item,) = reason(evidence, "W1_FEATURE_FAILS_WITHOUT_ASSERTION")["items"]
    assert "builtins.AttributeError" in item


def test_skipped_feature_test_is_not_accepted_as_a_failure(variant):
    def edit(package, manifest):
        _feature_test(package, """
            import pytest

            @pytest.mark.skip(reason="later")
            def test_placeholder():
                assert False
        """)
    assert "W1_FEATURE_FAILS_WITHOUT_ASSERTION" in codes(variant(edit))


def test_reference_that_breaks_existing_behaviour(variant):
    def edit(package, manifest):
        patch = package / "reference.patch"
        old = "+            return round_money(cart.subtotal() * self.percent / 100)"
        text = patch.read_text()
        assert old in text
        patch.write_text(text.replace(old, "+            return ZERO"))
    evidence = variant(edit)
    assert codes(evidence) == {"W2_REGRESSION_BROKEN"}
    worlds = evidence["runs"][0]["worlds"]
    assert worlds["world1"]["status"] == "passed"
    assert worlds["world3"]["status"] == "not_run"  # mutants are never judged against a broken reference
    assert any("tests/test_discounts.py" in item
               for item in reason(evidence, "W2_REGRESSION_BROKEN")["items"])


def test_reference_that_does_not_apply(variant):
    def edit(package, manifest):
        patch = package / "reference.patch"
        patch.write_text(patch.read_text().replace("-from shopcart.money import round_money",
                                                   "-from shopcart.money import something_else"))
    assert codes(variant(edit)) == {"REFERENCE_PATCH_DOES_NOT_APPLY"}


def test_reference_that_edits_repository_tests(variant):
    def edit(package, manifest):
        with open(package / "reference.patch", "a") as patch:
            patch.write("diff --git a/tests/test_new.py b/tests/test_new.py\n"
                        "new file mode 100644\n--- /dev/null\n+++ b/tests/test_new.py\n"
                        "@@ -0,0 +1,2 @@\n+def test_new():\n+    assert True\n")
    evidence = variant(edit)
    assert "REFERENCE_MODIFIES_TESTS" in codes(evidence)
    assert reason(evidence, "REFERENCE_MODIFIES_TESTS")["items"] == ["tests/test_new.py"]


def test_package_without_any_mutant(variant):
    assert codes(variant(lambda package, manifest: None, declared=())) == {"W3_NO_MUTANTS"}


def test_declared_mutant_that_does_not_apply(variant):
    def edit(package, manifest):
        (package / "mutants" / "ignore-min-subtotal.patch").write_text(
            "--- a/shopcart/discounts.py\n+++ b/shopcart/discounts.py\n"
            "@@ -1,1 +1,1 @@\n-no such line\n+replacement\n")
    assert codes(variant(edit)) == {"W3_MUTANT_INVALID"}


def _weaken(package, manifest):
    source = PACKAGES / "invalid-surviving-mutant" / "tests" / "feature" / "test_discount_eligibility.py"
    (package / "tests" / "feature" / "test_discount_eligibility.py").write_text(source.read_text())


def test_waived_survivor_is_accepted_and_recorded_as_manual_intervention(variant):
    def edit(package, manifest):
        _weaken(package, manifest)
        manifest["mutation"]["waivers"] = [{
            "mutant_id": "declared:min-subtotal-checks-whole-cart",
            "justification": "test fixture", "approved_by": "reviewer"}]
    evidence = variant(edit, declared=("min-subtotal-checks-whole-cart",))
    assert evidence["verdict"] == "accepted"
    (mutant,) = evidence["runs"][0]["worlds"]["world3"]["mutants"]
    assert mutant["status"] == "survived" and mutant["waiver"]["approved_by"] == "reviewer"
    assert evidence["summary"]["world3"]["survived_waived"] == 1
    assert evidence["process"]["manual_interventions"][0]["type"] == "mutant_waiver"


def test_waiver_for_a_mutant_that_does_not_exist(variant):
    def edit(package, manifest):
        manifest["mutation"]["waivers"] = [{
            "mutant_id": "declared:gone", "justification": "stale", "approved_by": "reviewer"}]
    assert codes(variant(edit)) == {"W3_WAIVER_UNKNOWN_MUTANT"}


def test_result_that_changes_between_runs(variant, tmp_path):
    marker = tmp_path / "seen"

    def edit(package, manifest):
        (package / "tests" / "regression" / "test_flaky.py").write_text(textwrap.dedent(f"""
            from pathlib import Path

            def test_passes_only_the_first_time():
                marker = Path({str(marker)!r})
                first_time = not marker.exists()
                marker.write_text("seen")
                assert first_time
        """))
    evidence = variant(edit)
    assert evidence["verdict"] == "rejected"
    assert "REPEATABILITY_MISMATCH" in codes(evidence)
    assert not evidence["repeatability"]["equivalent"]
    assert len(set(evidence["repeatability"]["fingerprints"])) == 2


def test_test_that_hangs_is_cut_off(variant):
    def edit(package, manifest):
        manifest["execution"]["timeout_seconds"] = 3
        (package / "tests" / "regression" / "test_hang.py").write_text(
            "import time\n\ndef test_hang():\n    time.sleep(60)\n")
    evidence = variant(edit)
    assert "EXECUTION_TIMEOUT" in codes(evidence)
    assert reason(evidence, "EXECUTION_TIMEOUT")["items"] == ["regression_package"]


def test_malformed_package_is_rejected_with_exit_status_1(tmp_path, pilot_repo, capsys):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "task.json").write_text("{}")
    exit_code = main(["verify", str(tmp_path / "pkg"), "--repo", str(pilot_repo),
                      "--runner", "local", "--out", str(tmp_path / "out")])
    assert exit_code == 1
    assert "PACKAGE_INVALID" in capsys.readouterr().out


def test_harness_problems_are_errors_not_rejections(tmp_path, pilot_repo, capsys):
    missing_repo = main(["verify", str(PACKAGES / VALID), "--repo", str(tmp_path / "nowhere"),
                         "--runner", "local", "--out", str(tmp_path / "out1")])
    assert missing_repo == 2 and "HARNESS ERROR" in capsys.readouterr().err

    (tmp_path / "precious").mkdir()
    (tmp_path / "precious" / "notes.txt").write_text("keep me")
    with pytest.raises(HarnessError, match="refusing to overwrite"):
        verify_package(PACKAGES / VALID, pilot_repo, make_runner("local"), tmp_path / "precious")
    with pytest.raises(HarnessError, match="at least 2 runs"):
        verify_package(PACKAGES / VALID, pilot_repo, make_runner("local"), tmp_path / "o", repeat=1)
