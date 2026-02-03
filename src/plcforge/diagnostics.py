"""Diagnostics shared by every frontend and backend.

A translation never raises on the first problem it meets.  It collects
diagnostics, keeps going where it can, and lets the caller decide whether the
result is good enough.  That matters for real PLC projects, where one
unsupported block should not hide the other two hundred that convert cleanly.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from enum import Enum
from typing import TextIO


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"
    NOTE = "note"

    @property
    def rank(self) -> int:
        return {"error": 2, "warning": 1, "note": 0}[self.value]


@dataclass(frozen=True)
class Diagnostic:
    severity: Severity
    message: str
    file: str = "<unknown>"
    line: int = 0
    column: int = 0
    code: str = ""
    hint: str | None = None

    def format(self, colour: bool = False) -> str:
        location = self.file
        if self.line:
            location = f"{location}:{self.line}:{self.column}" if self.column else f"{location}:{self.line}"
        label = self.severity.value
        if colour:
            tint = {"error": "\033[31m", "warning": "\033[33m", "note": "\033[36m"}[label]
            label = f"{tint}{label}\033[0m"
        code = f" [{self.code}]" if self.code else ""
        text = f"{location}: {label}{code}: {self.message}"
        if self.hint:
            text += f"\n    hint: {self.hint}"
        return text


class TranslationError(Exception):
    """Raised when translation cannot produce any usable output."""

    def __init__(self, message: str, diagnostics: Iterable[Diagnostic] = ()) -> None:
        super().__init__(message)
        self.diagnostics = list(diagnostics)


@dataclass
class DiagnosticBag:
    """Ordered collection of diagnostics produced during one translation."""

    items: list[Diagnostic] = field(default_factory=list)

    def add(
        self,
        severity: Severity,
        message: str,
        *,
        span: object | None = None,
        file: str = "<unknown>",
        line: int = 0,
        column: int = 0,
        code: str = "",
        hint: str | None = None,
    ) -> Diagnostic:
        if span is not None:
            file = getattr(span, "file", file)
            line = getattr(span, "line", line)
            column = getattr(span, "column", column)
        diagnostic = Diagnostic(severity, message, file, line, column, code, hint)
        self.items.append(diagnostic)
        return diagnostic

    def error(self, message: str, **kwargs: object) -> Diagnostic:
        return self.add(Severity.ERROR, message, **kwargs)  # type: ignore[arg-type]

    def warning(self, message: str, **kwargs: object) -> Diagnostic:
        return self.add(Severity.WARNING, message, **kwargs)  # type: ignore[arg-type]

    def note(self, message: str, **kwargs: object) -> Diagnostic:
        return self.add(Severity.NOTE, message, **kwargs)  # type: ignore[arg-type]

    def extend(self, other: DiagnosticBag | Iterable[Diagnostic]) -> None:
        self.items.extend(other.items if isinstance(other, DiagnosticBag) else other)

    def of(self, severity: Severity) -> list[Diagnostic]:
        return [d for d in self.items if d.severity is severity]

    @property
    def errors(self) -> list[Diagnostic]:
        return self.of(Severity.ERROR)

    @property
    def warnings(self) -> list[Diagnostic]:
        return self.of(Severity.WARNING)

    @property
    def has_errors(self) -> bool:
        return any(d.severity is Severity.ERROR for d in self.items)

    def raise_if_errors(self, message: str = "translation failed") -> None:
        if self.has_errors:
            raise TranslationError(message, self.errors)

    def report(self, stream: TextIO | None = None, colour: bool | None = None) -> None:
        stream = stream or sys.stderr
        if colour is None:
            colour = hasattr(stream, "isatty") and stream.isatty()
        for diagnostic in self.items:
            print(diagnostic.format(colour), file=stream)

    def summary(self) -> str:
        errors, warnings = len(self.errors), len(self.warnings)
        parts = []
        if errors:
            parts.append(f"{errors} error{'s' if errors != 1 else ''}")
        if warnings:
            parts.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
        return ", ".join(parts) if parts else "no issues"

    def __iter__(self) -> Iterator[Diagnostic]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __bool__(self) -> bool:
        return bool(self.items)
