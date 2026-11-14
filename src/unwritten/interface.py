"""Holding a task to the interface its statement declares.

A solver sees only the statement. So the only names that may be missing at the
base commit, and the only new names a hidden test may touch, are the ones the
statement declares: module, public name, and for a function its signature.
"""
from pathlib import Path


def repo_related(missing, top_levels):
    """Does a missing name belong to the repository (as opposed to a third-party module)?"""
    if missing["kind"] == "import":
        return (missing.get("module") or "").split(".")[0] in top_levels
    if missing["kind"] == "attribute":
        return (missing.get("owner") or "").split(".")[0] in top_levels
    return True  # an unexpected argument: the test called something of the repository


def match_declared(missing, package, adapter):
    """Return the declared interface entry that explains a missing name, or None."""
    for entry in package.public_interface:
        module, name = entry["module"], entry["name"]
        if missing["kind"] == "import":
            if missing.get("module") == module and missing.get("name") is None:
                return entry                       # the module itself does not exist yet
            if (missing.get("module") == module and name
                    and missing.get("name") == name.split(".")[0]):
                return entry                       # from <module> import <name>
        elif missing["kind"] == "attribute" and name:
            if f"{missing.get('owner')}.{missing['name']}" == f"{module}.{name}":
                return entry
        elif missing["kind"] == "argument" and name and entry["signature"]:
            called = missing.get("callable") or ""
            if name == called or name.endswith("." + called) or called.endswith("." + name):
                try:
                    _, parameters = adapter.declared_signature(entry["signature"])
                except ValueError:
                    continue
                if any(parameter == missing["name"] for parameter, _, _ in parameters):
                    return entry
    return None


def describe(missing):
    if missing["kind"] == "import":
        target = missing.get("module") or "?"
        return f"import of {target}.{missing['name']}" if missing.get("name") else f"import of module {target}"
    if missing["kind"] == "attribute":
        return f"attribute {missing.get('owner')}.{missing['name']}"
    return f"argument {missing['name']!r} of {missing.get('callable')}()"


def lookup(adapter, root, python_path, entry):
    """Find a declared entry in a source tree.

    Returns None when absent, otherwise {"kind", "signature"} as the adapter
    reports it (for a module: {"kind": "module", "signature": None}).
    """
    path = adapter.module_file(root, python_path, entry["module"])
    if path is None:
        return None
    if entry["kind"] == "module":
        return {"kind": "module", "signature": None}
    return adapter.member((Path(root) / path).read_bytes(), entry["name"])


def signature_problem(adapter, entry, found):
    """Compare a declared function signature with what the source defines."""
    if entry["kind"] != "function" or not entry["signature"]:
        return None
    try:
        declared_name, declared = adapter.declared_signature(entry["signature"])
    except ValueError as exc:
        return str(exc)
    if declared_name != entry["name"].split(".")[-1]:
        return f"signature names {declared_name!r}, but the entry is {entry['name']!r}"
    if found["kind"] != "function":
        return f"declared as a function, found a {found['kind']}"
    if found["signature"] != declared:
        actual = ", ".join(name + ("=..." if default else "") for name, _, default in found["signature"])
        return f"declared {entry['signature']!r}, the source defines ({actual})"
    return None
