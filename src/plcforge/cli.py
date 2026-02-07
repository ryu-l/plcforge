"""Command line interface.

    plcforge convert  Conveyor.scl --target python --out ./generated
    plcforge scaffold plant --from ./plc_src --out ./plant
    plcforge lift     controller.py --out Controller.st
    plcforge check    ./plc_src
    plcforge info

Every command shares the same pipeline, the same diagnostics and the same exit
code convention: 0 on success, 1 when an error was reported, 2 for a usage
problem.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from collections.abc import Sequence

from . import __version__
from .diagnostics import DiagnosticBag, Severity, TranslationError
from .pipeline import SOURCE_EXTENSIONS, available_targets, load_sources, prepare, translate

__all__ = ["build_parser", "main"]

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plcforge",
        description="Translate IEC 61131-3 control programs to and from general purpose code.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  plcforge convert Conveyor.scl -t python -o ./generated\n"
            "  plcforge convert MotorStarter.xml -t st          # ladder to Structured Text\n"
            "  plcforge scaffold plant --from ./plc_src -o ./plant\n"
            "  plcforge lift controller.py -o Controller.st     # Python back to ST\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"plcforge {__version__}")
    parser.add_argument("-q", "--quiet", action="store_true", help="only report errors")
    parser.add_argument(
        "--strict", action="store_true", help="treat warnings as errors"
    )
    parser.add_argument("--no-color", action="store_true", help="disable coloured diagnostics")
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    # -- convert ------------------------------------------------------- #
    convert = subparsers.add_parser(
        "convert",
        help="translate PLC sources into a target language",
        description="Translate one or more PLC sources into the chosen target language.",
    )
    _add_source_arguments(convert)
    convert.add_argument(
        "-t",
        "--target",
        default="python",
        choices=sorted(available_targets()),
        help="output language (default: %(default)s)",
    )
    convert.add_argument(
        "-o", "--out", type=pathlib.Path, default=None, metavar="PATH",
        help="output directory, or '-' for standard output (default: standard output)",
    )
    convert.add_argument("--name", default=None, help="project name used for the generated file names")
    convert.add_argument(
        "--namespace", default="plant", help="C++ namespace for the cpp target (default: %(default)s)"
    )
    convert.add_argument(
        "--strict-integers",
        action="store_true",
        help="wrap integer assignments so overflow matches the controller",
    )
    convert.add_argument(
        "--no-runtime",
        action="store_true",
        help="do not write the C++ runtime header next to the generated code",
    )
    convert.add_argument("-n", "--dry-run", action="store_true", help="report what would be written")
    convert.set_defaults(handler=_command_convert)

    # -- scaffold ------------------------------------------------------ #
    scaffold = subparsers.add_parser(
        "scaffold",
        help="generate a complete, runnable Python project",
        description=(
            "Generate a Python repository: translated logic, a vendored runtime, "
            "an entry point that runs the scan cycle, tests and packaging metadata."
        ),
    )
    scaffold.add_argument("package", help="name of the generated Python package")
    scaffold.add_argument(
        "--from",
        dest="sources",
        nargs="+",
        required=True,
        type=pathlib.Path,
        metavar="SOURCE",
        help="PLC source files or directories",
    )
    scaffold.add_argument(
        "-o", "--out", type=pathlib.Path, required=True, metavar="PATH", help="destination directory"
    )
    scaffold.add_argument(
        "--cycle-time", type=float, default=0.01, metavar="SECONDS",
        help="scan period written into the entry point (default: %(default)s)",
    )
    scaffold.add_argument("--no-tests", action="store_true", help="skip the generated test module")
    scaffold.add_argument("--no-packaging", action="store_true", help="skip pyproject.toml and README")
    scaffold.add_argument("--force", action="store_true", help="overwrite an existing directory")
    scaffold.add_argument("-n", "--dry-run", action="store_true", help="report what would be written")
    scaffold.set_defaults(handler=_command_scaffold)

    # -- lift ---------------------------------------------------------- #
    lift = subparsers.add_parser(
        "lift",
        help="translate Python control code back to Structured Text",
        description=(
            "Read Python that follows the plcforge runtime conventions and write "
            "Structured Text ready to paste into the engineering tool."
        ),
    )
    lift.add_argument("sources", nargs="+", type=pathlib.Path, metavar="SOURCE")
    lift.add_argument(
        "-o", "--out", type=pathlib.Path, default=None, metavar="PATH",
        help="output file or directory (default: standard output)",
    )
    lift.add_argument("--name", default=None, help="project name used for the generated file name")
    lift.add_argument("-n", "--dry-run", action="store_true", help="report what would be written")
    lift.set_defaults(handler=_command_lift)

    # -- check --------------------------------------------------------- #
    check = subparsers.add_parser(
        "check",
        help="parse and validate without generating anything",
        description="Parse the sources, resolve types and report every diagnostic found.",
    )
    _add_source_arguments(check)
    check.add_argument("--summary", action="store_true", help="print a table of the parsed POUs")
    check.set_defaults(handler=_command_check)

    # -- info ---------------------------------------------------------- #
    info = subparsers.add_parser(
        "info", help="show supported languages, targets and file extensions"
    )
    info.set_defaults(handler=_command_info)

    return parser


def _add_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "sources",
        nargs="+",
        type=pathlib.Path,
        metavar="SOURCE",
        help="source files or directories (.scl, .st, .xml, .py)",
    )
    parser.add_argument(
        "--frontend",
        default=None,
        choices=sorted(set(SOURCE_EXTENSIONS.values())),
        help="force a frontend instead of deducing it from the file extension",
    )


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def _command_convert(arguments: argparse.Namespace, diagnostics: DiagnosticBag) -> int:
    result = translate(
        arguments.sources,
        target=arguments.target,
        diagnostics=diagnostics,
        project_name=arguments.name,
        frontend=arguments.frontend,
        strict_integers=arguments.strict_integers,
        namespace=arguments.namespace,
        include_runtime=not arguments.no_runtime,
    )
    if diagnostics.has_errors:
        return EXIT_ERROR
    return _deliver(result.files, arguments.out, arguments.dry_run)


def _command_lift(arguments: argparse.Namespace, diagnostics: DiagnosticBag) -> int:
    result = translate(
        arguments.sources,
        target="st",
        diagnostics=diagnostics,
        project_name=arguments.name,
        frontend="python",
    )
    if diagnostics.has_errors:
        return EXIT_ERROR
    destination = arguments.out
    if destination is not None and destination.suffix:
        # A single file was named explicitly.
        content = "\n".join(result.files.values())
        if arguments.dry_run:
            print(f"would write {destination} ({len(content)} bytes)", file=sys.stderr)
            return EXIT_OK
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
        print(f"wrote {destination}", file=sys.stderr)
        return EXIT_OK
    return _deliver(result.files, destination, arguments.dry_run)


def _command_scaffold(arguments: argparse.Namespace, diagnostics: DiagnosticBag) -> int:
    from .backends.python.emitter import PythonOptions
    from .backends.python.project import ScaffoldOptions, scaffold_project

    destination: pathlib.Path = arguments.out
    if destination.exists() and any(destination.iterdir()) and not arguments.force:
        diagnostics.error(
            f"'{destination}' already exists and is not empty",
            code="PF003",
            hint="Pass --force to overwrite it.",
        )
        return EXIT_ERROR

    project = load_sources(arguments.sources, diagnostics, arguments.package, None)
    prepare(project, diagnostics)
    if diagnostics.has_errors:
        return EXIT_ERROR

    files = scaffold_project(
        project,
        ScaffoldOptions(
            package=arguments.package,
            cycle_time=arguments.cycle_time,
            tests=not arguments.no_tests,
            packaging=not arguments.no_packaging,
        ),
        PythonOptions(),
        diagnostics,
    )
    if diagnostics.has_errors:
        return EXIT_ERROR
    return _deliver(files, destination, arguments.dry_run)


def _command_check(arguments: argparse.Namespace, diagnostics: DiagnosticBag) -> int:
    project = load_sources(arguments.sources, diagnostics, None, arguments.frontend)
    prepare(project, diagnostics)
    if arguments.summary:
        _print_summary(project)
    return EXIT_ERROR if diagnostics.has_errors else EXIT_OK


def _command_info(arguments: argparse.Namespace, diagnostics: DiagnosticBag) -> int:
    del arguments, diagnostics
    print(f"plcforge {__version__}\n")
    print("Input languages")
    for name, description in _INPUT_LANGUAGES.items():
        print(f"  {name:<14} {description}")
    print("\nOutput targets")
    for name, description in available_targets().items():
        print(f"  {name:<14} {description}")
    print("\nRecognised extensions")
    grouped: dict[str, list[str]] = {}
    for extension, frontend in sorted(SOURCE_EXTENSIONS.items()):
        grouped.setdefault(frontend, []).append(extension)
    for frontend, extensions in grouped.items():
        print(f"  {frontend:<14} {', '.join(extensions)}")
    return EXIT_OK


_INPUT_LANGUAGES = {
    "SCL / ST": "Structured Text, including SCL dialects (.scl, .st, .iecst, .exp)",
    "KOP / LD": "Ladder Diagram via PLCopen XML (.xml)",
    "FUP / FBD": "Function Block Diagram via PLCopen XML (.xml)",
    "Python": "Python written against the plcforge runtime (.py)",
}


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #


def _deliver(files: dict[str, str], destination: pathlib.Path | None, dry_run: bool) -> int:
    if destination is None or str(destination) == "-":
        sys.stdout.write("\n".join(files[name] for name in sorted(files)))
        return EXIT_OK
    for relative in sorted(files):
        path = destination / relative
        if dry_run:
            print(f"would write {path} ({len(files[relative])} bytes)", file=sys.stderr)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(files[relative], encoding="utf-8")
        print(f"wrote {path}", file=sys.stderr)
    return EXIT_OK


def _print_summary(project) -> None:
    rows = [("POU", "KIND", "LANGUAGE", "VARIABLES", "STATEMENTS")]
    for pou in project.pous:
        rows.append(
            (
                pou.name,
                pou.kind.value,
                pou.source_language,
                str(len(list(pou.declarations()))),
                str(len(pou.body)),
            )
        )
    if project.structs or project.enums:
        rows.append(("", "", "", "", ""))
    for struct in project.structs:
        rows.append((struct.name, "STRUCT", "-", str(len(struct.fields)), "-"))
    for enum in project.enums:
        rows.append((enum.name, "ENUM", "-", str(len(enum.members)), "-"))

    widths = [max(len(row[column]) for row in rows) for column in range(5)]
    for index, row in enumerate(rows):
        print("  ".join(value.ljust(widths[column]) for column, value in enumerate(row)).rstrip())
        if index == 0:
            print("  ".join("-" * width for width in widths))


# --------------------------------------------------------------------------- #


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if not getattr(arguments, "handler", None):
        parser.print_help()
        return EXIT_USAGE

    diagnostics = DiagnosticBag()
    try:
        status = arguments.handler(arguments, diagnostics)
    except TranslationError as error:
        print(f"plcforge: error: {error}", file=sys.stderr)
        for diagnostic in error.diagnostics:
            print(diagnostic.format(colour=False), file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:  # pragma: no cover - interactive use
        return 130

    colour = not arguments.no_color
    shown = [
        diagnostic
        for diagnostic in diagnostics
        if not arguments.quiet or diagnostic.severity is Severity.ERROR
    ]
    for diagnostic in shown:
        print(diagnostic.format(colour and sys.stderr.isatty()), file=sys.stderr)
    if shown and not arguments.quiet:
        print(f"plcforge: {diagnostics.summary()}", file=sys.stderr)

    if arguments.strict and diagnostics.warnings and status == EXIT_OK:
        print("plcforge: warnings were treated as errors (--strict)", file=sys.stderr)
        return EXIT_ERROR
    return status


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
