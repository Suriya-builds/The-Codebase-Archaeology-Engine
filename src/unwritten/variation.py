"""Measuring how different two tasks are, from what they change and test.

Wording plays no part in any decision here. Two comparisons are made:

* **Structural distance** between fingerprints computed from the reference
  patch: files and symbols touched, contracts in play, and behavioural
  categories derived from the change itself.
* **Test overlap**: the share of task A's feature tests that pass with task
  B's reference implementation. Every feature test fails at the base commit,
  so a test that passes there is behaviour B already delivers. Full overlap
  means B subsumes A, whatever the two statements say.

A pair is flagged as a duplicate when the distance is below the threshold or
either task subsumes the other.
"""
import json
import shutil
import tempfile
from pathlib import Path

from unwritten.adapters import detect_adapter
from unwritten.errors import HarnessError
from unwritten.execution import run_in_sandbox
from unwritten.package import load_package
from unwritten.patches import apply_patch, is_under, parse_patch
from unwritten.workspace import PACKAGE_TESTS_DIR, export_commit, resolve_commit

WEIGHTS = {"symbols": 0.40, "categories": 0.25, "contracts": 0.20, "files": 0.15}
DEFAULT_THRESHOLD = 0.25
REPORT_FILE = "variation.json"


def _innermost(symbols, file, line):
    inside = [s for s in symbols if s.file == file and s.line <= line <= s.end_line]
    return min(inside, key=lambda s: s.end_line - s.line).qualname if inside else None


def fingerprint(package, repo, scratch):
    """Describe a task by what its reference implementation changes."""
    base, reference = Path(scratch) / "base", Path(scratch) / "reference"
    for directory in (base, reference):
        shutil.rmtree(directory, ignore_errors=True)
        export_commit(repo, package.commit, directory)
    apply_patch(reference, package.reference_patch)
    adapter = detect_adapter(reference)
    patch = parse_patch(package.reference_patch.read_text(encoding="utf-8"))

    files, touched, added_names, new_types, guards_added, signatures_changed = set(), set(), set(), 0, 0, 0
    lines_added = 0
    for file_patch in patch:
        path = file_patch.path
        if not path or is_under(path, package.repo_test_paths):
            continue
        files.add(path)
        lines_added += len(file_patch.added_lines)
        after = (reference / path).read_bytes() if (reference / path).is_file() else b""
        before = (base / path).read_bytes() if (base / path).is_file() else b""
        symbols_after, symbols_before = adapter.symbols(after, path), adapter.symbols(before, path)
        known = {s.qualname for s in symbols_before}
        text = after.decode("utf-8", "replace").splitlines()
        for line in file_patch.added_lines:
            if line <= len(text) and not text[line - 1].strip():
                continue  # a blank line changes nothing
            name = _innermost(symbols_after, path, line)
            touched.add(f"{path}:{name or '<module>'}")
        for symbol in symbols_after:
            public = not any(part.startswith("_") for part in symbol.qualname.split("."))
            if symbol.qualname not in known and public:
                added_names.add(f"{path}:{symbol.qualname}")
                new_types += symbol.kind == "class"
            elif symbol.qualname in known and symbol.kind == "function":
                now, then = adapter.member(after, symbol.qualname), adapter.member(before, symbol.qualname)
                if now and then and now["signature"] != then["signature"]:
                    signatures_changed += 1
        keys_before = {(g.symbol, g.key) for g in adapter.guards(before, path)}
        guards_added += sum((g.symbol, g.key) not in keys_before for g in adapter.guards(after, path))
    existing_touched = {t for t in touched if t not in added_names
                        and not any(t.startswith(a + ".") or t == a for a in added_names)}

    overridden = {o.contract_id for o in package.contract_overrides}
    categories = set()
    if added_names:
        categories.add("new_public_name")
    if new_types:
        categories.add("new_type")
    if signatures_changed:
        categories.add("signature_change")
    if guards_added:
        categories.add("input_validation")
    if existing_touched:
        categories.add("changes_existing_behaviour")
    if overridden:
        categories.add("contract_change")

    feature_tests = sum(len(adapter.test_functions(p.read_bytes()))
                        for p in package.feature_tests.rglob("*.py"))
    shutil.rmtree(base, ignore_errors=True)
    shutil.rmtree(reference, ignore_errors=True)
    return {
        "task_id": package.task_id,
        "commit": package.commit,
        "files": sorted(files),
        "symbols": sorted(touched),
        "contracts": sorted({c["id"] for c in package.contracts}),
        "contracts_overridden": sorted(overridden),
        "categories": sorted(categories),
        "counts": {"lines_added": lines_added, "public_names_added": len(added_names),
                   "new_types": new_types, "guards_added": guards_added,
                   "signatures_changed": signatures_changed,
                   "feature_test_functions": feature_tests,
                   "acceptance_criteria": len(package.acceptance_criteria)},
    }


def _jaccard(a, b):
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else None


