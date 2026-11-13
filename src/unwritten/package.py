"""Loading and validating a task package (schema 0.1).

Layout, fixed by convention:

    task.json               manifest
    statement.md            requirements shown to a solver
    reference.patch         unified diff against the pinned commit
    tests/feature/          must fail at base, pass with the reference
    tests/regression/       optional; must pass at base and with the reference
    mutants/*.patch         declared mutants, each a diff on top of the reference
    environment/Dockerfile  execution environment (dependencies only, no repo)
    contracts.json          optional; verified contracts this task must preserve
    tests/contracts/        optional; probes of those contracts (see contracts.attach)
"""
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from unwritten.errors import PackageError

SCHEMA_VERSION = "0.1"
CONTRACTS_FILE = "contracts.json"
_SHA = re.compile(r"[0-9a-f]{40}")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class DeclaredMutant:
    id: str
    patch: Path
    description: str


@dataclass(frozen=True)
class Waiver:
    mutant_id: str
    justification: str
    approved_by: str


@dataclass(frozen=True)
class TaskPackage:
    root: Path
    task_id: str
    title: str
    repo_name: str
    commit: str
    dockerfile: Path
    python_path: tuple
    repo_test_paths: tuple
    timeout_seconds: int
    declared_mutants: tuple
    generate_mutants: bool
    waivers: tuple
    provenance: dict = field(default_factory=dict)
    contracts: tuple = ()

    @property
    def contract_tests(self):
        return self.root / "tests" / "contracts"

    @property
    def reference_patch(self):
        return self.root / "reference.patch"

    @property
    def feature_tests(self):
        return self.root / "tests" / "feature"

    @property
    def regression_tests(self):
        return self.root / "tests" / "regression"


def _relative_path(problems, root, value, label, must_exist=True):
    if not isinstance(value, str) or not value:
        problems.append(f"{label} must be a non-empty relative path")
        return None
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        problems.append(f"{label} must stay inside the package: {value!r}")
        return None
    if must_exist and not (root / path).exists():
        problems.append(f"{label} does not exist: {value}")
        return None
    return root / path


def _string_list(problems, value, label):
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        problems.append(f"{label} must be a list of non-empty strings")
        return ()
    for item in value:
        if Path(item).is_absolute() or ".." in Path(item).parts:
            problems.append(f"{label} entries must be relative paths: {item!r}")
    return tuple(value)


def load_package(root):
    """Return a TaskPackage, or raise PackageError listing every problem."""
    root = Path(root).resolve()
    manifest = root / "task.json"
    if not manifest.is_file():
        raise PackageError([f"no task.json in {root}"])
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PackageError([f"task.json is not valid JSON: {exc}"])
    if not isinstance(raw, dict):
        raise PackageError(["task.json must contain a JSON object"])

    problems = []
    if raw.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version must be {SCHEMA_VERSION!r}")
    task_id = raw.get("task_id")
    if not isinstance(task_id, str) or not _ID.fullmatch(task_id):
        problems.append("task_id must match [A-Za-z0-9][A-Za-z0-9._-]*")
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        problems.append("title must be a non-empty string")

    repo = raw.get("repo") if isinstance(raw.get("repo"), dict) else {}
    repo_name = repo.get("name")
    if not isinstance(repo_name, str) or not repo_name:
        problems.append("repo.name must be a non-empty string")
    commit = repo.get("commit")
    if not isinstance(commit, str) or not _SHA.fullmatch(commit):
        problems.append("repo.commit must be a full 40-character lowercase commit hash")

    for required in ("statement.md", "reference.patch"):
        if not (root / required).is_file():
            problems.append(f"missing {required}")
    if not (root / "tests" / "feature").is_dir():
        problems.append("missing tests/feature/")

    execution = raw.get("execution") if isinstance(raw.get("execution"), dict) else {}
    dockerfile = _relative_path(
        problems, root, execution.get("dockerfile", "environment/Dockerfile"), "execution.dockerfile")
    python_path = _string_list(problems, execution.get("python_path", ["."]), "execution.python_path")
    repo_test_paths = _string_list(
        problems, execution.get("repo_test_paths"), "execution.repo_test_paths")
    timeout = execution.get("timeout_seconds", 120)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 3600:
        problems.append("execution.timeout_seconds must be an integer between 1 and 3600")

    mutation = raw.get("mutation") if isinstance(raw.get("mutation"), dict) else {}
    declared, seen = [], set()
    for index, entry in enumerate(mutation.get("declared", [])):
        label = f"mutation.declared[{index}]"
        if not isinstance(entry, dict):
            problems.append(f"{label} must be an object")
            continue
        mutant_id = entry.get("id")
        if not isinstance(mutant_id, str) or not _ID.fullmatch(mutant_id):
            problems.append(f"{label}.id must match [A-Za-z0-9][A-Za-z0-9._-]*")
            continue
        if mutant_id in seen:
            problems.append(f"{label}.id is duplicated: {mutant_id}")
        seen.add(mutant_id)
        description = entry.get("description")
        if not isinstance(description, str) or not description.strip():
            problems.append(f"{label}.description must say what the mutant breaks")
        patch = _relative_path(problems, root, entry.get("patch"), f"{label}.patch")
        if patch is not None:
            declared.append(DeclaredMutant(f"declared:{mutant_id}", patch, description or ""))
    generate = mutation.get("generate", True)
    if not isinstance(generate, bool):
        problems.append("mutation.generate must be true or false")
    waivers = []
    for index, entry in enumerate(mutation.get("waivers", [])):
        label = f"mutation.waivers[{index}]"
        fields = [entry.get(k) if isinstance(entry, dict) else None
                  for k in ("mutant_id", "justification", "approved_by")]
        if not all(isinstance(v, str) and v.strip() for v in fields):
            problems.append(f"{label} needs mutant_id, justification and approved_by")
            continue
        waivers.append(Waiver(*fields))

    provenance = raw.get("provenance", {})
    if not isinstance(provenance, dict):
        problems.append("provenance must be an object")

    contracts = []
    if (root / CONTRACTS_FILE).is_file():
        try:
            entries = json.loads((root / CONTRACTS_FILE).read_text(encoding="utf-8"))["contracts"]
            for entry in entries:
                if not (isinstance(entry["id"], str) and isinstance(entry["description"], str)
                        and entry["probes"]
                        and all(isinstance(p["suite"], str) and isinstance(p["nodeid"], str)
                                for p in entry["probes"])):
                    raise ValueError(entry.get("id"))
                contracts.append(entry)
        except (ValueError, KeyError, TypeError) as exc:
            problems.append(f"{CONTRACTS_FILE} is malformed: every contract needs id, description "
                            f"and at least one probe ({exc!r})")

    if problems:
        raise PackageError(problems)
    return TaskPackage(
        root=root, task_id=task_id, title=title.strip(), repo_name=repo_name, commit=commit,
        dockerfile=dockerfile, python_path=python_path, repo_test_paths=repo_test_paths,
        timeout_seconds=timeout, declared_mutants=tuple(declared), generate_mutants=generate,
        waivers=tuple(waivers), provenance=provenance, contracts=tuple(contracts),
    )


def package_digest(root):
    """SHA-256 over the relative path and content of every file in the package."""
    root = Path(root)
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or ".pytest_cache" in relative.parts:
            continue
        digest.update(relative.as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()
