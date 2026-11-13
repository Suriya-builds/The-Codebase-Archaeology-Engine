"""Orchestration: run the pipeline several times and decide on the evidence."""
import hashlib
import json
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from unwritten import __version__
from unwritten.errors import HarnessError, PackageError
from unwritten.package import SCHEMA_VERSION, load_package, package_digest
from unwritten.workspace import resolve_commit
from unwritten.worlds import PIPELINE, RunContext

EVIDENCE_FILE = "evidence.json"


def fingerprint(worlds):
    canonical = json.dumps({name: world.signature() for name, world in worlds.items()},
                           sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compare_runs(signatures):
    """Return human-readable differences between the first run and the others."""
    differences = []
    first = signatures[0]
    for index, other in enumerate(signatures[1:], start=2):
        for world in sorted(set(first) | set(other)):
            a, b = first.get(world, {}), other.get(world, {})
            if a == b:
                continue
            for key in sorted(set(a) | set(b)):
                if a.get(key) != b.get(key):
                    differences.append(f"run 1 and run {index} differ in {world}.{key}")
    return differences


def _prepare_out_dir(out_dir):
    out_dir = Path(out_dir)
    if out_dir.exists():
        if any(out_dir.iterdir()) and not (out_dir / EVIDENCE_FILE).is_file():
            raise HarnessError(
                f"{out_dir} is not empty and holds no earlier evidence; refusing to overwrite it")
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    return out_dir.resolve()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def verify_package(package_dir, repo, runner, out_dir, repeat=2, progress=None, jobs=1):
    """Verify one task package. Returns the evidence dictionary, also written to disk."""
    if repeat < 2:
        raise HarnessError("at least 2 runs are needed to say anything about repeatability")
    progress = progress or (lambda *_: None)
    started, clock = _now(), time.monotonic()
    out_dir = _prepare_out_dir(out_dir)
    evidence = {
        "evidence_schema": SCHEMA_VERSION,
        "harness": {"name": "unwritten", "version": __version__},
        "verdict": None,
        "reasons": [],
        "task": {"package": Path(package_dir).name,
                 "package_sha256": package_digest(package_dir)},
    }

    try:
        package = load_package(package_dir)
    except PackageError as exc:
        evidence["verdict"] = "rejected"
        evidence["reasons"] = [{"code": "PACKAGE_INVALID", "world": "package",
                                "message": "the package does not follow schema " + SCHEMA_VERSION,
                                "items": exc.problems, "runs": []}]
        return _finish(evidence, out_dir, started, clock)

    tree = resolve_commit(repo, package.commit)
    evidence["task"].update(task_id=package.task_id, title=package.title)
    evidence["repository"] = {"name": package.repo_name, "commit": package.commit, "tree": tree}
    environment = runner.prepare(package)
    evidence["environment"] = environment
    evidence["process"] = {
        "llm_calls": 0,
        "llm_note": "the harness contains no model client; verdicts come from executed tests only",
        "manual_interventions": [
            {"type": "mutant_waiver", "mutant_id": w.mutant_id,
             "justification": w.justification, "approved_by": w.approved_by}
            for w in package.waivers],
        "package_provenance": package.provenance,
    }

    runs, signatures = [], []
    with tempfile.TemporaryDirectory(prefix="unwritten-") as scratch:
        for index in range(1, repeat + 1):
            progress("run", index, repeat)
            ctx = RunContext(package, repo, runner, scratch, out_dir, f"run{index}", jobs)
            worlds = {}
            for world_class in PIPELINE:
                world = world_class().run(ctx, worlds)
                worlds[world.name] = world
                progress("world", world)
            environment.update({k: v for k, v in ctx.observed.items() if v})
            signatures.append({name: world.signature() for name, world in worlds.items()})
            runs.append({"index": index, "fingerprint": fingerprint(worlds),
                         "worlds": {name: world.to_dict() for name, world in worlds.items()}})

    reasons = {}
    for run in runs:
        for world in run["worlds"].values():
            for reason in world["reasons"]:
                key = (reason["code"], reason["world"], tuple(reason["items"]))
                reasons.setdefault(key, dict(reason, runs=[]))["runs"].append(run["index"])
    differences = compare_runs(signatures)
    equivalent = not differences
    if not equivalent:
        reasons[("REPEATABILITY_MISMATCH",)] = {
            "code": "REPEATABILITY_MISMATCH", "world": "repeatability",
            "message": "identical inputs produced different results, so no result can be trusted",
            "items": differences, "runs": [run["index"] for run in runs]}

    evidence["reasons"] = list(reasons.values())
    evidence["verdict"] = "rejected" if reasons else "accepted"
    evidence["repeatability"] = {
        "runs": repeat, "equivalent": equivalent,
        "fingerprints": [run["fingerprint"] for run in runs],
        "compared": "world status, rejection reasons, every test's id, outcome, failure class "
                    "and exception type, every mutant's status and killing tests",
    }
    evidence["summary"] = {name: {"status": world["status"], **world["summary"]}
                           for name, world in runs[0]["worlds"].items()}
    evidence["runs"] = runs
    return _finish(evidence, out_dir, started, clock)


def _finish(evidence, out_dir, started, clock):
    evidence["timing"] = {"started_at": started, "finished_at": _now(),
                          "duration_seconds": round(time.monotonic() - clock, 2)}
    evidence["evidence_dir"] = str(out_dir)
    (out_dir / EVIDENCE_FILE).write_text(
        json.dumps({k: v for k, v in evidence.items() if k != "evidence_dir"}, indent=2) + "\n",
        encoding="utf-8")
    return evidence
