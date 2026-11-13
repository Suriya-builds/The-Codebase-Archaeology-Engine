"""The worlds a task package must survive.

Each world is a small class with a ``run(ctx, prior)`` method, executed in
pipeline order. Adding a world (for example a blind solver) means adding a
class and appending it to ``PIPELINE``; nothing else depends on the count.

A world never decides anything from prose. Every reason it emits names the
tests or mutants that produced it.
"""
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from unwritten.classify import BENIGN
from unwritten.execution import run_in_sandbox
from unwritten.adapters import detect_adapter
from unwritten.mutation import declared_mutants, generate_mutants
from unwritten.patches import PatchDoesNotApply, apply_patch, is_under, touched_paths
from unwritten.workspace import export_commit, install_package_tests

REGRESSION_SUITES = ("regression_package", "regression_contracts", "regression_repo")


@dataclass
class Reason:
    code: str
    world: str
    message: str
    items: list = field(default_factory=list)

    def to_dict(self):
        return {"code": self.code, "world": self.world, "message": self.message,
                "items": sorted(self.items)}


@dataclass
class WorldResult:
    name: str
    title: str
    status: str = "passed"      # passed | failed | not_run
    expectation: str = ""
    reasons: list = field(default_factory=list)
    suites: dict = field(default_factory=dict)
    mutants: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def reject(self, code, message, items=()):
        self.status = "failed"
        self.reasons.append(Reason(code, self.name, message, list(items)))

    def to_dict(self):
        data = {"title": self.title, "status": self.status, "expectation": self.expectation,
                "summary": self.summary, "reasons": [r.to_dict() for r in self.reasons]}
        if self.suites:
            data["suites"] = {name: suite.to_dict() for name, suite in self.suites.items()}
        if self.name == "world3":
            data["mutants"] = self.mutants
        return data

    def signature(self):
        """Everything that must be identical between two honest runs."""
        return {
            "status": self.status,
            "reasons": sorted([r.code, sorted(r.items)] for r in self.reasons),
            "suites": {
                name: {
                    "tests": [[t.nodeid, t.outcome, t.failure_class, t.exc_type] for t in suite.tests],
                    "collection_errors": [e["nodeid"] for e in suite.collection_errors],
                    "timed_out": suite.timed_out,
                } for name, suite in sorted(self.suites.items())
            },
            "mutants": [[m["id"], m["status"], sorted([k["suite"], k["test"]] for k in m["killed_by"])]
                        for m in self.mutants],
        }


class RunContext:
    """One full pass through the pipeline: where to build, run and log."""

    def __init__(self, package, repo, runner, scratch, out_dir, label, jobs=1):
        self.package, self.repo, self.runner, self.jobs = package, Path(repo), runner, jobs
        self.scratch, self.out_dir, self.label = Path(scratch) / label, Path(out_dir), label
        self.observed = {}

    def build_source(self, name, patches=(), mutant=None):
        """Pinned commit + patches (+ mutant) in a fresh directory. Executes nothing."""
        workspace = self.scratch / name
        export_commit(self.repo, self.package.commit, workspace)
        for patch in patches:
            apply_patch(workspace, patch)
        if mutant is not None:
            mutant.apply(workspace)
        return workspace

    def execute(self, name, patches=(), mutant=None):
        """Build a workspace, run every suite in the sandbox, return SuiteResults."""
        workspace = self.build_source(name, patches, mutant)
        suites = install_package_tests(self.package, workspace)
        results, summary = run_in_sandbox(
            self.runner, self.package, workspace, suites, self.out_dir,
            Path("logs") / self.label / name)
        shutil.rmtree(workspace, ignore_errors=True)
        for result in results.values():
            if result.pytest_version:
                self.observed["pytest_version"] = result.pytest_version
        self.observed["python_version"] = summary.get("python_version")
        return results


def _timeouts(world, suites):
    timed_out = [name for name, suite in suites.items() if suite.timed_out]
    if timed_out:
        world.reject("EXECUTION_TIMEOUT",
                     "suites exceeded the package's time limit", timed_out)


