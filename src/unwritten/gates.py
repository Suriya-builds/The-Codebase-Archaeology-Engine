"""Static gates for schema 0.2 packages. Nothing is executed here.

A task is checked against its own statement before any test runs:

* the statement names every acceptance criterion and every declared name;
* every criterion has a feature test and every feature test names a criterion;
* feature tests import only names that exist at the base commit or that the
  statement declares, and never private names;
* a declared name is really new: it is absent at the base commit, or present
  with a different signature.
"""
from pathlib import Path

from unwritten.adapters import detect_adapter
from unwritten.interface import lookup, signature_problem


def _squash(text):
    return " ".join(text.split())


class StaticGates:
    name, title = "gates", "Static Gates"
    expectation = ("the statement, the declared interface, the acceptance criteria and the "
                   "feature tests are consistent with each other and with the base commit")

    def run(self, ctx, prior):
        from unwritten.worlds import WorldResult  # avoid a circular import at module load

        world = WorldResult(self.name, self.title, expectation=self.expectation)
        package = ctx.package
        base = ctx.build_source("gates-base")
        adapter = detect_adapter(base)
        statement = _squash(package.statement.read_text(encoding="utf-8"))

        # ---- the statement must carry what the manifest declares
        missing = [c["id"] for c in package.acceptance_criteria if c["id"] not in statement]
        if missing:
            world.reject("GATE_STATEMENT_MISSING_CRITERION",
                         "acceptance criteria in the manifest do not appear in statement.md",
                         missing)
        unstated = []
        for entry in package.public_interface:
            wanted = [entry["module"]]
            wanted.append(_squash(entry["signature"]) if entry["signature"]
                          else (entry["name"] or "").split(".")[-1])
            if not all(text in statement for text in wanted if text):
                unstated.append(_label(entry))
        if unstated:
            world.reject("GATE_STATEMENT_MISSING_INTERFACE",
                         "declared names must be stated in statement.md with their module and "
                         "signature, because a solver sees nothing else", unstated)

        # ---- declared names must be new or changed
        stale = []
        for entry in package.public_interface:
            found = lookup(adapter, base, package.python_path, entry)
            if found is not None and signature_problem(adapter, entry, found) is None:
                stale.append(_label(entry))
        if stale:
            world.reject("GATE_INTERFACE_NOT_NEW",
                         "declared names already exist at the base commit with this signature",
                         stale)

        # ---- traceability, both directions
        defined = []
        files = sorted(p for p in package.feature_tests.rglob("*.py"))
        for path in files:
            relative = path.relative_to(package.root).as_posix()
            defined += [f"{relative}::{test}" for test in adapter.test_functions(path.read_bytes())]
        untraced = sorted(set(defined) - set(package.test_criteria))
        if untraced:
            world.reject("GATE_TEST_WITHOUT_CRITERION",
                         "feature tests that name no acceptance criterion", untraced)
        unknown = sorted(set(package.test_criteria) - set(defined))
        if unknown:
            world.reject("GATE_UNKNOWN_TEST",
                         "test_criteria refers to tests that do not exist", unknown)
        covered = {c for test in defined for c in package.test_criteria.get(test, ())}
        untested = [c["id"] for c in package.acceptance_criteria if c["id"] not in covered]
        if untested:
            world.reject("GATE_CRITERION_WITHOUT_TEST",
                         "acceptance criteria that no feature test checks", untested)

        # ---- coupling: what the tests reach for
        top_levels = adapter.top_level_packages(base, package.python_path)
        declared = {(e["module"], (e["name"] or "").split(".")[0]) for e in package.public_interface}
        declared_modules = {e["module"] for e in package.public_interface if e["kind"] == "module"}
        declared_modules |= {e["module"] for e in package.public_interface
                             if adapter.module_file(base, package.python_path, e["module"]) is None}
        private, undeclared = [], []
        for path in files:
            relative = path.relative_to(package.root).as_posix()
            for module, name, line in adapter.imports(path.read_bytes()):
                if module.split(".")[0] not in top_levels:
                    continue  # standard library or a third-party package
                where = f"{relative}:{line} {module}" + (f".{name}" if name else "")
                parts = module.split(".") + ([name] if name else [])
                if any(p.startswith("_") and not p.startswith("__") for p in parts):
                    private.append(where)
                    continue
                source = adapter.module_file(base, package.python_path, module)
                if source is None:
                    if module not in declared_modules:
                        undeclared.append(where)
                elif name and name != "*":
                    exported = adapter.exported_names((Path(base) / source).read_bytes())
                    is_submodule = adapter.module_file(
                        base, package.python_path, f"{module}.{name}") is not None
                    if name not in exported and not is_submodule and (module, name) not in declared:
                        undeclared.append(where)
        if private:
            world.reject("GATE_TEST_USES_PRIVATE_NAME",
                         "feature tests import private names; a solver is free to implement "
                         "the internals differently", private)
        if undeclared:
            world.reject("GATE_TEST_IMPORTS_UNDECLARED_NAME",
                         "feature tests import names that neither exist at the base commit nor "
                         "are declared in the statement", undeclared)

        world.summary = {
            "acceptance_criteria": len(package.acceptance_criteria),
            "feature_test_functions": len(defined),
            "declared_interface": len(package.public_interface),
        }
        return world


def _label(entry):
    return entry["module"] + (f".{entry['name']}" if entry["name"] else "")
