"""M3a: schema 0.2, declared interfaces, static gates, variation, release states."""
import difflib
import json
import shutil
import textwrap
from types import SimpleNamespace

import pytest

from conftest import PACKAGES, ROOT, VALID, codes, reason
from unwritten.adapters.python import PythonAdapter
from unwritten.cli import main
from unwritten.errors import HarnessError
from unwritten.execution import run_in_sandbox
from unwritten.interface import match_declared
from unwritten.package import RELEASE_FILE, package_digest
from unwritten.release import package_status, release_package
from unwritten.runners import make_runner
from unwritten.variation import distance, variation_report
from unwritten.verify import verify_package

NEW_NAME = "pilot-cart-update-quantity"
FEATURE = "tests/feature/test_update_quantity.py::"


@pytest.fixture
def variant02(tmp_path, pilot_repo):
    """A damaged copy of the schema 0.2 fixture package, verified locally."""
    def build(edit, generate=False):
        package = tmp_path / "package"
        shutil.copytree(PACKAGES / NEW_NAME, package)
        (package / "contracts.json").unlink()
        manifest = json.loads((package / "task.json").read_text())
        manifest["mutation"]["generate"] = generate
        manifest["mutation"]["declared"] = manifest["mutation"]["declared"][:1]
        edit(package, manifest)
        (package / "task.json").write_text(json.dumps(manifest))
        return verify_package(package, pilot_repo, make_runner("local"), tmp_path / "evidence")
    return build


def _add_test(package, manifest, name, body, criteria=("AC-1",)):
    path = package / "tests" / "feature" / "test_extra.py"
    path.write_text(textwrap.dedent(body))
    manifest["test_criteria"][f"tests/feature/test_extra.py::{name}"] = list(criteria)


def _gates(evidence):
    return evidence["runs"][0]["worlds"]["gates"]


# ================================================================ World 1 extension


def test_task_adding_a_declared_public_name_is_accepted(verified, runner_name):
    evidence = verified(NEW_NAME, runner_name)
    assert evidence["verdict"] == "accepted" and evidence["reasons"] == []
    assert evidence["task"]["schema_version"] == "0.2"
    worlds = evidence["runs"][0]["worlds"]
    assert list(worlds) == ["gates", "world1", "world2", "world3"]
    summary = worlds["world1"]["summary"]
    assert summary["feature_tests"] == 15
    assert summary["feature_failing_by_declared_interface"] == 15
    assert summary["feature_failing_by_assertion"] == 0
    assert summary["feature_files_blocked_by_declared_interface"] == []
    # Attribution: every failure names the declared method, on the declared class, from test code.
    for test in worlds["world1"]["suites"]["feature"]["tests"]:
        assert test["outcome"] == "failed" and test["failure_class"] == "runtime_error"
        assert test["exc_type"] == "builtins.AttributeError"
        assert test["missing"] == {"kind": "attribute", "owner": "shopcart.cart.Cart",
                                   "name": "update_quantity"}
        assert test["raised_in_tests"] is True
        assert test["raised_in"] == "_unwritten/feature/test_update_quantity.py"
    # The same tests pass with the reference, and every mutant is killed.
    assert {t["outcome"] for t in worlds["world2"]["suites"]["feature"]["tests"]} == {"passed"}
    assert worlds["world3"]["summary"]["survived"] == 0
    assert worlds["world3"]["summary"]["killed"] >= 15
    assert evidence["repeatability"]["equivalent"]


def test_existing_schema_0_1_packages_run_without_the_new_gates(verified):
    evidence = verified(VALID, "local")
    assert evidence["task"]["schema_version"] == "0.1"
    assert list(evidence["runs"][0]["worlds"]) == ["world1", "world2", "world3"]
    assert "feature_failing_by_declared_interface" not in evidence["summary"]["world1"]


