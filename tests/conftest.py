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

    Generated mutants and attached contracts are switched off to keep these
    runs short and focused; pass ``generate=True`` or ``keep_contracts=True``
    to keep them.
    """
    def build(edit, generate=False, declared=("ignore-min-subtotal",), keep_contracts=False):
        package = tmp_path / "package"
        shutil.copytree(PACKAGES / VALID, package)
        if not keep_contracts:
            (package / "contracts.json").unlink()
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


HYPOTHESES = ROOT / "pilot" / "hypotheses.json"
PROFILE = ROOT / "pilot" / "profile.json"


@pytest.fixture(scope="session")
def contract_map(pilot_repo, tmp_path_factory):
    """Extract the pilot's contract map once per (runner, with/without hypotheses)."""
    from unwritten.contracts import extract_contracts
    from unwritten.profile import load_profile

    cache = {}

    def run(runner="local", hypotheses=True, fresh=False):
        key = (runner, hypotheses)
        if fresh or key not in cache:
            out = tmp_path_factory.mktemp("contracts") / "map"
            result = extract_contracts(
                pilot_repo, load_profile(PROFILE), make_runner(runner), out,
                HYPOTHESES if hypotheses else None, jobs=4)
            if fresh:
                return result
            cache[key] = result
        return cache[key]

    return run


REFERENCE_KEYS = ("log", "logs", "diff", "probe_file")


def referenced_files(document):
    """Every file or directory a piece of evidence points to."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in REFERENCE_KEYS and isinstance(value, str):
                    found.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(document)
    return found


def guard_id(contracts, symbol, condition):
    """Find a guard contract by what it says, the way a reader would."""
    (match,) = [c["id"] for c in contracts
                if c["evidence_type"] == "guard_clause" and c["guard"]["symbol"] == symbol
                and condition in c["guard"]["condition"]]
    return match
