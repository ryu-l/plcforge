"""The plcforge intermediate representation.

Every frontend (Structured Text, PLCopen XML for Ladder/FBD, Python) parses into
these nodes, and every backend (Python, C++, Structured Text) reads only these
nodes.  Adding a language therefore costs one frontend or one backend, never a
change to the other side.

The IR is intentionally close to Structured Text rather than to a compiler SSA
form: control engineers must be able to read the generated code and recognise
their own program in it.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum

from .types import IecType, TypeKind

__all__ = [
    "Aggregate",
    "Argument",
    "Assign",
    "Binary",
    "Branch",
    "Call",
    "Case",
    "CaseBranch",
    "CaseLabel",
    "Continue",
    "EnumType",
    "Exit",
    "Expr",
    "For",
    "If",
    "Index",
    "Invoke",
    "Literal",
    "Member",
    "Node",
    "Pou",
    "PouKind",
    "Project",
    "Repeat",
    "Return",
    "SourceSpan",
    "Stmt",
    "StructType",
    "Unary",
    "VarBlock",
    "VarDecl",
    "VarKind",
    "VarRef",
    "While",
    "walk_expressions",
    "walk_statements",
]


@dataclass(frozen=True)
class SourceSpan:
    """Where a node came from, used for diagnostics and for round-trip reports."""

    file: str = "<unknown>"
    line: int = 0
    column: int = 0

    def __str__(self) -> str:
        return f"{self.file}:{self.line}:{self.column}"


UNKNOWN_SPAN = SourceSpan()


@dataclass
class Node:
    """Base class carrying source provenance."""

    span: SourceSpan = field(default=UNKNOWN_SPAN, kw_only=True, repr=False)


# --------------------------------------------------------------------------- #
# Expressions
# --------------------------------------------------------------------------- #


@dataclass
class Expr(Node):
    """Base class for expressions.

    ``inferred_type`` is filled in by :mod:`plcforge.ir.validate`.  Backends use
    it to pick between boolean and bitwise operators, and between integer and
    floating point division.
    """

    inferred_type: IecType | None = field(default=None, kw_only=True, repr=False)


@dataclass
class Literal(Expr):
    value: bool | int | float | str
    type: IecType | None = None
    #: Original spelling (``16#FF``, ``T#1s500ms``) kept so the ST backend can
    #: reproduce the author's notation instead of a normalised value.
    text: str | None = None


@dataclass
class VarRef(Expr):
    name: str


@dataclass
class Member(Expr):
    base: Expr
    field_name: str


@dataclass
class Index(Expr):
    base: Expr
    indices: list[Expr]


@dataclass
class Unary(Expr):
    op: str  # 'NOT' | '-' | '+'
    operand: Expr


@dataclass
class Binary(Expr):
    op: str  # OR XOR AND = <> < > <= >= + - * / MOD **
    left: Expr
    right: Expr


@dataclass
class Argument:
    """One argument of a call.

    ``name`` is ``None`` for positional arguments.  ``is_output`` marks the
    ``=>`` form used to read a function block output straight into a variable.
    """

    value: Expr
    name: str | None = None
    is_output: bool = False


@dataclass
class Call(Expr):
    """A function call, a standard function, or a function block invocation."""

    callee: Expr
    args: list[Argument] = field(default_factory=list)

    @property
    def simple_name(self) -> str | None:
        return self.callee.name if isinstance(self.callee, VarRef) else None


@dataclass
class Aggregate(Expr):
    """An array or structure initialiser: ``[1, 2, 3]`` or ``(x := 1, y := 2)``."""

    elements: list[Argument] = field(default_factory=list)
    is_array: bool = True


# --------------------------------------------------------------------------- #
# Statements
# --------------------------------------------------------------------------- #


@dataclass
class Stmt(Node):
    #: Trailing or leading comment attached to the statement, preserved so that
    #: generated code keeps the engineer's own annotations.
    comment: str | None = field(default=None, kw_only=True, repr=False)


@dataclass
class Assign(Stmt):
    target: Expr
    value: Expr


@dataclass
class Invoke(Stmt):
    """A call used as a statement, typically a function block instance call."""

    call: Call


@dataclass
class Branch:
    condition: Expr
    body: list[Stmt]


@dataclass
class If(Stmt):
    branches: list[Branch]
    orelse: list[Stmt] = field(default_factory=list)


@dataclass
class CaseLabel:
    """A single ``CASE`` label: a value, an inclusive range, or an enum name."""

    low: Expr
    high: Expr | None = None


@dataclass
class CaseBranch:
    labels: list[CaseLabel]
    body: list[Stmt]


@dataclass
class Case(Stmt):
    selector: Expr
    branches: list[CaseBranch]
    orelse: list[Stmt] = field(default_factory=list)


@dataclass
class For(Stmt):
    variable: Expr
    start: Expr
    stop: Expr
    step: Expr | None
    body: list[Stmt]


@dataclass
class While(Stmt):
    condition: Expr
    body: list[Stmt]


@dataclass
class Repeat(Stmt):
    body: list[Stmt]
    condition: Expr


@dataclass
class Exit(Stmt):
    pass


@dataclass
class Continue(Stmt):
    pass


@dataclass
class Return(Stmt):
    value: Expr | None = None


# --------------------------------------------------------------------------- #
# Declarations
# --------------------------------------------------------------------------- #


class VarKind(Enum):
    INPUT = "VAR_INPUT"
    OUTPUT = "VAR_OUTPUT"
    IN_OUT = "VAR_IN_OUT"
    LOCAL = "VAR"
    TEMP = "VAR_TEMP"
    STATIC = "VAR_STAT"
    EXTERNAL = "VAR_EXTERNAL"
    GLOBAL = "VAR_GLOBAL"

    @property
    def is_interface(self) -> bool:
        """True for the blocks that form the callable signature of a POU."""
        return self in (VarKind.INPUT, VarKind.OUTPUT, VarKind.IN_OUT)


@dataclass
class VarDecl(Node):
    name: str
    type: IecType
    initial: Expr | None = None
    #: Direct hardware address such as ``%IX0.0`` when the source declared one.
    address: str | None = None
    comment: str | None = None

    @property
    def is_instance(self) -> bool:
        return self.type.kind is TypeKind.INSTANCE


@dataclass
class VarBlock(Node):
    kind: VarKind = VarKind.LOCAL
    declarations: list[VarDecl] = field(default_factory=list)
    constant: bool = False
    retain: bool = False


class PouKind(Enum):
    PROGRAM = "PROGRAM"
    FUNCTION_BLOCK = "FUNCTION_BLOCK"
    FUNCTION = "FUNCTION"


@dataclass
class Pou(Node):
    """A Program Organisation Unit: program, function block or function."""

    name: str
    kind: PouKind = PouKind.FUNCTION_BLOCK
    return_type: IecType | None = None
    var_blocks: list[VarBlock] = field(default_factory=list)
    body: list[Stmt] = field(default_factory=list)
    #: ``ST``, ``LD``, ``FBD``, ``SFC``, ``IL`` or ``PY`` - where the body came
    #: from.  Reported in the generated header so the origin stays traceable.
    source_language: str = "ST"
    comment: str | None = None

    def blocks(self, *kinds: VarKind) -> Iterator[VarBlock]:
        for block in self.var_blocks:
            if not kinds or block.kind in kinds:
                yield block

    def declarations(self, *kinds: VarKind) -> Iterator[VarDecl]:
        for block in self.blocks(*kinds):
            yield from block.declarations

    def find(self, name: str) -> VarDecl | None:
        lowered = name.lower()
        for decl in self.declarations():
            if decl.name.lower() == lowered:
                return decl
        return None

    @property
    def inputs(self) -> list[VarDecl]:
        return list(self.declarations(VarKind.INPUT, VarKind.IN_OUT))

    @property
    def outputs(self) -> list[VarDecl]:
        return list(self.declarations(VarKind.OUTPUT, VarKind.IN_OUT))

    @property
    def instances(self) -> list[VarDecl]:
        return [d for d in self.declarations() if d.is_instance]


@dataclass
class StructType(Node):
    name: str
    fields: list[VarDecl] = field(default_factory=list)
    comment: str | None = None


@dataclass
class EnumType(Node):
    name: str
    members: list[tuple[str, int]] = field(default_factory=list)
    base_type: IecType | None = None
    comment: str | None = None


@dataclass
class Project(Node):
    """A set of POUs and user defined types translated as one unit."""

    name: str = "project"
    pous: list[Pou] = field(default_factory=list)
    structs: list[StructType] = field(default_factory=list)
    enums: list[EnumType] = field(default_factory=list)
    globals: list[VarBlock] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)

    def pou(self, name: str) -> Pou | None:
        lowered = name.lower()
        return next((p for p in self.pous if p.name.lower() == lowered), None)

    def merge(self, other: Project) -> Project:
        self.pous.extend(other.pous)
        self.structs.extend(other.structs)
        self.enums.extend(other.enums)
        self.globals.extend(other.globals)
        self.source_files.extend(other.source_files)
        return self


# --------------------------------------------------------------------------- #
# Traversal helpers
# --------------------------------------------------------------------------- #


def walk_expressions(node: Expr) -> Iterator[Expr]:
    """Yield *node* and every nested expression, parents before children."""
    yield node
    if isinstance(node, Unary):
        yield from walk_expressions(node.operand)
    elif isinstance(node, Binary):
        yield from walk_expressions(node.left)
        yield from walk_expressions(node.right)
    elif isinstance(node, Member):
        yield from walk_expressions(node.base)
    elif isinstance(node, Index):
        yield from walk_expressions(node.base)
        for item in node.indices:
            yield from walk_expressions(item)
    elif isinstance(node, Call):
        yield from walk_expressions(node.callee)
        for arg in node.args:
            yield from walk_expressions(arg.value)
    elif isinstance(node, Aggregate):
        for element in node.elements:
            yield from walk_expressions(element.value)


def walk_statements(body: list[Stmt]) -> Iterator[Stmt]:
    """Yield every statement in *body*, descending into nested blocks."""
    for stmt in body:
        yield stmt
        if isinstance(stmt, (If, Case)):
            for branch in stmt.branches:
                yield from walk_statements(branch.body)
            yield from walk_statements(stmt.orelse)
        elif isinstance(stmt, (For, While, Repeat)):
            yield from walk_statements(stmt.body)