def test_schema_0_1_package_cannot_claim_schema_0_2_fields(variant):
    def edit(package, manifest):
        manifest["public_interface"] = [{"module": "shopcart.discounts", "name": "Eligibility",
                                         "kind": "class"}]
    evidence = variant(edit)
    assert codes(evidence) == {"PACKAGE_INVALID"}
    assert "needs schema_version 0.2" in reason(evidence, "PACKAGE_INVALID")["items"][0]


def test_test_that_depends_on_an_undeclared_name_is_rejected(variant02):
    def edit(package, manifest):
        _add_test(package, manifest, "test_clear_empties_the_cart", """
            from shopcart import Cart, Product

            def test_clear_empties_the_cart():
                cart = Cart()
                cart.add(Product("X", "X", "1", "misc"))
                cart.clear()
                assert cart.is_empty()
        """)
    evidence = variant02(edit)
    assert "W1_FEATURE_USES_UNDECLARED_INTERFACE" in codes(evidence)
    assert "W1_FEATURE_RUNTIME_ERROR" not in codes(evidence)
    (item,) = reason(evidence, "W1_FEATURE_USES_UNDECLARED_INTERFACE")["items"]
    assert item == ("_unwritten/feature/test_extra.py::test_clear_empties_the_cart "
                    "[attribute shopcart.cart.Cart.clear]")
    # The declared method's own tests are still accepted in the same run.
    assert evidence["summary"]["world1"]["feature_failing_by_declared_interface"] == 15


def test_missing_name_raised_inside_the_code_under_test_is_a_crash_not_an_interface(variant02):
    def edit(package, manifest):
        _add_test(package, manifest, "test_adding_a_cart_to_a_cart", """
            from shopcart import Cart

            def test_adding_a_cart_to_a_cart():
                Cart().add(Cart())       # AttributeError on .sku, raised in shopcart/cart.py
        """)
    evidence = variant02(edit)
    assert "W1_FEATURE_RUNTIME_ERROR" in codes(evidence)
    assert "W1_FEATURE_USES_UNDECLARED_INTERFACE" not in codes(evidence)
    (item,) = reason(evidence, "W1_FEATURE_RUNTIME_ERROR")["items"]
    assert "raised in shopcart/cart.py" in item


def test_unrelated_import_failure_is_a_collection_error(variant02):
    def edit(package, manifest):
        _add_test(package, manifest, "test_x", """
            import a_library_that_is_not_installed

            def test_x():
                assert False
        """)
    evidence = variant02(edit)
    assert "W1_FEATURE_COLLECTION_ERROR" in codes(evidence)
    assert "W1_FEATURE_USES_UNDECLARED_INTERFACE" not in codes(evidence)


BULK_MODULE = '''def update_many(cart, quantities):
    """Apply several quantity updates; quantities maps sku to quantity."""
    for sku, quantity in quantities.items():
        cart.update_quantity(sku, quantity)
'''
BULK_TEST = """
    from shopcart import Cart, Product
    from shopcart.bulk import update_many

    def test_update_many_applies_every_update():
        cart = Cart()
        cart.add(Product("A", "A", "1", "misc"))
        cart.add(Product("B", "B", "1", "misc"))
        update_many(cart, {"A": 3, "B": 0})
        assert [(line.product.sku, line.quantity) for line in cart.lines()] == [("A", 3)]
"""


def _add_bulk_module(package, manifest, declare):
    hunk = "".join(difflib.unified_diff([], BULK_MODULE.splitlines(keepends=True),
                                        "/dev/null", "b/shopcart/bulk.py"))
    with open(package / "reference.patch", "a") as patch:
        patch.write("diff --git a/shopcart/bulk.py b/shopcart/bulk.py\nnew file mode 100644\n" + hunk)
    manifest["acceptance_criteria"].append({"id": "AC-6", "text": "update_many applies each update."})
    _add_test(package, manifest, "test_update_many_applies_every_update", BULK_TEST, ["AC-6"])
    with open(package / "statement.md", "a") as statement:
        statement.write("\n- **AC-6** `update_many` applies every update in the mapping.\n")
        if declare:
            statement.write("\nModule `shopcart.bulk` is new and provides "
                            "`update_many(cart, quantities)`.\n")
    if declare:
        manifest["public_interface"].append({
            "module": "shopcart.bulk", "name": "update_many", "kind": "function",
            "signature": "update_many(cart, quantities)"})


