"""Turning raw probe reports into per-test results with a failure class.

Failure classes, kept strictly apart:

    assertion         an AssertionError raised by test code, or an explicit
                      pytest failure such as "DID NOT RAISE"
    runtime_error     any other exception in the test body or a fixture,
                      including an AssertionError raised by the code under test
    collection_error  pytest could not import or collect a file (suite level)
    timeout           the suite exceeded its time limit (suite level)
    infrastructure    the sandbox or pytest itself failed; raised as
                      HarnessError and never turned into a verdict
"""
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from unwritten.errors import HarnessError

# A test that ended in one of these did not report a defect.
BENIGN = ("passed", "skipped", "xfailed")


@dataclass
class TestResult:
    __test__ = False  # not a pytest test class

    nodeid: str
    outcome: str            # passed | failed | error | skipped | xfailed | xpassed
    failure_class: str | None   # assertion | runtime_error
    exc_type: str | None = None
    phase: str | None = None    # setup | call | teardown, for failures
    raised_in: str | None = None
    raised_in_tests: bool | None = None
    missing: dict | None = None   # the name that was not there, when the failure is of that kind
    message: str | None = None
    detail: str | None = None

    def to_dict(self):
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class SuiteResult:
    name: str
    tests: list = field(default_factory=list)
    collection_errors: list = field(default_factory=list)
    exit_code: int | None = None
    timed_out: bool = False
    duration_seconds: float = 0.0
    pytest_version: str | None = None
    log: str | None = None

    def by_id(self):
        return {test.nodeid: test for test in self.tests}

    def to_dict(self):
        return {
            "name": self.name, "exit_code": self.exit_code, "timed_out": self.timed_out,
            "duration_seconds": self.duration_seconds, "log": self.log,
            "counts": _counts(self.tests),
            "collection_errors": self.collection_errors,
            "tests": [test.to_dict() for test in self.tests],
        }


def _counts(tests):
    counts = {}
    for test in tests:
        counts[test.outcome] = counts.get(test.outcome, 0) + 1
    return dict(sorted(counts.items()))


def classify_test(raw):
    phases = raw.get("phases", {})
    setup, call, teardown = phases.get("setup"), phases.get("call"), phases.get("teardown")
    nodeid = raw["nodeid"]

    def failed(phase, name, outcome, failure_class):
        return TestResult(nodeid, outcome, failure_class, phase.get("exc_type"), name,
                          phase.get("raised_in"), phase.get("raised_in_tests"),
                          phase.get("missing"), phase.get("message"), phase.get("detail"))

    if setup and setup["outcome"] == "failed":
        return failed(setup, "setup", "error", "runtime_error")
    if setup and setup["outcome"] == "skipped":
        return TestResult(nodeid, "xfailed" if setup.get("xfail") else "skipped", None)
    if call is None:
        return TestResult(nodeid, "error", "runtime_error", phase="setup",
                          message="test body never ran")
    if call["outcome"] == "failed":
        failure_class = "assertion" if call.get("assertion_level") else "runtime_error"
        return failed(call, "call", "failed", failure_class)
    if teardown and teardown["outcome"] == "failed":
        return failed(teardown, "teardown", "error", "runtime_error")
    if call.get("xfail"):
        return TestResult(nodeid, "xfailed" if call["outcome"] == "skipped" else "xpassed", None)
    return TestResult(nodeid, call["outcome"], None)


def load_suite(name, out_dir, summary, log_reference):
    """Build a SuiteResult from the files a runner left in ``out_dir``."""
    meta = summary["suites"].get(name, {})
    suite = SuiteResult(
        name=name, exit_code=meta.get("exit_code"), timed_out=bool(meta.get("timed_out")),
        duration_seconds=meta.get("duration_seconds", 0.0), log=log_reference)
    report = Path(out_dir) / f"{name}.json"
    if report.is_file():
        raw = json.loads(report.read_text(encoding="utf-8"))
        suite.pytest_version = raw.get("pytest_version")
        suite.tests = sorted((classify_test(t) for t in raw["tests"]), key=lambda t: t.nodeid)
        suite.collection_errors = sorted(raw["collection_errors"], key=lambda e: e["nodeid"])
    elif not suite.timed_out:
        raise HarnessError(
            f"suite {name!r} ended without a report (pytest exit code {suite.exit_code}); "
            f"this is an infrastructure failure, not a test result. See {log_reference}")
    return suite
