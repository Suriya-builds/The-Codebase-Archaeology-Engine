"""Pytest plugin that records, per test, how it ended and with which exception.

Runs inside the sandbox. JUnit XML cannot distinguish an assertion failure
from, say, an AttributeError raised in a test body, and that distinction is
what World 1 is judged on.
"""
import json
import os
import re
import types

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


_UNEXPECTED_ARGUMENT = re.compile(r"([\w.]+)\(\) got an unexpected keyword argument '(\w+)'")


def _missing_name(exc):
    """What exactly was not there, read from the exception object.

    ImportError and AttributeError carry the missing name as attributes. A
    TypeError for an unexpected argument does not, so its message is parsed
    and the result is marked as derived from the message.
    """
    if isinstance(exc, ImportError):
        return {"kind": "import", "module": exc.name, "name": getattr(exc, "name_from", None)}
    if isinstance(exc, AttributeError) and getattr(exc, "name", None):
        owner, obj = None, getattr(exc, "obj", None)
        if isinstance(obj, types.ModuleType):
            owner = obj.__name__
        elif isinstance(obj, type):
            owner = f"{obj.__module__}.{obj.__qualname__}"
        elif obj is not None:
            owner = f"{type(obj).__module__}.{type(obj).__qualname__}"
        return {"kind": "attribute", "owner": owner, "name": exc.name}
    if isinstance(exc, TypeError):
        match = _UNEXPECTED_ARGUMENT.match(str(exc))
        if match:
            return {"kind": "argument", "callable": match.group(1), "name": match.group(2),
                    "source": "exception message"}
    return None


def _root_cause(exc):
    """pytest wraps import failures of a test module; find the original error."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, (ImportError, SyntaxError)):
            return exc
        exc = exc.__cause__ or exc.__context__
    return None


def _frames_in(traceback, rootdir):
    """Workspace-relative paths of the traceback's frames, outermost first."""
    root = os.path.realpath(str(rootdir)) + os.sep
    paths = []
    while traceback is not None:
        path = os.path.realpath(traceback.tb_frame.f_code.co_filename)
        if path.startswith(root):
            paths.append(path[len(root):].replace(os.sep, "/"))
        traceback = traceback.tb_next
    return paths


def _is_test_path(relative):
    return os.path.basename(relative) == "conftest.py" or any(
        relative == r or relative.startswith(r + "/") for r in _test_roots())


def _clip(text):
    text = str(text)
    return text if len(text) <= _LIMIT else text[:_LIMIT] + "\n...[truncated]"


class Probe:
    def __init__(self, path):
        self.path = path
        self.tests = {}
        self.collection_errors = []
        self.collection_info = {}

    def pytest_exception_interact(self, node, call, report):
        # Runs before pytest_collectreport; keeps what the failure report alone loses.
        if getattr(report, "when", None) != "collect" or call.excinfo is None:
            return
        cause = _root_cause(call.excinfo.value)
        if cause is None:
            return
        info = {"exc_type": f"{type(cause).__module__}.{type(cause).__qualname__}"}
        frames = _frames_in(cause.__traceback__, node.config.rootpath)
        if frames:
            info["raised_in"] = frames[-1]
            info["raised_in_tests"] = _is_test_path(frames[-1])
        missing = _missing_name(cause)
        if missing:
            info["missing"] = missing
        self.collection_info[report.nodeid] = info

    def pytest_collectreport(self, report):
        if report.failed:
            entry = {"nodeid": report.nodeid, "detail": _clip(report.longrepr)}
            entry.update(self.collection_info.get(report.nodeid, {}))
            self.collection_errors.append(entry)

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
                phase["raised_in_tests"] = bool(in_tests)
                missing = _missing_name(call.excinfo.value)
                if missing:
                    phase["missing"] = missing
                # A behavioural assertion: an explicit pytest failure (DID NOT RAISE,
                # pytest.fail), or an AssertionError that test code itself raised.
                # An AssertionError from inside the code under test is a crash.
                phase["assertion_level"] = bool(
                    issubclass(kind, pytest.fail.Exception)
                    or (issubclass(kind, AssertionError) and in_tests))
                phase["message"] = _clip(call.excinfo.value)
        entry = self.tests.setdefault(item.nodeid, {"nodeid": item.nodeid, "phases": {}})
        # Setup and teardown are recorded only when they say something; the call
        # phase always is. This keeps the report small without losing a result.
        if report.when == "call" or phase != {"outcome": "passed"}:
            entry["phases"][report.when] = phase

    def pytest_sessionfinish(self, session, exitstatus):
        payload = {
            "pytest_version": pytest.__version__,
            "exit_status": int(exitstatus),
            "tests": list(self.tests.values()),
            "collection_errors": self.collection_errors,
        }
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))


def pytest_configure(config):
    path = os.environ.get("UNWRITTEN_REPORT")
    if path:
        config.pluginmanager.register(Probe(path), "unwritten-probe")
