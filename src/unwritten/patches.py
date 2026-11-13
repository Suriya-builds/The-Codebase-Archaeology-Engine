"""Reading and applying unified diffs."""
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_HUNK = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass
class FilePatch:
    old_path: str | None
    new_path: str | None
    added_lines: set = field(default_factory=set)  # line numbers in the patched file

    @property
    def path(self):
        return self.new_path or self.old_path


def _strip(raw):
    raw = raw.split("\t")[0].strip()
    if raw == "/dev/null":
        return None
    return raw[2:] if raw[:2] in ("a/", "b/") else raw


def parse_patch(text):
    """Return one FilePatch per file touched by a unified diff."""
    files, current, new_line, in_hunk = [], None, 0, False
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("--- ") and index + 1 < len(lines) and lines[index + 1].startswith("+++ "):
            current = FilePatch(_strip(line[4:]), _strip(lines[index + 1][4:]))
            files.append(current)
            in_hunk = False
        elif line.startswith("+++ ") and current is not None and not in_hunk:
            continue
        elif line.startswith("@@"):
            match = _HUNK.match(line)
            if match and current is not None:
                new_line, in_hunk = int(match.group(1)), True
        elif line.startswith("diff --git"):
            in_hunk = False
        elif in_hunk and current is not None:
            if line.startswith("+"):
                current.added_lines.add(new_line)
                new_line += 1
            elif line.startswith("-") or line.startswith("\\"):
                pass
            else:
                new_line += 1
    return files


def touched_paths(text):
    paths = set()
    for file_patch in parse_patch(text):
        paths.update(p for p in (file_patch.old_path, file_patch.new_path) if p)
    return sorted(paths)


def is_under(path, directories):
    parts = Path(path).parts
    for directory in directories:
        prefix = Path(directory).parts
        if prefix and parts[:len(prefix)] == prefix:
            return True
    return False


class PatchDoesNotApply(Exception):
    pass


def apply_patch(workspace, patch_file):
    """Apply a unified diff inside ``workspace`` (which need not be a git repository)."""
    workspace = Path(workspace).resolve()
    env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(workspace.parent),
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    result = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", str(Path(patch_file).resolve())],
        cwd=workspace, env=env, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise PatchDoesNotApply(result.stderr.strip() or "git apply failed")
