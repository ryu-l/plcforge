"""Tokeniser for Structured Text / SCL.

Handles the notation that shows up in real projects rather than only the subset
in the standard: nested ``(* *)`` comments, ``//`` line comments, based literals
(``16#FF``, ``2#1010_1010``), duration literals (``T#1h30m``), typed literals
(``INT#5``), string literals with ``$`` escapes, direct addresses (``%IX0.0``)
and attribute pragmas (``{attribute 'hidden'}``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto

from ...diagnostics import DiagnosticBag

__all__ = ["KEYWORDS", "Lexer", "Token", "TokenType", "tokenize"]


class TokenType(Enum):
    IDENT = auto()
    KEYWORD = auto()
    INTEGER = auto()
    REAL = auto()
    DURATION = auto()
    STRING = auto()
    ADDRESS = auto()
    OPERATOR = auto()
    PRAGMA = auto()
    COMMENT = auto()
    EOF = auto()


KEYWORDS = frozenset(
    ["PROGRAM", "END_PROGRAM", "FUNCTION", "END_FUNCTION", "FUNCTION_BLOCK", "END_FUNCTION_BLOCK", "CONFIGURATION", "END_CONFIGURATION", "RESOURCE", "END_RESOURCE", "TASK", "VAR", "VAR_INPUT", "VAR_OUTPUT", "VAR_IN_OUT", "VAR_TEMP", "VAR_GLOBAL", "VAR_EXTERNAL", "VAR_ACCESS", "VAR_STAT", "VAR_CONFIG", "VAR_INST", "END_VAR", "TYPE", "END_TYPE", "STRUCT", "END_STRUCT", "ARRAY", "OF", "AT", "CONSTANT", "RETAIN", "NON_RETAIN", "PERSISTENT", "IF", "THEN", "ELSIF", "ELSE", "END_IF", "CASE", "END_CASE", "FOR", "TO", "BY", "DO", "END_FOR", "WHILE", "END_WHILE", "REPEAT", "UNTIL", "END_REPEAT", "EXIT", "CONTINUE", "RETURN", "AND", "OR", "XOR", "NOT", "MOD", "TRUE", "FALSE"]
)

_OPERATORS = [
    ":=",
    "=>",
    "<=",
    ">=",
    "<>",
    "**",
    "..",
    "+",
    "-",
    "*",
    "/",
    "<",
    ">",
    "=",
    "(",
    ")",
    "[",
    "]",
    ",",
    ";",
    ":",
    ".",
    "^",
    "&",
    "#",
    "%",
]

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
_ADDRESS_RE = re.compile(r"%[IQM][XBWDL]?[0-9]+(?:\.[0-9]+)*", re.IGNORECASE)
_BASED_RE = re.compile(r"(2|8|16)#([0-9A-Fa-f_]+)")
_DURATION_RE = re.compile(
    r"(?:T|TIME|LT|LTIME)#(-?)((?:[0-9_]+(?:\.[0-9_]+)?(?:d|h|ms|us|ns|m|s))+)",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"[0-9][0-9_]*(?:\.[0-9_]+)?(?:[eE][+-]?[0-9]+)?")
_DATE_RE = re.compile(
    r"(?:DATE|D|DATE_AND_TIME|DT|TIME_OF_DAY|TOD)#[0-9][0-9A-Za-z\-:.\_]*",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Token:
    type: TokenType
    value: str
    line: int
    column: int
    #: Normalised payload: int/float value, seconds for durations, decoded string.
    payload: object = None

    @property
    def upper(self) -> str:
        return self.value.upper()

    def is_keyword(self, *names: str) -> bool:
        return self.type is TokenType.KEYWORD and self.upper in {n.upper() for n in names}

    def is_op(self, *ops: str) -> bool:
        return self.type is TokenType.OPERATOR and self.value in ops

    def __str__(self) -> str:  # pragma: no cover - debugging helper
        return f"{self.type.name}({self.value!r})@{self.line}:{self.column}"


_DURATION_UNITS = {
    "d": 86400.0,
    "h": 3600.0,
    "m": 60.0,
    "s": 1.0,
    "ms": 1e-3,
    "us": 1e-6,
    "ns": 1e-9,
}
_UNIT_RE = re.compile(r"([0-9_]+(?:\.[0-9_]+)?)(d|h|ms|us|ns|m|s)", re.IGNORECASE)


def parse_duration(text: str) -> float:
    """Convert an IEC duration literal such as ``T#1h30m`` into seconds."""
    match = _DURATION_RE.fullmatch(text)
    if match is None:
        raise ValueError(f"not a duration literal: {text!r}")
    sign = -1.0 if match.group(1) == "-" else 1.0
    total = 0.0
    for amount, unit in _UNIT_RE.findall(match.group(2)):
        total += float(amount.replace("_", "")) * _DURATION_UNITS[unit.lower()]
    return sign * total


