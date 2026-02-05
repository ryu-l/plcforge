"""Recursive descent parser for Structured Text / SCL.

Covers the language as it is written in practice: POU declarations with all
variable sections, user defined structures and enumerations, the full statement
set (``IF``/``CASE``/``FOR``/``WHILE``/``REPEAT``), function block invocation
with named and output parameters, and expressions with the operator precedence
from IEC 61131-3 table 55.

Errors do not abort the parse.  The parser records a diagnostic, skips to the
next statement boundary and carries on, so one bad line does not cost the whole
file.
"""

from __future__ import annotations

from ...diagnostics import DiagnosticBag
from ...ir.nodes import (
    Aggregate,
    Argument,
    Assign,
    Binary,
    Branch,
    Call,
    Case,
    CaseBranch,
    CaseLabel,
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
    array_of,
    derived,
    elementary,
    instance_of,
    string_of,
)
from .lexer import Token, TokenType, tokenize

__all__ = ["ParseError", "Parser", "parse_expression_text", "parse_file", "parse_source"]


class ParseError(Exception):
    """Internal control-flow signal used for error recovery."""


#: Binary operator precedence, lowest binding first (IEC 61131-3 table 55).
_PRECEDENCE: list[tuple[str, ...]] = [
    ("OR",),
    ("XOR",),
    ("AND", "&"),
    ("=", "<>"),
    ("<", ">", "<=", ">="),
    ("+", "-"),
    ("*", "/", "MOD"),
]

_VAR_KINDS = {
    "VAR": VarKind.LOCAL,
    "VAR_INPUT": VarKind.INPUT,
    "VAR_OUTPUT": VarKind.OUTPUT,
    "VAR_IN_OUT": VarKind.IN_OUT,
    "VAR_TEMP": VarKind.TEMP,
    "VAR_STAT": VarKind.STATIC,
    "VAR_INST": VarKind.STATIC,
    "VAR_EXTERNAL": VarKind.EXTERNAL,
    "VAR_GLOBAL": VarKind.GLOBAL,
}

_POU_KINDS = {
    "PROGRAM": (PouKind.PROGRAM, "END_PROGRAM"),
    "FUNCTION_BLOCK": (PouKind.FUNCTION_BLOCK, "END_FUNCTION_BLOCK"),
    "FUNCTION": (PouKind.FUNCTION, "END_FUNCTION"),
}

_STATEMENT_TERMINATORS = frozenset(
    {
        "END_IF",
        "ELSE",
        "ELSIF",
        "END_CASE",
        "END_FOR",
        "END_WHILE",
        "END_REPEAT",
        "UNTIL",
        "END_PROGRAM",
        "END_FUNCTION",
        "END_FUNCTION_BLOCK",
        "END_VAR",
    }
)

#: Guard against pathological array initialisers such as ``[1000000(0)]``.
_MAX_REPEAT = 4096


def _integer_payload(token: Token) -> int:
    """The numeric value the lexer attached to an INTEGER token."""
    payload = token.payload
    return payload if isinstance(payload, int) else 0


def _clean_comment(text: str) -> str:
    text = text.strip()
    if text.startswith("(*") and text.endswith("*)"):
        text = text[2:-2]
    elif text.startswith("//"):
        text = text[2:]
    return " ".join(text.split())