def _failing_contracts(package, suites):
    """Attached contracts with a probe that is missing or not passing in ``suites``."""
    outcomes = {(name, t.nodeid): t.outcome for name, suite in suites.items() for t in suite.tests}
    failing = []
    for contract in package.contracts:
        bad = [p["nodeid"] for p in contract["probes"]
               if outcomes.get((p["suite"], p["nodeid"])) != "passed"]
        if bad:
            failing.append(f"{contract['id']}: {contract['description']} "
                           f"[{len(bad)} of {len(contract['probes'])} probes not passing]")
    return failing


def _contract_mutants(package, reference_workspace):
    """One mutant per attached guard contract: the guard, removed.

    The guard is located again in the patched source, because the reference
    patch may have moved it. Returns (mutants, ids of guards no longer found).
    """
    guarded = [c for c in package.contracts if "guard" in c]
    if not guarded:
        return [], []
    adapter = detect_adapter(reference_workspace)
    mutants, missing = [], []
    for contract in guarded:
        wanted = contract["guard"]
        path = Path(reference_workspace) / wanted["file"]
        data = path.read_bytes() if path.is_file() else b""
        guard = next((g for g in adapter.guards(data, wanted["file"])
                      if (g.symbol, g.condition, g.exception)
                      == (wanted["symbol"], wanted["condition"], wanted["exception"])), None)
        removal = next((m for m in adapter.mutants(data, wanted["file"], {guard.line})
                        if m.id == adapter.guard_mutant_id(guard)), None) if guard else None
        if removal is None:
            missing.append(contract["id"])
            continue
        removal.id = f"contract:{contract['id']}"
        removal.origin = "contract"
        removal.description = f"violates a verified contract: {contract['description']}"
        mutants.append(removal)
    return mutants, missing


def _regression_tests(suites):
    return {(name, test.nodeid): test
            for name in REGRESSION_SUITES if name in suites for test in suites[name].tests}


class BrokenWorld:
    """World 1: the pinned commit, untouched. The feature must be provably absent."""

    name, title = "world1", "Broken World"
    expectation = ("every feature test fails with an assertion-level failure; "
                   "every regression test passes")

    def run(self, ctx, prior):
        world = WorldResult(self.name, self.title, expectation=self.expectation)
        world.suites = ctx.execute(self.name)
        _timeouts(world, world.suites)
        feature = world.suites["feature"]

        if feature.collection_errors:
            world.reject("W1_FEATURE_COLLECTION_ERROR",
                         "feature tests could not be imported or collected at the base commit; "
                         "that says nothing about the missing behaviour",
                         [e["nodeid"] for e in feature.collection_errors])
        if not feature.tests and not feature.collection_errors:
            world.reject("W1_NO_FEATURE_TESTS", "the package has no feature tests")
        passing = [t.nodeid for t in feature.tests if t.outcome in ("passed", "xpassed")]
        if passing:
            world.reject("W1_FEATURE_PASSES_AT_BASE",
                         "feature tests already pass before the feature exists, so they do not "
                         "detect its absence", passing)
        crashed = [f"{t.nodeid} [{t.exc_type} in {t.phase}, raised in {t.raised_in}]"
                   for t in feature.tests if t.failure_class == "runtime_error"]
        if crashed:
            world.reject("W1_FEATURE_RUNTIME_ERROR",
                         "feature tests must fail through a behavioural assertion; these crash "
                         "instead (typically on an interface that does not exist yet)", crashed)
        idle = [f"{t.nodeid} [{t.outcome}]" for t in feature.tests
                if t.outcome in ("skipped", "xfailed")]
        if idle:
            world.reject("W1_FEATURE_NOT_EXECUTED",
                         "skipped or expected-failure tests assert nothing about the base commit",
                         idle)

        regression = _regression_tests(world.suites)
        broken = [f"{suite}::{e['nodeid']} [collection error]"
                  for suite in REGRESSION_SUITES if suite in world.suites
                  for e in world.suites[suite].collection_errors]
        broken += [f"{nodeid} [{t.outcome}]" for (_, nodeid), t in regression.items()
                   if t.outcome not in BENIGN]
        if broken:
            world.reject("W1_REGRESSION_FAILS_AT_BASE",
                         "regression tests must pass at the base commit", broken)
        if not regression:
            world.reject("W1_NO_REGRESSION_TESTS", "no regression test ran at the base commit")
        unproven = _failing_contracts(ctx.package, world.suites)
        if unproven:
            world.reject("W1_CONTRACT_PROBE_NOT_PASSING",
                         "attached contracts must hold at the base commit; these do not, so "
                         "they are not verified contracts of this commit", unproven)

        world.summary = {
            "feature_tests": len(feature.tests),
            "feature_failing_by_assertion": sum(t.failure_class == "assertion" for t in feature.tests),
            "regression_tests": len(regression),
            "regression_passing": sum(t.outcome == "passed" for t in regression.values()),
        }
        return world