def test_module_level_import_of_a_declared_new_module_is_accepted(variant02):
    evidence = variant02(lambda package, manifest: _add_bulk_module(package, manifest, True))
    assert evidence["verdict"] == "accepted", evidence["reasons"]
    summary = evidence["summary"]["world1"]
    assert summary["feature_files_blocked_by_declared_interface"] == ["_unwritten/feature/test_extra.py"]
    (error,) = evidence["runs"][0]["worlds"]["world1"]["suites"]["feature"]["collection_errors"]
    assert error["missing"]["kind"] == "import" and error["missing"]["module"] == "shopcart.bulk"
    assert error["raised_in_tests"] is True
    # With the reference in place the file is collected and its test passes.
    passed = {t["nodeid"] for t in evidence["runs"][0]["worlds"]["world2"]["suites"]["feature"]["tests"]}
    assert "_unwritten/feature/test_extra.py::test_update_many_applies_every_update" in passed


def test_module_level_import_of_an_undeclared_new_module_is_rejected(variant02):
    evidence = variant02(lambda package, manifest: _add_bulk_module(package, manifest, False))
    assert codes(evidence) == {"GATE_TEST_IMPORTS_UNDECLARED_NAME",
                               "W1_FEATURE_USES_UNDECLARED_INTERFACE"}
    assert "shopcart.bulk.update_many" in reason(evidence, "GATE_TEST_IMPORTS_UNDECLARED_NAME")["items"][0]
    assert reason(evidence, "W1_FEATURE_USES_UNDECLARED_INTERFACE")["items"] == [
        "_unwritten/feature/test_extra.py [import of module shopcart.bulk]"]


def _redeclare(signature=None, extra=None):
    def edit(package, manifest):
        statement = package / "statement.md"
        if signature:
            manifest["public_interface"][0]["signature"] = signature
            statement.write_text(statement.read_text().replace(
                "update_quantity(self, sku, quantity)", signature))
        if extra:
            manifest["public_interface"].append(extra)
            statement.write_text(statement.read_text() + f"\nAlso in `{extra['module']}`: "
                                 f"`{extra['signature']}`.\n")
    return edit


def test_reference_must_implement_the_declared_signature(variant02):
    evidence = variant02(_redeclare(signature="update_quantity(self, sku, quantity, strict=False)"))
    assert codes(evidence) == {"W2_DECLARED_SIGNATURE_MISMATCH"}
    assert "the source defines (self, sku, quantity)" in reason(
        evidence, "W2_DECLARED_SIGNATURE_MISMATCH")["items"][0]


def test_reference_must_implement_every_declared_name(variant02):
    evidence = variant02(_redeclare(extra={
        "module": "shopcart.cart", "name": "Cart.clear", "kind": "function",
        "signature": "clear(self)"}))
    assert codes(evidence) == {"W2_DECLARED_INTERFACE_NOT_IMPLEMENTED"}
    assert reason(evidence, "W2_DECLARED_INTERFACE_NOT_IMPLEMENTED")["items"] == ["shopcart.cart.Cart.clear"]


def test_declaring_a_name_that_already_exists_unchanged_is_rejected(variant02):
    evidence = variant02(_redeclare(extra={
        "module": "shopcart.cart", "name": "Cart.add", "kind": "function",
        "signature": "add(self, product, quantity=1)"}))
    assert codes(evidence) == {"GATE_INTERFACE_NOT_NEW"}


