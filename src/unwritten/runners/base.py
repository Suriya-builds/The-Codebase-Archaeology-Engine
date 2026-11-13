"""What every runner provides.

A runner executes the suite driver against a prepared workspace and leaves
<suite>.log, <suite>.json and summary.json in the output directory. It raises
HarnessError when the sandbox itself failed; test failures are not errors.
"""
import json
from pathlib import Path

from unwritten.errors import HarnessError

INNER_DIR = Path(__file__).resolve().parent / "_inner"


def write_spec(out_dir, suites, python_path, timeout_seconds):
    spec = {"suites": suites, "python_path": list(python_path), "timeout_seconds": timeout_seconds}
    path = Path(out_dir) / "spec.json"
    path.write_text(json.dumps(spec, indent=1), encoding="utf-8")
    return path


def read_summary(out_dir, output):
    path = Path(out_dir) / "summary.json"
    if not path.is_file():
        raise HarnessError(f"the sandbox produced no summary. Output:\n{output[-2000:]}")
    return json.loads(path.read_text(encoding="utf-8"))
