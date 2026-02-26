"""The command line interface: exit codes, output layout, error handling."""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from plcforge.cli import main

SIMPLE = """
FUNCTION_BLOCK Latch
VAR_INPUT set, clear : BOOL; END_VAR
VAR_OUTPUT held : BOOL; END_VAR
    IF set THEN held := TRUE; ELSIF clear THEN held := FALSE; END_IF;
END_FUNCTION_BLOCK
"""


@pytest.fixture
def source(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "latch.scl"
    path.write_text(SIMPLE, encoding="utf-8")
    return path


class TestConvert:
    def test_writes_a_python_module(self, source, tmp_path, capsys) -> None:
        out = tmp_path / "generated"
        assert main(["convert", str(source), "-t", "python", "-o", str(out)]) == 0
        produced = out / "latch.py"
        assert produced.exists()
        assert "class Latch(FunctionBlock):" in produced.read_text()

    def test_writes_to_standard_output_by_default(self, source, capsys) -> None:
        assert main(["convert", str(source)]) == 0
        assert "class Latch(FunctionBlock):" in capsys.readouterr().out

    def test_cpp_target_writes_the_runtime_too(self, source, tmp_path) -> None:
        out = tmp_path / "cpp"
        assert main(["convert", str(source), "-t", "cpp", "-o", str(out)]) == 0
        assert (out / "latch.hpp").exists()
        assert (out / "plcforge_runtime.hpp").exists()

    def test_no_runtime_flag(self, source, tmp_path) -> None:
        out = tmp_path / "cpp"
        main(["convert", str(source), "-t", "cpp", "--no-runtime", "-o", str(out)])
        assert not (out / "plcforge_runtime.hpp").exists()

    def test_st_target_round_trips_a_ladder_file(self, examples, tmp_path) -> None:
        out = tmp_path / "st"
        source = examples / "plcopen" / "MotorStarter.ld.xml"
        assert main(["convert", str(source), "-t", "st", "-o", str(out)]) == 0
        text = (out / "MotorStarter.ld.st").read_text()
        assert "FUNCTION_BLOCK MotorStarter" in text

    def test_dry_run_writes_nothing(self, source, tmp_path, capsys) -> None:
        out = tmp_path / "generated"
        assert main(["convert", str(source), "-o", str(out), "--dry-run"]) == 0
        assert not out.exists()
        assert "would write" in capsys.readouterr().err

    def test_a_directory_of_sources_is_translated_as_one_project(self, tmp_path) -> None:
        sources = tmp_path / "plc"
        sources.mkdir()
        (sources / "a.scl").write_text(SIMPLE, encoding="utf-8")
        (sources / "b.scl").write_text(
            "FUNCTION_BLOCK Second VAR_OUTPUT x : INT; END_VAR x := 1; END_FUNCTION_BLOCK",
            encoding="utf-8",
        )
        out = tmp_path / "generated"
        assert main(["convert", str(sources), "-o", str(out), "--name", "plant"]) == 0
        text = (out / "plant.py").read_text()
        assert "class Latch" in text and "class Second" in text

    def test_missing_source_is_reported_without_a_traceback(self, tmp_path, capsys) -> None:
        assert main(["convert", str(tmp_path / "nope.scl")]) == 1
        assert "no such file" in capsys.readouterr().err

    def test_a_syntax_error_gives_exit_code_one(self, tmp_path, capsys) -> None:
        broken = tmp_path / "broken.scl"
        broken.write_text("FUNCTION_BLOCK X VAR a : INT; END_VAR a := ; END_FUNCTION_BLOCK")
        assert main(["convert", str(broken)]) == 1
        assert "error" in capsys.readouterr().err


class TestCheck:
    def test_clean_sources_exit_zero(self, source) -> None:
        assert main(["check", str(source)]) == 0

    def test_summary_lists_the_parsed_pous(self, source, capsys) -> None:
        main(["check", str(source), "--summary"])
        out = capsys.readouterr().out
        assert "Latch" in out and "FUNCTION_BLOCK" in out

    def test_strict_turns_warnings_into_a_failure(self, tmp_path, capsys) -> None:
        path = tmp_path / "warn.scl"
        path.write_text(
            "FUNCTION_BLOCK W VAR a : Mystery; END_VAR a := 1; END_FUNCTION_BLOCK",
            encoding="utf-8",
        )
        assert main(["check", str(path)]) == 0
        assert main(["--strict", "check", str(path)]) == 1
        assert "treated as errors" in capsys.readouterr().err

    def test_quiet_hides_warnings(self, tmp_path, capsys) -> None:
        path = tmp_path / "warn.scl"
        path.write_text(
            "FUNCTION_BLOCK W VAR a : Mystery; END_VAR a := 1; END_FUNCTION_BLOCK",
            encoding="utf-8",
        )
        main(["--quiet", "check", str(path)])
        assert capsys.readouterr().err == ""


class TestScaffold:
    def test_generates_a_runnable_project(self, examples, tmp_path) -> None:
        out = tmp_path / "plant"
        status = main(
            ["scaffold", "plant", "--from", str(examples / "scl"), "-o", str(out)]
        )
        assert status == 0
        for expected in (
            "pyproject.toml",
            "README.md",
            ".gitignore",
            "src/plant/__init__.py",
            "src/plant/main.py",
            "src/plant/runtime/blocks.py",
            "tests/test_plant.py",
        ):
            assert (out / expected).exists(), f"{expected} missing"

    def test_the_generated_project_has_no_dependency_on_plcforge(self, examples, tmp_path) -> None:
        """The runtime is vendored, so the result must not import plcforge."""
        out = tmp_path / "plant"
        main(["scaffold", "plant", "--from", str(examples / "scl"), "-o", str(out)])

        for path in (out / "src").rglob("*.py"):
            for number, line in enumerate(path.read_text().splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith(("import ", "from ")):
                    assert "plcforge" not in stripped, f"{path.name}:{number} imports plcforge"

        assert "dependencies = []" in (out / "pyproject.toml").read_text()

    def test_the_generated_project_runs_and_passes_its_tests(self, examples, tmp_path) -> None:
        out = tmp_path / "plant"
        main(["scaffold", "plant", "--from", str(examples / "scl"), "-o", str(out)])

        run = subprocess.run(
            [sys.executable, "-m", "plant.main", "--cycles", "25"],
            cwd=out,
            env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert run.returncode == 0, run.stderr
        assert "25 scans" in run.stderr

    def test_refuses_to_overwrite_without_force(self, examples, tmp_path, capsys) -> None:
        out = tmp_path / "plant"
        out.mkdir()
        (out / "keep.txt").write_text("mine")
        assert main(["scaffold", "plant", "--from", str(examples / "scl"), "-o", str(out)]) == 1
        assert "already exists" in capsys.readouterr().err
        assert (out / "keep.txt").read_text() == "mine"

        assert (
            main(["scaffold", "plant", "--from", str(examples / "scl"), "-o", str(out), "--force"])
            == 0
        )


class TestLift:
    def test_python_becomes_structured_text(self, examples, tmp_path) -> None:
        out = tmp_path / "PumpStation.st"
        status = main(["lift", str(examples / "python" / "pump_controller.py"), "-o", str(out)])
        assert status == 0
        text = out.read_text()
        assert "FUNCTION_BLOCK Pump" in text and "PROGRAM PumpStation" in text

    def test_lifting_a_module_with_no_pous_warns(self, tmp_path, capsys) -> None:
        path = tmp_path / "plain.py"
        path.write_text("VALUE = 1\n", encoding="utf-8")
        main(["lift", str(path)])
        assert "no function blocks" in capsys.readouterr().err


class TestGeneral:
    def test_info_lists_languages_and_targets(self, capsys) -> None:
        assert main(["info"]) == 0
        out = capsys.readouterr().out
        assert "KOP / LD" in out and "FUP / FBD" in out and "cpp" in out

    def test_no_command_prints_help(self, capsys) -> None:
        assert main([]) == 2
        assert "usage: plcforge" in capsys.readouterr().out

    def test_version(self, capsys) -> None:
        with pytest.raises(SystemExit) as exit_info:
            main(["--version"])
        assert exit_info.value.code == 0
        assert "plcforge" in capsys.readouterr().out

    def test_module_entry_point(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "plcforge", "info"],
            capture_output=True,
            text=True,
            env={"PYTHONPATH": str(pathlib.Path(__file__).resolve().parent.parent / "src"), "PATH": "/usr/bin:/bin"},
            timeout=60,
        )
        assert run.returncode == 0
        assert "Output targets" in run.stdout
