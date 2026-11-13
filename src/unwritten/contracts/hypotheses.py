"""Hypotheses supplied from outside the harness: by a model or by a person.

    {"proposed_by": "llm:<model>" | "human:<name>" | ...,
     "hypotheses": [
        {"id": "negative-price-rejected",
         "description": "A product cannot have a negative price.",
         "category": "value_validation",
         "source": {"file": "shopcart/catalog.py", "symbol": "Product.__post_init__"},
         "confidence": "medium",
         "probe": "<python source of a pytest module, or null>"}]}

A hypothesis is a claim, not a fact. Nothing in this file is trusted: the
probe is executed in the sandbox and only its result changes the status. The
``proposed_by`` label stays on the contract whatever happens to it.
"""
import json
import re
from pathlib import Path

from unwritten.errors import HarnessError

_ID = re.compile(r"[a-z0-9][a-z0-9-]*")


def load_hypotheses(path):
    if path is None:
        return []
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HarnessError(f"cannot read hypotheses file {path}: {exc}") from exc
    default_origin = raw.get("proposed_by") if isinstance(raw, dict) else None
    entries = raw.get("hypotheses") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        raise HarnessError("hypotheses file must contain a 'hypotheses' list")
    hypotheses, seen = [], set()
    for index, entry in enumerate(entries):
        label = f"hypotheses[{index}]"
        if not isinstance(entry, dict):
            raise HarnessError(f"{label} must be an object")
        ident, description = entry.get("id"), entry.get("description")
        origin = entry.get("proposed_by", default_origin)
        if not isinstance(ident, str) or not _ID.fullmatch(ident) or ident in seen:
            raise HarnessError(f"{label}.id must be unique and match [a-z0-9][a-z0-9-]*")
        if not isinstance(description, str) or not description.strip():
            raise HarnessError(f"{label}.description is required")
        if not isinstance(origin, str) or not origin.strip():
            raise HarnessError(f"{label} needs proposed_by: say who or what made this claim")
        probe = entry.get("probe")
        if probe is not None and (not isinstance(probe, str) or not probe.strip()):
            raise HarnessError(f"{label}.probe must be source text or null")
        source = entry.get("source") if isinstance(entry.get("source"), dict) else {}
        seen.add(ident)
        hypotheses.append({
            "id": ident, "description": description.strip(), "proposed_by": origin,
            "category": entry.get("category") or "unspecified",
            "confidence": entry.get("confidence") or "unstated",
            "file": source.get("file"), "symbol": source.get("symbol"), "probe": probe,
        })
    return hypotheses
