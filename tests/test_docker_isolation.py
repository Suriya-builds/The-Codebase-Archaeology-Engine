"""The Docker sandbox must actually confine what it runs."""
import textwrap

import pytest

from conftest import PACKAGES, VALID, docker_available
from unwritten.classify import load_suite
from unwritten.package import load_package
from unwritten.runners import make_runner

pytestmark = pytest.mark.docker

PROBES = """
    import os
    import socket

    import pytest


    def test_no_network():
        with pytest.raises(OSError):
            socket.create_connection(("1.1.1.1", 53), timeout=3)


    def test_not_root():
        assert os.getuid() != 0


    def test_root_filesystem_is_read_only():
        with pytest.raises(OSError):
            open("/usr/probe", "w")


    def test_harness_files_are_read_only():
        with pytest.raises(OSError):
            open("/unwritten-inner/probe", "w")
"""


def test_container_is_confined(tmp_path):
    if not docker_available():
        pytest.skip("Docker daemon not reachable: container isolation was NOT checked")
    package = load_package(PACKAGES / VALID)
    runner = make_runner("docker")
    environment = runner.prepare(package)
    assert environment["isolation"]["network"] == "none"
    assert environment["image_id"].startswith("sha256:")

    workspace, out = tmp_path / "ws", tmp_path / "out"
    (workspace / "probe").mkdir(parents=True)
    out.mkdir()
    (workspace / "probe" / "test_sandbox.py").write_text(textwrap.dedent(PROBES))
    summary = runner.run(workspace, [{"name": "probe", "paths": ["probe"]}], package, out)
    suite = load_suite("probe", out, summary, "probe.log")
    assert {t.nodeid.split("::")[1]: t.outcome for t in suite.tests} == {
        "test_no_network": "passed", "test_not_root": "passed",
        "test_root_filesystem_is_read_only": "passed",
        "test_harness_files_are_read_only": "passed"}
