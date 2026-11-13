"""Building throwaway workspaces from an exact commit."""
import io
import shutil
import subprocess
import tarfile
from pathlib import Path

from unwritten.errors import HarnessError

PACKAGE_TESTS_DIR = "_unwritten"
_IGNORE = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc")


def _git(repo, *args, binary=False):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if result.returncode != 0:
        raise HarnessError(f"git {' '.join(args)} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout if binary else result.stdout.decode().strip()


def resolve_commit(repo, commit):
    """Check the pinned commit exists in ``repo``; return its tree hash."""
    repo = Path(repo)
    if not repo.is_dir():
        raise HarnessError(f"repository not found: {repo}")
    try:
        resolved = _git(repo, "rev-parse", "--verify", f"{commit}^{{commit}}")
    except HarnessError as exc:
        raise HarnessError(f"commit {commit} is not in repository {repo}") from exc
    if resolved != commit:
        raise HarnessError(f"commit {commit} resolved to a different object {resolved}")
    return _git(repo, "rev-parse", f"{commit}^{{tree}}")


def export_commit(repo, commit, dest):
    """Write the exact tree of ``commit`` to ``dest`` (no .git directory)."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    archive = _git(repo, "archive", "--format=tar", commit, binary=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, filter="data")
    if (dest / PACKAGE_TESTS_DIR).exists():
        raise HarnessError(f"repository already contains a {PACKAGE_TESTS_DIR}/ directory")


def install_package_tests(package, workspace):
    """Copy the package's tests into the workspace; return the suites to run."""
    workspace = Path(workspace)
    target = workspace / PACKAGE_TESTS_DIR
    suites = []
    shutil.copytree(package.feature_tests, target / "feature", ignore=_IGNORE)
    suites.append({"name": "feature", "paths": [f"{PACKAGE_TESTS_DIR}/feature"]})
    if package.regression_tests.is_dir():
        shutil.copytree(package.regression_tests, target / "regression", ignore=_IGNORE)
        suites.append({"name": "regression_package", "paths": [f"{PACKAGE_TESTS_DIR}/regression"]})
    suites.append({"name": "regression_repo", "paths": list(package.repo_test_paths)})
    return suites
