"""World 3 mutants.

Two sources:

* declared mutants: patches shipped with the package, each a diff on top of
  the reference implementation, describing a realistic wrong implementation;
* generated mutants: produced here, deterministically, by small operators
  applied only to lines the reference patch added. A package author cannot
  cherry-pick these.

Operators edit the original source text at the exact token, so every mutant is
a one-token diff that a reviewer can read.
"""
import ast
import bisect
import difflib
import re
from dataclasses import dataclass
from pathlib import Path

from unwritten.patches import apply_patch, is_under, parse_patch

_COMPARE = {
    ast.Lt: (rb"<", b"<=", "CMP_BOUNDARY"), ast.LtE: (rb"<=", b"<", "CMP_BOUNDARY"),
    ast.Gt: (rb">", b">=", "CMP_BOUNDARY"), ast.GtE: (rb">=", b">", "CMP_BOUNDARY"),
    ast.Eq: (rb"==", b"!=", "CMP_NEGATE"), ast.NotEq: (rb"!=", b"==", "CMP_NEGATE"),
    ast.In: (rb"in", b"not in", "CMP_NEGATE"), ast.NotIn: (rb"not\s+in", b"in", "CMP_NEGATE"),
    ast.Is: (rb"is", b"is not", "CMP_NEGATE"), ast.IsNot: (rb"is\s+not", b"is", "CMP_NEGATE"),
}
_BOOL = {ast.And: (rb"and", b"or"), ast.Or: (rb"or", b"and")}


@dataclass
class Mutant:
    id: str
    origin: str          # declared | generated
    operator: str
    description: str
    file: str | None = None
    line: int | None = None
    patch: Path | None = None        # declared: diff on top of the reference
    new_source: bytes | None = None  # generated: full replacement of ``file``
    diff: str = ""

    def apply(self, workspace):
        if self.origin == "declared":
            apply_patch(workspace, self.patch)
        else:
            (Path(workspace) / self.file).write_bytes(self.new_source)


class _Source:
    def __init__(self, data):
        self.data = data
        self.starts = [0]
        for line in data.splitlines(keepends=True):
            self.starts.append(self.starts[-1] + len(line))

    def offset(self, lineno, col):
        return self.starts[lineno - 1] + col

    def position(self, offset):
        lineno = max(bisect.bisect_right(self.starts, offset), 1)
        return lineno, offset - self.starts[lineno - 1]

    def start(self, node):
        return self.offset(node.lineno, node.col_offset)

    def end(self, node):
        return self.offset(node.end_lineno, node.end_col_offset)

    def text(self, node):
        return self.data[self.start(node):self.end(node)].decode("utf-8", "replace")


def _between(source, left, right, token):
    """Locate an operator token in the gap between two operands."""
    begin, end = source.end(left), source.start(right)
    match = re.fullmatch(rb"([\s)]*)(" + token + rb")([\s(]*)", source.data[begin:end])
    if match is None:  # comments, line continuations: leave it alone
        return None
    return begin + match.start(2), begin + match.end(2)


def _edits(source, tree):
    """Yield (start, end, replacement, operator, description) for every candidate."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            for index, op in enumerate(node.ops):
                if type(op) not in _COMPARE:
                    continue
                token, replacement, operator = _COMPARE[type(op)]
                span = _between(source, operands[index], operands[index + 1], token)
                if span:
                    old = source.data[span[0]:span[1]].decode()
                    yield (*span, replacement, operator,
                           f"`{old}` becomes `{replacement.decode()}` in `{source.text(node)}`")
        elif isinstance(node, ast.BoolOp):
            token, replacement = _BOOL[type(node.op)]
            for left, right in zip(node.values, node.values[1:]):
                span = _between(source, left, right, token)
                if span:
                    yield (*span, replacement, "BOOL_SWAP",
                           f"`{token.decode()}` becomes `{replacement.decode()}` in `{source.text(node)}`")
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            begin = source.start(node)
            match = re.match(rb"not\s*", source.data[begin:source.start(node.operand)])
            if match:
                yield (begin, begin + match.end(), b"", "NOT_REMOVE",
                       f"`not` removed from `{source.text(node)}`")
        elif isinstance(node, (ast.If, ast.While)) and not isinstance(node.test, ast.Constant):
            keyword = "if" if isinstance(node, ast.If) else "while"
            for value in (b"True", b"False"):
                yield (source.start(node.test), source.end(node.test), value,
                       f"COND_FORCE_{value.decode().upper()}",
                       f"`{keyword} {source.text(node.test)}` becomes `{keyword} {value.decode()}`")


def mutate_source(data, path, lines):
    """Return generated mutants for one file, restricted to the given line numbers."""
    try:
        tree = ast.parse(data)
    except SyntaxError:
        return []
    source = _Source(data)
    baseline = ast.dump(tree)
    mutants, seen = [], set()
    for begin, end, replacement, operator, description in sorted(
            _edits(source, tree), key=lambda e: (e[0], e[3])):
        lineno, col = source.position(begin)
        if lineno not in lines:
            continue
        mutated = data[:begin] + replacement + data[end:]
        try:
            if ast.dump(ast.parse(mutated)) == baseline:
                continue
        except SyntaxError:
            continue
        mutant_id = f"generated:{operator}:{path}:{lineno}:{col}"
        if mutant_id in seen:
            continue
        seen.add(mutant_id)
        diff = "".join(difflib.unified_diff(
            data.decode("utf-8", "replace").splitlines(keepends=True),
            mutated.decode("utf-8", "replace").splitlines(keepends=True),
            f"reference/{path}", f"mutant/{path}", n=2))
        mutants.append(Mutant(mutant_id, "generated", operator, description, path, lineno,
                              new_source=mutated, diff=diff))
    return mutants


def generate_mutants(reference_workspace, reference_patch_text, excluded_dirs):
    """Mutate the Python lines the reference patch added, outside test directories."""
    mutants = []
    for file_patch in sorted(parse_patch(reference_patch_text), key=lambda f: f.path or ""):
        path = file_patch.new_path
        if not path or not path.endswith(".py") or is_under(path, excluded_dirs):
            continue
        target = Path(reference_workspace) / path
        if target.is_file():
            mutants.extend(mutate_source(target.read_bytes(), path, file_patch.added_lines))
    return mutants


def declared_mutants(package):
    return [
        Mutant(d.id, "declared", "DECLARED", d.description, patch=d.patch,
               diff=d.patch.read_text(encoding="utf-8"))
        for d in package.declared_mutants
    ]
