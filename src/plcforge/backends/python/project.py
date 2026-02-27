"""Scaffold a complete, runnable Python project from PLC sources.

``plcforge convert`` gives you a module.  ``plcforge scaffold`` gives you a
repository: the translated logic, a vendored copy of the runtime so the result
has no dependency on plcforge, an entry point that runs the scan cycle, a test
that exercises it, and the packaging metadata to install it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...diagnostics import DiagnosticBag
from ...ir.nodes import Pou, PouKind, Project
from ..naming import class_name, module_name
from .emitter import PythonEmitter, PythonOptions, _docstring_safe

__all__ = ["ScaffoldOptions", "scaffold_project"]

_RUNTIME_MODULES = ("__init__", "blocks", "clock", "functions", "scan", "types")


@dataclass
class ScaffoldOptions:
    """Choices that shape the generated repository."""

    package: str = "plant"
    #: Scan period of the generated entry point, in seconds.
    cycle_time: float = 0.01
    #: Write pyproject.toml, README and .gitignore alongside the code.
    packaging: bool = True
    #: Write a pytest module that runs the program for a few scans.
    tests: bool = True
    version: str = "0.1.0"
    description: str = "Control logic translated from IEC 61131-3 sources."


def scaffold_project(
    project: Project,
    options: ScaffoldOptions | None = None,
    python_options: PythonOptions | None = None,
    diagnostics: DiagnosticBag | None = None,
) -> dict[str, str]:
    """Return every file of the generated repository, keyed by relative path."""
    options = options or ScaffoldOptions()
    diagnostics = diagnostics if diagnostics is not None else DiagnosticBag()
    package = module_name(options.package)
    root = f"src/{package}"

    emitter_options = python_options or PythonOptions()
    emitter_options.runtime_package = ".runtime"
    emitter = PythonEmitter(project, emitter_options, diagnostics)

    logic_module = module_name(project.name) or "logic"
    if logic_module in ("runtime", "main", "__init__"):
        logic_module = f"{logic_module}_logic"

    files: dict[str, str] = {
        f"{root}/{logic_module}.py": emitter.emit_module(),
        f"{root}/__init__.py": _package_init(project, package, logic_module, options),
        f"{root}/runtime/__init__.py": "",  # replaced below by the vendored copy
    }
    files.update(_vendored_runtime(root))

    entry_point = _entry_point(project, diagnostics)
    files[f"{root}/main.py"] = _main_module(project, logic_module, entry_point, options)

    if options.tests:
        files[f"tests/test_{logic_module}.py"] = _test_module(
            project, package, logic_module, entry_point, options
        )
    if options.packaging:
        files["pyproject.toml"] = _pyproject(package, options)
        files["README.md"] = _readme(project, package, logic_module, entry_point, options)
        files[".gitignore"] = _gitignore()
    return files


def _entry_point(project: Project, diagnostics: DiagnosticBag) -> Pou | None:
    """The POU the generated entry point runs: the first PROGRAM, if there is one."""
    programs = [pou for pou in project.pous if pou.kind is PouKind.PROGRAM]
    if programs:
        if len(programs) > 1:
            diagnostics.note(
                f"{len(programs)} PROGRAMs found; the entry point runs '{programs[0].name}'",
                code="PF310",
            )
        return programs[0]
    blocks = [pou for pou in project.pous if pou.kind is PouKind.FUNCTION_BLOCK]
    if blocks:
        diagnostics.note(
            f"no PROGRAM found; the entry point calls the function block '{blocks[0].name}'",
            code="PF311",
        )
        return blocks[0]
    return None


def _vendored_runtime(root: str) -> dict[str, str]:
    """Copy the runtime into the generated package so it stands on its own."""
    from importlib import resources

    files: dict[str, str] = {}
    for name in _RUNTIME_MODULES:
        text = (resources.files("plcforge.runtime") / f"{name}.py").read_text(encoding="utf-8")
        files[f"{root}/runtime/{name}.py"] = text
    return files


def _package_init(project: Project, package: str, logic_module: str, options: ScaffoldOptions) -> str:
    exported = [class_name(p.name) for p in project.pous if p.kind is not PouKind.FUNCTION]
    exported += [class_name(s.name) for s in project.structs]
    exported += [class_name(e.name) for e in project.enums]
    names = ", ".join(sorted(set(exported))) or "*"
    listed = ",\n".join(f'    "{name}"' for name in sorted(set(exported)))
    sources = _docstring_safe(", ".join(project.source_files) or "PLC sources")
    return (
        f'"""{_docstring_safe(options.description)}\n\n'
        f"Translated from {sources}.\n"
        f'"""\n\n'
        f"from .{logic_module} import {names}\n\n"
        f'__version__ = "{options.version}"\n\n'
        f"__all__ = [\n{listed},\n]\n"
    )


