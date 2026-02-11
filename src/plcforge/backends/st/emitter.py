"""Structured Text backend.

Writing ST back out serves two purposes.  It closes the round trip - Python or
PLCopen XML in, ST out, ready to paste into the engineering tool - and it gives
the graphical frontends a textual rendering of a ladder rung, which is far
easier to review than a diff of XML.

The output is normalised: uppercase keywords, four space indentation, one
declaration per line.  That makes generated files diffable across runs.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...diagnostics import DiagnosticBag
from ...ir.nodes import (
    Aggregate,
    Assign,
    Binary,
    Call,
    Case,
    CaseBranch,
    Continue,
    EnumType,
    Exit,
    Expr,
    For,
    If,
    Index,
    Invoke,
    Literal,
    Member,
    Pou,
    PouKind,
    Project,
    Repeat,
    Return,
    Stmt,
    StructType,
    Unary,
    VarBlock,
    VarDecl,
    VarRef,
    While,
)
from ...ir.types import IecType, TypeKind, is_time
from ...runtime.types import format_time

__all__ = ["StEmitter", "StOptions", "emit_pou", "emit_project"]

_INDENT = "    "

#: IEC operator precedence, lowest binding first.  Used to decide brackets.
_PRECEDENCE = {
    "OR": 1,
    "XOR": 2,
    "AND": 3,
    "=": 4,
    "<>": 4,
    "<": 5,
    ">": 5,
    "<=": 5,
    ">=": 5,
    "+": 6,
    "-": 6,
    "*": 7,
    "/": 7,
    "MOD": 7,
    "**": 8,
    "unary": 9,
    "atom": 10,
}


@dataclass
class StOptions:
    """Formatting choices for the Structured Text writer."""

    #: Prefix each POU with a comment naming the source it was translated from.
    header: bool = True
    #: Keep comments captured by the frontend.
    comments: bool = True


class StEmitter:
    """Renders IR back to Structured Text."""

    def __init__(
        self,
        project: Project,
        options: StOptions | None = None,
        diagnostics: DiagnosticBag | None = None,
    ) -> None:
        self.project = project
        self.options = options or StOptions()
        self.diagnostics = diagnostics if diagnostics is not None else DiagnosticBag()

    # ------------------------------------------------------------------ #

    def emit_project(self) -> str:
        parts: list[str] = []
        if self.options.header and self.project.source_files:
            sources = ", ".join(self.project.source_files)
            parts.append(f"(* Translated from {sources} by plcforge. *)")
        for enum in self.project.enums:
            parts.append(self.emit_enum(enum))
        for struct in self.project.structs:
            parts.append(self.emit_struct(struct))
        for block in self.project.globals:
            parts.append(self.emit_var_block(block, ""))
        for pou in self.project.pous:
            parts.append(self.emit_pou(pou))
        return "\n\n".join(part for part in parts if part).rstrip() + "\n"

    def emit_enum(self, enum: EnumType) -> str:
        members = []
        expected = 0
        for name, value in enum.members:
            members.append(name if value == expected else f"{name} := {value}")
            expected = value + 1
        base = f" : {self.type_name(enum.base_type)}" if enum.base_type is not None else ""
        comment = f"(* {enum.comment} *)\n" if enum.comment and self.options.comments else ""
        return f"{comment}TYPE {enum.name} : ({', '.join(members)}){base}; END_TYPE"

    def emit_struct(self, struct: StructType) -> str:
        comment = f"(* {struct.comment} *)\n" if struct.comment and self.options.comments else ""
        lines = [f"{comment}TYPE {struct.name} :", "STRUCT"]
        for member in struct.fields:
            lines.append(_INDENT + self.declaration(member))
        lines.extend(["END_STRUCT", "END_TYPE"])
        return "\n".join(lines)

    def emit_pou(self, pou: Pou) -> str:
        lines: list[str] = []
        if self.options.header and pou.source_language not in ("ST", ""):
            lines.append(f"(* Translated from {pou.source_language} by plcforge. *)")
        if pou.comment and self.options.comments:
            lines.append(f"(* {pou.comment} *)")

        header = f"{pou.kind.value} {pou.name}"
        if pou.kind is PouKind.FUNCTION and pou.return_type is not None:
            header += f" : {self.type_name(pou.return_type)}"
        lines.append(header)

        for block in pou.var_blocks:
            if block.declarations:
                lines.append(self.emit_var_block(block, ""))
        lines.append("")
        body = self.statements(pou.body, _INDENT)
        lines.extend(body or [f"{_INDENT};"])
        lines.append(f"END_{pou.kind.value}")
        return "\n".join(lines)

    def emit_var_block(self, block: VarBlock, indent: str) -> str:
        header = block.kind.value
        if block.constant:
            header += " CONSTANT"
        if block.retain:
            header += " RETAIN"
        lines = [f"{indent}{header}"]
        for declaration in block.declarations:
            lines.append(indent + _INDENT + self.declaration(declaration))
        lines.append(f"{indent}END_VAR")
        return "\n".join(lines)

    def declaration(self, declaration: VarDecl) -> str:
        address = f" AT {declaration.address}" if declaration.address else ""
        initial = f" := {self.expression(declaration.initial)}" if declaration.initial is not None else ""
        comment = (
            f"  (* {declaration.comment} *)"
            if declaration.comment and self.options.comments
            else ""
        )
        return f"{declaration.name}{address} : {self.type_name(declaration.type)}{initial};{comment}"

    def type_name(self, declared: IecType | None) -> str:
        if declared is None:
            return "BOOL"
        if declared.kind is TypeKind.ARRAY and declared.element is not None:
            dimensions = ", ".join(f"{low}..{high}" for low, high in declared.dimensions)
            return f"ARRAY [{dimensions}] OF {self.type_name(declared.element)}"
        if declared.kind is TypeKind.STRING and declared.length is not None:
            return f"{declared.name}[{declared.length}]"
        return declared.name

    # ------------------------------------------------------------------ #
    # Statements
    # ------------------------------------------------------------------ #

    def statements(self, body: list[Stmt], indent: str) -> list[str]:
        lines: list[str] = []
        for statement in body:
            lines.extend(self.statement(statement, indent))
        return lines

    def _block(self, body: list[Stmt], indent: str) -> list[str]:
        return self.statements(body, indent) or [f"{indent};"]

    def statement(self, statement: Stmt, indent: str) -> list[str]:
        lines: list[str] = []
        if statement.comment and self.options.comments:
            lines.append(f"{indent}(* {statement.comment} *)")

        if isinstance(statement, Assign):
            lines.append(f"{indent}{self.expression(statement.target)} := {self.expression(statement.value)};")
        elif isinstance(statement, Invoke):
            lines.append(f"{indent}{self.expression(statement.call)};")
        elif isinstance(statement, If):
            for position, branch in enumerate(statement.branches):
                keyword = "IF" if position == 0 else "ELSIF"
                lines.append(f"{indent}{keyword} {self.expression(branch.condition)} THEN")
                lines.extend(self._block(branch.body, indent + _INDENT))
            if statement.orelse:
                lines.append(f"{indent}ELSE")
                lines.extend(self._block(statement.orelse, indent + _INDENT))
            lines.append(f"{indent}END_IF;")
        elif isinstance(statement, Case):
            lines.append(f"{indent}CASE {self.expression(statement.selector)} OF")
            for case_branch in statement.branches:
                lines.append(f"{indent}{_INDENT}{self._labels(case_branch)}:")
                lines.extend(self._block(case_branch.body, indent + _INDENT * 2))
            if statement.orelse:
                lines.append(f"{indent}ELSE")
                lines.extend(self._block(statement.orelse, indent + _INDENT))
            lines.append(f"{indent}END_CASE;")
        elif isinstance(statement, For):
            step = f" BY {self.expression(statement.step)}" if statement.step is not None else ""
            lines.append(
                f"{indent}FOR {self.expression(statement.variable)} := "
                f"{self.expression(statement.start)} TO {self.expression(statement.stop)}{step} DO"
            )
            lines.extend(self._block(statement.body, indent + _INDENT))
            lines.append(f"{indent}END_FOR;")
        elif isinstance(statement, While):
            lines.append(f"{indent}WHILE {self.expression(statement.condition)} DO")
            lines.extend(self._block(statement.body, indent + _INDENT))
            lines.append(f"{indent}END_WHILE;")
        elif isinstance(statement, Repeat):
            lines.append(f"{indent}REPEAT")
            lines.extend(self._block(statement.body, indent + _INDENT))
            lines.append(f"{indent}UNTIL {self.expression(statement.condition)}")
            lines.append(f"{indent}END_REPEAT;")
        elif isinstance(statement, Exit):
            lines.append(f"{indent}EXIT;")
        elif isinstance(statement, Continue):
            lines.append(f"{indent}CONTINUE;")
        elif isinstance(statement, Return):
            lines.append(f"{indent}RETURN;")
        else:  # pragma: no cover - all statement types are covered above
            self.diagnostics.warning(
                f"unsupported statement {type(statement).__name__}",
                span=statement.span,
                code="PF401",
            )
        return lines

    def _labels(self, branch: CaseBranch) -> str:
        parts = []
        for label in branch.labels:
            low = self.expression(label.low)
            parts.append(f"{low}..{self.expression(label.high)}" if label.high is not None else low)
        return ", ".join(parts)

    # ------------------------------------------------------------------ #
    # Expressions
    # ------------------------------------------------------------------ #

    def expression(self, expr: Expr | None) -> str:
        if expr is None:
            return ""
        text, _ = self._expression(expr)
        return text

    def _wrap(self, rendered: tuple[str, int], minimum: int) -> str:
        text, precedence = rendered
        return f"({text})" if precedence < minimum else text

    def _expression(self, expr: Expr) -> tuple[str, int]:
        if isinstance(expr, Literal):
            return self._literal(expr), _PRECEDENCE["atom"]
        if isinstance(expr, VarRef):
            return expr.name, _PRECEDENCE["atom"]
        if isinstance(expr, Member):
            return f"{self.expression(expr.base)}.{expr.field_name}", _PRECEDENCE["atom"]
        if isinstance(expr, Index):
            indices = ", ".join(self.expression(item) for item in expr.indices)
            return f"{self.expression(expr.base)}[{indices}]", _PRECEDENCE["atom"]
        if isinstance(expr, Unary):
            operand = self._wrap(self._expression(expr.operand), _PRECEDENCE["unary"])
            return (f"NOT {operand}" if expr.op == "NOT" else f"-{operand}"), _PRECEDENCE["unary"]
        if isinstance(expr, Binary):
            precedence = _PRECEDENCE.get(expr.op, _PRECEDENCE["+"])
            left = self._wrap(self._expression(expr.left), precedence)
            right = self._wrap(self._expression(expr.right), precedence + 1)
            return f"{left} {expr.op} {right}", precedence
        if isinstance(expr, Call):
            return self._call(expr), _PRECEDENCE["atom"]
        if isinstance(expr, Aggregate):
            return self._aggregate(expr), _PRECEDENCE["atom"]
        self.diagnostics.warning(
            f"unsupported expression {type(expr).__name__}",
            span=expr.span,
            code="PF402",
        )
        return "0", _PRECEDENCE["atom"]

    def _call(self, call: Call) -> str:
        parts = []
        for argument in call.args:
            value = self.expression(argument.value)
            if argument.name is None:
                parts.append(value)
            else:
                parts.append(f"{argument.name} {'=>' if argument.is_output else ':='} {value}")
        return f"{self.expression(call.callee)}({', '.join(parts)})"

    def _aggregate(self, expr: Aggregate) -> str:
        if expr.is_array:
            return "[" + ", ".join(self.expression(item.value) for item in expr.elements) + "]"
        pairs = ", ".join(f"{item.name} := {self.expression(item.value)}" for item in expr.elements)
        return f"({pairs})"

    def _literal(self, literal: Literal) -> str:
        if literal.text:
            return literal.text
        value = literal.value
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if is_time(literal.type) and isinstance(value, (int, float)):
            return format_time(float(value))
        if isinstance(value, str):
            return "'" + value.replace("$", "$$").replace("'", "$'") + "'"
        return repr(value)


def emit_project(
    project: Project,
    options: StOptions | None = None,
    diagnostics: DiagnosticBag | None = None,
) -> str:
    return StEmitter(project, options, diagnostics).emit_project()


def emit_pou(
    pou: Pou,
    options: StOptions | None = None,
    diagnostics: DiagnosticBag | None = None,
) -> str:
    return StEmitter(Project(pous=[pou]), options, diagnostics).emit_pou(pou)