class CorrectWorld:
    """World 2: the reference implementation applied. Everything must hold."""

    name, title = "world2", "Correct World"
    expectation = ("with the reference implementation, every feature test passes and every "
                   "regression test ends exactly as it did in World 1")

    def run(self, ctx, prior):
        world = WorldResult(self.name, self.title, expectation=self.expectation)
        package = ctx.package
        patch_text = package.reference_patch.read_text(encoding="utf-8")
        in_tests = [p for p in touched_paths(patch_text) if is_under(p, package.repo_test_paths)]
        if in_tests:
            world.reject("REFERENCE_MODIFIES_TESTS",
                         "the reference patch edits the repository's own tests, so regression "
                         "results would not be comparable", in_tests)
        try:
            world.suites = ctx.execute(self.name, [package.reference_patch])
        except PatchDoesNotApply as exc:
            world.reject("REFERENCE_PATCH_DOES_NOT_APPLY", str(exc))
            return world
        _timeouts(world, world.suites)

        feature = world.suites["feature"]
        failing = [f"{t.nodeid} [{t.outcome}: {t.exc_type or t.failure_class}]"
                   for t in feature.tests if t.outcome != "passed"]
        failing += [f"{e['nodeid']} [collection error]" for e in feature.collection_errors]
        if failing:
            world.reject("W2_FEATURE_FAILS_WITH_REFERENCE",
                         "feature tests must pass once the reference implementation is applied",
                         failing)

        before = prior.get("world1")
        regression = _regression_tests(world.suites)
        broken = [f"{suite}::{e['nodeid']} [collection error]"
                  for suite in REGRESSION_SUITES if suite in world.suites
                  for e in world.suites[suite].collection_errors]
        broken += [f"{nodeid} [{t.outcome}]" for (_, nodeid), t in regression.items()
                   if t.outcome not in BENIGN]
        if before is not None and before.suites:
            earlier = _regression_tests(before.suites)
            broken += [f"{key[1]} [was {earlier[key].outcome}, now {regression[key].outcome}]"
                       for key in sorted(set(earlier) & set(regression))
                       if earlier[key].outcome != regression[key].outcome
                       and regression[key].outcome in BENIGN]
            broken += [f"{key[1]} [no longer collected]" for key in sorted(set(earlier) - set(regression))]
            earlier_feature = {t.nodeid for t in before.suites["feature"].tests}
            now_feature = {t.nodeid for t in feature.tests}
            if earlier_feature != now_feature and not before.suites["feature"].collection_errors:
                world.reject("W2_TEST_SET_DIFFERS",
                             "the set of feature tests changed between World 1 and World 2",
                             sorted(earlier_feature ^ now_feature))
        if broken:
            world.reject("W2_REGRESSION_BROKEN",
                         "the reference implementation changes existing behaviour", broken)
        violated = _failing_contracts(package, world.suites)
        if violated:
            world.reject("W2_CONTRACT_VIOLATED",
                         "the reference implementation breaks contracts that were verified at "
                         "the base commit", violated)

        world.summary = {
            "feature_tests": len(feature.tests),
            "feature_passing": sum(t.outcome == "passed" for t in feature.tests),
            "regression_tests": len(regression),
            "regression_passing": sum(t.outcome == "passed" for t in regression.values()),
        }
        return world


