"""Pytest plugin that records, per test, how it ended and with which exception.

Runs inside the sandbox. JUnit XML cannot distinguish an assertion failure
from, say, an AttributeError raised in a test body, and that distinction is
what World 1 is judged on.
"""
import json
import os

import pytest

_LIMIT = 4000


def _test_roots():
    return [r.rstrip("/") for r in os.environ.get("UNWRITTEN_TEST_ROOTS", "").split(os.pathsep) if r]


def _raised_in(excinfo, rootdir):
    """Innermost traceback frame that lies inside the workspace.

    Returns (relative path, is test code). Frames outside the workspace (the
    standard library, pytest, assertion helpers) are skipped, so a failing
    ``self.assertEqual`` is attributed to the test that called it.
    """
    root = os.path.realpath(str(rootdir)) + os.sep
    for entry in reversed(list(excinfo.traceback)):
        path = os.path.realpath(str(entry.path))
        if not path.startswith(root):
            continue
        relative = path[len(root):].replace(os.sep, "/")
        in_tests = os.path.basename(relative) == "conftest.py" or any(
            relative == r or relative.startswith(r + "/") for r in _test_roots())
        return relative, in_tests
    return None, True


def _clip(text):
    text = str(text)
    return text if len(text) <= _LIMIT else text[:_LIMIT] + "\n...[truncated]"


class Probe:
    def __init__(self, path):
        self.path = path
        self.tests = {}
        self.collection_errors = []

    def pytest_collectreport(self, report):
        if report.failed:
            self.collection_errors.append(
                {"nodeid": report.nodeid, "detail": _clip(report.longrepr)})

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        outcome = yield
        report = outcome.get_result()
        phase = {"outcome": report.outcome}
        if hasattr(report, "wasxfail"):
            phase["xfail"] = True
        if report.outcome == "failed":
            phase["detail"] = _clip(report.longrepr)
            if call.excinfo is not None:
                kind = call.excinfo.type
                phase["exc_type"] = f"{kind.__module__}.{kind.__qualname__}"
                raised_in, in_tests = _raised_in(call.excinfo, item.config.rootpath)
                phase["raised_in"] = raised_in
                # A behavioural assertion: an explicit pytest failure (DID NOT RAISE,
                # pytest.fail), or an AssertionError that test code itself raised.
                # An AssertionError from inside the code under test is a crash.
                phase["assertion_level"] = bool(
                    issubclass(kind, pytest.fail.Exception)
                    or (issubclass(kind, AssertionError) and in_tests))
                phase["message"] = _clip(call.excinfo.value)
        entry = self.tests.setdefault(item.nodeid, {"nodeid": item.nodeid, "phases": {}})
        entry["phases"][report.when] = phase

    def pytest_sessionfinish(self, session, exitstatus):
        payload = {
            "pytest_version": pytest.__version__,
            "exit_status": int(exitstatus),
            "tests": list(self.tests.values()),
            "collection_errors": self.collection_errors,
        }
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=1)


def pytest_configure(config):
    path = os.environ.get("UNWRITTEN_REPORT")
    if path:
        config.pluginmanager.register(Probe(path), "unwritten-probe")