def decode_string(raw: str, quote: str) -> str:
    """Decode ``$`` escapes from an ST string literal body."""
    out: list[str] = []
    index = 0
    escapes = {
        "$": "$",
        "'": "'",
        '"': '"',
        "L": "\n",
        "l": "\n",
        "N": "\n",
        "n": "\n",
        "P": "\f",
        "p": "\f",
        "R": "\r",
        "r": "\r",
        "T": "\t",
        "t": "\t",
    }
    while index < len(raw):
        char = raw[index]
        if char != "$":
            out.append(char)
            index += 1
            continue
        index += 1
        if index >= len(raw):
            break
        marker = raw[index]
        if marker in escapes:
            out.append(escapes[marker])
            index += 1
        elif len(raw) >= index + 2 and all(c in "0123456789abcdefABCDEF" for c in raw[index : index + 2]):
            out.append(chr(int(raw[index : index + 2], 16)))
            index += 2
        else:
            out.append(marker)
            index += 1
    return "".join(out)


class Lexer:
    """Converts ST source text into a token list."""

    def __init__(
        self,
        source: str,
        filename: str = "<st>",
        diagnostics: DiagnosticBag | None = None,
        keep_comments: bool = True,
    ) -> None:
        self.source = source
        self.filename = filename
        self.diagnostics = diagnostics if diagnostics is not None else DiagnosticBag()
        self.keep_comments = keep_comments
        self.pos = 0
        self.line = 1
        self.column = 1

    # -- helpers ----------------------------------------------------------- #

    def _peek(self, offset: int = 0) -> str:
        index = self.pos + offset
        return self.source[index] if index < len(self.source) else ""

    def _advance(self, count: int = 1) -> str:
        chunk = self.source[self.pos : self.pos + count]
        for char in chunk:
            if char == "\n":
                self.line += 1
                self.column = 1
            else:
                self.column += 1
        self.pos += count
        return chunk

    def _match(self, pattern: re.Pattern[str]) -> re.Match[str] | None:
        return pattern.match(self.source, self.pos)

    # -- main loop --------------------------------------------------------- #

    def tokenize(self) -> list[Token]:
        tokens: list[Token] = []
        while self.pos < len(self.source):
            char = self._peek()
            if char in " \t\r\n":
                self._advance()
                continue
            line, column = self.line, self.column

            # Comments -------------------------------------------------- #
            if char == "/" and self._peek(1) == "/":
                text = self._line_comment()
                if self.keep_comments:
                    tokens.append(Token(TokenType.COMMENT, text, line, column))
                continue
            if char == "(" and self._peek(1) == "*":
                text = self._block_comment()
                if self.keep_comments:
                    tokens.append(Token(TokenType.COMMENT, text, line, column))
                continue
            if char == "{":
                text = self._pragma()
                tokens.append(Token(TokenType.PRAGMA, text, line, column))
                continue

            # Literals -------------------------------------------------- #
            match = self._match(_DURATION_RE)
            if match:
                text = self._advance(len(match.group(0)))
                tokens.append(Token(TokenType.DURATION, text, line, column, parse_duration(text)))
                continue
            match = self._match(_DATE_RE)
            if match:
                text = self._advance(len(match.group(0)))
                tokens.append(Token(TokenType.STRING, text, line, column, text))
                continue
            match = self._match(_ADDRESS_RE)
            if match:
                text = self._advance(len(match.group(0)))
                tokens.append(Token(TokenType.ADDRESS, text, line, column, text))
                continue
            match = self._match(_BASED_RE)
            if match:
                text = self._advance(len(match.group(0)))
                base = int(match.group(1))
                tokens.append(
                    Token(TokenType.INTEGER, text, line, column, int(match.group(2).replace("_", ""), base))
                )
                continue
            if char.isdigit():
                tokens.append(self._number(line, column))
                continue
            if char in "'\"":
                tokens.append(self._string(line, column))
                continue

            # Identifiers and keywords ---------------------------------- #
            match = self._match(_IDENT_RE)
            if match:
                text = self._advance(len(match.group(0)))
                upper = text.upper()
                # Typed literal such as INT#5 or WORD#16#FF.
                if self._peek() == "#" and upper not in KEYWORDS:
                    self._advance()
                    tokens.append(self._typed_literal(upper, line, column))
                    continue
                kind = TokenType.KEYWORD if upper in KEYWORDS else TokenType.IDENT
                tokens.append(Token(kind, text, line, column))
                continue

            # Operators -------------------------------------------------- #
            operator = next((op for op in _OPERATORS if self.source.startswith(op, self.pos)), None)
            if operator is not None:
                self._advance(len(operator))
                tokens.append(Token(TokenType.OPERATOR, operator, line, column))
                continue

            self.diagnostics.error(
                f"unexpected character {char!r}",
                file=self.filename,
                line=line,
                column=column,
                code="PF101",
            )
            self._advance()

        tokens.append(Token(TokenType.EOF, "", self.line, self.column))
        return tokens

    # -- individual scanners ----------------------------------------------- #

    def _line_comment(self) -> str:
        start = self.pos
        while self.pos < len(self.source) and self._peek() != "\n":
            self._advance()
        return self.source[start : self.pos]

    def _block_comment(self) -> str:
        start = self.pos
        depth = 0
        while self.pos < len(self.source):
            if self._peek() == "(" and self._peek(1) == "*":
                depth += 1
                self._advance(2)
            elif self._peek() == "*" and self._peek(1) == ")":
                depth -= 1
                self._advance(2)
                if depth == 0:
                    break
            else:
                self._advance()
        else:
            self.diagnostics.error(
                "unterminated block comment",
                file=self.filename,
                line=self.line,
                column=self.column,
                code="PF102",
            )
        return self.source[start : self.pos]

    def _pragma(self) -> str:
        start = self.pos
        while self.pos < len(self.source) and self._peek() != "}":
            self._advance()
        self._advance()  # closing brace
        return self.source[start : self.pos]

    def _number(self, line: int, column: int) -> Token:
        match = self._match(_NUMBER_RE)
        assert match is not None
        text = self._advance(len(match.group(0)))
        cleaned = text.replace("_", "")
        if "." in cleaned or "e" in cleaned.lower():
            return Token(TokenType.REAL, text, line, column, float(cleaned))
        return Token(TokenType.INTEGER, text, line, column, int(cleaned))

    def _typed_literal(self, type_name: str, line: int, column: int) -> Token:
        """Scan the value part of ``TYPE#value``."""
        match = self._match(_BASED_RE)
        if match:
            text = self._advance(len(match.group(0)))
            value = int(match.group(2).replace("_", ""), int(match.group(1)))
            return Token(TokenType.INTEGER, f"{type_name}#{text}", line, column, value)
        if self._peek() in "+-" or self._peek().isdigit():
            sign = self._advance() if self._peek() in "+-" else ""
            number = self._number(line, column)
            payload = number.payload
            if sign == "-" and isinstance(payload, (int, float)):
                payload = -payload
            return Token(number.type, f"{type_name}#{sign}{number.value}", line, column, payload)
        match = self._match(_IDENT_RE)
        if match:
            text = self._advance(len(match.group(0)))
            if text.upper() in ("TRUE", "FALSE"):
                return Token(TokenType.KEYWORD, text, line, column)
            # Qualified enumeration value, e.g. STATE#RUNNING.
            return Token(TokenType.IDENT, text, line, column)
        self.diagnostics.error(
            f"malformed typed literal after '{type_name}#'",
            file=self.filename,
            line=line,
            column=column,
            code="PF103",
        )
        return Token(TokenType.INTEGER, f"{type_name}#0", line, column, 0)

    def _string(self, line: int, column: int) -> Token:
        quote = self._advance()
        start = self.pos
        while self.pos < len(self.source):
            char = self._peek()
            if char == "$":
                self._advance(2)
                continue
            if char == quote:
                break
            if char == "\n":
                self.diagnostics.error(
                    "unterminated string literal",
                    file=self.filename,
                    line=line,
                    column=column,
                    code="PF104",
                )
                break
            self._advance()
        raw = self.source[start : self.pos]
        if self._peek() == quote:
            self._advance()
        return Token(TokenType.STRING, f"{quote}{raw}{quote}", line, column, decode_string(raw, quote))


def tokenize(
    source: str,
    filename: str = "<st>",
    diagnostics: DiagnosticBag | None = None,
    keep_comments: bool = True,
) -> list[Token]:
    return Lexer(source, filename, diagnostics, keep_comments).tokenize()
