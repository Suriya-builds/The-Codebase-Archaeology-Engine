"""Loading and validating a task package (schema 0.1 or 0.2).

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

Schema 0.2 adds, in task.json, what makes a task checkable against its own
statement:

    "acceptance_criteria": [{"id": "AC-1", "text": "..."}]
    "test_criteria": {"tests/feature/test_x.py::test_name": ["AC-1"]}
    "public_interface": [{"module": "pkg.mod", "name": "Class.method",
                          "kind": "function", "signature": "method(self, a, b=None)"}]

``public_interface`` lists every public name a solver is expected to add or
change. Only names listed there (and repeated in statement.md) may be missing
at the base commit.

A task that intentionally changes a verified contract declares it in task.json:

    "contract_overrides": [{
        "contract_id": "<id from contracts.json>",
        "reason": "why the existing behaviour has to change",
        "new_behaviour": "what holds instead",
        "replacement_tests": ["tests/feature/test_x.py::test_name"]}]
"""
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from unwritten.errors import PackageError

SCHEMA_VERSION = "0.2"
SUPPORTED_SCHEMAS = ("0.1", "0.2")
RELEASE_FILE = "release.json"
INTERFACE_KINDS = ("function", "class", "attribute", "module")
_DOTTED = re.compile(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*")
_CRITERION = re.compile(r"AC-\d+")
_TEST_ID = re.compile(r"tests/feature/[\w./-]+\.py::[\w:]+")
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
class ContractOverride:
    """A declared, intentional change to a verified contract."""
    contract_id: str
    reason: str
    new_behaviour: str
    replacement_tests: tuple    # feature test ids, e.g. tests/feature/test_x.py::test_name


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
    contract_overrides: tuple = ()
    schema_version: str = "0.1"
    acceptance_criteria: tuple = ()     # ({"id", "text"}, ...)
    test_criteria: dict = field(default_factory=dict)
    public_interface: tuple = ()        # ({"module", "name", "kind", "signature"}, ...)

    @property
    def statement(self):
        return self.root / "statement.md"

    @property
    def checks_statement(self):
        """Schema 0.2 packages are held to their own statement."""
        return self.schema_version != "0.1"

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
    schema_version = raw.get("schema_version")
    if schema_version not in SUPPORTED_SCHEMAS:
        problems.append(f"schema_version must be one of {', '.join(SUPPORTED_SCHEMAS)}")
        schema_version = "0.1"
    criteria, test_criteria, interface = _statement_fields(raw, problems, schema_version)
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

    overrides = []
    for index, entry in enumerate(raw.get("contract_overrides", [])):
        label = f"contract_overrides[{index}]"
        entry = entry if isinstance(entry, dict) else {}
        texts = [entry.get(k) for k in ("contract_id", "reason", "new_behaviour")]
        tests = entry.get("replacement_tests")
        if not all(isinstance(v, str) and v.strip() for v in texts):
            problems.append(f"{label} needs contract_id, reason and new_behaviour")
        elif (not isinstance(tests, list) or not tests
              or not all(isinstance(t, str) and t.startswith("tests/feature/") and "::" in t
                         for t in tests)):
            problems.append(f"{label}.replacement_tests must list feature tests as "
                            "tests/feature/<file>::<test>")
        elif any(o.contract_id == texts[0] for o in overrides):
            problems.append(f"{label} overrides {texts[0]} a second time")
        else:
            overrides.append(ContractOverride(*texts, tuple(tests)))

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
        contract_overrides=tuple(overrides), schema_version=schema_version,
        acceptance_criteria=tuple(criteria), test_criteria=test_criteria,
        public_interface=tuple(interface),
    )


def _statement_fields(raw, problems, schema_version):
    """Parse acceptance criteria, test mapping and declared interface (schema 0.2)."""
    if schema_version == "0.1":
        for key in ("acceptance_criteria", "test_criteria", "public_interface"):
            if key in raw:
                problems.append(f"{key} needs schema_version 0.2")
        return [], {}, []
    criteria, ids = [], set()
    for index, entry in enumerate(raw.get("acceptance_criteria") or []):
        entry = entry if isinstance(entry, dict) else {}
        ident, text = entry.get("id"), entry.get("text")
        if (not isinstance(ident, str) or not _CRITERION.fullmatch(ident) or ident in ids
                or not isinstance(text, str) or not text.strip()):
            problems.append(f"acceptance_criteria[{index}] needs a unique id like AC-1 and a text")
            continue
        ids.add(ident)
        criteria.append({"id": ident, "text": text.strip()})
    if not criteria:
        problems.append("schema 0.2 needs at least one acceptance criterion")

    test_criteria = {}
    mapping = raw.get("test_criteria")
    if not isinstance(mapping, dict) or not mapping:
        problems.append("schema 0.2 needs test_criteria: feature test -> acceptance criteria")
        mapping = {}
    for test, wanted in mapping.items():
        if not _TEST_ID.fullmatch(test):
            problems.append(f"test_criteria key must look like tests/feature/<file>.py::<test>: {test!r}")
        elif (not isinstance(wanted, list) or not wanted
              or not all(isinstance(w, str) and w in ids for w in wanted)):
            problems.append(f"test_criteria[{test!r}] must list known acceptance criteria")
        else:
            test_criteria[test] = list(wanted)

    interface, seen = [], set()
    declared = raw.get("public_interface", [])
    if not isinstance(declared, list):
        problems.append("public_interface must be a list")
        declared = []
    for index, entry in enumerate(declared):
        label = f"public_interface[{index}]"
        entry = entry if isinstance(entry, dict) else {}
        module, name, kind, signature = (entry.get(k) for k in ("module", "name", "kind", "signature"))
        if not isinstance(module, str) or not _DOTTED.fullmatch(module):
            problems.append(f"{label}.module must be a dotted module name")
        elif kind not in INTERFACE_KINDS:
            problems.append(f"{label}.kind must be one of {', '.join(INTERFACE_KINDS)}")
        elif kind != "module" and (not isinstance(name, str) or not _DOTTED.fullmatch(name)):
            problems.append(f"{label}.name must be a (dotted) name inside the module")
        elif any(part.startswith("_") for part in f"{module}.{name or ''}".split(".") if part):
            problems.append(f"{label} declares a private name; only public names can be declared")
        elif kind == "function" and (not isinstance(signature, str) or "(" not in signature):
            problems.append(f"{label}.signature is required for a function, e.g. \"name(a, b=None)\"")
        elif (module, name) in seen:
            problems.append(f"{label} is declared twice")
        else:
            seen.add((module, name))
            interface.append({"module": module, "name": name if kind != "module" else None,
                              "kind": kind,
                              "signature": signature if isinstance(signature, str) else None})
    return criteria, test_criteria, interface


def package_digest(root):
    """SHA-256 over the relative path and content of every file in the package."""
    root = Path(root)
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or ".pytest_cache" in relative.parts:
            continue
        if relative.as_posix() == RELEASE_FILE:
            continue  # the release record refers to this digest and cannot be part of it
        digest.update(relative.as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()
