"""Build the Repository Contract Map for one pinned commit.

A contract becomes *verified* only through execution:

* an existing test, or a supplied probe, must pass at the pinned commit in two
  baseline runs AND fail under at least one injected fault. A test that no
  fault can disturb has not been shown to defend anything;
* a guard clause found in the source is verified when removing the guard makes
  a baseline-passing test fail. Those tests become its probes.

Everything else stays a hypothesis (or is refuted, when its probe fails by
assertion). Guards that no test defends are reported as such: they are the
repository's unwritten rules.
"""
import hashlib
import json
import re
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from unwritten import __version__
from unwritten.adapters import detect_adapter
from unwritten.contracts.hypotheses import load_hypotheses
from unwritten.errors import HarnessError
from unwritten.execution import run_in_sandbox
from unwritten.workspace import PACKAGE_TESTS_DIR, export_commit, resolve_commit

MAP_FILE = "contract-map.json"
MAP_SCHEMA = "0.1"
PROBE_DIR = f"{PACKAGE_TESTS_DIR}/probes"
_PROBE_LIMIT = 12


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _prepare_out_dir(out_dir):
    out_dir = Path(out_dir)
    if out_dir.exists():
        if any(out_dir.iterdir()) and not (out_dir / MAP_FILE).is_file():
            raise HarnessError(
                f"{out_dir} is not empty and holds no earlier contract map; refusing to overwrite it")
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    return out_dir.resolve()


def _symbol_at(symbols, file, line):
    """Innermost symbol of ``file`` containing ``line``."""
    inside = [s for s in symbols if s.file == file and s.line <= line <= s.end_line]
    return min(inside, key=lambda s: s.end_line - s.line).qualname if inside else "<module>"


def _humanise(name):
    return re.sub(r"^test_?", "", name).replace("_", " ").strip() or name