def test_private_names_cannot_be_declared_or_imported(variant02):
    def declare_private(package, manifest):
        manifest["public_interface"].append({"module": "shopcart.cart", "name": "_helper",
                                             "kind": "function", "signature": "_helper(x)"})
    assert codes(variant02(declare_private)) == {"PACKAGE_INVALID"}


def test_feature_test_importing_a_private_name_is_rejected(variant02):
    def edit(package, manifest):
        _add_test(package, manifest, "test_private", """
            from shopcart.cart import _check_quantity

            def test_private():
                assert _check_quantity(1) is None
        """)
    evidence = variant02(edit)
    assert "GATE_TEST_USES_PRIVATE_NAME" in codes(evidence)
    assert "shopcart.cart._check_quantity" in reason(evidence, "GATE_TEST_USES_PRIVATE_NAME")["items"][0]


# ---------------------------------------------------------------- the third kind of missing name


def test_missing_names_are_read_from_the_exception_not_guessed(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "checks").mkdir(parents=True)
    (workspace / "library.py").write_text("def area(width, height):\n    return width * height\n")
    (workspace / "checks" / "test_missing.py").write_text(textwrap.dedent("""
        import library

        def test_attribute():
            library.volume(1, 2, 3)

        def test_argument():
            library.area(1, 2, unit="cm")

        def test_import_inside_test():
            from library import perimeter

        def test_plain_type_error():
            library.area(1)
    """))
    settings = SimpleNamespace(python_path=["."], timeout_seconds=30)
    results, _ = run_in_sandbox(make_runner("local"), settings, workspace,
                                [{"name": "cases", "paths": ["checks"]}], tmp_path / "out", "logs")
    missing = {t.nodeid.split("::")[1]: t.missing for t in results["cases"].tests}
    assert missing["test_attribute"] == {"kind": "attribute", "owner": "library", "name": "volume"}
    assert missing["test_argument"] == {"kind": "argument", "callable": "area", "name": "unit",
                                        "source": "exception message"}
    assert missing["test_import_inside_test"] == {"kind": "import", "module": "library",
                                                  "name": "perimeter"}
    assert missing["test_plain_type_error"] is None   # a wrong call, not a missing name
    assert all(t.raised_in_tests for t in results["cases"].tests)


def test_declared_interface_matching():
    adapter = PythonAdapter()
    package = SimpleNamespace(public_interface=(
        {"module": "library", "name": "area", "kind": "function",
         "signature": "area(width, height, unit='m')"},
        {"module": "library", "name": "Shape.scale", "kind": "function",
         "signature": "scale(self, factor)"},
        {"module": "library.extras", "name": None, "kind": "module", "signature": None},
    ))
    match = lambda missing: match_declared(missing, package, adapter)
    assert match({"kind": "argument", "callable": "area", "name": "unit"})
    assert not match({"kind": "argument", "callable": "area", "name": "colour"})
    assert not match({"kind": "argument", "callable": "volume", "name": "unit"})
    assert match({"kind": "attribute", "owner": "library.Shape", "name": "scale"})
    assert not match({"kind": "attribute", "owner": "library.Shape", "name": "rotate"})
    assert not match({"kind": "attribute", "owner": "other.Shape", "name": "scale"})
    assert match({"kind": "import", "module": "library", "name": "area"})
    assert match({"kind": "import", "module": "library.extras", "name": None})
    assert not match({"kind": "import", "module": "library", "name": "perimeter"})
    assert not match({"kind": "import", "module": "library.other", "name": None})


def test_signatures_are_compared_by_parameters():
    adapter = PythonAdapter()
    source = b"class C:\n    def f(self, a, b=1, *args, key, flag=False, **extra):\n        pass\n    limit = 3\n"
    found = adapter.member(source, "C.f")
    assert found["kind"] == "function"
    assert adapter.declared_signature("f(self, a, b=2, *args, key, flag=True, **extra)")[1] == found["signature"]
    assert adapter.declared_signature("f(self, a, b, *args, key, flag=True, **extra)")[1] != found["signature"]
    assert adapter.member(source, "C.limit") == {"kind": "attribute", "signature": None}
    assert adapter.member(source, "C") == {"kind": "class", "signature": None}
    assert adapter.member(source, "C.missing") is None and adapter.member(source, "D.f") is None
    with pytest.raises(ValueError):
        adapter.declared_signature("not a signature")


