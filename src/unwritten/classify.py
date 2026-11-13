"""Turning raw probe reports into per-test results with a failure class."""
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# A test that ended in one of these did not report a defect.
BENIGN = ("passed", "skipped", "xfailed")


@dataclass
class TestResult:
    __test__ = False  # not a pytest test class

    nodeid: str
    outcome: str            # passed | failed | error | skipped | xfailed | xpassed
    category: str | None    # assertion | exception | setup_error | teardown_error
    exc_type: str | None = None
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

    def failed(phase, outcome, category):
        return TestResult(raw["nodeid"], outcome, category, phase.get("exc_type"),
                          phase.get("message"), phase.get("detail"))

    if setup and setup["outcome"] == "failed":
        return failed(setup, "error", "setup_error")
    if setup and setup["outcome"] == "skipped":
        return TestResult(raw["nodeid"], "xfailed" if setup.get("xfail") else "skipped", None)
    if call is None:
        return TestResult(raw["nodeid"], "error", "setup_error", message="test body never ran")
    if call["outcome"] == "failed":
        category = "assertion" if call.get("assertion_level") else "exception"
        return failed(call, "failed", category)
    if teardown and teardown["outcome"] == "failed":
        return failed(teardown, "error", "teardown_error")
    if call.get("xfail"):
        return TestResult(raw["nodeid"], "xfailed" if call["outcome"] == "skipped" else "xpassed", None)
    return TestResult(raw["nodeid"], call["outcome"], None)


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
        suite.collection_errors = [{
            "nodeid": name, "detail": "pytest wrote no report; see the suite log"}]
    return suite
