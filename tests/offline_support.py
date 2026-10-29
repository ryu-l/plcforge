"""Shared, bounded execution helpers for the extended offline tests."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from conftest import load_generated
from plcforge.backends.cpp.emitter import emit_project as emit_cpp
from plcforge.backends.python.emitter import emit_project as emit_python
from plcforge.backends.st.emitter import emit_project as emit_st
from plcforge.diagnostics import DiagnosticBag
from plcforge.frontends.python.lifter import lift_source
from plcforge.frontends.st.parser import parse_source
from plcforge.pipeline import prepare

COMPILER = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
requires_compiler = pytest.mark.skipif(COMPILER is None, reason="no C++ compiler on PATH")


class CompilationError(AssertionError):
    """Generated C++ failed compilation (distinct from execution/assertion failures)."""


def checked_project(source: str):
    bag = DiagnosticBag()
    project = parse_source(source, "offline.st", bag, project_name="offline")
    prepare(project, bag)
    assert not bag.has_errors, bag.summary()
    return project


def roundtrip(project):
    bag = DiagnosticBag()
    code = emit_python(project, None, bag)
    lifted = lift_source(code, "offline.py", bag, project_name="offline")
    prepare(lifted, bag)
    text = emit_st(lifted, None, bag)
    assert not bag.has_errors, bag.summary()
    return checked_project(text)


def python_module(project, name="offline_generated"):
    bag = DiagnosticBag()
    code = emit_python(project, None, bag)
    assert not bag.has_errors, bag.summary()
    return load_generated(code, name)


def cpp_output(project, main: str, tmp_path):
    bag = DiagnosticBag()
    files = emit_cpp(project, None, bag)
    assert not bag.has_errors, bag.summary()
    for name, content in files.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    (tmp_path / "main.cpp").write_text(main, encoding="utf-8")
    binary = tmp_path / "offline"
    result = subprocess.run(
        [COMPILER, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O1", "main.cpp", "-o", str(binary)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise CompilationError(result.stderr)
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return result.stdout