def distance(first, second):
    """Weighted Jaccard distance in [0, 1]; 0 means structurally identical."""
    similarity, weight, parts = 0.0, 0.0, {}
    for dimension, share in WEIGHTS.items():
        value = _jaccard(first[dimension], second[dimension])
        parts[dimension] = None if value is None else round(value, 4)
        if value is not None:      # a dimension both tasks leave empty says nothing
            similarity += share * value
            weight += share
    result = 1.0 if weight == 0 else 1.0 - similarity / weight
    return round(result, 4), parts


def _words(text):
    return {w.lower().strip(".,:;()`*#\"'") for w in text.split()} - {""}


def test_overlap(runner, tests_of, reference_of, repo, scratch, out_dir, label):
    """Share of ``tests_of``'s feature tests that pass with ``reference_of``'s reference."""
    workspace = Path(scratch) / label
    shutil.rmtree(workspace, ignore_errors=True)
    export_commit(repo, reference_of.commit, workspace)
    apply_patch(workspace, reference_of.reference_patch)
    target = workspace / PACKAGE_TESTS_DIR / "feature"
    shutil.copytree(tests_of.feature_tests, target,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    suites = [{"name": "feature", "paths": [f"{PACKAGE_TESTS_DIR}/feature"]}]
    results, _ = run_in_sandbox(runner, tests_of, workspace, suites, out_dir, Path("logs") / label)
    shutil.rmtree(workspace, ignore_errors=True)
    suite = results["feature"]
    total = len(suite.tests)
    passed = sum(t.outcome == "passed" for t in suite.tests)
    return {"passed": passed, "tests": total,
            "uncollectable_files": len(suite.collection_errors),
            "overlap": round(passed / total, 4) if total else 0.0,
            "log": suite.log}


test_overlap.__test__ = False  # not a pytest test


def variation_report(package_dirs, repo, runner, out_dir, threshold=DEFAULT_THRESHOLD):
    """Compare every pair of packages. Returns the report, also written to disk."""
    packages = [load_package(p) for p in package_dirs]
    if len(packages) < 2:
        raise HarnessError("variation needs at least two packages")
    if len({p.task_id for p in packages}) != len(packages):
        raise HarnessError("packages must have distinct task ids")
    for package in packages:
        resolve_commit(repo, package.commit)
    out_dir = Path(out_dir)
    if out_dir.exists():
        if any(out_dir.iterdir()) and not (out_dir / REPORT_FILE).is_file():
            raise HarnessError(f"{out_dir} is not empty and holds no earlier variation report")
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    environment = runner.prepare(packages[0])
    for package in packages[1:]:
        if package.dockerfile.read_bytes() != packages[0].dockerfile.read_bytes():
            runner.prepare(package)

    pairs = []
    with tempfile.TemporaryDirectory(prefix="unwritten-") as scratch:
        prints = {p.task_id: fingerprint(p, repo, scratch) for p in packages}
        statements = {p.task_id: _words(p.statement.read_text(encoding="utf-8")) for p in packages}
        for i, first in enumerate(packages):
            for second in packages[i + 1:]:
                a, b = first.task_id, second.task_id
                value, parts = distance(prints[a], prints[b])
                pair = {"tasks": [a, b], "distance": value, "similarity_by_dimension": parts,
                        "statement_word_overlap": round(_jaccard(statements[a], statements[b]) or 0, 4)}
                if first.commit == second.commit:
                    if first.dockerfile.read_bytes() != second.dockerfile.read_bytes():
                        runner.prepare(first)
                    pair["test_overlap"] = {
                        f"{a} tests on {b} reference": test_overlap(
                            runner, first, second, repo, scratch, out_dir, f"{a}__on__{b}"),
                        f"{b} tests on {a} reference": test_overlap(
                            runner, second, first, repo, scratch, out_dir, f"{b}__on__{a}"),
                    }
                else:
                    pair["test_overlap"] = None
                reasons = []
                if value < threshold:
                    reasons.append(f"structural distance {value} is below the threshold {threshold}")
                for name, result in (pair["test_overlap"] or {}).items():
                    if result["tests"] and result["overlap"] == 1.0:
                        reasons.append(f"subsumed: all {name.replace(' tests on ', ' feature tests pass with the ')}")
                pair["duplicate"] = bool(reasons)
                pair["duplicate_reasons"] = reasons
                pairs.append(pair)

    report = {
        "method": {
            "distance": "1 - weighted Jaccard similarity over the dimensions below; a dimension "
                        "empty in both tasks is left out",
            "weights": WEIGHTS, "threshold": threshold,
            "threshold_note": "the threshold is a chosen value, not a measured one",
            "test_overlap": "share of one task's feature tests that pass with the other task's "
                            "reference implementation; 1.0 means subsumed",
            "statement_word_overlap": "reported for information; it decides nothing",
        },
        "environment": environment,
        "fingerprints": [prints[p.task_id] for p in packages],
        "pairs": pairs,
        "duplicates": [p["tasks"] for p in pairs if p["duplicate"]],
    }
    (out_dir / REPORT_FILE).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["report_dir"] = str(out_dir.resolve())
    return report