class Parser:
    """Parses one ST source file into IR."""

    def __init__(
        self,
        source: str,
        filename: str = "<st>",
        diagnostics: DiagnosticBag | None = None,
    ) -> None:
        self.filename = filename
        self.diagnostics = diagnostics if diagnostics is not None else DiagnosticBag()
        raw = tokenize(source, filename, self.diagnostics, keep_comments=True)
        self.tokens: list[Token] = []
        self._leading: dict[int, list[str]] = {}
        self._trailing: dict[int, str] = {}
        self._collect(raw)
        self.pos = 0

    def _collect(self, raw: list[Token]) -> None:
        """Split the raw stream into significant tokens plus attached comments."""
        pending: list[str] = []
        last_line = 0
        for token in raw:
            if token.type is TokenType.COMMENT:
                text = _clean_comment(token.value)
                if not text:
                    continue
                if self.tokens and token.line == last_line:
                    index = len(self.tokens) - 1
                    existing = self._trailing.get(index)
                    self._trailing[index] = f"{existing} {text}" if existing else text
                else:
                    pending.append(text)
                continue
            if token.type is TokenType.PRAGMA:
                continue
            if pending:
                self._leading[len(self.tokens)] = pending
                pending = []
            self.tokens.append(token)
            last_line = token.line

    # -- token helpers ----------------------------------------------------- #

    @property
    def current(self) -> Token:
        return self.tokens[min(self.pos, len(self.tokens) - 1)]

    def peek(self, offset: int = 0) -> Token:
        return self.tokens[min(self.pos + offset, len(self.tokens) - 1)]

    def advance(self) -> Token:
        token = self.current
        if self.pos < len(self.tokens) - 1:
            self.pos += 1
        return token

    def at_end(self) -> bool:
        return self.current.type is TokenType.EOF

    def span(self, token: Token | None = None) -> SourceSpan:
        token = token or self.current
        return SourceSpan(self.filename, token.line, token.column)

    def leading_comment(self) -> str | None:
        parts = self._leading.get(self.pos)
        return " ".join(parts) if parts else None

    def trailing_comment(self, index: int) -> str | None:
        return self._trailing.get(index)

    def accept_keyword(self, *names: str) -> Token | None:
        if self.current.is_keyword(*names):
            return self.advance()
        return None

    def accept_op(self, *ops: str) -> Token | None:
        if self.current.is_op(*ops):
            return self.advance()
        return None

    def expect_op(self, op: str) -> Token:
        if not self.current.is_op(op):
            self.error(f"expected '{op}' but found '{self.current.value or 'end of file'}'")
            raise ParseError(op)
        return self.advance()

    def expect_keyword(self, *names: str) -> Token:
        if not self.current.is_keyword(*names):
            expected = " or ".join(names)
            self.error(f"expected {expected} but found '{self.current.value or 'end of file'}'")
            raise ParseError(names[0])
        return self.advance()

    def expect_identifier(self, what: str = "identifier") -> str:
        token = self.current
        if token.type in (TokenType.IDENT, TokenType.KEYWORD) and token.value:
            self.advance()
            return token.value
        self.error(f"expected {what} but found '{token.value or 'end of file'}'")
        raise ParseError(what)

    def error(self, message: str, token: Token | None = None, hint: str | None = None) -> None:
        token = token or self.current
        self.diagnostics.error(
            message,
            file=self.filename,
            line=token.line,
            column=token.column,
            code="PF110",
            hint=hint,
        )

    def recover(self, stop_keywords: frozenset[str] = _STATEMENT_TERMINATORS) -> None:
        """Skip forward to the next plausible statement boundary."""
        depth = 0
        while not self.at_end():
            token = self.current
            if token.type is TokenType.OPERATOR and token.value == ";" and depth == 0:
                self.advance()
                return
            if token.type is TokenType.OPERATOR and token.value in "([":
                depth += 1
            elif token.type is TokenType.OPERATOR and token.value in ")]":
                depth = max(0, depth - 1)
            elif token.type is TokenType.KEYWORD and token.upper in stop_keywords:
                return
            self.advance()

    # -- top level --------------------------------------------------------- #

    def parse_project(self, name: str = "project") -> Project:
        project = Project(name=name, source_files=[self.filename], span=self.span())
        while not self.at_end():
            token = self.current
            try:
                if token.is_keyword("TYPE"):
                    self.parse_type_section(project)
                elif token.is_keyword("VAR_GLOBAL"):
                    project.globals.append(self.parse_var_block())
                elif token.type is TokenType.KEYWORD and token.upper in _POU_KINDS:
                    project.pous.append(self.parse_pou())
                elif token.is_keyword("CONFIGURATION", "RESOURCE"):
                    self.skip_block("END_CONFIGURATION", "END_RESOURCE")
                elif token.is_op(";"):
                    self.advance()
                else:
                    self.error(f"unexpected '{token.value}' at top level")
                    self.advance()
                    self.recover()
            except ParseError:
                self.recover()
        return project

    def skip_block(self, *terminators: str) -> None:
        self.advance()
        while not self.at_end() and not self.current.is_keyword(*terminators):
            self.advance()
        if not self.at_end():
            self.advance()

    # -- declarations ------------------------------------------------------ #

    def parse_type_section(self, project: Project) -> None:
        self.expect_keyword("TYPE")
        while not self.at_end() and not self.current.is_keyword("END_TYPE"):
            comment = self.leading_comment()
            name = self.expect_identifier("type name")
            span = self.span()
            self.expect_op(":")
            if self.current.is_keyword("STRUCT"):
                project.structs.append(self.parse_struct(name, comment, span))
            elif self.current.is_op("("):
                project.enums.append(self.parse_enum(name, comment, span))
            else:
                # Alias type: TYPE Level : INT := 0; END_TYPE
                aliased = self.parse_type()
                initial = self.parse_initialiser()
                project.structs.append(
                    StructType(
                        name=name,
                        fields=[VarDecl(name="value", type=aliased, initial=initial, span=span)],
                        comment=comment or f"Alias for {aliased}.",
                        span=span,
                    )
                )
            self.accept_op(";")
        self.expect_keyword("END_TYPE")
        self.accept_op(";")

    def parse_struct(self, name: str, comment: str | None, span: SourceSpan) -> StructType:
        self.expect_keyword("STRUCT")
        fields: list[VarDecl] = []
        while not self.at_end() and not self.current.is_keyword("END_STRUCT"):
            fields.extend(self.parse_declaration_line())
        self.expect_keyword("END_STRUCT")
        return StructType(name=name, fields=fields, comment=comment, span=span)

    def parse_enum(self, name: str, comment: str | None, span: SourceSpan) -> EnumType:
        self.expect_op("(")
        members: list[tuple[str, int]] = []
        next_value = 0
        while not self.at_end() and not self.current.is_op(")"):
            member = self.expect_identifier("enumeration member")
            if self.accept_op(":="):
                value_expr = self.parse_expression()
                if isinstance(value_expr, Literal) and isinstance(value_expr.value, int):
                    next_value = value_expr.value
                else:
                    self.diagnostics.warning(
                        f"enumeration '{name}.{member}' has a non constant value; numbering continues",
                        file=self.filename,
                        line=self.current.line,
                        code="PF111",
                    )
            members.append((member, next_value))
            next_value += 1
            if not self.accept_op(","):
                break
        self.expect_op(")")
        base_type: IecType | None = None
        if self.accept_op(":"):
            base_type = self.parse_type()
        self.parse_initialiser()
        return EnumType(name=name, members=members, base_type=base_type, comment=comment, span=span)

    def parse_var_block(self) -> VarBlock:
        token = self.advance()
        kind = _VAR_KINDS.get(token.upper, VarKind.LOCAL)
        block = VarBlock(kind=kind, span=self.span(token))
        while True:
            if self.accept_keyword("CONSTANT"):
                block.constant = True
            elif self.accept_keyword("RETAIN", "PERSISTENT"):
                block.retain = True
            elif self.accept_keyword("NON_RETAIN"):
                block.retain = False
            else:
                break
        while not self.at_end() and not self.current.is_keyword("END_VAR"):
            try:
                block.declarations.extend(self.parse_declaration_line())
            except ParseError:
                self.recover(frozenset({"END_VAR"}))
        self.expect_keyword("END_VAR")
        self.accept_op(";")
        return block

    def parse_declaration_line(self) -> list[VarDecl]:
        """Parse ``a, b : INT := 0;`` into one declaration per name."""
        comment = self.leading_comment()
        span = self.span()
        names = [self.expect_identifier("variable name")]
        while self.accept_op(","):
            names.append(self.expect_identifier("variable name"))
        address: str | None = None
        if self.accept_keyword("AT"):
            if self.current.type is TokenType.ADDRESS:
                address = self.advance().value
            else:
                address = self.expect_identifier("direct address")
        self.expect_op(":")
        declared = self.parse_type()
        initial = self.parse_initialiser()
        semicolon_index = self.pos
        self.accept_op(";")
        trailing = self.trailing_comment(semicolon_index) or self.trailing_comment(semicolon_index - 1)
        return [
            VarDecl(
                name=name,
                type=declared,
                initial=initial,
                address=address if len(names) == 1 else None,
                comment=comment or trailing,
                span=span,
            )
            for name in names
        ]

    def parse_initialiser(self) -> Expr | None:
        if self.accept_op(":="):
            return self.parse_expression()
        return None

    def parse_type(self) -> IecType:
        if self.accept_keyword("ARRAY"):
            self.expect_op("[")
            dimensions: list[tuple[int, int]] = []
            while True:
                low = self.parse_bound()
                self.expect_op("..")
                high = self.parse_bound()
                dimensions.append((low, high))
                if not self.accept_op(","):
                    break
            self.expect_op("]")
            self.expect_keyword("OF")
            return array_of(self.parse_type(), tuple(dimensions))

        if self.current.is_keyword("STRUCT"):
            # Anonymous inline structure; flatten it to a derived placeholder.
            self.skip_block("END_STRUCT")
            return derived("STRUCT")

        name = self.expect_identifier("type name")
        upper = name.upper()
        if upper in ("STRING", "WSTRING"):
            length: int | None = None
            if self.accept_op("[") or self.accept_op("("):
                closing = "]" if self.tokens[self.pos - 1].value == "[" else ")"
                length = self.parse_bound()
                self.accept_op(closing)
            return string_of(upper, length)
        if upper in ELEMENTARY_TYPES:
            return elementary(upper)
        # A user defined type: struct, enumeration or function block instance.
        # The resolver decides later; encode it as an instance so that member
        # access keeps working, and let the resolver reclassify.
        return instance_of(name)

    def parse_bound(self) -> int:
        negative = bool(self.accept_op("-"))
        token = self.current
        if token.type is TokenType.INTEGER:
            self.advance()
            value = _integer_payload(token)
            return -value if negative else value
        if token.type is TokenType.IDENT:
            # Symbolic bound (a CONSTANT); keep the array open ended.
            self.advance()
            self.diagnostics.warning(
                f"array bound '{token.value}' is symbolic; using 0 for code generation",
                file=self.filename,
                line=token.line,
                code="PF112",
                hint="Replace the constant with a literal to get an exact array size.",
            )
            return 0
        self.error("expected an array bound")
        raise ParseError("bound")

    # -- POUs -------------------------------------------------------------- #

    def parse_pou(self) -> Pou:
        comment = self.leading_comment()
        token = self.advance()
        kind, terminator = _POU_KINDS[token.upper]
        span = self.span(token)
        name = self.expect_identifier("POU name")
        self.parse_pou_header_extensions(name)
        return_type: IecType | None = None
        if self.accept_op(":"):
            return_type = self.parse_type()
        if kind is PouKind.FUNCTION and return_type is None:
            self.diagnostics.warning(
                f"FUNCTION '{name}' has no return type",
                file=self.filename,
                line=token.line,
                code="PF113",
            )

        pou = Pou(
            name=name,
            kind=kind,
            return_type=return_type,
            source_language="ST",
            comment=comment,
            span=span,
        )
        while not self.at_end() and self.current.type is TokenType.KEYWORD and self.current.upper in _VAR_KINDS:
            pou.var_blocks.append(self.parse_var_block())
        pou.body = self.parse_statements(frozenset({terminator}))
        self.expect_keyword(terminator)
        self.accept_op(";")
        return pou

    def parse_pou_header_extensions(self, pou_name: str) -> None:
        """Consume vendor extensions such as ``EXTENDS Base IMPLEMENTS IFoo``.

        plcforge does not model inheritance, so the relationship is reported and
        the members are then translated as if the block were standalone.
        """
        while self.current.type is TokenType.IDENT and self.current.upper in ("EXTENDS", "IMPLEMENTS"):
            keyword = self.advance().upper
            names = [self.expect_identifier("type name")]
            while self.accept_op(","):
                names.append(self.expect_identifier("type name"))
            self.diagnostics.warning(
                f"'{pou_name}' {keyword.lower()} {', '.join(names)}; "
                "inherited members are not carried over",
                file=self.filename,
                line=self.current.line,
                code="PF115",
                hint="Flatten the hierarchy or declare the inherited members explicitly.",
            )

    # -- statements -------------------------------------------------------- #

    def parse_statements(self, terminators: frozenset[str]) -> list[Stmt]:
        body: list[Stmt] = []
        while not self.at_end():
            token = self.current
            if token.type is TokenType.KEYWORD and token.upper in terminators:
                break
            if token.is_op(";"):
                self.advance()
                continue
            try:
                statement = self.parse_statement()
            except ParseError:
                self.recover(terminators | _STATEMENT_TERMINATORS)
                continue
            if statement is not None:
                body.append(statement)
        return body

    def parse_statement(self) -> Stmt | None:
        comment = self.leading_comment()
        token = self.current
        start_index = self.pos

        if token.is_keyword("IF"):
            statement: Stmt = self.parse_if()
        elif token.is_keyword("CASE"):
            statement = self.parse_case()
        elif token.is_keyword("FOR"):
            statement = self.parse_for()
        elif token.is_keyword("WHILE"):
            statement = self.parse_while()
        elif token.is_keyword("REPEAT"):
            statement = self.parse_repeat()
        elif token.is_keyword("EXIT"):
            self.advance()
            self.accept_op(";")
            statement = Exit(span=self.span(token))
        elif token.is_keyword("CONTINUE"):
            self.advance()
            self.accept_op(";")
            statement = Continue(span=self.span(token))
        elif token.is_keyword("RETURN"):
            self.advance()
            self.accept_op(";")
            statement = Return(value=None, span=self.span(token))
        else:
            statement = self.parse_simple_statement()

        if statement is not None:
            trailing = None
            for index in range(self.pos - 1, start_index - 1, -1):
                trailing = self.trailing_comment(index)
                if trailing:
                    break
            statement.comment = comment or trailing
        return statement

    def parse_simple_statement(self) -> Stmt:
        token = self.current
        span = self.span(token)
        target = self.parse_expression()
        if self.accept_op(":="):
            value = self.parse_expression()
            self.accept_op(";")
            return Assign(target=target, value=value, span=span)
        if self.accept_op("=>"):
            # Output assignment used outside a call; treat as a plain assignment.
            value = self.parse_expression()
            self.accept_op(";")
            return Assign(target=value, value=target, span=span)
        self.accept_op(";")
        if isinstance(target, Call):
            return Invoke(call=target, span=span)
        if isinstance(target, VarRef):
            # A bare identifier is a parameterless call, e.g. ``MotorControl;``.
            return Invoke(call=Call(callee=target, args=[], span=span), span=span)
        self.error("expression statement has no effect", token)
        raise ParseError("statement")

    def parse_if(self) -> If:
        token = self.expect_keyword("IF")
        branches: list[Branch] = []
        condition = self.parse_expression()
        self.expect_keyword("THEN")
        body = self.parse_statements(frozenset({"ELSIF", "ELSE", "END_IF"}))
        branches.append(Branch(condition=condition, body=body))
        orelse: list[Stmt] = []
        while self.accept_keyword("ELSIF"):
            elsif_condition = self.parse_expression()
            self.expect_keyword("THEN")
            elsif_body = self.parse_statements(frozenset({"ELSIF", "ELSE", "END_IF"}))
            branches.append(Branch(condition=elsif_condition, body=elsif_body))
        if self.accept_keyword("ELSE"):
            orelse = self.parse_statements(frozenset({"END_IF"}))
        self.expect_keyword("END_IF")
        self.accept_op(";")
        return If(branches=branches, orelse=orelse, span=self.span(token))

    def parse_case(self) -> Case:
        token = self.expect_keyword("CASE")
        selector = self.parse_expression()
        self.expect_keyword("OF")
        branches: list[CaseBranch] = []
        orelse: list[Stmt] = []
        while not self.at_end() and not self.current.is_keyword("END_CASE", "ELSE"):
            labels = self.parse_case_labels()
            self.expect_op(":")
            body = self.parse_case_body()
            branches.append(CaseBranch(labels=labels, body=body))
        if self.accept_keyword("ELSE"):
            orelse = self.parse_statements(frozenset({"END_CASE"}))
        self.expect_keyword("END_CASE")
        self.accept_op(";")
        return Case(selector=selector, branches=branches, orelse=orelse, span=self.span(token))

    def parse_case_labels(self) -> list[CaseLabel]:
        labels: list[CaseLabel] = []
        while True:
            low = self.parse_case_label_value()
            high = self.parse_case_label_value() if self.accept_op("..") else None
            labels.append(CaseLabel(low=low, high=high))
            if not self.accept_op(","):
                break
        return labels

    def parse_case_label_value(self) -> Expr:
        token = self.current
        span = self.span(token)
        negative = bool(self.accept_op("-"))
        token = self.current
        if token.type is TokenType.INTEGER:
            self.advance()
            value = _integer_payload(token)
            return Literal(
                value=-value if negative else value,
                type=elementary("DINT"),
                text=token.value,
                span=span,
            )
        if token.type is TokenType.IDENT:
            self.advance()
            name = token.value
            if self.accept_op("#"):
                # Qualified enumeration label such as STATE#IDLE.
                name = self.expect_identifier("enumeration member")
            return VarRef(name=name, span=span)
        if token.is_keyword("TRUE", "FALSE"):
            self.advance()
            return Literal(value=token.upper == "TRUE", type=BOOL, text=token.value, span=span)
        self.error("expected a CASE label")
        raise ParseError("case label")

    def parse_case_body(self) -> list[Stmt]:
        """Parse statements until the next label, ``ELSE`` or ``END_CASE``."""
        body: list[Stmt] = []
        while not self.at_end():
            if self.current.is_keyword("END_CASE", "ELSE"):
                break
            if self._at_case_label():
                break
            if self.current.is_op(";"):
                self.advance()
                continue
            try:
                statement = self.parse_statement()
            except ParseError:
                self.recover(frozenset({"END_CASE", "ELSE"}))
                continue
            if statement is not None:
                body.append(statement)
        return body

    def _at_case_label(self) -> bool:
        """Look ahead for ``<constant> [, <constant>] :`` which starts a branch."""
        index = self.pos
        seen_value = False
        while index < len(self.tokens):
            token = self.tokens[index]
            if token.type in (TokenType.INTEGER, TokenType.IDENT) or token.is_keyword("TRUE", "FALSE"):
                if seen_value:
                    return False
                seen_value = True
                index += 1
                continue
            if token.is_op("-", ",", "..", "#"):
                index += 1
                if token.value in (",", ".."):
                    seen_value = False
                continue
            if token.is_op(":"):
                return seen_value
            return False
        return False

    def parse_for(self) -> For:
        token = self.expect_keyword("FOR")
        variable = self.parse_primary()
        self.expect_op(":=")
        start = self.parse_expression()
        self.expect_keyword("TO")
        stop = self.parse_expression()
        step = self.parse_expression() if self.accept_keyword("BY") else None
        self.expect_keyword("DO")
        body = self.parse_statements(frozenset({"END_FOR"}))
        self.expect_keyword("END_FOR")
        self.accept_op(";")
        return For(variable=variable, start=start, stop=stop, step=step, body=body, span=self.span(token))

    def parse_while(self) -> While:
        token = self.expect_keyword("WHILE")
        condition = self.parse_expression()
        self.expect_keyword("DO")
        body = self.parse_statements(frozenset({"END_WHILE"}))
        self.expect_keyword("END_WHILE")
        self.accept_op(";")
        return While(condition=condition, body=body, span=self.span(token))

    def parse_repeat(self) -> Repeat:
        token = self.expect_keyword("REPEAT")
        body = self.parse_statements(frozenset({"UNTIL"}))
        self.expect_keyword("UNTIL")
        condition = self.parse_expression()
        self.expect_keyword("END_REPEAT")
        self.accept_op(";")
        return Repeat(body=body, condition=condition, span=self.span(token))

    # -- expressions ------------------------------------------------------- #

    def parse_expression(self, level: int = 0) -> Expr:
        if level >= len(_PRECEDENCE):
            return self.parse_power()
        left = self.parse_expression(level + 1)
        while True:
            token = self.current
            operator = self._binary_operator(token, _PRECEDENCE[level])
            if operator is None:
                return left
            self.advance()
            right = self.parse_expression(level + 1)
            left = Binary(op=operator, left=left, right=right, span=self.span(token))

    @staticmethod
    def _binary_operator(token: Token, accepted: tuple[str, ...]) -> str | None:
        if token.type is TokenType.KEYWORD and token.upper in accepted:
            return token.upper
        if token.type is TokenType.OPERATOR and token.value in accepted:
            return "AND" if token.value == "&" else token.value
        return None

    def parse_power(self) -> Expr:
        base = self.parse_unary()
        token = self.current
        if token.is_op("**"):
            self.advance()
            exponent = self.parse_power()  # right associative
            return Binary(op="**", left=base, right=exponent, span=self.span(token))
        return base

    def parse_unary(self) -> Expr:
        token = self.current
        if token.is_keyword("NOT"):
            self.advance()
            return Unary(op="NOT", operand=self.parse_unary(), span=self.span(token))
        if token.is_op("-"):
            self.advance()
            return Unary(op="-", operand=self.parse_unary(), span=self.span(token))
        if token.is_op("+"):
            self.advance()
            return self.parse_unary()
        return self.parse_postfix()

    def parse_postfix(self) -> Expr:
        expr = self.parse_primary()
        while True:
            token = self.current
            if token.is_op("."):
                self.advance()
                field_name = self.expect_identifier("member name")
                expr = Member(base=expr, field_name=field_name, span=self.span(token))
            elif token.is_op("["):
                self.advance()
                indices = [self.parse_expression()]
                while self.accept_op(","):
                    indices.append(self.parse_expression())
                self.expect_op("]")
                expr = Index(base=expr, indices=indices, span=self.span(token))
            elif token.is_op("(") and isinstance(expr, (VarRef, Member, Index)):
                expr = Call(callee=expr, args=self.parse_arguments(), span=self.span(token))
            elif token.is_op("^"):
                self.advance()  # pointer dereference; the IR keeps the base
            else:
                return expr

    def parse_arguments(self) -> list[Argument]:
        self.expect_op("(")
        args: list[Argument] = []
        if self.accept_op(")"):
            return args
        while True:
            name: str | None = None
            is_output = False
            if (
                self.current.type in (TokenType.IDENT, TokenType.KEYWORD)
                and (self.peek(1).is_op(":=") or self.peek(1).is_op("=>"))
            ):
                name = self.advance().value
                is_output = self.advance().value == "=>"
            value = self.parse_expression()
            args.append(Argument(value=value, name=name, is_output=is_output))
            if not self.accept_op(","):
                break
        self.expect_op(")")
        return args

    def parse_primary(self) -> Expr:
        token = self.current
        span = self.span(token)

        if token.type is TokenType.INTEGER:
            self.advance()
            return Literal(value=_integer_payload(token), type=elementary("DINT"), text=token.value, span=span)
        if token.type is TokenType.REAL:
            self.advance()
            return Literal(value=token.payload, type=elementary("REAL"), text=token.value, span=span)  # type: ignore[arg-type]
        if token.type is TokenType.DURATION:
            self.advance()
            return Literal(value=token.payload, type=elementary("TIME"), text=token.value, span=span)  # type: ignore[arg-type]
        if token.type is TokenType.STRING:
            self.advance()
            return Literal(value=token.payload, type=string_of("STRING"), text=token.value, span=span)  # type: ignore[arg-type]
        if token.type is TokenType.ADDRESS:
            self.advance()
            return VarRef(name=token.value, span=span)
        if token.is_keyword("TRUE", "FALSE"):
            self.advance()
            return Literal(value=token.upper == "TRUE", type=BOOL, text=token.value, span=span)
        if token.is_op("("):
            self.advance()
            if self._looks_like_struct_initialiser():
                return self._finish_struct_initialiser(span)
            expr = self.parse_expression()
            self.expect_op(")")
            return expr
        if token.is_op("["):
            return self.parse_array_initialiser()
        if token.type is TokenType.IDENT or (
            token.type is TokenType.KEYWORD and token.upper in ("MOD", "NOT", "AND", "OR", "XOR")
        ):
            self.advance()
            return VarRef(name=token.value, span=span)

        self.error(f"unexpected '{token.value or 'end of file'}' in expression")
        raise ParseError("expression")

    def _looks_like_struct_initialiser(self) -> bool:
        return (
            self.current.type in (TokenType.IDENT, TokenType.KEYWORD)
            and self.peek(1).is_op(":=")
        )

    def _finish_struct_initialiser(self, span: SourceSpan) -> Aggregate:
        elements: list[Argument] = []
        while True:
            name = self.expect_identifier("field name")
            self.expect_op(":=")
            elements.append(Argument(value=self.parse_expression(), name=name))
            if not self.accept_op(","):
                break
        self.expect_op(")")
        return Aggregate(elements=elements, is_array=False, span=span)

    def parse_array_initialiser(self) -> Aggregate:
        token = self.expect_op("[")
        span = self.span(token)
        elements: list[Argument] = []
        if self.accept_op("]"):
            return Aggregate(elements=elements, is_array=True, span=span)
        while True:
            value = self.parse_expression()
            # Repetition form: ``10(0)`` expands to ten copies of ``0``.
            if (
                isinstance(value, Literal)
                and isinstance(value.value, int)
                and self.tokens[self.pos - 1].type is TokenType.INTEGER
                and self.current.is_op("(")
            ):
                count = int(value.value)
                self.advance()
                repeated = self.parse_expression()
                self.expect_op(")")
                if count > _MAX_REPEAT:
                    self.diagnostics.warning(
                        f"array initialiser repeats {count} times; truncated to {_MAX_REPEAT}",
                        file=self.filename,
                        line=token.line,
                        code="PF114",
                    )
                    count = _MAX_REPEAT
                elements.extend(Argument(value=repeated) for _ in range(max(count, 0)))
            else:
                elements.append(Argument(value=value))
            if not self.accept_op(","):
                break
        self.expect_op("]")
        return Aggregate(elements=elements, is_array=True, span=span)


def parse_source(
    source: str,
    filename: str = "<st>",
    diagnostics: DiagnosticBag | None = None,
    project_name: str | None = None,
) -> Project:
    """Parse ST source text into a :class:`~plcforge.ir.nodes.Project`."""
    parser = Parser(source, filename, diagnostics)
    return parser.parse_project(project_name or "project")


def parse_expression_text(
    text: str,
    filename: str = "<expression>",
    diagnostics: DiagnosticBag | None = None,
) -> Expr:
    """Parse a bare expression, as found in PLCopen ``<expression>`` elements.

    Graphical editors store the contents of an input variable box as ST text, so
    reusing the expression parser keeps ``16#FF``, ``NOT enable`` and
    ``tank.level`` working in ladder diagrams without a second grammar.
    """
    parser = Parser(text, filename, diagnostics)
    try:
        return parser.parse_expression()
    except ParseError:
        return VarRef(name=text.strip(), span=SourceSpan(filename, 1, 1))


def parse_file(path: str, diagnostics: DiagnosticBag | None = None) -> Project:
    import pathlib

    file = pathlib.Path(path)
    return parse_source(
        file.read_text(encoding="utf-8-sig"),
        str(file),
        diagnostics,
        project_name=file.stem,
    )
