"""Shared fixtures and helpers for the plcforge test suite."""

from __future__ import annotations

import pathlib
import sys
import types

import pytest

from plcforge.diagnostics import DiagnosticBag
from plcforge.ir.nodes import Project
from plcforge.pipeline import load_sources, prepare

EXAMPLES = pathlib.Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture
def bag() -> DiagnosticBag:
    return DiagnosticBag()


@pytest.fixture
def examples() -> pathlib.Path:
    return EXAMPLES


def build(source: str, tmp_path: pathlib.Path, name: str = "unit.scl") -> tuple[Project, DiagnosticBag]:
    """Parse Structured Text from a string and run the full preparation pass."""
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    diagnostics = DiagnosticBag()
    project = load_sources([path], diagnostics, project_name=path.stem)
    prepare(project, diagnostics)
    return project, diagnostics


def load_generated(code: str, module_name: str) -> types.ModuleType:
    """Execute generated Python and return it as an importable module.

    Running the output is the only honest way to test a code generator: a golden
    file proves the text did not change, not that the program still works.
    """
    module = types.ModuleType(module_name)
    module.__dict__["__name__"] = module_name
    sys.modules[module_name] = module
    exec(compile(code, f"<generated:{module_name}>", "exec"), module.__dict__)
    return module
