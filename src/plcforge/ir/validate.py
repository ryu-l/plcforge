"""Type resolution and semantic checks over the IR.

This pass annotates every expression with ``inferred_type``.  The backends rely
on it for decisions that change program behaviour rather than only looks:

* ``AND`` becomes ``and`` for booleans and ``&`` for bit strings,
* ``/`` becomes truncating integer division for integer operands,
* ``MOD`` follows the sign of the dividend as IEC requires, not Python's rule.

Anything the pass cannot resolve stays ``None``; backends then fall back to the
boolean reading, which is what the overwhelming majority of PLC code means.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..diagnostics import DiagnosticBag
from . import stdlib
from .nodes import (
    Argument,
    Assign,
    Binary,
    Call,
    Case,
    Expr,
    For,
    If,
    Index,
    Invoke,
    Literal,
    Member,
    Pou,
    Project,
    Repeat,
    Return,
    Stmt,
    Unary,
    VarDecl,
    VarKind,
    VarRef,
    While,
)
from .types import (
    BOOL,
    IecType,
    TypeKind,
    common_type,
    is_bool,
    is_integer,
    is_numeric,
    is_real,
)

__all__ = ["Scope", "TypeResolver", "resolve_types"]

_COMPARISONS = {"=", "<>", "<", ">", "<=", ">="}
_LOGICAL = {"AND", "OR", "XOR"}
_ARITHMETIC = {"+", "-", "*", "/", "MOD", "**"}


@dataclass
class Scope:
    """Names visible inside one POU."""

    variables: dict[str, VarDecl] = field(default_factory=dict)
    enum_members: dict[str, IecType] = field(default_factory=dict)

    def lookup(self, name: str) -> VarDecl | None:
        return self.variables.get(name.lower())


class TypeResolver:
    """Annotates a :class:`~plcforge.ir.nodes.Project` in place."""

    def __init__(self, project: Project, diagnostics: DiagnosticBag | None = None) -> None:
        self.project = project
        self.diagnostics = diagnostics if diagnostics is not None else DiagnosticBag()
        self._pou_index = {p.name.lower(): p for p in project.pous}
        self._struct_index = {s.name.lower(): s for s in project.structs}
        self._enum_index = {e.name.lower(): e for e in project.enums}
        self._enum_members: dict[str, IecType] = {}
        for enum in project.enums:
            for member, _ in enum.members:
                self._enum_members[member.lower()] = IecType(enum.name, TypeKind.DERIVED)
        self._globals: dict[str, VarDecl] = {}
        for block in project.globals:
            for decl in block.declarations:
                self._globals[decl.name.lower()] = decl

    # -- entry points ------------------------------------------------------ #

    def run(self) -> DiagnosticBag:
        for pou in self.project.pous:
            self.resolve_pou(pou)
        return self.diagnostics

    def resolve_pou(self, pou: Pou) -> None:
        scope = Scope(dict(self._globals), self._enum_members)
        for decl in pou.declarations():
            key = decl.name.lower()
            if key in scope.variables and scope.variables[key] is not self._globals.get(key):
                self.diagnostics.warning(
                    f"'{decl.name}' is declared more than once in {pou.name}",
                    span=decl.span,
                    code="PF201",
                )
            if key in self._enum_members:
                self.diagnostics.warning(
                    f"'{decl.name}' shadows enumeration member "
                    f"'{self._enum_members[key].name}.{decl.name}'",
                    span=decl.span,
                    code="PF209",
                    hint="IEC identifiers are case insensitive, so the two names collide.",
                )
            scope.variables[key] = decl
            if decl.initial is not None:
                self.expression(decl.initial, scope)
        if pou.return_type is not None:
            scope.variables.setdefault(
                pou.name.lower(),
                VarDecl(name=pou.name, type=pou.return_type),
            )
        self.statements(pou.body, scope, pou)

    # -- statements -------------------------------------------------------- #

    def statements(self, body: list[Stmt], scope: Scope, pou: Pou) -> None:
        for stmt in body:
            self.statement(stmt, scope, pou)

    def statement(self, stmt: Stmt, scope: Scope, pou: Pou) -> None:
        if isinstance(stmt, Assign):
            target = self.expression(stmt.target, scope)
            value = self.expression(stmt.value, scope)
            self._check_assignment(stmt, target, value)
        elif isinstance(stmt, Invoke):
            self.expression(stmt.call, scope)
        elif isinstance(stmt, If):
            for branch in stmt.branches:
                condition = self.expression(branch.condition, scope)
                self._expect_bool(condition, branch.condition, "IF")
                self.statements(branch.body, scope, pou)
            self.statements(stmt.orelse, scope, pou)
        elif isinstance(stmt, Case):
            self.expression(stmt.selector, scope)
            for case_branch in stmt.branches:
                for label in case_branch.labels:
                    self.expression(label.low, scope)
                    if label.high is not None:
                        self.expression(label.high, scope)
                self.statements(case_branch.body, scope, pou)
            self.statements(stmt.orelse, scope, pou)
        elif isinstance(stmt, For):
            self.expression(stmt.variable, scope)
            self.expression(stmt.start, scope)
            self.expression(stmt.stop, scope)
            if stmt.step is not None:
                self.expression(stmt.step, scope)
            self.statements(stmt.body, scope, pou)
        elif isinstance(stmt, While):
            self._expect_bool(self.expression(stmt.condition, scope), stmt.condition, "WHILE")
            self.statements(stmt.body, scope, pou)
        elif isinstance(stmt, Repeat):
            self.statements(stmt.body, scope, pou)
            self._expect_bool(self.expression(stmt.condition, scope), stmt.condition, "UNTIL")
        elif isinstance(stmt, Return) and stmt.value is not None:
            self.expression(stmt.value, scope)

    # -- expressions ------------------------------------------------------- #

    def expression(self, expr: Expr, scope: Scope) -> IecType | None:
        result = self._expression(expr, scope)
        expr.inferred_type = result
        return result

    def _expression(self, expr: Expr, scope: Scope) -> IecType | None:
        if isinstance(expr, Literal):
            return expr.type
        if isinstance(expr, VarRef):
            decl = scope.lookup(expr.name)
            if decl is not None:
                return decl.type
            enum_type = scope.enum_members.get(expr.name.lower())
            if enum_type is not None:
                return enum_type
            if expr.name.upper() in ("TRUE", "FALSE"):
                return BOOL
            return None
        if isinstance(expr, Member):
            return self._member_type(expr, scope)
        if isinstance(expr, Index):
            base = self.expression(expr.base, scope)
            for item in expr.indices:
                self.expression(item, scope)
            if base is not None and base.kind is TypeKind.ARRAY:
                return base.element
            return None
        if isinstance(expr, Unary):
            operand = self.expression(expr.operand, scope)
            return BOOL if expr.op == "NOT" and is_bool(operand) else operand
        if isinstance(expr, Binary):
            return self._binary_type(expr, scope)
        if isinstance(expr, Call):
            return self._call_type(expr, scope)
        return None

    def _member_type(self, expr: Member, scope: Scope) -> IecType | None:
        base = self.expression(expr.base, scope)
        if base is None:
            return None
        if base.kind is TypeKind.INSTANCE:
            signature = stdlib.block_signature(base.name)
            if signature is not None:
                resolved = signature.member_type(expr.field_name)
                if resolved is None:
                    pins = ", ".join(name for name, _ in signature.outputs + signature.inputs)
                    self.diagnostics.warning(
                        f"'{base.name}' has no member '{expr.field_name}'",
                        span=expr.span,
                        code="PF202",
                        hint=f"{base.name} exposes: {pins}",
                    )
                return resolved
            pou = self._pou_index.get(base.name.lower())
            if pou is not None:
                decl = pou.find(expr.field_name)
                if decl is not None:
                    return decl.type
                self.diagnostics.warning(
                    f"'{base.name}' has no member '{expr.field_name}'",
                    span=expr.span,
                    code="PF202",
                )
            return None
        struct = self._struct_index.get(base.name.lower())
        if struct is not None:
            for member in struct.fields:
                if member.name.lower() == expr.field_name.lower():
                    return member.type
            self.diagnostics.warning(
                f"struct '{base.name}' has no field '{expr.field_name}'",
                span=expr.span,
                code="PF203",
            )
        return None

    def _binary_type(self, expr: Binary, scope: Scope) -> IecType | None:
        left = self.expression(expr.left, scope)
        right = self.expression(expr.right, scope)
        if expr.op in _COMPARISONS:
            return BOOL
        if expr.op in _LOGICAL:
            if is_bool(left) or is_bool(right):
                return BOOL
            merged = common_type(left, right)
            return merged if merged is not None else (left or right)
        if expr.op in _ARITHMETIC:
            if expr.op == "**":
                return left if is_real(left) else common_type(left, right) or left
            merged = common_type(left, right)
            mixed = (
                merged is None
                and left is not None
                and right is not None
                and (is_numeric(left) or is_numeric(right))
            )
            if mixed:
                self.diagnostics.warning(
                    f"mixed operand types '{left}' {expr.op} '{right}'",
                    span=expr.span,
                    code="PF204",
                    hint="IEC 61131-3 asks for an explicit conversion here.",
                )
            return merged if merged is not None else (left or right)
        return None

    def _call_type(self, expr: Call, scope: Scope) -> IecType | None:
        name = expr.simple_name
        arg_types: list[IecType | None] = []
        for arg in expr.args:
            arg_types.append(self.expression(arg.value, scope))
        if name is None:
            self.expression(expr.callee, scope)
            return None

        decl = scope.lookup(name)
        if decl is not None and decl.type.kind is TypeKind.INSTANCE:
            # Function block invocation: the statement has no value of its own.
            self._check_block_arguments(expr, decl.type.name)
            return None

        pou = self._pou_index.get(name.lower())
        if pou is not None:
            return pou.return_type

        if stdlib.is_standard_function(name):
            return stdlib.function_return_type(name, arg_types)
        if stdlib.is_standard_block(name):
            return None

        self.diagnostics.warning(
            f"call to unknown routine '{name}'",
            span=expr.span,
            code="PF205",
            hint="Declare it as a FUNCTION/FUNCTION_BLOCK or add it to the standard library table.",
        )
        return None

    def _check_block_arguments(self, call: Call, block_name: str) -> None:
        signature = stdlib.block_signature(block_name)
        known: set[str] | None = None
        if signature is not None:
            known = {n.lower() for n, _ in signature.inputs} | {n.lower() for n, _ in signature.outputs}
        else:
            pou = self._pou_index.get(block_name.lower())
            if pou is not None:
                known = {d.name.lower() for d in pou.declarations(VarKind.INPUT, VarKind.OUTPUT, VarKind.IN_OUT)}
        if known is None:
            return
        for arg in call.args:
            if arg.name and arg.name.lower() not in known:
                self.diagnostics.warning(
                    f"'{block_name}' has no parameter '{arg.name}'",
                    span=call.span,
                    code="PF206",
                )

    # -- checks ------------------------------------------------------------ #

    def _expect_bool(self, actual: IecType | None, expr: Expr, context: str) -> None:
        if actual is not None and not is_bool(actual) and not is_integer(actual):
            self.diagnostics.warning(
                f"{context} condition is '{actual}', expected BOOL",
                span=expr.span,
                code="PF207",
            )

    def _check_assignment(self, stmt: Assign, target: IecType | None, value: IecType | None) -> None:
        if target is None or value is None or target == value:
            return
        if common_type(target, value) is not None:
            return
        if target.kind is TypeKind.DERIVED or value.kind is TypeKind.DERIVED:
            return
        self.diagnostics.warning(
            f"assigning '{value}' to '{target}'",
            span=stmt.span,
            code="PF208",
            hint="Add an explicit conversion such as INT_TO_REAL to keep the intent visible.",
        )


def resolve_types(project: Project, diagnostics: DiagnosticBag | None = None) -> DiagnosticBag:
    """Annotate *project* with inferred types and return the diagnostics."""
    return TypeResolver(project, diagnostics).run()


def _argument_names(args: list[Argument]) -> list[str]:  # pragma: no cover - helper
    return [a.name for a in args if a.name]
