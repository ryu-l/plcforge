"""Python backend.

Produces readable Python that a control engineer can follow line by line
alongside the original program.  Three rules drive every decision here:

1. **Behaviour first.**  Where Python and IEC 61131-3 disagree - integer
   division, ``MOD`` with negative operands, inclusive ``FOR`` bounds, array
   index origins - the generated code calls a runtime helper instead of the
   Python operator that looks right but computes something else.
2. **Names survive.**  Variables keep the engineer's spelling; only collisions
   with Python keywords are mangled.
3. **Comments survive.**  Declarations and statements carry their original
   comments into the output.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...diagnostics import DiagnosticBag
from ...ir import stdlib
from ...ir.nodes import (
    Aggregate,
    Argument,
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
    VarDecl,
    VarKind,
    VarRef,
    While,
)
from ...ir.types import (
    IecType,
    TypeKind,
    is_bit_string,
    is_bool,
    is_integer,
    is_real,
    is_time,
)
from ..naming import class_name, python_name

__all__ = ["PythonEmitter", "PythonOptions", "emit_module", "emit_project"]


# Python operator precedence, lowest binding first.
_PREC = {
    "or": 1,
    "and": 2,
    "not": 3,
    "cmp": 4,
    "|": 5,
    "^": 6,
    "&": 7,
    "shift": 8,
    "+": 9,
    "-": 9,
    "*": 10,
    "/": 10,
    "//": 10,
    "%": 10,
    "unary": 11,
    "**": 12,
    "atom": 13,
}

_COMPARISON_OPS = {"=": "==", "<>": "!=", "<": "<", ">": ">", "<=": "<=", ">=": ">="}

_ELEMENTARY_PYTHON = {
    "BOOL": "BOOL",
    "SINT": "SINT",
    "INT": "INT",
    "DINT": "DINT",
    "LINT": "LINT",
    "USINT": "USINT",
    "UINT": "UINT",
    "UDINT": "UDINT",
    "ULINT": "ULINT",
    "BYTE": "BYTE",
    "WORD": "WORD",
    "DWORD": "DWORD",
    "LWORD": "LWORD",
    "REAL": "REAL",
    "LREAL": "LREAL",
    "TIME": "TIME",
    "LTIME": "TIME",
    "STRING": "STRING",
    "WSTRING": "STRING",
    "CHAR": "STRING",
    "WCHAR": "STRING",
}

_INDENT = "    "


def _docstring_safe(text: str) -> str:
    """Make arbitrary text safe to embed in a triple-quoted docstring.

    Source paths and engineer-written comments end up inside docstrings, and
    both can contain a backslash - a Windows path always does.  Left alone,
    ``D:\\plant\\Motor.scl`` becomes an escape sequence and the generated
    module does not parse.
    """
    return text.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')


@dataclass
class PythonOptions:
    """Knobs the CLI exposes for the Python backend."""

    #: Emit ``wrap(value, "INT")`` around assignments to fixed width integers so
    #: that overflow matches the controller instead of growing without bound.
    strict_integers: bool = False
    #: Prefix for the runtime import: ``from {runtime_package} import ...``.
    runtime_package: str = "plcforge.runtime"
    #: Emit type annotations on generated attributes.
    annotations: bool = True
    #: Header line naming the tool and the source files.
    header: bool = True


@dataclass
class _Context:
    """State for one POU being emitted."""

    pou: Pou
    #: ``self.`` for programs and function blocks, empty for functions.
    prefix: str
    locals: dict[str, VarDecl] = field(default_factory=dict)
    used_runtime: set[str] = field(default_factory=set)


class PythonEmitter:
    """Translates a linked and type-resolved project into Python source."""

    def __init__(
        self,
        project: Project,
        options: PythonOptions | None = None,
        diagnostics: DiagnosticBag | None = None,
    ) -> None:
        self.project = project
        self.options = options or PythonOptions()
        self.diagnostics = diagnostics if diagnostics is not None else DiagnosticBag()
        self.structs = {s.name.lower(): s for s in project.structs}
        self.enums = {e.name.lower(): e for e in project.enums}
        self.pous = {p.name.lower(): p for p in project.pous}
        self.enum_members: dict[str, EnumType] = {}
        for enum in project.enums:
            for member, _ in enum.members:
                self.enum_members[member.lower()] = enum
        self.globals: dict[str, VarDecl] = {}
        for block in project.globals:
            for declaration in block.declarations:
                self.globals[declaration.name.lower()] = declaration
        self._runtime_names: set[str] = set()
        self._needs_dataclass = False

    # ------------------------------------------------------------------ #
    # Module assembly
    # ------------------------------------------------------------------ #

    def emit_module(self, pous: list[Pou] | None = None, module_doc: str | None = None) -> str:
        """Render the given POUs (default: all) as one Python module."""
        selected = self.project.pous if pous is None else pous
        self._runtime_names = set()
        self._needs_dataclass = False
        body: list[str] = []

        for enum in self.project.enums:
            body.append(self.emit_enum(enum))
        for struct in self.project.structs:
            body.append(self.emit_struct(struct))
        if self.project.globals:
            body.append(self.emit_globals())
        for pou in selected:
            body.append(self.emit_pou(pou))

        preamble = [self._module_header(selected, module_doc)]
        imports = self._imports()
        if imports:
            preamble.append(imports)
        exported = self._exports(selected)
        if exported:
            preamble.append(exported)
        rendered = "\n\n".join(part.rstrip() for part in preamble if part)
        definitions = "\n\n\n".join(part.rstrip() for part in body if part)
        return (f"{rendered}\n\n\n{definitions}" if definitions else rendered).rstrip() + "\n"

    def _module_header(self, pous: list[Pou], module_doc: str | None) -> str:
        if not self.options.header:
            return '"""' + _docstring_safe(module_doc or self.project.name) + '"""'
        languages = sorted({pou.source_language for pou in pous}) or ["ST"]
        sources = _docstring_safe(", ".join(self.project.source_files) or "unknown source")
        lines = [
            '"""' + (_docstring_safe(module_doc) if module_doc else f"Control logic translated from {sources}."),
            "",
            f"Source language: {', '.join(languages)}",
            "Generated by plcforge - do not edit by hand; edit the PLC source and re-run.",
            '"""',
            "",
            "from __future__ import annotations",
        ]
        return "\n".join(lines)

    def _imports(self) -> str:
        runtime = sorted(self._runtime_names)
        lines: list[str] = []
        if self._needs_dataclass:
            lines.append("from dataclasses import dataclass, field")
        if any(self.project.enums):
            lines.append("from enum import IntEnum")
        if runtime:
            wrapped = ", ".join(runtime)
            if len(wrapped) <= 84:
                lines.append(f"from {self.options.runtime_package} import {wrapped}")
            else:
                joined = ",\n".join(f"{_INDENT}{name}" for name in runtime)
                lines.append(f"from {self.options.runtime_package} import (\n{joined},\n)")
        return "\n".join(lines)

    def _exports(self, pous: list[Pou]) -> str:
        names = [class_name(e.name) for e in self.project.enums]
        names += [class_name(s.name) for s in self.project.structs]
        names += [
            python_name(p.name) if p.kind is PouKind.FUNCTION else class_name(p.name)
            for p in pous
        ]
        if self.project.globals:
            names.append("GLOBALS")
        if not names:
            return ""
        joined = ",\n".join(f'{_INDENT}"{name}"' for name in sorted(set(names)))
        return f"__all__ = [\n{joined},\n]"

    def _need(self, *names: str) -> None:
        self._runtime_names.update(names)

    # ------------------------------------------------------------------ #
    # Declarations
    # ------------------------------------------------------------------ #

    def emit_enum(self, enum: EnumType) -> str:
        lines = [f"class {class_name(enum.name)}(IntEnum):"]
        doc = _docstring_safe(enum.comment) if enum.comment else f"Enumeration {enum.name} from the PLC source."
        lines.append(f'{_INDENT}"""{doc}"""')
        lines.append("")
        for member, value in enum.members:
            lines.append(f"{_INDENT}{python_name(member)} = {value}")
        if not enum.members:
            lines.append(f"{_INDENT}pass")
        return "\n".join(lines)

    def emit_struct(self, struct: StructType) -> str:
        self._need("Struct")
        self._needs_dataclass = True
        lines = ["@dataclass", f"class {class_name(struct.name)}(Struct):"]
        doc = _docstring_safe(struct.comment) if struct.comment else f"Structure {struct.name} from the PLC source."
        lines.append(f'{_INDENT}"""{doc}"""')
        lines.append("")
        lines.append(f'{_INDENT}__iec_name__ = "{struct.name}"')
        for member in struct.fields:
            annotation = self._annotation(member.type)
            default = self._default_expression(member, factory_required=True)
            comment = f"  # {member.comment}" if member.comment else ""
            lines.append(f"{_INDENT}{python_name(member.name)}: {annotation} = {default}{comment}")
        if not struct.fields:
            lines.append(f"{_INDENT}pass")
        return "\n".join(lines)

    def emit_globals(self) -> str:
        self._need("Struct")
        self._needs_dataclass = True
        lines = ["@dataclass", "class _Globals(Struct):"]
        lines.append(f'{_INDENT}"""Variables declared in VAR_GLOBAL blocks."""')
        lines.append("")
        lines.append(f'{_INDENT}__iec_name__ = "VAR_GLOBAL"')
        for block in self.project.globals:
            for declaration in block.declarations:
                annotation = self._annotation(declaration.type)
                default = self._default_expression(declaration, factory_required=True)
                note = " (CONSTANT)" if block.constant else ""
                comment = f"  # {declaration.comment}{note}" if declaration.comment or note else ""
                lines.append(
                    f"{_INDENT}{python_name(declaration.name)}: {annotation} = {default}{comment}"
                )
        lines.append("")
        lines.append("")
        lines.append("#: Single instance shared by every POU, like the controller's global area.")
        lines.append("GLOBALS = _Globals()")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # POUs
    # ------------------------------------------------------------------ #

    def emit_pou(self, pou: Pou) -> str:
        if pou.kind is PouKind.FUNCTION:
            return self._emit_function(pou)
        return self._emit_block(pou)

    def _docstring(self, pou: Pou, indent: str) -> list[str]:
        summary = _docstring_safe(pou.comment) if pou.comment else f"{pou.kind.value} {pou.name}."
        lines = [f'{indent}"""{summary}']
        interface = [
            (block.kind, declaration)
            for block in pou.blocks(VarKind.INPUT, VarKind.IN_OUT, VarKind.OUTPUT)
            for declaration in block.declarations
        ]
        if interface:
            lines.append("")
            for kind, declaration in interface:
                tag = {"VAR_INPUT": "in", "VAR_OUTPUT": "out", "VAR_IN_OUT": "in/out"}[kind.value]
                note = f" - {_docstring_safe(declaration.comment)}" if declaration.comment else ""
                lines.append(f"{indent}{tag:>6}  {declaration.name} : {declaration.type}{note}")
        lines.append("")
        lines.append(f"{indent}Translated from {pou.source_language} source.")
        lines.append(f'{indent}"""')
        return lines

    def _emit_block(self, pou: Pou) -> str:
        base = "Program" if pou.kind is PouKind.PROGRAM else "FunctionBlock"
        self._need(base)
        name = class_name(pou.name)
        lines = [f"class {name}({base}):"]
        lines.extend(self._docstring(pou, _INDENT))
        lines.append("")

        context = _Context(pou=pou, prefix="self.")
        for declaration in pou.declarations():
            context.locals[declaration.name.lower()] = declaration

        lines.extend(self._emit_constructor(pou))
        lines.append("")
        if pou.kind is PouKind.PROGRAM:
            lines.extend(self._emit_scan(pou, context))
        else:
            lines.extend(self._emit_call(pou, context))
        return "\n".join(lines)

    def _emit_constructor(self, pou: Pou) -> list[str]:
        self._need("Clock")
        lines = [
            f"{_INDENT}def __init__(self, clock: Clock | None = None) -> None:",
            f"{_INDENT * 2}super().__init__(clock)",
        ]
        emitted = False
        for block in pou.var_blocks:
            declarations = block.declarations
            if not declarations:
                continue
            label = block.kind.value + (" CONSTANT" if block.constant else "")
            lines.append(f"{_INDENT * 2}# {label}")
            for declaration in declarations:
                annotation = f": {self._annotation(declaration.type)}" if self.options.annotations else ""
                value = self._default_expression(declaration, factory_required=False)
                comment = self._declaration_comment(declaration)
                lines.append(
                    f"{_INDENT * 2}self.{python_name(declaration.name)}{annotation} = {value}{comment}"
                )
                emitted = True
        if not emitted:
            lines.append(f"{_INDENT * 2}# This POU declares no variables.")
        return lines

    def _declaration_comment(self, declaration: VarDecl) -> str:
        notes = []
        if declaration.comment:
            notes.append(declaration.comment)
        if declaration.address:
            notes.append(f"at {declaration.address}")
        return f"  # {'; '.join(notes)}" if notes else ""

    def _emit_scan(self, pou: Pou, context: _Context) -> list[str]:
        lines = [f"{_INDENT}def scan(self) -> None:", f'{_INDENT * 2}"""One scan of {pou.name}."""']
        body = self.emit_statements(pou.body, context, _INDENT * 2)
        lines.extend(body or [f"{_INDENT * 2}pass"])
        return lines

    def _emit_call(self, pou: Pou, context: _Context) -> list[str]:
        name = class_name(pou.name)
        inputs = list(pou.declarations(VarKind.INPUT, VarKind.IN_OUT))
        parameters = [f"{_INDENT * 2}self,"]
        if inputs:
            parameters.append(f"{_INDENT * 2}*,")
        for declaration in inputs:
            annotation = self._annotation(declaration.type)
            parameters.append(f"{_INDENT * 2}{python_name(declaration.name)}: {annotation} | None = None,")

        lines = [f"{_INDENT}def __call__("]
        lines.extend(parameters)
        lines.append(f'{_INDENT}) -> "{name}":')
        lines.append(f'{_INDENT * 2}"""Apply the inputs, run one execution, return the instance."""')
        for declaration in inputs:
            attribute = python_name(declaration.name)
            lines.append(f"{_INDENT * 2}if {attribute} is not None:")
            lines.append(f"{_INDENT * 3}self.{attribute} = {attribute}")
        lines.append(f"{_INDENT * 2}self.body()")
        lines.append(f"{_INDENT * 2}return self")
        lines.append("")
        lines.append(f"{_INDENT}def body(self) -> None:")
        lines.append(f'{_INDENT * 2}"""The block logic, executed once per call."""')
        body = self.emit_statements(pou.body, context, _INDENT * 2)
        lines.extend(body or [f"{_INDENT * 2}pass"])
        return lines

    def _emit_function(self, pou: Pou) -> str:
        name = python_name(pou.name)
        context = _Context(pou=pou, prefix="")
        for declaration in pou.declarations():
            context.locals[declaration.name.lower()] = declaration

        parameters = []
        for declaration in pou.declarations(VarKind.INPUT, VarKind.IN_OUT):
            annotation = self._annotation(declaration.type)
            default = ""
            if declaration.initial is not None:
                default = f" = {self.expression(declaration.initial, context)}"
            parameters.append(f"{python_name(declaration.name)}: {annotation}{default}")
        signature = ", ".join(parameters)
        returns = self._annotation(pou.return_type) if pou.return_type is not None else "None"

        lines = [f"def {name}({signature}) -> {returns}:"]
        lines.extend(self._docstring(pou, _INDENT))

        result = python_name(pou.name)
        locals_emitted = False
        for block in pou.blocks(VarKind.LOCAL, VarKind.TEMP, VarKind.STATIC, VarKind.OUTPUT):
            for declaration in block.declarations:
                value = self._default_expression(declaration, factory_required=False)
                comment = self._declaration_comment(declaration)
                lines.append(f"{_INDENT}{python_name(declaration.name)} = {value}{comment}")
                locals_emitted = True
        if pou.return_type is not None and result not in {python_name(d.name) for d in pou.declarations()}:
            lines.append(f"{_INDENT}{result} = {self._zero_value(pou.return_type)}")
            locals_emitted = True
        if locals_emitted:
            lines.append("")

        body = self.emit_statements(pou.body, context, _INDENT)
        lines.extend(body or [f"{_INDENT}pass"])
        if pou.return_type is not None:
            lines.append(f"{_INDENT}return {result}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Statements
    # ------------------------------------------------------------------ #

    def emit_statements(self, body: list[Stmt], context: _Context, indent: str) -> list[str]:
        lines: list[str] = []
        for statement in body:
            lines.extend(self.emit_statement(statement, context, indent))
        return lines

    def _block(self, body: list[Stmt], context: _Context, indent: str) -> list[str]:
        emitted = self.emit_statements(body, context, indent)
        return emitted or [f"{indent}pass"]

    def emit_statement(self, statement: Stmt, context: _Context, indent: str) -> list[str]:
        lines: list[str] = []
        if statement.comment:
            lines.append(f"{indent}# {statement.comment}")

        if isinstance(statement, Assign):
            lines.append(f"{indent}{self._assignment(statement, context)}")
        elif isinstance(statement, Invoke):
            lines.extend(self._invoke(statement, context, indent))
        elif isinstance(statement, If):
            for position, branch in enumerate(statement.branches):
                keyword = "if" if position == 0 else "elif"
                condition = self.condition(branch.condition, context)
                lines.append(f"{indent}{keyword} {condition}:")
                lines.extend(self._block(branch.body, context, indent + _INDENT))
            if statement.orelse:
                lines.append(f"{indent}else:")
                lines.extend(self._block(statement.orelse, context, indent + _INDENT))
        elif isinstance(statement, Case):
            lines.extend(self._case(statement, context, indent))
        elif isinstance(statement, For):
            lines.extend(self._for(statement, context, indent))
        elif isinstance(statement, While):
            lines.append(f"{indent}while {self.condition(statement.condition, context)}:")
            lines.extend(self._block(statement.body, context, indent + _INDENT))
        elif isinstance(statement, Repeat):
            lines.append(f"{indent}while True:")
            lines.extend(self._block(statement.body, context, indent + _INDENT))
            lines.append(f"{indent + _INDENT}if {self.condition(statement.condition, context)}:")
            lines.append(f"{indent + _INDENT * 2}break")
        elif isinstance(statement, Exit):
            lines.append(f"{indent}break")
        elif isinstance(statement, Continue):
            lines.append(f"{indent}continue")
        elif isinstance(statement, Return):
            if context.pou.kind is PouKind.FUNCTION and context.pou.return_type is not None:
                lines.append(f"{indent}return {python_name(context.pou.name)}")
            else:
                lines.append(f"{indent}return")
        else:  # pragma: no cover - every statement type is handled above
            self.diagnostics.warning(
                f"unsupported statement {type(statement).__name__}",
                span=statement.span,
                code="PF301",
            )
            lines.append(f"{indent}pass  # unsupported: {type(statement).__name__}")
        return lines

    def _assignment(self, statement: Assign, context: _Context) -> str:
        target = self.expression(statement.target, context)
        value = self.expression(statement.value, context)
        target_type = statement.target.inferred_type
        if (
            self.options.strict_integers
            and target_type is not None
            and is_integer(target_type)
            and not isinstance(statement.value, Literal)
        ):
            self._need("wrap")
            value = f'wrap({value}, "{target_type.name}")'
        return f"{target} = {value}"

    def _invoke(self, statement: Invoke, context: _Context, indent: str) -> list[str]:
        call = statement.call
        name = call.simple_name
        outputs = [argument for argument in call.args if argument.is_output]
        inputs = [argument for argument in call.args if not argument.is_output]
        rendered = self._call_expression(
            Call(callee=call.callee, args=inputs, span=call.span), context, statement_position=True
        )
        lines = [f"{indent}{rendered}"]
        if outputs and name is not None:
            instance = self._reference(name, context)
            for argument in outputs:
                target = self.expression(argument.value, context)
                lines.append(f"{indent}{target} = {instance}.{argument.name}")
        return lines

    def _case(self, statement: Case, context: _Context, indent: str) -> list[str]:
        selector = self.expression(statement.selector, context)
        lines: list[str] = []
        for position, branch in enumerate(statement.branches):
            keyword = "if" if position == 0 else "elif"
            lines.append(f"{indent}{keyword} {self._case_condition(selector, branch, context)}:")
            lines.extend(self._block(branch.body, context, indent + _INDENT))
        if statement.orelse:
            if lines:
                lines.append(f"{indent}else:")
                lines.extend(self._block(statement.orelse, context, indent + _INDENT))
            else:
                lines.extend(self._block(statement.orelse, context, indent))
        elif not lines:
            lines.append(f"{indent}pass  # CASE without branches")
        return lines

    def _case_condition(self, selector: str, branch: CaseBranch, context: _Context) -> str:
        singles: list[str] = []
        conditions: list[str] = []
        for label in branch.labels:
            low = self.expression(label.low, context)
            if label.high is None:
                singles.append(low)
            else:
                high = self.expression(label.high, context)
                conditions.append(f"{low} <= {selector} <= {high}")
        if len(singles) == 1:
            conditions.insert(0, f"{selector} == {singles[0]}")
        elif singles:
            conditions.insert(0, f"{selector} in ({', '.join(singles)})")
        return " or ".join(conditions) if conditions else "False"

    def _for(self, statement: For, context: _Context, indent: str) -> list[str]:
        self._need("iec_range")
        variable = self.expression(statement.variable, context)
        start = self.expression(statement.start, context)
        stop = self.expression(statement.stop, context)
        step = self.expression(statement.step, context) if statement.step is not None else None
        arguments = f"{start}, {stop}" + (f", {step}" if step is not None else "")
        lines = [f"{indent}for {variable} in iec_range({arguments}):"]
        lines.extend(self._block(statement.body, context, indent + _INDENT))
        return lines

    # ------------------------------------------------------------------ #
    # Expressions
    # ------------------------------------------------------------------ #

    def condition(self, expr: Expr, context: _Context) -> str:
        """Render *expr* where a boolean is expected."""
        return self.expression(expr, context)

    def expression(self, expr: Expr, context: _Context) -> str:
        text, _ = self._expression(expr, context)
        return text

    def _wrap(self, rendered: tuple[str, int], minimum: int) -> str:
        text, precedence = rendered
        return f"({text})" if precedence < minimum else text

    def _expression(self, expr: Expr, context: _Context) -> tuple[str, int]:
        if isinstance(expr, Literal):
            return self._literal(expr), _PREC["atom"]
        if isinstance(expr, VarRef):
            return self._reference(expr.name, context), _PREC["atom"]
        if isinstance(expr, Member):
            base = self._wrap(self._expression(expr.base, context), _PREC["atom"])
            return f"{base}.{python_name(expr.field_name)}", _PREC["atom"]
        if isinstance(expr, Index):
            base = self._wrap(self._expression(expr.base, context), _PREC["atom"])
            indices = ", ".join(self.expression(item, context) for item in expr.indices)
            return f"{base}[{indices}]", _PREC["atom"]
        if isinstance(expr, Unary):
            return self._unary(expr, context)
        if isinstance(expr, Binary):
            return self._binary(expr, context)
        if isinstance(expr, Call):
            return self._call_expression(expr, context), _PREC["atom"]
        if isinstance(expr, Aggregate):
            return self._aggregate(expr, context), _PREC["atom"]
        self.diagnostics.warning(
            f"unsupported expression {type(expr).__name__}",
            span=expr.span,
            code="PF302",
        )
        return "None", _PREC["atom"]

    def _literal(self, literal: Literal) -> str:
        value = literal.value
        if isinstance(value, bool):
            return "True" if value else "False"
        if is_time(literal.type) and isinstance(value, (int, float)):
            self._need("T")
            from ...runtime.types import format_time

            return f'T("{format_time(float(value))}")'
        if isinstance(value, float):
            return repr(value)
        if isinstance(value, int):
            # Keep hexadecimal and binary notation, it carries meaning in PLC code.
            if literal.text and "#" in literal.text:
                base = literal.text.split("#")[-2] if literal.text.count("#") > 1 else literal.text.split("#")[0]
                if base == "16":
                    return f"0x{value:X}"
                if base == "2":
                    return f"0b{value:b}"
                if base == "8":
                    return f"0o{value:o}"
            return repr(value)
        return repr(value)

    def _reference(self, name: str, context: _Context) -> str:
        lowered = name.lower()
        declaration = context.locals.get(lowered)
        if declaration is not None:
            # IEC identifiers are case insensitive but Python attributes are not,
            # so always emit the spelling used in the declaration.
            return f"{context.prefix}{python_name(declaration.name)}"
        if lowered in self.enum_members:
            enum = self.enum_members[lowered]
            member = next(m for m, _ in enum.members if m.lower() == lowered)
            return f"{class_name(enum.name)}.{python_name(member)}"
        if lowered in self.globals:
            return f"GLOBALS.{python_name(self.globals[lowered].name)}"
        if lowered in self.enums:
            return class_name(name)
        if lowered in self.structs:
            return class_name(name)
        if lowered in self.pous:
            return class_name(name) if self.pous[lowered].kind is not PouKind.FUNCTION else python_name(name)
        if name.upper() in ("TRUE", "FALSE"):
            return name.title()
        return python_name(name)

    def _unary(self, expr: Unary, context: _Context) -> tuple[str, int]:
        operand_type = expr.operand.inferred_type
        if expr.op == "NOT":
            if is_bit_string(operand_type) and not is_bool(operand_type):
                operand = self._wrap(self._expression(expr.operand, context), _PREC["unary"])
                return f"~{operand}", _PREC["unary"]
            operand = self._wrap(self._expression(expr.operand, context), _PREC["not"] + 1)
            return f"not {operand}", _PREC["not"]
        operand = self._wrap(self._expression(expr.operand, context), _PREC["unary"])
        return f"-{operand}", _PREC["unary"]

    def _binary(self, expr: Binary, context: _Context) -> tuple[str, int]:
        operator = expr.op
        left_type = expr.left.inferred_type
        right_type = expr.right.inferred_type

        if operator in _COMPARISON_OPS:
            python_operator = _COMPARISON_OPS[operator]
            # Nested comparisons must be parenthesised: Python would chain them.
            left = self._wrap(self._expression(expr.left, context), _PREC["cmp"] + 1)
            right = self._wrap(self._expression(expr.right, context), _PREC["cmp"] + 1)
            return f"{left} {python_operator} {right}", _PREC["cmp"]

        if operator in ("AND", "OR", "XOR"):
            bitwise = self._is_bitwise(left_type, right_type)
            if operator == "XOR":
                symbol, precedence = "^", _PREC["^"]
            elif bitwise:
                symbol, precedence = ("&", _PREC["&"]) if operator == "AND" else ("|", _PREC["|"])
            else:
                symbol, precedence = ("and", _PREC["and"]) if operator == "AND" else ("or", _PREC["or"])
            left = self._wrap(self._expression(expr.left, context), precedence)
            right = self._wrap(self._expression(expr.right, context), precedence + 1)
            return f"{left} {symbol} {right}", precedence

        if operator == "/":
            if is_integer(left_type) and is_integer(right_type):
                self._need("idiv")
                left = self.expression(expr.left, context)
                right = self.expression(expr.right, context)
                return f"idiv({left}, {right})", _PREC["atom"]
            left = self._wrap(self._expression(expr.left, context), _PREC["/"])
            right = self._wrap(self._expression(expr.right, context), _PREC["/"] + 1)
            return f"{left} / {right}", _PREC["/"]

        if operator == "MOD":
            if is_real(left_type) or is_real(right_type):
                left = self._wrap(self._expression(expr.left, context), _PREC["%"])
                right = self._wrap(self._expression(expr.right, context), _PREC["%"] + 1)
                return f"{left} % {right}", _PREC["%"]
            self._need("imod")
            left = self.expression(expr.left, context)
            right = self.expression(expr.right, context)
            return f"imod({left}, {right})", _PREC["atom"]

        if operator == "**":
            left = self._wrap(self._expression(expr.left, context), _PREC["**"] + 1)
            right = self._wrap(self._expression(expr.right, context), _PREC["**"])
            return f"{left} ** {right}", _PREC["**"]

        precedence = _PREC.get(operator, _PREC["+"])
        left = self._wrap(self._expression(expr.left, context), precedence)
        right = self._wrap(self._expression(expr.right, context), precedence + 1)
        return f"{left} {operator} {right}", precedence

    @staticmethod
    def _is_bitwise(left: IecType | None, right: IecType | None) -> bool:
        """Decide between the logical and the bitwise reading of AND/OR/XOR.

        Only a declared bit string operand forces the bitwise form.  When the
        types are unknown the logical reading wins, because that is what almost
        all PLC code means and because it keeps the output readable.
        """
        for candidate in (left, right):
            if candidate is None:
                continue
            if is_bool(candidate):
                return False
            if is_bit_string(candidate) or is_integer(candidate):
                return True
        return False

    def _aggregate(self, expr: Aggregate, context: _Context) -> str:
        if expr.is_array:
            values = ", ".join(self.expression(item.value, context) for item in expr.elements)
            return f"[{values}]"
        pairs = ", ".join(
            f"{python_name(item.name or 'value')}={self.expression(item.value, context)}"
            for item in expr.elements
        )
        return f"dict({pairs})"

    def _call_expression(self, call: Call, context: _Context, statement_position: bool = False) -> str:
        name = call.simple_name
        if name is None:
            callee = self.expression(call.callee, context)
            arguments = ", ".join(self.expression(a.value, context) for a in call.args)
            return f"{callee}({arguments})"

        lowered = name.lower()
        declaration = context.locals.get(lowered) or self.globals.get(lowered)
        if declaration is not None and declaration.type.kind is TypeKind.INSTANCE:
            return self._instance_call(call, declaration, context)

        if lowered in self.pous:
            target = self.pous[lowered]
            if target.kind is PouKind.FUNCTION:
                return f"{python_name(name)}({self._positional(call, target, context)})"

        upper = name.upper()
        if stdlib.is_standard_function(upper):
            return self._standard_call(upper, call, context)

        arguments = ", ".join(self._argument(a, context) for a in call.args)
        return f"{self._reference(name, context)}({arguments})"

    def _instance_call(self, call: Call, declaration: VarDecl, context: _Context) -> str:
        """Render ``inst(IN := x, PT := T#1s)`` with the block's own parameter names."""
        instance = f"{context.prefix}{python_name(declaration.name)}"
        signature = stdlib.block_signature(declaration.type.name)
        owner = self.pous.get(declaration.type.name.lower())
        parts: list[str] = []
        for position, argument in enumerate(call.args):
            value = self.expression(argument.value, context)
            keyword = argument.name
            if keyword is None:
                keyword = self._positional_parameter(position, signature, owner)
            else:
                keyword = self._canonical_parameter(keyword, signature, owner)
            parts.append(f"{python_name(keyword)}={value}" if keyword else value)
        return f"{instance}({', '.join(parts)})"

    @staticmethod
    def _positional_parameter(position: int, signature, owner: Pou | None) -> str | None:
        if signature is not None and position < len(signature.inputs):
            return signature.inputs[position][0]
        if owner is not None:
            inputs = owner.inputs
            if position < len(inputs):
                return inputs[position].name
        return None

    @staticmethod
    def _canonical_parameter(name: str, signature, owner: Pou | None) -> str:
        lowered = name.lower()
        if signature is not None:
            for candidate, _ in signature.inputs + signature.outputs:
                if candidate.lower() == lowered:
                    return candidate
        if owner is not None:
            for declaration in owner.declarations():
                if declaration.name.lower() == lowered:
                    return declaration.name
        return name

    def _positional(self, call: Call, target: Pou, context: _Context) -> str:
        """Arguments for a user FUNCTION, mapped onto its declared parameters."""
        inputs = target.inputs
        ordered: list[str] = []
        for position, argument in enumerate(call.args):
            value = self.expression(argument.value, context)
            if argument.name is None:
                ordered.append(value)
                continue
            match = next(
                (d.name for d in inputs if d.name.lower() == argument.name.lower()),
                argument.name,
            )
            ordered.append(f"{python_name(match)}={value}")
            del position
        return ", ".join(ordered)

    def _standard_call(self, name: str, call: Call, context: _Context) -> str:
        arguments = [self.expression(a.value, context) for a in call.args]
        if "_TO_" in name:
            self._need("convert")
            target = name.split("_TO_", 1)[1]
            return f'convert({arguments[0] if arguments else "None"}, "{target}")'
        if name in ("SHL", "SHR", "ROL", "ROR"):
            self._need(name)
            operand_type = call.args[0].value.inferred_type if call.args else None
            width = operand_type.name if operand_type is not None else "DWORD"
            return f'{name}({", ".join(arguments)}, "{width}")'
        if name == "NOT" and len(arguments) == 1:
            return f"not {arguments[0]}"
        if name in ("AND", "OR", "XOR") and len(arguments) >= 2:
            symbol = {"AND": " and ", "OR": " or ", "XOR": " ^ "}[name]
            return "(" + symbol.join(arguments) + ")"
        if name in ("ADD", "SUB", "MUL", "DIV"):
            symbol = {"ADD": " + ", "SUB": " - ", "MUL": " * ", "DIV": " / "}[name]
            return "(" + symbol.join(arguments) + ")"
        if name == "MOD" and len(arguments) == 2:
            self._need("imod")
            return f"imod({arguments[0]}, {arguments[1]})"
        if name == "EXPT" and len(arguments) == 2:
            return f"{arguments[0]} ** {arguments[1]}"
        if name == "MOVE" and len(arguments) == 1:
            return arguments[0]
        if name in _RUNTIME_FUNCTIONS:
            self._need(name)
            return f'{name}({", ".join(arguments)})'
        self.diagnostics.warning(
            f"standard function '{name}' has no Python mapping; emitted as a direct call",
            span=call.span,
            code="PF303",
        )
        return f'{python_name(name)}({", ".join(arguments)})'

    def _argument(self, argument: Argument, context: _Context) -> str:
        value = self.expression(argument.value, context)
        return f"{python_name(argument.name)}={value}" if argument.name else value

    # ------------------------------------------------------------------ #
    # Types and default values
    # ------------------------------------------------------------------ #

    def _annotation(self, declared: IecType | None) -> str:
        if declared is None:
            return "object"
        if declared.kind is TypeKind.ARRAY:
            self._need("Array")
            return "Array"
        if declared.kind is TypeKind.STRING:
            self._need("STRING")
            return "STRING"
        if declared.kind is TypeKind.INSTANCE:
            self._need_block(declared.name)
            return class_name(declared.name)
        if declared.kind is TypeKind.DERIVED:
            return class_name(declared.name)
        mapped = _ELEMENTARY_PYTHON.get(declared.name.upper())
        if mapped is None:
            return class_name(declared.name)
        self._need(mapped)
        return mapped

    def _zero_value(self, declared: IecType | None) -> str:
        if declared is None:
            return "None"
        if declared.kind is TypeKind.ARRAY:
            return self._array_value(declared)
        if declared.kind is TypeKind.STRING:
            return '""'
        if declared.kind is TypeKind.INSTANCE:
            self._need_block(declared.name)
            return f"{class_name(declared.name)}(clock)"
        if declared.kind is TypeKind.DERIVED:
            lowered = declared.name.lower()
            if lowered in self.enums:
                enum = self.enums[lowered]
                if enum.members:
                    return f"{class_name(enum.name)}.{python_name(enum.members[0][0])}"
                return "0"
            return f"{class_name(declared.name)}()"
        name = declared.name.upper()
        if name == "BOOL":
            return "False"
        if name in ("REAL", "LREAL", "TIME", "LTIME"):
            return "0.0"
        if name in ("STRING", "WSTRING", "CHAR", "WCHAR"):
            return '""'
        return "0"

    def _array_value(self, declared: IecType) -> str:
        self._need("Array")
        bounds = ", ".join(f"({low}, {high})" for low, high in declared.dimensions)
        element = declared.element
        fill = self._zero_value(element)
        if element is not None and element.kind in (TypeKind.DERIVED, TypeKind.INSTANCE):
            fill = f"lambda: {fill}"
        return f"Array([{bounds}], {fill})"

    def _need_block(self, block_name: str) -> None:
        if stdlib.is_standard_block(block_name):
            self._need(block_name.upper())

    def _default_expression(self, declaration: VarDecl, factory_required: bool) -> str:
        """Initial value for a declaration, in a form valid at the use site."""
        context = _Context(pou=Pou(name="<init>"), prefix="")
        declared = declaration.type

        if declaration.initial is not None:
            if declared.kind is TypeKind.ARRAY and isinstance(declaration.initial, Aggregate):
                return self._array_literal(declared, declaration.initial, context)
            if declared.kind is TypeKind.DERIVED and isinstance(declaration.initial, Aggregate):
                pairs = ", ".join(
                    f"{python_name(item.name or 'value')}={self.expression(item.value, context)}"
                    for item in declaration.initial.elements
                )
                value = f"{class_name(declared.name)}({pairs})"
                return f"field(default_factory=lambda: {value})" if factory_required else value
            rendered = self.expression(declaration.initial, context)
            if declared.kind is TypeKind.DERIVED and declared.name.lower() in self.enums:
                enum = self.enums[declared.name.lower()]
                if isinstance(declaration.initial, Literal) and isinstance(declaration.initial.value, int):
                    for member, value_number in enum.members:
                        if value_number == declaration.initial.value:
                            return f"{class_name(enum.name)}.{python_name(member)}"
            return rendered

        zero = self._zero_value(declared)
        if factory_required and (
            declared.kind in (TypeKind.ARRAY, TypeKind.INSTANCE)
            or (declared.kind is TypeKind.DERIVED and declared.name.lower() in self.structs)
        ):
            return f"field(default_factory=lambda: {zero.replace('(clock)', '()')})"
        return zero

    def _array_literal(self, declared: IecType, initial: Aggregate, context: _Context) -> str:
        bounds = ", ".join(f"({low}, {high})" for low, high in declared.dimensions)
        self._need("Array")
        values = ", ".join(self.expression(item.value, context) for item in initial.elements)
        expected = 1
        for low, high in declared.dimensions:
            expected *= high - low + 1
        if len(initial.elements) not in (0, expected):
            self.diagnostics.warning(
                f"array initialiser has {len(initial.elements)} values but the array holds {expected}",
                span=initial.span,
                code="PF304",
            )
        if not initial.elements:
            return f"Array([{bounds}], {self._zero_value(declared.element)})"
        self._need("filled")
        return f"filled(Array([{bounds}], {self._zero_value(declared.element)}), [{values}])"


#: Standard functions that exist verbatim in the runtime.
_RUNTIME_FUNCTIONS = {
    "ABS",
    "SQRT",
    "LN",
    "LOG",
    "EXP",
    "SIN",
    "COS",
    "TAN",
    "ASIN",
    "ACOS",
    "ATAN",
    "ATAN2",
    "MIN",
    "MAX",
    "LIMIT",
    "SEL",
    "MUX",
    "TRUNC",
    "ROUND",
    "LEN",
    "LEFT",
    "RIGHT",
    "MID",
    "CONCAT",
    "INSERT",
    "DELETE",
    "REPLACE",
    "FIND",
}


def emit_project(
    project: Project,
    options: PythonOptions | None = None,
    diagnostics: DiagnosticBag | None = None,
) -> str:
    """Render a whole project as a single Python module."""
    return PythonEmitter(project, options, diagnostics).emit_module()


def emit_module(
    project: Project,
    pous: list[Pou],
    options: PythonOptions | None = None,
    diagnostics: DiagnosticBag | None = None,
) -> str:
    """Render selected POUs as one module, sharing the project's type context."""
    return PythonEmitter(project, options, diagnostics).emit_module(pous)