class DeceptionWorld:
    """World 3: plausible wrong implementations. The tests must notice each one."""

    name, title = "world3", "Deception World"
    expectation = ("every mutant of the reference implementation makes at least one test fail "
                   "that passed in World 2")

    def run(self, ctx, prior):
        world = WorldResult(self.name, self.title, expectation=self.expectation)
        correct = prior.get("world2")
        if correct is None or correct.status != "passed":
            world.status = "not_run"
            world.summary = {"note": "skipped: mutants are judged against World 2, which did not pass"}
            return world

        package = ctx.package
        mutants = declared_mutants(package)
        guards_not_found = []
        if package.generate_mutants or package.contracts:
            reference = ctx.build_source("reference-source", [package.reference_patch])
            if package.generate_mutants:
                mutants += generate_mutants(
                    reference, package.reference_patch.read_text(encoding="utf-8"),
                    package.repo_test_paths)
            contract_mutants, guards_not_found = _contract_mutants(package, reference)
            mutants += contract_mutants
            shutil.rmtree(reference, ignore_errors=True)
        if not mutants:
            world.reject("W3_NO_MUTANTS",
                         "no mutant was executed, so nothing shows the tests can detect a wrong "
                         "implementation")

        waivers = {w.mutant_id: w for w in package.waivers}
        unknown = sorted(set(waivers) - {m.id for m in mutants})
        if unknown:
            world.reject("W3_WAIVER_UNKNOWN_MUTANT",
                         "waivers refer to mutants that do not exist", unknown)

        baseline = {(name, t.nodeid) for name, suite in correct.suites.items()
                    for t in suite.tests if t.outcome == "passed"}
        diff_dir = ctx.out_dir / "mutants"
        diff_dir.mkdir(parents=True, exist_ok=True)
        def judge(mutant):
            slug = re.sub(r"[^A-Za-z0-9._-]+", "_", mutant.id)
            (diff_dir / f"{slug}.diff").write_text(mutant.diff, encoding="utf-8")
            record = {
                "id": mutant.id, "origin": mutant.origin, "operator": mutant.operator,
                "description": mutant.description, "file": mutant.file, "line": mutant.line,
                "diff": f"mutants/{slug}.diff", "status": "killed", "killed_by": [],
                "logs": f"logs/{ctx.label}/{slug}",
            }
            waiver = waivers.get(mutant.id)
            if waiver:
                record["waiver"] = {"justification": waiver.justification,
                                    "approved_by": waiver.approved_by}
            try:
                suites = ctx.execute(slug, [package.reference_patch], mutant)
            except PatchDoesNotApply as exc:
                record.update(status="invalid", error=str(exc), logs=None)
                return record
            record["killed_by"] = _killers(baseline, suites)
            if not record["killed_by"]:
                record["status"] = "survived"
            return record

        # Mutants are independent, each in its own workspace; results keep mutant order.
        with ThreadPoolExecutor(max_workers=max(1, ctx.jobs)) as pool:
            world.mutants = list(pool.map(judge, mutants))
        invalid = [m["id"] for m in world.mutants if m["status"] == "invalid"]
        survived = [m["id"] for m in world.mutants
                    if m["status"] == "survived" and "waiver" not in m]

        if invalid:
            world.reject("W3_MUTANT_INVALID",
                         "declared mutants do not apply on top of the reference implementation",
                         invalid)
        if survived:
            world.reject("W3_MUTANT_SURVIVED",
                         "wrong implementations pass every test, so the tests would accept them",
                         survived)
        executed = [m for m in world.mutants if m["status"] != "invalid"]
        killed = sum(m["status"] == "killed" for m in executed)
        world.summary = {
            "mutants_executed": len(executed),
            "declared": sum(m["origin"] == "declared" for m in executed),
            "generated": sum(m["origin"] == "generated" for m in executed),
            "contract": sum(m["origin"] == "contract" for m in executed),
            "contract_guards_not_found": guards_not_found,
            "killed": killed,
            "survived": len(executed) - killed,
            "survived_waived": sum(m["status"] == "survived" and "waiver" in m for m in executed),
            "mutation_score": round(killed / len(executed), 4) if executed else None,
        }
        return world


def _killers(baseline, suites):
    """Tests that passed in World 2 and no longer pass under this mutant."""
    killers = []
    for name, suite in sorted(suites.items()):
        if suite.timed_out:
            killers.append({"suite": name, "test": "<suite timed out>", "outcome": "timeout"})
        for error in suite.collection_errors:
            killers.append({"suite": name, "test": error["nodeid"], "outcome": "collection_error"})
        for test in suite.tests:
            if (name, test.nodeid) in baseline and test.outcome != "passed":
                killer = {"suite": name, "test": test.nodeid, "outcome": test.outcome,
                          "failure_class": test.failure_class, "exc_type": test.exc_type}
                if test.message:
                    killer["message"] = test.message[:300]
                killers.append(killer)
    return killers


PIPELINE = (BrokenWorld, CorrectWorld, DeceptionWorld)
