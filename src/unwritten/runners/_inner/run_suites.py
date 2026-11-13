"""Suite driver. Runs inside the sandbox with the workspace as working directory.

    python run_suites.py <spec.json> <output-directory>

Each suite runs in its own pytest process so that a broken suite cannot hide
or disturb another. Writes <suite>.log, <suite>.json and summary.json.
"""
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path


def main():
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = Path(sys.argv[2])
    inner = Path(__file__).resolve().parent
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    search = [str(inner)] + [str(Path(p).resolve()) for p in spec["python_path"]]
    env["PYTHONPATH"] = os.pathsep.join(search)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONHASHSEED"] = "0"
    env["UNWRITTEN_TEST_ROOTS"] = os.pathsep.join(
        path for suite in spec["suites"] for path in suite["paths"])

    summary = {"python_version": platform.python_version(), "suites": {}}
    for suite in spec["suites"]:
        name = suite["name"]
        report = out / f"{name}.json"
        env["UNWRITTEN_REPORT"] = str(report)
        command = [
            sys.executable, "-m", "pytest", "-p", "unwritten_probe", "-p", "no:cacheprovider",
            "--rootdir", ".", "-q", "--continue-on-collection-errors", *suite["paths"],
        ]
        started = time.monotonic()
        timed_out, exit_code = False, None
        with open(out / f"{name}.log", "w", encoding="utf-8") as log:
            log.write("$ " + " ".join(command[2:]) + "\n")
            log.flush()
            try:
                exit_code = subprocess.run(
                    command, env=env, stdout=log, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, timeout=spec["timeout_seconds"],
                ).returncode
            except subprocess.TimeoutExpired:
                timed_out = True
        summary["suites"][name] = {
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_seconds": round(time.monotonic() - started, 3),
            "report_written": report.is_file(),
        }
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
