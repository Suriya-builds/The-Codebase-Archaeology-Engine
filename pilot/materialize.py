#!/usr/bin/env python3
"""Materialise the pilot repository as a git repository with a fixed commit.

The pilot source lives in this repository as plain files. Task packages must
pin an exact starting commit, so this script builds a git repository from
those files with a fixed author, committer, date and message. The same source
tree therefore always yields the same commit hash.

    python pilot/materialize.py <destination-directory>
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent / "unwritten-pilot"
IDENTITY = {
    "GIT_AUTHOR_NAME": "UNWRITTEN Pilot",
    "GIT_AUTHOR_EMAIL": "pilot@unwritten.invalid",
    "GIT_AUTHOR_DATE": "2026-11-13T00:00:00+00:00",
    "GIT_COMMITTER_NAME": "UNWRITTEN Pilot",
    "GIT_COMMITTER_EMAIL": "pilot@unwritten.invalid",
    "GIT_COMMITTER_DATE": "2026-11-13T00:00:00+00:00",
}
IGNORED = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc")


def _git(dest, *args):
    env = dict(os.environ, **IDENTITY)
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    result = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-c", "core.filemode=false",
         "-c", "commit.gpgsign=false", *args],
        cwd=dest, env=env, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def materialize(dest):
    """Create the pilot git repository at ``dest`` and return its commit hash."""
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"destination is not empty: {dest}")
    shutil.copytree(SOURCE, dest, ignore=IGNORED, dirs_exist_ok=True)
    for path in dest.rglob("*"):
        if path.is_file():
            path.chmod(0o644)
    _git(dest, "init", "--quiet", "--initial-branch=main")
    _git(dest, "add", "--all")
    _git(dest, "commit", "--quiet", "--message", "unwritten-pilot: initial version")
    return _git(dest, "rev-parse", "HEAD")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(materialize(sys.argv[1]))
