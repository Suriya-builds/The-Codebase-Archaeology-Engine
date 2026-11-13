"""Runs suites as a plain subprocess on the host.

No isolation at all. Only for repositories you wrote or trust; it must be
selected explicitly and the evidence records that it was used.
"""
import subprocess
import sys

from unwritten.errors import HarnessError
from unwritten.runners.base import INNER_DIR, read_summary, write_spec


class LocalRunner:
    name = "local"

    def prepare(self, package):
        probe = subprocess.run([sys.executable, "-m", "pytest", "--version"],
                               capture_output=True, text=True)
        if probe.returncode != 0:
            raise HarnessError(f"the local runner needs pytest installed for {sys.executable}")
        return {"runner": "local", "isolation": "none", "interpreter": sys.executable}

    def run(self, workspace, suites, package, out_dir):
        spec = write_spec(out_dir, suites, package.python_path, package.timeout_seconds)
        budget = package.timeout_seconds * len(suites) + 30
        try:
            result = subprocess.run(
                [sys.executable, str(INNER_DIR / "run_suites.py"), str(spec), str(out_dir)],
                cwd=workspace, capture_output=True, text=True, timeout=budget,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            raise HarnessError(f"suite driver exceeded {budget}s") from exc
        return read_summary(out_dir, result.stdout + result.stderr)
