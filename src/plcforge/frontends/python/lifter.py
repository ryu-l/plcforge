"""Python frontend: lift Python control code back into the IR.

This is the return leg.  A Python module that follows the runtime conventions -
a class deriving from ``FunctionBlock`` or ``Program``, attributes declared in
``__init__``, logic in ``body()`` or ``scan()`` - is turned back into POUs, which
the Structured Text backend then writes out for the engineering tool.

The conventions are exactly the ones the Python backend emits, so a project can
go SCL -> Python -> SCL and come back recognisable.  Hand written Python works
too as long as it keeps to the same shape; anything the lifter cannot map is
reported as a diagnostic rather than silently dropped.
"""

from __future__ import annotations

import ast
import pathlib

from ...diagnostics import DiagnosticBag
from ...ir.nodes import (
    Aggregate,
    Argument,
    Assign,
    Binary,
    Branch,
    Call,
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
    SourceSpan,
    Stmt,
    StructType,
    Unary,
    VarBlock,
    VarDecl,
    VarKind,
    VarRef,
    While,
)
from ...ir.types import (
    BOOL,
    ELEMENTARY_TYPES,
    IecType,
    TypeKind,
    array_of,
    derived,
    elementary,
    instance_of,
    string_of,
)

__all__ = ["PythonLifter", "lift_file", "lift_source"]

_BLOCK_BASES = {"FunctionBlock", "Program"}
_SECTION_COMMENTS = {
    "VAR_INPUT": VarKind.INPUT,
    "VAR_OUTPUT": VarKind.OUTPUT,
    "VAR_IN_OUT": VarKind.IN_OUT,
    "VAR": VarKind.LOCAL,
    "VAR_TEMP": VarKind.TEMP,
    "VAR_STAT": VarKind.STATIC,
    "VAR_GLOBAL": VarKind.GLOBAL,
}

_BIN_OPS = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
    ast.FloorDiv: "/",
    ast.Mod: "MOD",
    ast.Pow: "**",
    ast.BitAnd: "AND",
    ast.BitOr: "OR",
    ast.BitXor: "XOR",
}

_COMPARE_OPS = {
    ast.Eq: "=",
    ast.NotEq: "<>",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
}

#: Runtime helpers that map back onto an IEC operator rather than a call.
_OPERATOR_HELPERS = {"idiv": "/", "imod": "MOD"}


