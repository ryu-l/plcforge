"""The C++ backend.

Where a compiler is available the generated header is compiled and run, because
"it looks right" is not a useful standard for generated C++.  Without a compiler
the tests fall back to checking the decisions that change behaviour.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from conftest import build
from plcforge.backends.cpp.emitter import RUNTIME_HEADER, CppOptions, emit_project

COMPILER = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
requires_compiler = pytest.mark.skipif(COMPILER is None, reason="no C++ compiler on PATH")


def generate(source: str, tmp_path, options: CppOptions | None = None) -> dict[str, str]:
    project, diagnostics = build(source, tmp_path)
    files = emit_project(project, options, diagnostics)
    assert not diagnostics.has_errors, diagnostics.summary()
    return files


def compile_and_run(files: dict[str, str], main: str, tmp_path) -> str:
    directory = tmp_path / "cpp"
    directory.mkdir(exist_ok=True)
    for name, content in files.items():
        (directory / name).write_text(content, encoding="utf-8")
    (directory / "main.cpp").write_text(main, encoding="utf-8")

    binary = directory / "unit"
    compiled = subprocess.run(
        [COMPILER, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O1", "main.cpp", "-o", str(binary)],
        cwd=directory,
        capture_output=True,
        text=True,
    )
    assert compiled.returncode == 0, compiled.stderr
    executed = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
    assert executed.returncode == 0, executed.stderr
    return executed.stdout


SAMPLE = """
TYPE Mode : (Idle, Active); END_TYPE

FUNCTION_BLOCK Counter
VAR_INPUT step : BOOL; END_VAR
VAR_OUTPUT total : DINT; mode : Mode; END_VAR
VAR edge : R_TRIG; window : ARRAY [1..3] OF DINT; END_VAR
    edge(CLK := step);
    IF edge.Q THEN
        total := total + 1;
        window[1] := total;
    END_IF;
    CASE total OF
        0: mode := Idle;
    ELSE
        mode := Active;
    END_CASE;
