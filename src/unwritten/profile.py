"""A repository profile: what the harness needs to run a repository's tests.

    {"repo": {"name": "...", "commit": "<40 hex>"},
     "execution": {"dockerfile": "environment/Dockerfile", "python_path": ["."],
                   "repo_test_paths": ["tests"], "timeout_seconds": 120}}

The keys match a task package's task.json, so a package manifest is also a
valid profile.
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path

from unwritten.errors import HarnessError


@dataclass(frozen=True)
class RepoProfile:
    root: Path
    repo_name: str
    commit: str
    dockerfile: Path
    python_path: tuple
    repo_test_paths: tuple
    timeout_seconds: int


def load_profile(path):
    path = Path(path).resolve()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        repo, execution = raw["repo"], raw["execution"]
        profile = RepoProfile(
            root=path.parent, repo_name=str(repo["name"]), commit=str(repo["commit"]),
            dockerfile=path.parent / execution.get("dockerfile", "environment/Dockerfile"),
            python_path=tuple(execution.get("python_path", ["."])),
            repo_test_paths=tuple(execution["repo_test_paths"]),
            timeout_seconds=int(execution.get("timeout_seconds", 120)))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HarnessError(f"cannot read repository profile {path}: {exc!r}") from exc
    if not re.fullmatch(r"[0-9a-f]{40}", profile.commit):
        raise HarnessError("profile repo.commit must be a full 40-character commit hash")
    if not profile.dockerfile.is_file():
        raise HarnessError(f"profile dockerfile not found: {profile.dockerfile}")
    return profile