# ================================================================ traceability and the statement


def test_every_test_needs_a_criterion_and_every_criterion_a_test(variant02):
    def edit(package, manifest):
        del manifest["test_criteria"][FEATURE + "test_zero_removes_the_line"]     # test without criterion
        manifest["acceptance_criteria"].append({"id": "AC-7", "text": "Never tested."})
        manifest["test_criteria"]["tests/feature/test_update_quantity.py::test_gone"] = ["AC-1"]
        with open(package / "statement.md", "a") as statement:
            statement.write("\n- **AC-7** Something nothing tests.\n")
    evidence = variant02(edit)
    assert codes(evidence) == {"GATE_TEST_WITHOUT_CRITERION", "GATE_CRITERION_WITHOUT_TEST",
                               "GATE_UNKNOWN_TEST"}
    assert reason(evidence, "GATE_TEST_WITHOUT_CRITERION")["items"] == [FEATURE + "test_zero_removes_the_line"]
    # AC-2 lost its only test, AC-7 never had one.
    assert reason(evidence, "GATE_CRITERION_WITHOUT_TEST")["items"] == ["AC-2", "AC-7"]
    assert reason(evidence, "GATE_UNKNOWN_TEST")["items"] == [
        "tests/feature/test_update_quantity.py::test_gone"]
    assert _gates(evidence)["status"] == "failed"


def test_statement_must_state_criteria_and_declared_interface(variant02):
    def edit(package, manifest):
        statement = package / "statement.md"
        text = statement.read_text().replace("**AC-5**", "**(unnumbered)**")
        statement.write_text(text.replace("update_quantity(self, sku, quantity)", "a new method"))
    evidence = variant02(edit)
    assert codes(evidence) == {"GATE_STATEMENT_MISSING_CRITERION", "GATE_STATEMENT_MISSING_INTERFACE"}
    assert reason(evidence, "GATE_STATEMENT_MISSING_CRITERION")["items"] == ["AC-5"]
    assert reason(evidence, "GATE_STATEMENT_MISSING_INTERFACE")["items"] == [
        "shopcart.cart.Cart.update_quantity"]


def test_criteria_must_be_well_formed(variant02):
    def edit(package, manifest):
        manifest["test_criteria"][FEATURE + "test_zero_removes_the_line"] = ["AC-99"]
    evidence = variant02(edit)
    assert codes(evidence) == {"PACKAGE_INVALID"}
    assert "must list known acceptance criteria" in reason(evidence, "PACKAGE_INVALID")["items"][0]


# ================================================================ variation


@pytest.fixture(scope="module")
def variation(pilot_repo, tmp_path_factory):
    """The two shipped tasks plus a reworded copy of the first."""
    work = tmp_path_factory.mktemp("variation")
    copy = work / "reworded-copy"
    shutil.copytree(PACKAGES / VALID, copy)
    manifest = json.loads((copy / "task.json").read_text())
    manifest["task_id"] = "reworded-copy"
    manifest["title"] = "Conditional promotions for baskets"
    (copy / "task.json").write_text(json.dumps(manifest))
    (copy / "statement.md").write_text(
        "# Conditional promotions\n\nPromotions should only kick in for some baskets. "
        "Entirely different wording, same behaviour.\n")
    return variation_report([PACKAGES / VALID, PACKAGES / NEW_NAME, copy], pilot_repo,
                            make_runner("local"), work / "report")


def _pair(report, a, b):
    return next(p for p in report["pairs"] if set(p["tasks"]) == {a, b})


