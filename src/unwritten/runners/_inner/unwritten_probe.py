"""Pytest plugin that records, per test, how it ended and with which exception.

Runs inside the sandbox. JUnit XML cannot distinguish an assertion failure
from, say, an AttributeError raised in a test body, and that distinction is
what World 1 is judged on.
"""
import json
import os

import pytest

_LIMIT = 4000


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
                phase["assertion_level"] = bool(
                    issubclass(kind, AssertionError) or issubclass(kind, pytest.fail.Exception))
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
