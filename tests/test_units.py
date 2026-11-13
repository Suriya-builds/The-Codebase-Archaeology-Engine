"""Unit tests for the deterministic building blocks."""
import ast
import json
from pathlib import Path

import pytest

from conftest import PACKAGES, ROOT, VALID
from pilot.materialize import materialize
from unwritten.classify import classify_test
from unwritten.errors import PackageError
from unwritten.mutation import mutate_source
from unwritten.package import load_package, package_digest
from unwritten.patches import apply_patch, is_under, parse_patch, touched_paths
from unwritten.verify import compare_runs
from unwritten.workspace import export_commit

# ---------------------------------------------------------------- pilot repository


def test_pilot_commit_is_deterministic_and_matches_every_package(pilot_repo, tmp_path):
    again = materialize(tmp_path / "again")
    pinned = {json.loads((p / "task.json").read_text())["repo"]["commit"]
              for p in PACKAGES.iterdir() if (p / "task.json").is_file()}
    assert pinned == {again}


# ---------------------------------------------------------------- patches


def test_parse_patch_reports_added_lines_of_the_patched_file(pilot_repo, tmp_path):
    package = load_package(PACKAGES / VALID)
    text = package.reference_patch.read_text()
    (file_patch,) = parse_patch(text)
    assert file_patch.path == "shopcart/discounts.py"
    export_commit(pilot_repo, package.commit, tmp_path / "ws")
    apply_patch(tmp_path / "ws", package.reference_patch)
    patched = (tmp_path / "ws" / file_patch.path).read_text().splitlines()
    added_in_diff = [line[1:] for line in text.splitlines()
                     if line.startswith("+") and not line.startswith("+++")]
    assert [patched[n - 1] for n in sorted(file_patch.added_lines)] == added_in_diff


def test_touched_paths_and_is_under():
    text = "--- /dev/null\n+++ b/tests/test_new.py\n@@ -0,0 +1 @@\n+x = 1\n"
    assert touched_paths(text) == ["tests/test_new.py"]
    assert is_under("tests/test_new.py", ["tests"])
    assert not is_under("testsuite/x.py", ["tests"])
    assert not is_under("shopcart/x.py", ["tests"])


# ---------------------------------------------------------------- mutation operators

SNIPPET = b"""\
def check(amount, limit, tags):
    if amount < limit and not tags:
        return "low"
    if amount == limit or "vip" in tags:
        return "edge"
    untouched = amount >= 0
    return "high"
"""


def _ids(mutants):
    return [m.id.split(":", 1)[1] for m in mutants]


def test_operators_produce_expected_single_token_mutants():
    mutants = mutate_source(SNIPPET, "m.py", {2, 4})
    assert _ids(mutants) == [
        "COND_FORCE_FALSE:m.py:2:7", "COND_FORCE_TRUE:m.py:2:7", "CMP_BOUNDARY:m.py:2:14",
        "BOOL_SWAP:m.py:2:22", "NOT_REMOVE:m.py:2:26",
        "COND_FORCE_FALSE:m.py:4:7", "COND_FORCE_TRUE:m.py:4:7", "CMP_NEGATE:m.py:4:14",
        "BOOL_SWAP:m.py:4:23", "CMP_NEGATE:m.py:4:32",
    ]
    by_id = {m.id.split(":", 1)[1]: m.new_source.decode().splitlines() for m in mutants}
    assert by_id["CMP_BOUNDARY:m.py:2:14"][1] == "    if amount <= limit and not tags:"
    assert by_id["BOOL_SWAP:m.py:2:22"][1] == "    if amount < limit or not tags:"
    assert by_id["NOT_REMOVE:m.py:2:26"][1] == "    if amount < limit and tags:"
    assert by_id["CMP_NEGATE:m.py:4:32"][3] == '    if amount == limit or "vip" not in tags:'
    assert by_id["COND_FORCE_FALSE:m.py:4:7"][3] == "    if False:"


def test_mutants_only_touch_requested_lines_and_always_parse():
    assert mutate_source(SNIPPET, "m.py", set()) == []
    line6 = mutate_source(SNIPPET, "m.py", {6})
    assert _ids(line6) == ["CMP_BOUNDARY:m.py:6:23"]
    for mutant in mutate_source(SNIPPET, "m.py", set(range(1, 8))):
        ast.parse(mutant.new_source)
        assert mutant.new_source != SNIPPET
        assert mutant.diff.count("\n+") == 2 and mutant.diff.count("\n-") == 1  # one line changed