def test_fingerprints_come_from_the_change_not_the_wording(variation):
    prints = {f["task_id"]: f for f in variation["fingerprints"]}
    eligibility, quantity = prints[VALID], prints[NEW_NAME]
    assert eligibility["files"] == ["shopcart/discounts.py"]
    assert "shopcart/discounts.py:Discount.amount_for" in eligibility["symbols"]
    assert set(eligibility["categories"]) == {"changes_existing_behaviour", "input_validation",
                                              "new_public_name", "new_type"}
    assert quantity["files"] == ["shopcart/cart.py"]
    assert quantity["symbols"] == ["shopcart/cart.py:Cart.update_quantity"]
    assert set(quantity["categories"]) == {"input_validation", "new_public_name"}
    assert quantity["counts"]["public_names_added"] == 1 and quantity["counts"]["guards_added"] == 3
    assert len(eligibility["contracts"]) == 12 and len(quantity["contracts"]) == 11
    # A reworded copy has the same fingerprint apart from its id.
    copy = prints["reworded-copy"]
    assert {k: v for k, v in copy.items() if k != "task_id"} == \
        {k: v for k, v in eligibility.items() if k != "task_id"}


def test_distinct_tasks_are_far_apart_and_share_no_behaviour(variation):
    pair = _pair(variation, VALID, NEW_NAME)
    assert not pair["duplicate"] and pair["duplicate_reasons"] == []
    assert pair["distance"] > 0.7
    assert pair["similarity_by_dimension"]["symbols"] == 0.0
    assert pair["similarity_by_dimension"]["files"] == 0.0
    overlaps = list(pair["test_overlap"].values())
    assert [o["passed"] for o in overlaps] == [0, 0] and all(o["tests"] > 0 for o in overlaps)


def test_reworded_copy_is_caught_by_structure_and_by_behaviour(variation):
    pair = _pair(variation, VALID, "reworded-copy")
    assert pair["duplicate"]
    assert pair["distance"] == 0.0
    assert pair["statement_word_overlap"] < 0.1          # the wording really is different
    assert all(o["overlap"] == 1.0 and o["tests"] == 25 for o in pair["test_overlap"].values())
    assert len(pair["duplicate_reasons"]) == 3           # distance, and subsumption both ways
    assert variation["duplicates"] == [[VALID, "reworded-copy"]]
    assert not _pair(variation, NEW_NAME, "reworded-copy")["duplicate"]


def test_distance_ignores_dimensions_both_tasks_leave_empty():
    a = {"symbols": ["f"], "files": ["x.py"], "categories": ["c"], "contracts": []}
    b = {"symbols": ["f"], "files": ["x.py"], "categories": ["c"], "contracts": []}
    assert distance(a, b) == (0.0, {"symbols": 1.0, "categories": 1.0, "contracts": None, "files": 1.0})
    c = dict(b, symbols=["g"], categories=["d"], files=["y.py"])
    assert distance(a, c)[0] == 1.0


def test_variation_command_exit_status(pilot_repo, tmp_path, capsys):
    distinct = main(["variation", str(PACKAGES / VALID), str(PACKAGES / NEW_NAME),
                     "--repo", str(pilot_repo), "--runner", "local", "--out", str(tmp_path / "a")])
    assert distinct == 0 and "distinct" in capsys.readouterr().out
    one = main(["variation", str(PACKAGES / VALID), "--repo", str(pilot_repo),
                "--runner", "local", "--out", str(tmp_path / "b")])
    assert one == 2 and "at least two packages" in capsys.readouterr().err


# ================================================================ validated and released


@pytest.fixture
def validated(tmp_path, pilot_repo):
    package = tmp_path / "package"
    shutil.copytree(PACKAGES / NEW_NAME, package)
    manifest = json.loads((package / "task.json").read_text())
    manifest["mutation"]["generate"] = False
    (package / "task.json").write_text(json.dumps(manifest))
    evidence = verify_package(package, pilot_repo, make_runner("local"), tmp_path / "evidence")
    assert evidence["verdict"] == "accepted"
    return package, tmp_path / "evidence"


