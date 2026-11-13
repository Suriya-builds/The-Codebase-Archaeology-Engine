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