def extract_contracts(repo, profile, runner, out_dir, hypotheses_file=None, jobs=1, progress=None):
    """Extract and verify contracts. Returns the contract map, also written to disk."""
    progress = progress or (lambda *_: None)
    started, clock = _now(), time.monotonic()
    out_dir = _prepare_out_dir(out_dir)
    tree = resolve_commit(repo, profile.commit)
    hypotheses = load_hypotheses(hypotheses_file)
    environment = runner.prepare(profile)

    with tempfile.TemporaryDirectory(prefix="unwritten-") as scratch:
        scratch = Path(scratch)
        source = scratch / "source"
        export_commit(repo, profile.commit, source)
        adapter = detect_adapter(source)

        # ---- static pass: nothing is executed here
        symbols, guards, mutants = [], [], []
        for path in adapter.source_files(source, profile.repo_test_paths):
            data = (source / path).read_bytes()
            symbols += adapter.symbols(data, path)
            guards += adapter.guards(data, path)
            mutants += adapter.mutants(data, path, range(1, data.count(b"\n") + 2))
        test_symbols = []
        for test_root in profile.repo_test_paths:
            for path in sorted((source / test_root).rglob("*")) if (source / test_root).is_dir() else []:
                if path.is_file() and path.suffix == ".py":
                    relative = path.relative_to(source).as_posix()
                    test_symbols += adapter.symbols(path.read_bytes(), relative)
        progress("static", adapter.name, len(symbols), len(guards), len(mutants))

        probe_files = {}
        for hypothesis in hypotheses:
            if hypothesis["probe"]:
                name = adapter.probe_filename("hyp_" + hypothesis["id"].replace("-", "_"))
                hypothesis["probe_file"] = name
                probe_files[name] = hypothesis["probe"]
        (out_dir / "probes").mkdir()
        for name, code in probe_files.items():
            (out_dir / "probes" / name).write_text(code, encoding="utf-8")

        suites = [{"name": "repo_tests", "paths": list(profile.repo_test_paths)}]
        if probe_files:
            suites.append({"name": "probes", "paths": [PROBE_DIR]})

        def execute(label, mutant=None):
            workspace = scratch / label
            export_commit(repo, profile.commit, workspace)
            if mutant is not None:
                mutant.apply(workspace)
            if probe_files:
                (workspace / PROBE_DIR).mkdir(parents=True)
                for name, code in probe_files.items():
                    (workspace / PROBE_DIR / name).write_text(code, encoding="utf-8")
            results, summary = run_in_sandbox(runner, profile, workspace, suites, out_dir,
                                              Path("logs") / label)
            shutil.rmtree(workspace, ignore_errors=True)
            environment.setdefault("python_version", summary.get("python_version"))
            for result in results.values():
                if result.pytest_version:
                    environment.setdefault("pytest_version", result.pytest_version)
            return results

        # ---- baseline, twice: a probe has to pass both times
        baselines = [execute("baseline1"), execute("baseline2")]
        for run in baselines:
            timed_out = [name for name, suite in run.items() if suite.timed_out]
            if timed_out:
                raise HarnessError(f"baseline suites timed out: {', '.join(timed_out)}")
        outcomes = [{(name, t.nodeid): t for name, suite in run.items() for t in suite.tests}
                    for run in baselines]
        stable_pass = {key for key in outcomes[0]
                       if key in outcomes[1] and outcomes[0][key].outcome == "passed"
                       and outcomes[1][key].outcome == "passed"}
        flaky = {key for key in set(outcomes[0]) | set(outcomes[1])
                 if (outcomes[0].get(key) and outcomes[0][key].outcome)
                 != (outcomes[1].get(key) and outcomes[1][key].outcome)}
        collection_errors = {e["nodeid"]: e["detail"] for run in baselines
                             for suite in run.values() for e in suite.collection_errors}
        progress("baseline", len(outcomes[0]), len(stable_pass), len(flaky))

        # ---- fault injection: which tests notice which change
        def judge(mutant):
            results = execute("mutant-" + re.sub(r"[^A-Za-z0-9._-]+", "_", mutant.id), mutant)
            if any(suite.timed_out for suite in results.values()):
                return mutant.id, None
            now = {(name, t.nodeid): t.outcome for name, suite in results.items() for t in suite.tests}
            return mutant.id, sorted(key for key in stable_pass if now.get(key) != "passed")

        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            killers = dict(pool.map(judge, mutants))
        progress("mutants", len(mutants), sum(bool(k) for k in killers.values()))

    mutant_by_id = {m.id: m for m in mutants}
    kills = {}
    for mutant_id, tests in killers.items():
        for key in tests or ():
            kills.setdefault(key, []).append(mutant_id)

    def related(test_keys):
        found = {(mutant_by_id[m].file, _symbol_at(symbols, mutant_by_id[m].file, mutant_by_id[m].line))
                 for key in test_keys for m in kills.get(key, ())}
        return [{"file": f, "symbol": s} for f, s in sorted(found)]

    def probes(keys):
        keys = sorted(keys)
        return [{"suite": suite, "nodeid": nodeid} for suite, nodeid in keys[:_PROBE_LIMIT]], len(keys)

    contracts = []

    # ---- contracts witnessed by existing tests
    groups = {}
    for (suite, nodeid) in sorted(set(outcomes[0]) | set(outcomes[1])):
        if suite == "repo_tests":
            groups.setdefault(nodeid.split("[")[0], []).append((suite, nodeid))
    for function, keys in groups.items():
        file, _, qualname = function.partition("::")
        qualname = qualname.replace("::", ".")
        killed = sorted({m for key in keys for m in kills.get(key, ())})
        line = next((s.line for s in test_symbols if s.file == file and s.qualname == qualname), None)
        probe_list, probe_count = probes(keys)
        contract = {
            "id": f"test:{function}", "description": _humanise(qualname.split(".")[-1]),
            "category": "behaviour", "origin": "repository",
            "evidence_type": "existing_test",
            "source": {"file": file, "symbol": qualname, "line": line},
            "related": related(keys),
            "validation": {"method": "the repository's own test, run at the pinned commit twice "
                                     "and against every injected fault",
                           "probes": probe_list, "probe_count": probe_count,
                           "faults_detected": len(killed)},
        }
        if any(key in flaky for key in keys):
            _set(contract, "hypothesis", "low", "flaky", "the test gave different results in two runs")
        elif not all(key in stable_pass for key in keys):
            _set(contract, "hypothesis", "low", "fails_at_baseline",
                 "the test does not pass at the pinned commit")
        elif not killed:
            _set(contract, "hypothesis", "medium", "passes_but_insensitive",
                 "passes at the pinned commit, but no injected fault changes its result, so it is "
                 "not shown to defend any behaviour")
        else:
            _set(contract, "verified", "high", "passes_and_detects_faults", None)
        contracts.append(contract)

    # ---- contracts read from guard clauses
    for guard in guards:
        mutant_id = adapter.guard_mutant_id(guard)
        detecting = killers.get(mutant_id)
        text = f"`{guard.symbol}` raises `{guard.exception}` when `{guard.condition}`"
        if guard.message:
            text += f' ("{guard.message}")'
        probe_list, probe_count = probes(detecting or ())
        contract = {
            "id": f"guard:{guard.file}:{guard.symbol}:{guard.index}", "description": text,
            "category": guard.category, "origin": "static_analysis",
            "evidence_type": "guard_clause",
            "source": {"file": guard.file, "symbol": guard.symbol, "line": guard.line},
            "related": [{"file": guard.file, "symbol": guard.symbol}],
            "guard": {"file": guard.file, "symbol": guard.symbol, "index": guard.index,
                      "condition": guard.condition, "exception": guard.exception},
            "validation": {"method": "remove the guard and see whether a baseline-passing test fails",
                           "fault": mutant_id if mutant_id in mutant_by_id else None,
                           "probes": probe_list, "probe_count": probe_count},
        }
        if mutant_id not in mutant_by_id:
            _set(contract, "hypothesis", "medium", "not_measurable",
                 "the guard could not be removed mechanically")
        elif detecting is None:
            _set(contract, "hypothesis", "medium", "not_measurable",
                 "removing the guard made the tests time out")
        elif not detecting:
            _set(contract, "hypothesis", "medium", "unprotected",
                 "the code enforces this, but no test fails when the check is removed")
        else:
            _set(contract, "verified", "high", "guard_removal_detected", None)
        contracts.append(contract)

    # ---- supplied hypotheses
    for hypothesis in hypotheses:
        file = hypothesis.get("probe_file")
        prefix = f"{PROBE_DIR}/{file}"
        keys = sorted(key for key in set(outcomes[0]) | set(outcomes[1])
                      if key[0] == "probes" and key[1].split("::")[0] == prefix) if file else []
        results = [run[key] for run in outcomes for key in keys if key in run]
        killed = sorted({m for key in keys for m in kills.get(key, ())})
        probe_list, probe_count = probes(keys)
        contract = {
            "id": f"hyp:{hypothesis['id']}", "description": hypothesis["description"],
            "category": hypothesis["category"], "origin": hypothesis["proposed_by"],
            "evidence_type": "supplied_hypothesis",
            "source": {"file": hypothesis["file"], "symbol": hypothesis["symbol"], "line": None},
            "related": related(keys),
            "stated_confidence": hypothesis["confidence"],
            "validation": {"method": "supplied probe, run at the pinned commit twice and against "
                                     "every injected fault",
                           "probe_file": f"probes/{file}" if file else None,
                           "probes": probe_list, "probe_count": probe_count,
                           "faults_detected": len(killed)},
        }
        failed = [r for r in results if r.outcome not in ("passed",)]
        if not file:
            _set(contract, "hypothesis", "low", "no_probe", "no probe was supplied")
        elif prefix in collection_errors:
            _set(contract, "hypothesis", "low", "probe_collection_error",
                 "the probe could not be imported: " + collection_errors[prefix].strip().splitlines()[-1])
        elif not keys:
            _set(contract, "hypothesis", "low", "probe_has_no_tests", "the probe defines no test")
        elif any(key in flaky for key in keys):
            _set(contract, "hypothesis", "low", "flaky", "the probe gave different results in two runs")
        elif any(r.failure_class == "assertion" for r in failed):
            first = next(r for r in failed if r.failure_class == "assertion")
            _set(contract, "refuted", "refuted", "probe_assertion_failed",
                 "the pinned commit contradicts this claim: " + (first.message or "").splitlines()[0][:200])
        elif failed:
            _set(contract, "hypothesis", "low", "probe_runtime_error",
                 f"the probe crashed ({failed[0].exc_type}) instead of testing the claim")
        elif not killed:
            _set(contract, "hypothesis", "low", "passes_but_insensitive",
                 "the probe passes, but no injected fault changes its result; it may not test "
                 "the code it claims to")
        else:
            _set(contract, "verified", "high", "passes_and_detects_faults", None)
        contracts.append(contract)

    unprotected = [
        {"fault": m.id, "file": m.file, "line": m.line, "description": m.description,
         "symbol": _symbol_at(symbols, m.file, m.line)}
        for m in mutants if killers[m.id] == []]
    by_status = {}
    for contract in contracts:
        by_status[contract["status"]] = by_status.get(contract["status"], 0) + 1
    fingerprint = hashlib.sha256(json.dumps(
        [[c["id"], c["status"], c["validation"]["result"], c["validation"]["probes"]] for c in contracts]
        + [u["fault"] for u in unprotected], sort_keys=True).encode()).hexdigest()

    contract_map = {
        "contract_map_schema": MAP_SCHEMA,
        "harness": {"name": "unwritten", "version": __version__},
        "repository": {"name": profile.repo_name, "commit": profile.commit, "tree": tree},
        "adapter": adapter.name,
        "environment": environment,
        "process": {
            "llm_calls": 0,
            "llm_note": "the harness contains no model client; supplied hypotheses are read from "
                        "a file and judged only by executing their probes",
            "hypotheses_file": Path(hypotheses_file).name if hypotheses_file else None,
        },
        "summary": {
            "contracts": len(contracts), **dict(sorted(by_status.items())),
            "baseline_tests": len(outcomes[0]), "baseline_runs": 2,
            "faults_injected": len(mutants),
            "faults_detected": sum(bool(k) for k in killers.values()),
            "faults_undetected": len(unprotected),
            "faults_timed_out": sum(k is None for k in killers.values()),
        },
        "fingerprint": fingerprint,
        "contracts": contracts,
        "unprotected_behaviour": unprotected,
        "timing": {"started_at": started, "finished_at": _now(),
                   "duration_seconds": round(time.monotonic() - clock, 2)},
    }
    (out_dir / MAP_FILE).write_text(json.dumps(contract_map, indent=2) + "\n", encoding="utf-8")
    contract_map["map_dir"] = str(out_dir)
    return contract_map


def _set(contract, status, confidence, result, note):
    contract["status"] = status
    contract["confidence"] = confidence
    contract["validation"]["result"] = result
    if note:
        contract["validation"]["note"] = note
