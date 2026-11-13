import json
import shutil
import subprocess
from pathlib import Path

import pytest

from pilot.materialize import materialize
from unwritten.runners import make_runner
from unwritten.verify import verify_package

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "packages"
VALID = "pilot-cart-discount-eligibility"


def docker_available():
    try:
        return subprocess.run(["docker", "version"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@pytest.fixture(scope="session")
def pilot_repo(tmp_path_factory):
    repo = tmp_path_factory.mktemp("pilot") / "unwritten-pilot"
    materialize(repo)
    return repo


@pytest.fixture(params=["local", pytest.param("docker", marks=pytest.mark.docker)])
def runner_name(request):
    if request.param == "docker" and not docker_available():
        pytest.skip("Docker daemon not reachable: the isolated runner was NOT exercised")
    return request.param


@pytest.fixture(scope="session")
def verified(pilot_repo, tmp_path_factory):
    """Verify a shipped package once per runner and reuse the evidence."""
    cache = {}

    def run(package, runner):
        if (package, runner) not in cache:
            out = tmp_path_factory.mktemp("evidence") / f"{package}-{runner}"
            cache[package, runner] = verify_package(
                PACKAGES / package, pilot_repo, make_runner(runner), out, jobs=4)
        return cache[package, runner]

    return run


@pytest.fixture
def variant(tmp_path, pilot_repo):
    """Copy the valid package, let the test damage it, then verify it locally.

    Generated mutants are switched off to keep these runs short; pass
    ``generate=True`` to keep them.
    """
    def build(edit, generate=False, declared=("ignore-min-subtotal",)):
        package = tmp_path / "package"
        shutil.copytree(PACKAGES / VALID, package)
        manifest = json.loads((package / "task.json").read_text())
        manifest["mutation"]["generate"] = generate
        manifest["mutation"]["declared"] = [
            m for m in manifest["mutation"]["declared"] if m["id"] in declared]
        edit(package, manifest)
        (package / "task.json").write_text(json.dumps(manifest))
        return verify_package(package, pilot_repo, make_runner("local"), tmp_path / "evidence")

    return build


def codes(evidence):
    return {reason["code"] for reason in evidence["reasons"]}


def reason(evidence, code):
    return next(r for r in evidence["reasons"] if r["code"] == code)
