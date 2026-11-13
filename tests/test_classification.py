"""The five failure classes, told apart from structured pytest data."""
import textwrap
from types import SimpleNamespace

import pytest

from unwritten.errors import HarnessError
from unwritten.execution import run_in_sandbox
from unwritten.runners import make_runner

LIBRARY = """
    def checked(value):
        assert value > 0, "internal invariant"
        return value
"""

HELPERS = """
    def expect_equal(a, b):
        assert a == b
"""

CASES = """
    import unittest

    import pytest

    from checks.helpers import expect_equal
    from library import checked


    def test_plain_assert():
        assert checked(1) == 2

    def test_did_not_raise():
        with pytest.raises(ValueError):
            checked(1)

    def test_explicit_fail():
        pytest.fail("not implemented behaviour")

    def test_assert_in_test_helper():
        expect_equal(checked(1), 2)

    class TestUnittestStyle(unittest.TestCase):
        def test_assert_equal(self):
            self.assertEqual(checked(1), 2)

    def test_assertion_raised_by_the_library():
        checked(-1)

    def test_missing_interface():
        checked.no_such_attribute

    @pytest.fixture
    def broken():
        raise RuntimeError("fixture failed")

    def test_fixture_error(broken):
        assert False

    def test_passes():
        assert checked(1) == 1
"""


@pytest.fixture
def sandbox(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "checks").mkdir(parents=True)
    (workspace / "library.py").write_text(textwrap.dedent(LIBRARY))
    (workspace / "checks" / "__init__.py").write_text("")
    (workspace / "checks" / "helpers.py").write_text(textwrap.dedent(HELPERS))

    def run(files, timeout=30):
        for name, body in files.items():
            (workspace / "checks" / name).write_text(textwrap.dedent(body))
        settings = SimpleNamespace(python_path=["."], timeout_seconds=timeout)
        results, _ = run_in_sandbox(make_runner("local"), settings, workspace,
                                    [{"name": "cases", "paths": ["checks"]}], tmp_path / "out", "logs")
        return results["cases"]

    return run


def test_assertions_and_runtime_errors_are_told_apart(sandbox):
    suite = sandbox({"test_cases.py": CASES})
    got = {t.nodeid.split("::")[-1]: (t.outcome, t.failure_class) for t in suite.tests}
    assert got == {
        "test_plain_assert": ("failed", "assertion"),
        "test_did_not_raise": ("failed", "assertion"),
        "test_explicit_fail": ("failed", "assertion"),
        "test_assert_in_test_helper": ("failed", "assertion"),
        "test_assert_equal": ("failed", "assertion"),
        # An AssertionError from inside the code under test is a crash, not a test verdict.
        "test_assertion_raised_by_the_library": ("failed", "runtime_error"),
        "test_missing_interface": ("failed", "runtime_error"),
        "test_fixture_error": ("error", "runtime_error"),
        "test_passes": ("passed", None),
    }
    by_name = {t.nodeid.split("::")[-1]: t for t in suite.tests}
    assert by_name["test_assertion_raised_by_the_library"].raised_in == "library.py"
    assert by_name["test_assertion_raised_by_the_library"].exc_type == "builtins.AssertionError"
    assert by_name["test_plain_assert"].raised_in == "checks/test_cases.py"
    assert by_name["test_assert_in_test_helper"].raised_in == "checks/helpers.py"
    assert by_name["test_did_not_raise"].exc_type.endswith("Failed")
    assert by_name["test_missing_interface"].exc_type == "builtins.AttributeError"
    assert by_name["test_fixture_error"].phase == "setup"
    assert suite.collection_errors == [] and not suite.timed_out


def test_collection_errors_are_their_own_class(sandbox):
    suite = sandbox({"test_ok.py": "def test_ok():\n    assert True\n",
                     "test_import.py": "import no_such_module\n\ndef test_x():\n    assert False\n",
                     "test_syntax.py": "def test_y(:\n"})
    assert [t.outcome for t in suite.tests] == ["passed"]
    assert [e["nodeid"] for e in suite.collection_errors] == [
        "checks/test_import.py", "checks/test_syntax.py"]
    assert "ModuleNotFoundError" in suite.collection_errors[0]["detail"]


def test_timeouts_are_their_own_class(sandbox):
    suite = sandbox({"test_slow.py": "import time\n\ndef test_slow():\n    time.sleep(60)\n"},
                    timeout=2)
    assert suite.timed_out and suite.tests == [] and suite.collection_errors == []


def test_a_run_that_dies_without_a_report_is_an_infrastructure_error(sandbox):
    with pytest.raises(HarnessError, match="infrastructure failure"):
        sandbox({"test_dies.py": "import os\n\ndef test_dies():\n    os._exit(7)\n"})