class PythonLifter:
    """Turns a Python module into POUs."""

    def __init__(self, filename: str = "<python>", diagnostics: DiagnosticBag | None = None) -> None:
        self.filename = filename
        self.diagnostics = diagnostics if diagnostics is not None else DiagnosticBag()
        self._enums: set[str] = set()
        self._structs: set[str] = set()
        self._function: str | None = None
        self._source = ""

    # ------------------------------------------------------------------ #

    def lift(self, source: str, project_name: str | None = None) -> Project:
        self._source = source
        project = Project(name=project_name or "project", source_files=[self.filename])
        try:
            tree = ast.parse(source, self.filename)
        except SyntaxError as error:
            self.diagnostics.error(
                f"cannot parse Python source: {error.msg}",
                file=self.filename,
                line=error.lineno or 0,
                column=error.offset or 0,
                code="PF140",
            )
            return project

        # First pass: learn which names are enumerations and structures so that
        # declarations referring to them resolve correctly.
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                bases = {self._base_name(base) for base in node.bases}
                if "IntEnum" in bases or "Enum" in bases:
                    self._enums.add(node.name)
                elif "Struct" in bases or any(self._decorator_name(d) == "dataclass" for d in node.decorator_list):
                    self._structs.add(node.name)

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                self._lift_class(node, project)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                pou = self._lift_function(node)
                if pou is not None:
                    project.pous.append(pou)
        self._declare_implicit_locals(project)
        if not project.pous and not project.structs and not project.enums:
            self.diagnostics.warning(
                "no function blocks, programs or functions were found",
                file=self.filename,
                code="PF141",
                hint="A POU is a class deriving from FunctionBlock or Program, or a module level function.",
            )
        return project

    def _declare_implicit_locals(self, project: Project) -> None:
        """Declare variables that Python creates on first assignment.

        ``dry = suction < minimum`` needs no declaration in Python but does in
        Structured Text.  Running the type resolver first means the declaration
        gets the type the expression actually produces rather than a guess.
        """
        from ...ir.link import link_types
        from ...ir.nodes import walk_statements
        from ...ir.validate import resolve_types

        quiet = DiagnosticBag()
        link_types(project, quiet)
        resolve_types(project, quiet)

        enum_members = {
            member.lower() for enum in project.enums for member, _ in enum.members
        }
        known_pous = {pou.name.lower() for pou in project.pous}

        for pou in project.pous:
            declared = {decl.name.lower() for decl in pou.declarations()}
            declared.add(pou.name.lower())
            introduced: list[VarDecl] = []
            for statement in walk_statements(pou.body):
                targets: list[tuple[Expr, Expr | None]] = []
                if isinstance(statement, Assign):
                    targets.append((statement.target, statement.value))
                elif isinstance(statement, For):
                    targets.append((statement.variable, statement.start))
                for target, value in targets:
                    if not isinstance(target, VarRef):
                        continue
                    key = target.name.lower()
                    if key in declared or key in enum_members or key in known_pous:
                        continue
                    declared.add(key)
                    introduced.append(
                        VarDecl(
                            name=target.name,
                            type=(value.inferred_type if value is not None else None) or BOOL,
                            comment="Local introduced by the Python source.",
                            span=target.span,
                        )
                    )
            if introduced:
                pou.var_blocks.append(VarBlock(kind=VarKind.TEMP, declarations=introduced))

        # Types changed, so annotate once more for the benefit of the backends.
        resolve_types(project, DiagnosticBag())

    @staticmethod
    def _base_name(node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return ""

    @staticmethod
    def _decorator_name(node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Call):
            return PythonLifter._decorator_name(node.func)
        if isinstance(node, ast.Attribute):
            return node.attr
        return ""

    @staticmethod
    def _summary(docstring: str | None) -> str | None:
        """First paragraph of a docstring, which is the part a POU comment holds."""
        if not docstring:
            return None
        paragraph = docstring.strip().split("\n\n", 1)[0]
        return " ".join(paragraph.split()) or None

    def _span(self, node: ast.AST) -> SourceSpan:
        return SourceSpan(self.filename, getattr(node, "lineno", 0), getattr(node, "col_offset", 0) + 1)

    # ------------------------------------------------------------------ #
    # Classes
    # ------------------------------------------------------------------ #

    def _lift_class(self, node: ast.ClassDef, project: Project) -> None:
        if node.name in self._enums:
            project.enums.append(self._lift_enum(node))
            return
        if node.name in self._structs:
            project.structs.append(self._lift_struct(node))
            return

        bases = {self._base_name(base) for base in node.bases}
        if not bases & _BLOCK_BASES:
            self.diagnostics.note(
                f"class '{node.name}' is not a POU; it derives from "
                f"{', '.join(sorted(bases)) or 'object'}",
                file=self.filename,
                line=node.lineno,
                code="PF142",
            )
            return

        kind = PouKind.PROGRAM if "Program" in bases else PouKind.FUNCTION_BLOCK
        pou = Pou(
            name=node.name,
            kind=kind,
            source_language="PY",
            comment=self._summary(ast.get_docstring(node)),
            span=self._span(node),
        )
        for member in node.body:
            if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if member.name == "__init__":
                pou.var_blocks.extend(self._lift_declarations(member))
            elif member.name in ("body", "scan"):
                pou.body = self.statements(member.body)
            elif member.name == "__call__":
                continue
            else:
                self.diagnostics.note(
                    f"method '{node.name}.{member.name}' has no IEC equivalent and was skipped",
                    file=self.filename,
                    line=member.lineno,
                    code="PF143",
                )
        if not pou.body:
            self.diagnostics.warning(
                f"'{node.name}' has no body() or scan() method",
                file=self.filename,
                line=node.lineno,
                code="PF144",
            )
        project.pous.append(pou)

    def _lift_enum(self, node: ast.ClassDef) -> EnumType:
        members: list[tuple[str, int]] = []
        for item in node.body:
            if isinstance(item, ast.Assign) and len(item.targets) == 1:
                target = item.targets[0]
                if (
                    isinstance(target, ast.Name)
                    and isinstance(item.value, ast.Constant)
                    and isinstance(item.value.value, int)
                ):
                    members.append((target.id, item.value.value))
        return EnumType(
            name=node.name,
            members=members,
            comment=self._summary(ast.get_docstring(node)),
            span=self._span(node),
        )

    def _lift_struct(self, node: ast.ClassDef) -> StructType:
        fields: list[VarDecl] = []
        for item in node.body:
            if not isinstance(item, ast.AnnAssign) or not isinstance(item.target, ast.Name):
                continue
            if item.target.id.startswith("__"):
                continue
            fields.append(
                VarDecl(
                    name=item.target.id,
                    type=self._type_from_annotation(item.annotation, item.value),
                    initial=self._initial_value(item.value),
                    span=self._span(item),
                )
            )
        return StructType(
            name=node.name,
            fields=fields,
            comment=self._summary(ast.get_docstring(node)),
            span=self._span(node),
        )

    def _lift_declarations(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[VarBlock]:
        """Read ``self.x: BOOL = False`` assignments, grouped by section comment."""
        blocks: list[VarBlock] = []
        current = VarBlock(kind=VarKind.LOCAL, span=self._span(node))
        sections = self._section_markers(node)

        for item in node.body:
            target: ast.expr | None = None
            annotation: ast.expr | None = None
            value: ast.expr | None = None
            if isinstance(item, ast.AnnAssign):
                target, annotation, value = item.target, item.annotation, item.value
            elif isinstance(item, ast.Assign) and len(item.targets) == 1:
                target, value = item.targets[0], item.value
            if not isinstance(target, ast.Attribute) or not isinstance(target.value, ast.Name):
                continue
            if target.value.id != "self" or target.attr.startswith("_"):
                continue

            kind = sections.get(item.lineno, current.kind)
            if kind is not current.kind:
                if current.declarations:
                    blocks.append(current)
                current = VarBlock(kind=kind, span=self._span(item))
            declared = self._type_from_annotation(annotation, value)
            current.declarations.append(
                VarDecl(
                    name=target.attr,
                    type=declared,
                    initial=self._meaningful_initial(declared, value),
                    span=self._span(item),
                )
            )
        if current.declarations:
            blocks.append(current)
        return blocks

    def _section_markers(self, node: ast.AST) -> dict[int, VarKind]:
        """Map each line to the ``# VAR_INPUT`` style marker in force above it.

        The markers are comments, which the abstract syntax tree discards, so the
        source has to be tokenised a second time.  It is tokenised from the text
        that was handed in rather than re-read from disk: the caller may be
        lifting a string that was never written to a file.
        """
        del node
        import io
        import tokenize

        by_line: dict[int, VarKind] = {}
        if not self._source:
            return by_line
        current: VarKind | None = None
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(self._source).readline))
        except (tokenize.TokenError, IndentationError):  # pragma: no cover - already reported
            return by_line
        for token in tokens:
            if token.type == tokenize.COMMENT:
                label = token.string.lstrip("# ").split()[0].upper() if token.string.strip("# ") else ""
                if label in _SECTION_COMMENTS:
                    current = _SECTION_COMMENTS[label]
                    by_line[token.start[0]] = current
            elif current is not None and token.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT):
                by_line.setdefault(token.start[0], current)
        return by_line

    # ------------------------------------------------------------------ #
    # Functions
    # ------------------------------------------------------------------ #

    def _lift_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> Pou | None:
        if node.name.startswith("_"):
            return None
        pou = Pou(
            name=node.name,
            kind=PouKind.FUNCTION,
            return_type=self._type_from_annotation(node.returns, None) if node.returns else None,
            source_language="PY",
            comment=self._summary(ast.get_docstring(node)),
            span=self._span(node),
        )
        inputs = VarBlock(kind=VarKind.INPUT, span=self._span(node))
        arguments = list(node.args.args) + list(node.args.kwonlyargs)
        defaults: list[ast.expr | None] = [None] * (len(arguments) - len(node.args.defaults))
        defaults += list(node.args.defaults)
        for argument, default in zip(arguments, defaults, strict=True):
            if argument.arg in ("self", "cls"):
                continue
            inputs.declarations.append(
                VarDecl(
                    name=argument.arg,
                    type=self._type_from_annotation(argument.annotation, None),
                    initial=self._initial_value(default),
                    span=self._span(argument),
                )
            )
        if inputs.declarations:
            pou.var_blocks.append(inputs)

        body = [item for item in node.body if not _is_docstring(item)]
        locals_block = VarBlock(kind=VarKind.LOCAL, span=self._span(node))
        remaining: list[ast.stmt] = []
        for item in body:
            declaration = self._local_declaration(item, pou.name)
            if declaration is not None:
                locals_block.declarations.append(declaration)
            else:
                remaining.append(item)
        if locals_block.declarations:
            pou.var_blocks.append(locals_block)
        self._function = pou.name
        try:
            pou.body = self.statements(self._strip_result_scaffolding(remaining, pou.name))
        finally:
            self._function = None
        if pou.body and isinstance(pou.body[-1], Return):
            pou.body.pop()
        return pou

    @staticmethod
    def _strip_result_scaffolding(body: list[ast.stmt], function_name: str) -> list[ast.stmt]:
        """Remove the ``<name> = <default>`` line the Python backend emits."""
        if not body:
            return body
        first = body[0]
        if (
            isinstance(first, ast.Assign)
            and len(first.targets) == 1
            and isinstance(first.targets[0], ast.Name)
            and first.targets[0].id == function_name
            and isinstance(first.value, ast.Constant)
        ):
            return body[1:]
        return body

    def _local_declaration(self, item: ast.stmt, function_name: str) -> VarDecl | None:
        """Recognise the ``name = <constant>`` prologue the backend emits."""
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            if item.target.id == function_name:
                return None
            return VarDecl(
                name=item.target.id,
                type=self._type_from_annotation(item.annotation, item.value),
                initial=self._initial_value(item.value),
                span=self._span(item),
            )
        if (
            isinstance(item, ast.Assign)
            and len(item.targets) == 1
            and isinstance(item.targets[0], ast.Name)
            and isinstance(item.value, ast.Constant)
            and item.targets[0].id != function_name
        ):
            return VarDecl(
                name=item.targets[0].id,
                type=self._type_from_constant(item.value.value),
                initial=self._initial_value(item.value),
                span=self._span(item),
            )
        return None

    # ------------------------------------------------------------------ #
    # Types
    # ------------------------------------------------------------------ #

    def _type_from_annotation(self, annotation: ast.expr | None, value: ast.expr | None) -> IecType:
        name = self._annotation_name(annotation)
        if name == "Array":
            return self._array_type(value)
        if name:
            upper = name.upper()
            if upper in ELEMENTARY_TYPES:
                return elementary(upper) if upper not in ("STRING", "WSTRING") else string_of(upper)
            if name in self._enums or name in self._structs:
                return derived(name)
            return instance_of(name)
        if value is not None:
            return self._type_from_value(value)
        return BOOL

    def _annotation_name(self, annotation: ast.expr | None) -> str:
        if annotation is None:
            return ""
        if isinstance(annotation, ast.Name):
            return annotation.id
        if isinstance(annotation, ast.Attribute):
            return annotation.attr
        if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
            return annotation.value
        if isinstance(annotation, ast.Subscript):
            return self._annotation_name(annotation.value)
        if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
            # ``BOOL | None`` from a generated __call__ signature.
            return self._annotation_name(annotation.left)
        return ""

    def _array_type(self, value: ast.expr | None) -> IecType:
        """Recover ``ARRAY [lo..hi] OF T`` from ``Array([(lo, hi)], fill)``."""
        call = value
        if isinstance(call, ast.Call) and self._call_name(call) == "filled" and call.args:
            call = call.args[0]
        if isinstance(call, ast.Call) and self._call_name(call) == "Array" and call.args:
            bounds: list[tuple[int, int]] = []
            if isinstance(call.args[0], (ast.List, ast.Tuple)):
                for entry in call.args[0].elts:
                    if isinstance(entry, ast.Tuple) and len(entry.elts) == 2:
                        low, high = (self._constant_int(item) for item in entry.elts)
                        bounds.append((low, high))
            element = self._type_from_value(call.args[1]) if len(call.args) > 1 else BOOL
            return array_of(element, tuple(bounds) or ((0, 0),))
        return array_of(BOOL, ((0, 0),))

    @staticmethod
    def _constant_int(node: ast.expr) -> int:
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -PythonLifter._constant_int(node.operand)
        return 0

    def _type_from_value(self, value: ast.expr | None) -> IecType:
        if value is None:
            return BOOL
        if isinstance(value, ast.Constant):
            return self._type_from_constant(value.value)
        if isinstance(value, ast.Call):
            name = self._call_name(value)
            if name == "T":
                return elementary("TIME")
            if name in self._structs or name in self._enums:
                return derived(name)
            if name:
                return instance_of(name)
        if (
            isinstance(value, ast.Attribute)
            and isinstance(value.value, ast.Name)
            and value.value.id in self._enums
        ):
            return derived(value.value.id)
        return BOOL

    @staticmethod
    def _type_from_constant(value: object) -> IecType:
        if isinstance(value, bool):
            return BOOL
        if isinstance(value, int):
            return elementary("DINT")
        if isinstance(value, float):
            return elementary("REAL")
        if isinstance(value, str):
            return string_of("STRING")
        return BOOL

    def _meaningful_initial(self, declared: IecType, value: ast.expr | None) -> Expr | None:
        """Drop initial values that only restate the target language's default.

        ``self.delay: TON = TON(clock)`` and ``self.enable: BOOL = False`` carry
        no information the IEC declaration does not already imply, and writing
        them out would produce Structured Text that does not compile.
        """
        if value is None:
            return None
        if declared.kind in (TypeKind.INSTANCE, TypeKind.DERIVED, TypeKind.ARRAY) and isinstance(
            value, ast.Call
        ):
            return None
        if isinstance(value, ast.Constant):
            defaults = {"BOOL": False, "STRING": "", "WSTRING": ""}
            expected = defaults.get(declared.name.upper(), 0)
            if value.value == expected and type(value.value) is type(expected):
                return None
            if isinstance(value.value, float) and value.value == 0.0:
                return None
        return self._initial_value(value)

    def _initial_value(self, value: ast.expr | None) -> Expr | None:
        if value is None:
            return None
        if isinstance(value, ast.Call) and self._call_name(value) in ("Array", "filled", "field"):
            return None
        if isinstance(value, ast.Constant) and value.value is None:
            return None
        try:
            return self.expression(value)
        except _Unsupported:
            return None

    @staticmethod
    def _call_name(node: ast.Call) -> str:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        return ""

    # ------------------------------------------------------------------ #
    # Statements
    # ------------------------------------------------------------------ #

    def statements(self, body: list[ast.stmt]) -> list[Stmt]:
        result: list[Stmt] = []
        for item in body:
            if _is_docstring(item):
                continue
            try:
                result.extend(self.statement(item))
            except _Unsupported as error:
                self.diagnostics.warning(
                    str(error),
                    file=self.filename,
                    line=getattr(item, "lineno", 0),
                    code="PF145",
                )
        return result

    def statement(self, node: ast.stmt) -> list[Stmt]:
        span = self._span(node)

        if isinstance(node, ast.Pass):
            return []
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)):
            return []
        if isinstance(node, ast.Assign):
            value = self.expression(node.value)
            return [Assign(target=self.expression(target), value=value, span=span) for target in node.targets]
        if isinstance(node, ast.AnnAssign) and node.value is not None:
            return [Assign(target=self.expression(node.target), value=self.expression(node.value), span=span)]
        if isinstance(node, ast.AugAssign):
            operator = _BIN_OPS.get(type(node.op))
            if operator is None:
                raise _Unsupported(f"augmented assignment '{type(node.op).__name__}' has no IEC equivalent")
            target = self.expression(node.target)
            combined = Binary(op=operator, left=target, right=self.expression(node.value), span=span)
            return [Assign(target=target, value=combined, span=span)]
        if isinstance(node, ast.Expr):
            if isinstance(node.value, ast.Call):
                call = self.expression(node.value)
                if isinstance(call, Call):
                    return [Invoke(call=call, span=span)]
            return []
        if isinstance(node, ast.If):
            return [self._if(node, span)]
        if isinstance(node, ast.While):
            return [self._while(node, span)]
        if isinstance(node, ast.For):
            return [self._for(node, span)]
        if isinstance(node, ast.Break):
            return [Exit(span=span)]
        if isinstance(node, ast.Continue):
            return [Continue(span=span)]
        if isinstance(node, ast.Return):
            if node.value is not None and self._function is not None:
                # ST has no value carrying RETURN: the result is assigned to a
                # variable named after the function.
                return [
                    Assign(
                        target=VarRef(name=self._function, span=span),
                        value=self.expression(node.value),
                        span=span,
                    ),
                    Return(span=span),
                ]
            return [Return(span=span)]
        if isinstance(node, ast.Assert):
            return []
        raise _Unsupported(f"statement '{type(node).__name__}' has no IEC equivalent")

    def _if(self, node: ast.If, span: SourceSpan) -> If:
        branches = [Branch(condition=self.expression(node.test), body=self.statements(node.body))]
        orelse: list[Stmt] = []
        tail = node.orelse
        while len(tail) == 1 and isinstance(tail[0], ast.If):
            nested = tail[0]
            branches.append(
                Branch(condition=self.expression(nested.test), body=self.statements(nested.body))
            )
            tail = nested.orelse
        if tail:
            orelse = self.statements(tail)
        return If(branches=branches, orelse=orelse, span=span)

    def _while(self, node: ast.While, span: SourceSpan) -> Stmt:
        # ``while True: ... if cond: break`` is how REPEAT ... UNTIL is emitted.
        is_forever = isinstance(node.test, ast.Constant) and node.test.value is True
        if is_forever and node.body:
            last = node.body[-1]
            if (
                isinstance(last, ast.If)
                and len(last.body) == 1
                and isinstance(last.body[0], ast.Break)
                and not last.orelse
            ):
                return Repeat(
                    body=self.statements(node.body[:-1]),
                    condition=self.expression(last.test),
                    span=span,
                )
        return While(condition=self.expression(node.test), body=self.statements(node.body), span=span)

    def _for(self, node: ast.For, span: SourceSpan) -> Stmt:
        if not isinstance(node.iter, ast.Call):
            raise _Unsupported("only counting FOR loops translate to IEC; iterate with iec_range or range")
        name = self._call_name(node.iter)
        arguments = node.iter.args
        if name not in ("iec_range", "range") or not arguments:
            raise _Unsupported(f"cannot translate iteration over '{name}'")

        if len(arguments) == 1:
            start: Expr = Literal(value=0, type=elementary("DINT"), span=span)
            stop_source = arguments[0]
        else:
            start = self.expression(arguments[0])
            stop_source = arguments[1]
        step = self.expression(arguments[2]) if len(arguments) > 2 else None

        stop = self.expression(stop_source)
        if name == "range":
            # ``range`` is exclusive and IEC ``TO`` is inclusive.
            if isinstance(stop, Literal) and isinstance(stop.value, int):
                stop = Literal(value=stop.value - 1, type=stop.type, span=stop.span)
            else:
                stop = Binary(
                    op="-",
                    left=stop,
                    right=Literal(value=1, type=elementary("DINT"), span=span),
                    span=span,
                )
        return For(
            variable=self.expression(node.target),
            start=start,
            stop=stop,
            step=step,
            body=self.statements(node.body),
            span=span,
        )

    # ------------------------------------------------------------------ #
    # Expressions
    # ------------------------------------------------------------------ #

    def expression(self, node: ast.expr) -> Expr:
        span = self._span(node)

        if isinstance(node, ast.Constant):
            return self._constant(node, span)
        if isinstance(node, ast.Name):
            return VarRef(name=node.id, span=span)
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "self":
                return VarRef(name=node.attr, span=span)
            if isinstance(node.value, ast.Name) and node.value.id in self._enums:
                return VarRef(name=node.attr, span=span)
            if isinstance(node.value, ast.Name) and node.value.id == "GLOBALS":
                return VarRef(name=node.attr, span=span)
            return Member(base=self.expression(node.value), field_name=node.attr, span=span)
        if isinstance(node, ast.Subscript):
            indices = self._indices(node.slice)
            return Index(base=self.expression(node.value), indices=indices, span=span)
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, (ast.Not, ast.Invert)):
                return Unary(op="NOT", operand=self.expression(node.operand), span=span)
            if isinstance(node.op, ast.USub):
                return Unary(op="-", operand=self.expression(node.operand), span=span)
            return self.expression(node.operand)
        if isinstance(node, ast.BoolOp):
            operator = "AND" if isinstance(node.op, ast.And) else "OR"
            result = self.expression(node.values[0])
            for extra in node.values[1:]:
                result = Binary(op=operator, left=result, right=self.expression(extra), span=span)
            return result
        if isinstance(node, ast.BinOp):
            binary_operator = _BIN_OPS.get(type(node.op))
            if binary_operator is None:
                raise _Unsupported(f"operator '{type(node.op).__name__}' has no IEC equivalent")
            return Binary(
                op=binary_operator,
                left=self.expression(node.left),
                right=self.expression(node.right),
                span=span,
            )
        if isinstance(node, ast.Compare):
            return self._compare(node, span)
        if isinstance(node, ast.Call):
            return self._call(node, span)
        if isinstance(node, ast.IfExp):
            # ``a if c else b`` is SEL(c, b, a) in IEC argument order.
            return Call(
                callee=VarRef(name="SEL", span=span),
                args=[
                    Argument(value=self.expression(node.test)),
                    Argument(value=self.expression(node.orelse)),
                    Argument(value=self.expression(node.body)),
                ],
                span=span,
            )
        if isinstance(node, (ast.List, ast.Tuple)):
            return Aggregate(
                elements=[Argument(value=self.expression(item)) for item in node.elts],
                is_array=True,
                span=span,
            )
        raise _Unsupported(f"expression '{type(node).__name__}' has no IEC equivalent")

    def _indices(self, node: ast.expr) -> list[Expr]:
        if isinstance(node, ast.Tuple):
            return [self.expression(item) for item in node.elts]
        return [self.expression(node)]

    def _constant(self, node: ast.Constant, span: SourceSpan) -> Literal:
        value = node.value
        if isinstance(value, bool):
            return Literal(value=value, type=BOOL, text="TRUE" if value else "FALSE", span=span)
        if isinstance(value, int):
            return Literal(value=value, type=elementary("DINT"), span=span)
        if isinstance(value, float):
            return Literal(value=value, type=elementary("REAL"), span=span)
        if isinstance(value, str):
            return Literal(value=value, type=string_of("STRING"), span=span)
        raise _Unsupported(f"constant {value!r} has no IEC equivalent")

    def _compare(self, node: ast.Compare, span: SourceSpan) -> Expr:
        operands = [node.left, *node.comparators]
        parts: list[Expr] = []
        for index, operation in enumerate(node.ops):
            operator = _COMPARE_OPS.get(type(operation))
            if operator is None:
                raise _Unsupported(f"comparison '{type(operation).__name__}' has no IEC equivalent")
            parts.append(
                Binary(
                    op=operator,
                    left=self.expression(operands[index]),
                    right=self.expression(operands[index + 1]),
                    span=span,
                )
            )
        result = parts[0]
        for extra in parts[1:]:
            # Python chains comparisons; IEC needs the explicit conjunction.
            result = Binary(op="AND", left=result, right=extra, span=span)
        return result

    def _call(self, node: ast.Call, span: SourceSpan) -> Expr:
        name = self._call_name(node)

        if name in _OPERATOR_HELPERS and len(node.args) == 2:
            return Binary(
                op=_OPERATOR_HELPERS[name],
                left=self.expression(node.args[0]),
                right=self.expression(node.args[1]),
                span=span,
            )
        if name == "T" and node.args and isinstance(node.args[0], ast.Constant):
            from ...runtime.types import T as parse_duration

            text = str(node.args[0].value)
            return Literal(value=parse_duration(text), type=elementary("TIME"), text=text, span=span)
        if name == "convert" and len(node.args) == 2 and isinstance(node.args[1], ast.Constant):
            target = str(node.args[1].value).upper()
            return Call(
                callee=VarRef(name=f"TO_{target}", span=span),
                args=[Argument(value=self.expression(node.args[0]))],
                span=span,
            )
        if name == "wrap" and node.args:
            return self.expression(node.args[0])

        callee: Expr
        if not isinstance(node.func, ast.Attribute):
            callee = self.expression(node.func)
        elif isinstance(node.func.value, ast.Name) and node.func.value.id == "self":
            callee = VarRef(name=node.func.attr, span=span)
        else:
            callee = Member(
                base=self.expression(node.func.value),
                field_name=node.func.attr,
                span=span,
            )
        arguments = [Argument(value=self.expression(item)) for item in node.args]
        for keyword in node.keywords:
            if keyword.arg is None:
                raise _Unsupported("argument unpacking has no IEC equivalent")
            arguments.append(Argument(value=self.expression(keyword.value), name=keyword.arg))
        return Call(callee=callee, args=arguments, span=span)


class _Unsupported(Exception):
    """Raised for a Python construct with no IEC 61131-3 counterpart."""


def _is_docstring(node: ast.stmt) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def lift_source(
    source: str,
    filename: str = "<python>",
    diagnostics: DiagnosticBag | None = None,
    project_name: str | None = None,
) -> Project:
    return PythonLifter(filename, diagnostics).lift(source, project_name)


def lift_file(path: str, diagnostics: DiagnosticBag | None = None) -> Project:
    file = pathlib.Path(path)
    return lift_source(
        file.read_text(encoding="utf-8"),
        str(file),
        diagnostics,
        project_name=file.stem,
    )
