"""One sandbox execution: run suites against a workspace, read back the results."""
from pathlib import Path

from unwritten.classify import load_suite


def run_in_sandbox(runner, settings, workspace, suites, out_root, relative):
    """Run ``suites`` in ``workspace``; logs land in ``out_root/relative``.

    ``settings`` supplies python_path and timeout_seconds (a TaskPackage or a
    RepoProfile). Returns ({suite name: SuiteResult}, driver summary).
    """
    relative = Path(relative)
    out = Path(out_root) / relative
    out.mkdir(parents=True, exist_ok=True)
    summary = runner.run(workspace, suites, settings, out)
    # The driver's input and its bookkeeping are fully reflected in the results
    # returned here; they are not evidence and are not kept.
    for transient in ("spec.json", "summary.json"):
        (out / transient).unlink(missing_ok=True)
    results = {
        suite["name"]: load_suite(suite["name"], out, summary,
                                  (relative / f"{suite['name']}.log").as_posix())
        for suite in suites
    }
    return results, summary