END_FUNCTION_BLOCK
"""


class TestStructure:
    def test_the_runtime_header_ships_alongside_the_translation(self, tmp_path) -> None:
        files = generate(SAMPLE, tmp_path)
        assert RUNTIME_HEADER in files
        assert "#include <cstdint>" in files[RUNTIME_HEADER]

    def test_the_runtime_can_be_left_out(self, tmp_path) -> None:
        files = generate(SAMPLE, tmp_path, CppOptions(include_runtime=False))
        assert RUNTIME_HEADER not in files

    def test_types_map_to_fixed_width_integers(self, tmp_path) -> None:
        header = generate(
            """
            FUNCTION_BLOCK Widths
            VAR_OUTPUT a : SINT; b : INT; c : DINT; d : UDINT; e : REAL; f : LREAL; END_VAR
                a := 1;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )["unit.hpp"]
        for expected in ("plcforge::SINT", "plcforge::INT", "plcforge::DINT", "plcforge::UDINT"):
            assert expected in header

    def test_arrays_keep_their_bounds_in_the_type(self, tmp_path) -> None:
        header = generate(SAMPLE, tmp_path)["unit.hpp"]
        assert "plcforge::Array<plcforge::DINT, 1, 3>" in header

    def test_a_case_of_single_labels_becomes_a_switch(self, tmp_path) -> None:
        header = generate(SAMPLE, tmp_path)["unit.hpp"]
        assert "switch (" in header and "default:" in header

    def test_a_case_with_ranges_becomes_a_branch_chain(self, tmp_path) -> None:
        header = generate(
            """
            FUNCTION_BLOCK Ranged
            VAR_INPUT value : INT; END_VAR
            VAR_OUTPUT band : INT; END_VAR
                CASE value OF
                    0..9: band := 1;
                    10..99: band := 2;
                END_CASE;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )["unit.hpp"]
        assert "switch (" not in header
        assert ">= 0 && " in header

    def test_function_block_calls_assign_fields_then_invoke(self, tmp_path) -> None:
        header = generate(
            """
            FUNCTION_BLOCK Delayed
            VAR_INPUT go : BOOL; END_VAR
            VAR_OUTPUT ready : BOOL; END_VAR
            VAR timer : TON; END_VAR
                timer(IN := go, PT := T#1s);
                ready := timer.Q;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )["unit.hpp"]
        assert "timer.IN = go;" in header
        assert "timer.PT = 1.0" in header
        assert "timer();" in header

    def test_an_undeclared_loop_counter_is_declared(self, tmp_path) -> None:
        header = generate(
            """
            FUNCTION_BLOCK Loop
            VAR_OUTPUT total : DINT; END_VAR
                FOR i := 1 TO 3 DO
                    total := total + i;
                END_FOR;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )["unit.hpp"]
        assert "for (plcforge::DINT i = 1; i <= 3;" in header

    def test_a_descending_loop_compares_the_other_way(self, tmp_path) -> None:
        header = generate(
            """
            FUNCTION_BLOCK Down
            VAR_OUTPUT total : DINT; END_VAR
            VAR i : DINT; END_VAR
                FOR i := 5 TO 1 BY -1 DO
                    total := total + i;
                END_FOR;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )["unit.hpp"]
        assert "i >= 1;" in header

    def test_the_namespace_is_configurable(self, tmp_path) -> None:
        header = generate(SAMPLE, tmp_path, CppOptions(namespace="line7"))["unit.hpp"]
        assert "namespace line7 {" in header


@requires_compiler
class TestCompiled:
    def test_the_generated_header_compiles_without_warnings(self, tmp_path) -> None:
        files = generate(SAMPLE, tmp_path)
        output = compile_and_run(
            files,
            """
            #include "unit.hpp"
            #include <cstdio>

            int main() {
                plant::Counter counter;
                for (int i = 0; i < 3; ++i) {
                    counter.step = true;  counter();
                    counter.step = false; counter();
                }
                std::printf("%d %d %d\\n", counter.total, counter.window[1],
                            static_cast<int>(counter.mode));
                return 0;
            }
            """,
            tmp_path,
        )
        assert output.strip() == "3 3 1"

    def test_timers_behave_identically_to_the_python_backend(self, tmp_path) -> None:
        files = generate(
            """
            FUNCTION_BLOCK Delayed
            VAR_INPUT go : BOOL; END_VAR
            VAR_OUTPUT ready : BOOL; END_VAR
            VAR timer : TON; END_VAR
                timer(IN := go, PT := T#1s500ms);
                ready := timer.Q;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )
        output = compile_and_run(
            files,
            """
            #include "unit.hpp"
            #include <cstdio>

            int main() {
                plcforge::SimulatedClock sim;
                plcforge::set_clock(&sim);
                plant::Delayed block;

                block.go = true; block();
                std::printf("%d", block.ready ? 1 : 0);
                sim.advance(1.499); block();
                std::printf("%d", block.ready ? 1 : 0);
                sim.advance(0.001); block();
                std::printf("%d\\n", block.ready ? 1 : 0);
                return 0;
            }
            """,
            tmp_path,
        )
        assert output.strip() == "001"

    def test_integer_division_and_modulo_match_iec(self, tmp_path) -> None:
        files = generate(
            """
            FUNCTION_BLOCK Maths
            VAR_INPUT value : DINT; END_VAR
            VAR_OUTPUT quotient : DINT; remainder : DINT; END_VAR
                quotient := value / 2;
                remainder := value MOD 2;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )
        output = compile_and_run(
            files,
            """
            #include "unit.hpp"
            #include <cstdio>

            int main() {
                plant::Maths maths;
                maths.value = -7;
                maths();
                std::printf("%d %d\\n", maths.quotient, maths.remainder);
                return 0;
            }
            """,
            tmp_path,
        )
        assert output.strip() == "-3 -1"

    def test_array_bounds_are_enforced(self, tmp_path) -> None:
        files = generate(SAMPLE, tmp_path)
        output = compile_and_run(
            files,
            """
            #include "unit.hpp"
            #include <cstdio>
            #include <stdexcept>

            int main() {
                plant::Counter counter;
                try {
                    counter.window[0] = 1;   // the array is declared [1..3]
                    std::puts("no check");
                } catch (const std::out_of_range&) {
                    std::puts("checked");
                }
                return 0;
            }
            """,
            tmp_path,
        )
        assert output.strip() == "checked"

    def test_the_example_project_compiles(self, examples, tmp_path) -> None:
        import pathlib

        from plcforge.diagnostics import DiagnosticBag
        from plcforge.pipeline import load_sources, prepare

        diagnostics = DiagnosticBag()
        project = load_sources(
            [pathlib.Path(examples / "scl" / "conveyor.scl")], diagnostics, project_name="conveyor"
        )
        prepare(project, diagnostics)
        files = emit_project(project, None, diagnostics)
        assert not diagnostics.has_errors, diagnostics.summary()

        output = compile_and_run(
            files,
            """
            #include "conveyor.hpp"
            #include <cstdio>

            int main() {
                plcforge::SimulatedClock sim;
                plcforge::set_clock(&sim);
                plant::ConveyorLine line;
                plcforge::ScanEngine engine(line, 0.05);
                line.lineEnable = true;
                line.rawSpeedPot = 13824;
                engine.run_cycles(45, &sim);
                std::printf("%d %.3f\\n", static_cast<int>(line.infeed.state), line.infeed.speed);
                return 0;
            }
            """,
            tmp_path,
        )
        state, speed = output.split()
        assert int(state) == 2, "the segment should have reached Running"
        assert abs(float(speed) - 1.35) < 1e-3