def test_release_lifecycle_and_invalidation(validated):
    package, evidence = validated
    assert package_status(package)["status"] == "draft"
    assert package_status(package, evidence)["status"] == "validated"

    with pytest.raises(HarnessError, match="explicit approval"):
        release_package(package, evidence, "Reviewer One", approve=False)
    with pytest.raises(HarnessError, match="reviewer's identity"):
        release_package(package, evidence, "  ", approve=True)
    assert not (package / RELEASE_FILE).exists()

    digest_before = package_digest(package)
    record = release_package(package, evidence, "Reviewer One", approve=True, notes="read AC-1..5")
    assert package_digest(package) == digest_before        # the record is not part of the digest
    assert record["reviewer"] == "Reviewer One" and record["reviewed_at"].endswith("+00:00")
    assert len(record["statement_sha256"]) == len(record["tests_sha256"]) == 64
    assert record["evidence"]["verdict"] == "accepted"
    status = package_status(package)
    assert status["status"] == "released" and status["reviewer"] == "Reviewer One"

    # Any change after approval invalidates the release, and says what changed.
    statement = package / "statement.md"
    original = statement.read_text()
    statement.write_text(original + "\nOne more sentence.\n")
    assert package_status(package) == {
        "status": "release_invalid", "changed_since_review": ["statement", "package"],
        "reviewer": "Reviewer One", "reviewed_at": record["reviewed_at"]}
    statement.write_text(original)
    assert package_status(package)["status"] == "released"

    test_file = package / "tests" / "feature" / "test_update_quantity.py"
    test_file.write_text(test_file.read_text() + "\n# edited after review\n")
    assert package_status(package)["changed_since_review"] == ["tests", "package"]
    test_file.write_text(test_file.read_text().replace("\n# edited after review\n", ""))

    patch = package / "reference.patch"
    patch.write_text(patch.read_text() + "\n")
    assert package_status(package)["changed_since_review"] == ["package"]


def test_only_validated_content_can_be_released(validated, pilot_repo, tmp_path):
    package, evidence = validated
    # Stale evidence: the package changed after it was verified.
    statement = package / "statement.md"
    statement.write_text(statement.read_text() + "\nChanged after verification.\n")
    assert package_status(package, evidence)["status"] == "draft"
    with pytest.raises(HarnessError, match="different package content"):
        release_package(package, evidence, "Reviewer One", approve=True)

    # Rejected evidence.
    rejected = tmp_path / "rejected"
    verify_package(PACKAGES / "invalid-baseline-passes", pilot_repo, make_runner("local"), rejected)
    with pytest.raises(HarnessError, match="not accepted"):
        release_package(PACKAGES / "invalid-baseline-passes", rejected, "Reviewer One", approve=True)
    assert not (PACKAGES / "invalid-baseline-passes" / RELEASE_FILE).exists()


def test_release_and_status_commands(validated, capsys):
    package, evidence = validated
    assert main(["status", str(package), "--evidence", str(evidence)]) == 0
    assert "VALIDATED" in capsys.readouterr().out
    assert main(["release", str(package), "--evidence", str(evidence), "--reviewer", "R"]) == 2
    assert "explicit approval" in capsys.readouterr().err
    assert main(["release", str(package), "--evidence", str(evidence), "--reviewer", "R",
                 "--approve"]) == 0
    assert main(["status", str(package)]) == 0 and "RELEASED" in capsys.readouterr().out
    (package / "statement.md").write_text("changed")
    assert main(["status", str(package)]) == 1
    assert "RELEASE_INVALID" in capsys.readouterr().out


def test_no_shipped_package_is_marked_released():
    """Releasing is a human act. Nothing in the repository has been released by the tooling."""
    assert [p.parent.name for p in PACKAGES.glob(f"*/{RELEASE_FILE}")] == []