def _main_module(project: Project, logic_module: str, entry: Pou | None, options: ScaffoldOptions) -> str:
    if entry is None:
        return (
            '"""Entry point.\n\nNo PROGRAM was found in the translated sources, so there is nothing\n'
            'to run here yet.  Import the generated function blocks and drive them\n'
            'from your own loop.\n"""\n'
        )

    name = class_name(entry.name)
    is_program = entry.kind is PouKind.PROGRAM
    wrapper = (
        ""
        if is_program
        else f'''

class {name}Program(Program):
    """Runs the {entry.name} function block once per scan."""

    def __init__(self, clock=None) -> None:
        super().__init__(clock)
        self.block = {name}(clock)

    def scan(self) -> None:
        self.block()

'''
    )
    program_class = name if is_program else f"{name}Program"
    imports = f"from .{logic_module} import {name}"
    runtime_imports = "from .runtime import ScanEngine" if is_program else "from .runtime import Program, ScanEngine"

    return f'''"""Run the control logic on a fixed scan cycle.

    python -m {module_name(options.package)}.main

Stops cleanly on Ctrl+C or SIGTERM.
"""

from __future__ import annotations

import argparse
import logging

{imports}
{runtime_imports}

LOGGER = logging.getLogger(__name__)
{wrapper}

def build_engine(cycle_time: float = {options.cycle_time!r}) -> ScanEngine:
    """Create the program and the scan engine that drives it."""
    return ScanEngine({program_class}(), cycle_time=cycle_time)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--cycle-time",
        type=float,
        default={options.cycle_time!r},
        metavar="SECONDS",
        help="scan period in seconds (default: %(default)s)",
    )
    parser.add_argument("--cycles", type=int, default=0, help="run N scans and exit (0 = run forever)")
    parser.add_argument("-v", "--verbose", action="store_true", help="log every scan")
    arguments = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if arguments.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
    )
    engine = build_engine(arguments.cycle_time)
    LOGGER.info("starting %s at %.1f ms cycle time", type(engine.program).__name__, arguments.cycle_time * 1e3)
    try:
        if arguments.cycles:
            engine.run_cycles(arguments.cycles)
        else:
            engine.run_forever()
    except KeyboardInterrupt:  # pragma: no cover - interactive use
        pass
    LOGGER.info("stopped: %s", engine.statistics.summarise(engine.cycle_time))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _test_module(
    project: Project,
    package: str,
    logic_module: str,
    entry: Pou | None,
    options: ScaffoldOptions,
) -> str:
    if entry is None:
        return (
            "\"\"\"Placeholder test: the sources contained no runnable POU.\"\"\"\n\n\n"
            "def test_package_imports() -> None:\n"
            f"    import {package}  # noqa: F401\n"
        )
    name = class_name(entry.name)
    driver = (
        f"    program = {name}()\n"
        if entry.kind is PouKind.PROGRAM
        else f"    from {package}.main import {name}Program\n\n    program = {name}Program()\n"
    )
    return f'''"""Smoke tests for the translated logic.

The simulated clock makes timing deterministic: advancing it by one cycle per
scan reproduces controller behaviour without the test waiting in real time.
"""

from __future__ import annotations

from {package}.{logic_module} import {name}
from {package}.runtime import ScanEngine, SimulatedClock


def test_runs_a_hundred_scans_without_error() -> None:
{driver}    clock = SimulatedClock()
    engine = ScanEngine(program, cycle_time={options.cycle_time!r}, clock=clock)

    engine.run_cycles(100)

    assert engine.statistics.cycles == 100
    assert engine.statistics.overruns == 0


def test_state_is_observable() -> None:
    """Every public variable of the program can be read back for logging."""
{driver}    assert isinstance(program.variables(), dict)
'''


def _pyproject(package: str, options: ScaffoldOptions) -> str:
    return f'''[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "{package.replace('_', '-')}"
version = "{options.version}"
description = "{options.description}"
requires-python = ">=3.10"
dependencies = []

[project.scripts]
{package.replace('_', '-')} = "{package}.main:main"

[project.optional-dependencies]
dev = ["pytest>=7.0"]

[tool.hatch.build.targets.wheel]
packages = ["src/{package}"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
'''


def _readme(
    project: Project,
    package: str,
    logic_module: str,
    entry: Pou | None,
    options: ScaffoldOptions,
) -> str:
    sources = "\n".join(f"- `{source}`" for source in project.source_files) or "- (unknown)"
    pou_rows = "\n".join(
        f"| `{pou.name}` | {pou.kind.value} | {pou.source_language} |" for pou in project.pous
    )
    entry_name = class_name(entry.name) if entry else "(none)"
    return f'''# {package}

{options.description}

Generated by [plcforge](https://github.com/alex-hahn/plcforge). The logic in
`src/{package}/{logic_module}.py` is a translation - edit the PLC source and run
the translation again rather than editing it by hand.

## Source

{sources}

| POU | Kind | Language |
|---|---|---|
{pou_rows}

## Run it

```bash
pip install -e ".[dev]"
python -m {package}.main --cycle-time {options.cycle_time}
```

The entry point runs `{entry_name}` on a fixed scan cycle and stops cleanly on
Ctrl+C. `--cycles N` runs a fixed number of scans instead, which is useful in CI.

## Test it

```bash
pytest
```

Tests drive the program with a simulated clock, so timer behaviour is exact and
nothing has to wait in real time.

## Wire it to real I/O

`ScanEngine` takes two hooks that run before and after each scan:

```python
engine = ScanEngine({entry_name}(), cycle_time={options.cycle_time})
engine.on_read_inputs = lambda program: read_from_fieldbus(program)
engine.on_write_outputs = lambda program: write_to_fieldbus(program)
engine.run_forever()
```

## Runtime

`src/{package}/runtime/` is a vendored copy of the plcforge runtime: IEC types,
the standard function blocks (`TON`, `CTU`, `R_TRIG`, ...), and the scan engine.
It has no third-party dependencies, so this project runs on a plain Python
installation.
'''


def _gitignore() -> str:
    return """__pycache__/
*.py[cod]
.venv/
venv/
build/
dist/
*.egg-info/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/
"""
