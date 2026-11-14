"""Python adapter, built on the standard library's ast module."""
import ast
import hashlib
from pathlib import Path

from unwritten.adapters.base import Guard, Symbol
from unwritten.mutation import mutate_source
from unwritten.patches import is_under
from unwritten.workspace import PACKAGE_TESTS_DIR


def _exception_name(node):
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _category(test):
    names = {n.func.id for n in ast.walk(test)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    if "isinstance" in names:
        return "type_validation"
    if any(isinstance(n, ast.Compare) and
           any(isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)) for op in n.ops)
           for n in ast.walk(test)):
        return "value_validation"
    return "error_handling"


class _Visitor(ast.NodeVisitor):
    def __init__(self, data, path):
        self.data, self.path = data, path
        self.stack, self.symbols, self.guards, self.counts = [], [], [], {}

    def _scope(self, node, kind):
        self.stack.append(node.name)
        self.symbols.append(Symbol(".".join(self.stack), kind, self.path, node.lineno, node.end_lineno))
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node):
        self._scope(node, "function")

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self._scope(node, "class")

    def visit_If(self, node):
        raised = next((s for s in node.body if isinstance(s, ast.Raise) and s.exc is not None), None)
        exception = _exception_name(raised.exc) if raised else None
        if exception and not isinstance(node.test, ast.Constant):
            symbol = ".".join(self.stack) or "<module>"
            self.counts[symbol] = self.counts.get(symbol, 0) + 1
            message = None
            if isinstance(raised.exc, ast.Call) and raised.exc.args:
                first = raised.exc.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    message = first.value
            condition = ast.get_source_segment(self.data.decode("utf-8", "replace"), node.test) or ""
            # The key depends on what the guard says, not where it sits, so adding or
            # moving another guard does not rename this one.
            key = hashlib.sha256(
                f"{' '.join(condition.split())}\0{exception}".encode()).hexdigest()[:8]
            repeat = sum(g.symbol == symbol and g.key.split("-")[0] == key for g in self.guards)
            if repeat:
                key += f"-{repeat + 1}"
            self.guards.append(Guard(
                self.path, symbol, self.counts[symbol], key, node.test.lineno,
                node.test.col_offset, condition, exception, message, _category(node.test)))
        self.generic_visit(node)


class PythonAdapter:
    name = "python"

    def detect(self, root):
        return any(self.source_files(root, ()))

    def source_files(self, root, test_paths):
        root = Path(root)
        files = []
        for path in sorted(root.rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            parts = Path(relative).parts
            if any(part.startswith(".") for part in parts) or parts[0] == PACKAGE_TESTS_DIR:
                continue
            if is_under(relative, test_paths) or path.name in ("setup.py", "conftest.py"):
                continue
            files.append(relative)
        return files

    def _visit(self, data, path):
        visitor = _Visitor(data, path)
        try:
            visitor.visit(ast.parse(data))
        except SyntaxError:
            pass
        return visitor

    def symbols(self, data, path):
        return self._visit(data, path).symbols

    def guards(self, data, path):
        return self._visit(data, path).guards

    def mutants(self, data, path, lines):
        return mutate_source(data, path, lines)

    def guard_mutant_id(self, guard):
        return f"generated:COND_FORCE_FALSE:{guard.file}:{guard.line}:{guard.col}"

    def probe_filename(self, slug):
        return f"test_{slug}.py"

    # ---- public surface: used to hold a task's tests to its declared interface

    def top_level_packages(self, root, python_path):
        """Importable top-level names: packages and modules under the python path."""
        names = set()
        for base in python_path:
            directory = Path(root) / base
            if not directory.is_dir():
                continue
            for entry in directory.iterdir():
                if entry.is_dir() and (entry / "__init__.py").is_file():
                    names.add(entry.name)
                elif entry.suffix == ".py":
                    names.add(entry.stem)
        return names

    def module_file(self, root, python_path, module):
        """Workspace-relative path of a module, or None when it does not exist."""
        parts = module.split(".")
        for base in python_path:
            for candidate in (Path(base, *parts).with_suffix(".py"), Path(base, *parts, "__init__.py")):
                if (Path(root) / candidate).is_file():
                    return candidate.as_posix()
        return None

    def exported_names(self, data):
        """Names bound at the top level of a module, including re-exports."""
        names = set()
        try:
            tree = ast.parse(data)
        except SyntaxError:
            return names
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names.update(t.id for t in targets if isinstance(t, ast.Name))
        return names

    def member(self, data, dotted):
        """Describe ``Class.method``-style member of a module, or return None.

        Result: {"kind": function | class | attribute, "signature": canonical or None}.
        """
        try:
            scope = ast.parse(data).body
        except SyntaxError:
            return None
        parts = dotted.split(".")
        for depth, part in enumerate(parts):
            last = depth == len(parts) - 1
            found = None
            for node in scope:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == part:
                    found = node
                    break
            if found is None:
                return {"kind": "attribute", "signature": None} if last and self._binds(scope, part) else None
            if last:
                if isinstance(found, ast.ClassDef):
                    return {"kind": "class", "signature": None}
                return {"kind": "function", "signature": _canonical(found.args)}
            if not isinstance(found, ast.ClassDef):
                return None
            scope = found.body
        return None

    @staticmethod
    def _binds(scope, name):
        """Is ``name`` assigned in this scope, or as ``self.name`` in one of its methods?"""
        for node in scope:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(t, ast.Name) and t.id == name for t in targets):
                    return True
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                if any((alias.asname or alias.name).split(".")[0] == name for alias in node.names):
                    return True
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for inner in ast.walk(node):
                    if (isinstance(inner, ast.Attribute) and isinstance(inner.ctx, ast.Store)
                            and inner.attr == name and isinstance(inner.value, ast.Name)
                            and inner.value.id == "self"):
                        return True
        return False

    def declared_signature(self, text):
        """Parse a declared signature such as ``name(self, a, b=None)``.

        Returns (name, canonical parameters); raises ValueError when it is not one.
        """
        try:
            (node,) = ast.parse(f"def {text.strip()}: pass").body
        except (SyntaxError, ValueError) as exc:
            raise ValueError(f"not a signature: {text!r}") from exc
        return node.name, _canonical(node.args)

    def test_functions(self, data):
        """Test ids defined in a test module: ``test_x`` and ``TestClass::test_y``."""
        found = []
        try:
            tree = ast.parse(data)
        except SyntaxError:
            return found
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                found.append(node.name)
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                found += [f"{node.name}::{inner.name}" for inner in node.body
                          if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef))
                          and inner.name.startswith("test")]
        return found

    def imports(self, data):
        """Absolute imports of a module as (module, imported name or None, line)."""
        found = []
        try:
            tree = ast.parse(data)
        except SyntaxError:
            return found
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found += [(alias.name, None, node.lineno) for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found += [(node.module, alias.name, node.lineno) for alias in node.names]
        return found


def _canonical(arguments):
    """Parameters as (name, kind, has default): what callers can rely on."""
    result = []
    positional = arguments.posonlyargs + arguments.args
    first_default = len(positional) - len(arguments.defaults)
    for index, argument in enumerate(positional):
        kind = "positional_only" if index < len(arguments.posonlyargs) else "positional"
        result.append((argument.arg, kind, index >= first_default))
    if arguments.vararg:
        result.append((arguments.vararg.arg, "var_positional", False))
    for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
        result.append((argument.arg, "keyword_only", default is not None))
    if arguments.kwarg:
        result.append((arguments.kwarg.arg, "var_keyword", False))
    return result