def test_mutation_is_deterministic_and_skips_what_it_cannot_place():
    lines = set(range(1, 8))
    assert _ids(mutate_source(SNIPPET, "m.py", lines)) == _ids(mutate_source(SNIPPET, "m.py", lines))
    awkward = b"ok = (1 <  # comment\n      2)\n"
    assert mutate_source(awkward, "m.py", {1, 2}) == []
    assert mutate_source(b"def broken(:\n", "m.py", {1}) == []


# ---------------------------------------------------------------- failure classification


def _raw(**phases):
    return {"nodeid": "t.py::test", "phases": phases}


PASSED = {"outcome": "passed"}


@pytest.mark.parametrize("raw, outcome, category", [
    (_raw(setup=PASSED, call=PASSED, teardown=PASSED), "passed", None),
    (_raw(setup=PASSED, call={"outcome": "failed", "assertion_level": True,
                              "exc_type": "builtins.AssertionError"}, teardown=PASSED),
     "failed", "assertion"),
    (_raw(setup=PASSED, call={"outcome": "failed", "assertion_level": False,
                              "exc_type": "builtins.AttributeError"}, teardown=PASSED),
     "failed", "runtime_error"),
    (_raw(setup={"outcome": "failed", "exc_type": "builtins.RuntimeError"}), "error", "runtime_error"),
    (_raw(setup={"outcome": "skipped"}), "skipped", None),
    (_raw(setup=PASSED, call=PASSED, teardown={"outcome": "failed"}), "error", "runtime_error"),
    (_raw(setup=PASSED, call={"outcome": "skipped", "xfail": True}, teardown=PASSED), "xfailed", None),
    (_raw(setup=PASSED, call={"outcome": "passed", "xfail": True}, teardown=PASSED), "xpassed", None),
    (_raw(setup=PASSED), "error", "runtime_error"),
])
def test_classify_test(raw, outcome, category):
    result = classify_test(raw)
    assert (result.outcome, result.failure_class) == (outcome, category)


# ---------------------------------------------------------------- repeatability comparison


def test_compare_runs_names_what_differs():
    run = {"world1": {"status": "passed", "suites": {"feature": {"tests": [["a", "failed"]]}}}}
    flaky = {"world1": {"status": "passed", "suites": {"feature": {"tests": [["a", "passed"]]}}}}
    assert compare_runs([run, run]) == []
    assert compare_runs([run, flaky]) == ["run 1 and run 2 differ in world1.suites"]


# ---------------------------------------------------------------- package loading


def test_valid_package_loads_and_digest_is_content_based(tmp_path):
    package = load_package(PACKAGES / VALID)
    assert package.task_id == VALID
    assert [m.id for m in package.declared_mutants][0] == "declared:ignore-min-subtotal"
    assert package_digest(PACKAGES / VALID) == package_digest(PACKAGES / VALID)
    assert package_digest(PACKAGES / VALID) != package_digest(PACKAGES / "invalid-baseline-passes")


def test_malformed_package_reports_every_problem(tmp_path):
    (tmp_path / "task.json").write_text(json.dumps({
        "schema_version": "9", "task_id": "bad id", "title": "",
        "repo": {"name": "x", "commit": "abc123"},
        "execution": {"repo_test_paths": ["../outside"], "timeout_seconds": 0},
        "mutation": {"declared": [{"id": "m", "patch": "/etc/passwd", "description": "d"}],
                     "waivers": [{"mutant_id": "m"}]},
    }))
    with pytest.raises(PackageError) as caught:
        load_package(tmp_path)
    text = " | ".join(caught.value.problems)
    for expected in ("schema_version", "task_id", "title", "repo.commit", "missing statement.md",
                     "missing reference.patch", "missing tests/feature/", "execution.dockerfile",
                     "relative paths", "timeout_seconds", "must stay inside the package",
                     "justification"):
        assert expected in text


# ---------------------------------------------------------------- no model, no network

BANNED = {"anthropic", "openai", "httpx", "requests", "urllib3", "aiohttp", "socket",
          "http", "urllib", "ftplib", "smtplib", "xmlrpc"}


def test_harness_imports_no_network_or_model_client():
    offenders = []
    for path in (ROOT / "src" / "unwritten").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            offenders += [f"{path.name}: {n}" for n in names if n.split(".")[0] in BANNED]
    assert offenders == []


# ---------------------------------------------------------------- command line output


def test_world_line_copes_with_a_world_that_stopped_early():
    from unwritten.cli import _world_line
    from unwritten.worlds import WorldResult

    stopped = WorldResult("world2", "Correct World", status="failed")
    assert _world_line(stopped).split() == ["2", "Correct", "World", "FAILED"]
    skipped = WorldResult("world3", "Deception World", status="not_run", summary={"note": "skipped"})
    assert _world_line(skipped).endswith("skipped")
