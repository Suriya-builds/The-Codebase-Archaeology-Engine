"""What a language adapter provides.

Everything language-specific about *reading* a repository goes through this
interface: which files are source, which symbols and guard clauses they
contain, and how to mutate them. Contract extraction and World 3 use nothing
else, so supporting a language means writing one adapter.

Running tests is the runner's job and is, for now, pytest only.
"""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Symbol:
    qualname: str     # e.g. "Cart.add"
    kind: str         # function | class
    file: str
    line: int
    end_line: int


@dataclass(frozen=True)
class Guard:
    """A condition that, when true, makes the code raise."""
    file: str
    symbol: str       # enclosing symbol, "<module>" at top level
    index: int        # position among the guards of that symbol, from 1
    key: str          # digest of condition and exception: survives reordering
    line: int         # where the condition starts
    col: int
    condition: str
    exception: str
    message: str | None
    category: str     # type_validation | value_validation | error_handling


class LanguageAdapter(Protocol):
    name: str

    def detect(self, root) -> bool: ...
    def source_files(self, root, test_paths) -> list: ...
    def symbols(self, data: bytes, path: str) -> list: ...
    def guards(self, data: bytes, path: str) -> list: ...
    def mutants(self, data: bytes, path: str, lines) -> list: ...
    def guard_mutant_id(self, guard: Guard) -> str: ...
    def probe_filename(self, slug: str) -> str: ...
