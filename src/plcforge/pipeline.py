"""The translation pipeline.

    sources -> frontend -> IR -> link -> type resolution -> backend -> files

Everything the CLI does goes through :func:`translate`, which keeps the command
line thin and makes the same path available to anyone embedding plcforge in
their own tooling.
"""

from __future__ import annotations

import dataclasses
import pathlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .diagnostics import DiagnosticBag, TranslationError
from .ir.link import link_types
from .ir.nodes import Project
from .ir.validate import resolve_types

__all__ = [
    "SOURCE_EXTENSIONS",
    "TranslationResult",
    "available_targets",
    "detect_frontend",
    "load_sources",
    "prepare",
    "translate",
]

#: File extension to frontend name.  ``.st``/``.scl`` and the PLCopen XML
#: interchange format cover every export path the supported tools offer.
SOURCE_EXTENSIONS: dict[str, str] = {
    ".st": "st",
    ".scl": "st",
    ".iecst": "st",
    ".exp": "st",
    ".xml": "plcopen",
    ".plcopen": "plcopen",
    ".py": "python",
}


@dataclass
class TranslationResult:
    """What one run of the pipeline produced."""

    project: Project
    diagnostics: DiagnosticBag
    files: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.diagnostics.has_errors

    def write(self, destination: pathlib.Path, dry_run: bool = False) -> list[pathlib.Path]:
        """Write every generated file below *destination*."""
        written: list[pathlib.Path] = []
        for relative, content in sorted(self.files.items()):
            path = destination / relative
            if not dry_run:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            written.append(path)
        return written


def detect_frontend(path: pathlib.Path) -> str | None:
    """Guess the frontend for *path* from its extension."""
    return SOURCE_EXTENSIONS.get(path.suffix.lower())


def _read_frontend(name: str) -> Callable[[pathlib.Path, DiagnosticBag], Project]:
    if name == "st":
        from .frontends.st.parser import parse_file as parse_st

        return lambda path, bag: parse_st(str(path), bag)
    if name == "plcopen":
        from .frontends.plcopen.reader import parse_file as parse_plcopen

        return lambda path, bag: parse_plcopen(str(path), bag)
    if name == "python":
        from .frontends.python.lifter import lift_file

        return lambda path, bag: lift_file(str(path), bag)
    raise TranslationError(f"unknown frontend {name!r}")


def load_sources(
    paths: Sequence[pathlib.Path],
    diagnostics: DiagnosticBag,
    project_name: str | None = None,
    frontend: str | None = None,
) -> Project:
    """Parse every path (files or directories) into one project."""
    files = list(_expand(paths))
    if not files:
        raise TranslationError("no source files found", diagnostics.items)

    combined = Project(name=project_name or files[0].stem)
    for path in files:
        kind = frontend or detect_frontend(path)
        if kind is None:
            diagnostics.warning(
                f"skipping '{path.name}': unknown extension",
                file=str(path),
                code="PF001",
                hint=f"Supported: {', '.join(sorted(SOURCE_EXTENSIONS))}",
            )
            continue
        parse = _read_frontend(kind)
        combined.merge(parse(path, diagnostics))
    if not combined.pous:
        diagnostics.error("no POUs were parsed from the given sources", code="PF002")
    return combined


def _expand(paths: Iterable[pathlib.Path]) -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for path in paths:
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and child.suffix.lower() in SOURCE_EXTENSIONS:
                    found.append(child)
        elif path.is_file():
            found.append(path)
        else:
            raise TranslationError(f"no such file or directory: {path}")
    return found


def prepare(project: Project, diagnostics: DiagnosticBag) -> Project:
    """Resolve user type references and annotate expressions with their types."""
    link_types(project, diagnostics)
    resolve_types(project, diagnostics)
    return project


def available_targets() -> dict[str, str]:
    """Backend name to one line description, for ``--help`` and ``plcforge info``."""
    return {
        "python": "Python 3.10+ module using the bundled scan-cycle runtime",
        "cpp": "Header-only C++17 translation with an equivalent runtime",
        "st": "Structured Text, for round-tripping and for graphical sources",
    }


def _accepted(options_class: type, supplied: dict[str, Any]) -> dict[str, Any]:
    """Keep only the options this backend understands.

    The CLI passes every backend flag it knows about, which keeps the command
    handlers free of per-target branching; each backend picks out its own.
    """
    fields = {f.name for f in dataclasses.fields(options_class)}
    return {name: value for name, value in supplied.items() if name in fields}


def translate(
    paths: Sequence[pathlib.Path],
    target: str = "python",
    diagnostics: DiagnosticBag | None = None,
    project_name: str | None = None,
    frontend: str | None = None,
    **backend_options: Any,
) -> TranslationResult:
    """Run the whole pipeline and return the generated files."""
    diagnostics = diagnostics if diagnostics is not None else DiagnosticBag()
    project = load_sources(paths, diagnostics, project_name, frontend)
    prepare(project, diagnostics)

    files: dict[str, str]
    if target == "python":
        from .backends.python.emitter import PythonOptions, emit_project

        python_options = PythonOptions(**_accepted(PythonOptions, backend_options))
        files = {f"{project.name}.py": emit_project(project, python_options, diagnostics)}
    elif target == "cpp":
        from .backends.cpp.emitter import CppOptions
        from .backends.cpp.emitter import emit_project as emit_cpp

        cpp_options = CppOptions(**_accepted(CppOptions, backend_options))
        files = emit_cpp(project, cpp_options, diagnostics)
    elif target == "st":
        from .backends.st.emitter import StOptions
        from .backends.st.emitter import emit_project as emit_st

        st_options = StOptions(**_accepted(StOptions, backend_options))
        files = {f"{project.name}.st": emit_st(project, st_options, diagnostics)}
    else:
        raise TranslationError(
            f"unknown target {target!r}; expected one of {', '.join(available_targets())}"
        )

    return TranslationResult(project=project, diagnostics=diagnostics, files=files)
