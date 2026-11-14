"""Validated versus released.

    draft             no accepted evidence for the package as it is now
    validated         every automated check passed for exactly this content
    released          validated, and a named person approved the statement and
                      the tests as they are now
    release_invalid   a release record exists, but the content has changed since

The harness can check that a release record matches the content. It cannot
check who the reviewer is: the name is whatever was typed.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from unwritten.errors import HarnessError
from unwritten.package import RELEASE_FILE, load_package, package_digest
from unwritten.verify import EVIDENCE_FILE


def _file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _tree_hash(directory):
    digest = hashlib.sha256()
    for path in sorted(p for p in Path(directory).rglob("*") if p.is_file()):
        relative = path.relative_to(directory)
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        digest.update(relative.as_posix().encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _hashes(root):
    return {"statement_sha256": _file_hash(root / "statement.md"),
            "tests_sha256": _tree_hash(root / "tests"),
            "package_sha256": package_digest(root)}


def _accepted_evidence(root, evidence_dir):
    """Return (evidence, problem). The evidence must be for exactly this content."""
    path = Path(evidence_dir) / EVIDENCE_FILE
    if not path.is_file():
        return None, f"no {EVIDENCE_FILE} in {evidence_dir}"
    evidence = json.loads(path.read_text(encoding="utf-8"))
    if evidence.get("verdict") != "accepted":
        return evidence, f"the evidence verdict is {evidence.get('verdict')!r}, not accepted"
    if evidence["task"]["package_sha256"] != package_digest(root):
        return evidence, "the evidence was produced for different package content"
    return evidence, None


def package_status(package_dir, evidence_dir=None):
    root = load_package(package_dir).root
    record_path = root / RELEASE_FILE
    if record_path.is_file():
        record = json.loads(record_path.read_text(encoding="utf-8"))
        current = _hashes(root)
        changed = [key.replace("_sha256", "") for key, value in current.items()
                   if record.get(key) != value]
        if changed:
            return {"status": "release_invalid", "changed_since_review": changed,
                    "reviewer": record.get("reviewer"), "reviewed_at": record.get("reviewed_at")}
        return {"status": "released", "reviewer": record["reviewer"],
                "reviewed_at": record["reviewed_at"], **current}
    if evidence_dir is not None:
        _, problem = _accepted_evidence(root, evidence_dir)
        if problem is None:
            return {"status": "validated"}
        return {"status": "draft", "detail": problem}
    return {"status": "draft", "detail": "no evidence given"}


def release_package(package_dir, evidence_dir, reviewer, approve, notes=""):
    """Record a human approval. Refuses unless the package is validated right now."""
    root = load_package(package_dir).root
    if not approve:
        raise HarnessError("a release needs explicit approval (--approve): the reviewer states "
                           "that they have read the statement against the tests")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise HarnessError("a release needs the reviewer's identity")
    evidence, problem = _accepted_evidence(root, evidence_dir)
    if problem:
        raise HarnessError(f"cannot release: {problem}")
    record = {
        "status": "released",
        "reviewer": reviewer.strip(),
        "reviewer_note": "identity as stated by the person running the command; not authenticated",
        "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "approved": "the reviewer states that the statement and the tests were read against "
                    "each other and approved",
        **_hashes(root),
        "evidence": {"verdict": evidence["verdict"],
                     "fingerprint": evidence["repeatability"]["fingerprints"][0],
                     "harness_version": evidence["harness"]["version"],
                     "commit": evidence["repository"]["commit"]},
        "notes": notes,
    }
    (root / RELEASE_FILE).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record
