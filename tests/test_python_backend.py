"""The Python backend must produce code that runs and behaves like the source.

These tests execute the generated module rather than comparing it to a golden
file.  A golden file proves the text did not change; running it proves the
program still does what the PLC source says.
"""

from __future__ import annotations

import pytest

from conftest import build, load_generated
from plcforge.backends.python.emitter import PythonOptions, emit_project
from plcforge.runtime import SimulatedClock, use_clock


def generate(source: str, tmp_path, name: str, options: PythonOptions | None = None):
    project, diagnostics = build(source, tmp_path)
    code = emit_project(project, options, diagnostics)
    assert not diagnostics.has_errors, diagnostics.summary()
    return load_generated(code, name), code


class TestSemanticsThatPythonWouldGetWrong:
    def test_integer_division_truncates_toward_zero(self, tmp_path) -> None:
        module, code = generate(
            """
            FUNCTION Halve : DINT
            VAR_INPUT value : DINT; END_VAR
                Halve := value / 2;
            END_FUNCTION
            """,
            tmp_path,
            "gen_idiv",
        )
        assert "idiv(" in code, "integer division must not fall through to Python's //"
        assert module.Halve(-7) == -3, "IEC truncates toward zero; Python's // would give -4"
        assert module.Halve(7) == 3

    def test_modulo_follows_the_sign_of_the_dividend(self, tmp_path) -> None:
        module, code = generate(
            """
            FUNCTION Rest : DINT
            VAR_INPUT value : DINT; END_VAR
                Rest := value MOD 2;
            END_FUNCTION
            """,
            tmp_path,
            "gen_imod",
        )
        assert "imod(" in code
        assert module.Rest(-7) == -1, "Python's % would give 1"

    def test_for_loops_include_the_upper_bound(self, tmp_path) -> None:
        module, _ = generate(
            """
            FUNCTION SumTo : DINT
            VAR_INPUT last : DINT; END_VAR
            VAR i : DINT; END_VAR
                FOR i := 1 TO last DO
                    SumTo := SumTo + i;
                END_FOR;
            END_FUNCTION
            """,
            tmp_path,
            "gen_for",
        )
        assert module.SumTo(4) == 10, "1+2+3+4; an exclusive bound would give 6"

    def test_arrays_keep_their_declared_index_origin(self, tmp_path) -> None:
        module, _ = generate(
            """
            FUNCTION_BLOCK Buffer
            VAR_OUTPUT first : REAL; END_VAR
            VAR samples : ARRAY [1..3] OF REAL := [1.5, 2.5, 3.5]; END_VAR
                first := samples[1];
            END_FUNCTION_BLOCK
            """,
            tmp_path,
            "gen_array",
        )
        block = module.Buffer()()
        assert block.first == 1.5
        with pytest.raises(IndexError):
            block.samples[0]

    def test_negative_array_bounds(self, tmp_path) -> None:
        module, _ = generate(
            """
            FUNCTION_BLOCK Offsets
            VAR_OUTPUT value : INT; END_VAR
            VAR table : ARRAY [-2..2] OF INT; END_VAR
                table[-2] := 7;
                value := table[-2];
            END_FUNCTION_BLOCK
            """,
            tmp_path,
            "gen_negative_array",
        )
        assert module.Offsets()().value == 7

    def test_bit_strings_use_bitwise_operators(self, tmp_path) -> None:
        module, code = generate(
            """
            FUNCTION_BLOCK Masking
            VAR_OUTPUT result : WORD; END_VAR
            VAR mask : WORD := 16#00FF; value : WORD := 16#1234; END_VAR
                result := value AND mask;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
            "gen_bitwise",
        )
        assert " & " in code, "AND between WORDs is bitwise, not logical"
        assert module.Masking()().result == 0x34

    def test_booleans_use_logical_operators(self, tmp_path) -> None:
        _, code = generate(
            """
            FUNCTION_BLOCK Interlock
            VAR_INPUT a, b : BOOL; END_VAR
            VAR_OUTPUT ok : BOOL; END_VAR
                ok := a AND NOT b;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
            "gen_logical",
        )
        assert " and " in code and "not " in code


class TestStructure:
    def test_case_becomes_a_readable_branch_chain(self, tmp_path) -> None:
        module, code = generate(
            """
            FUNCTION Classify : INT
            VAR_INPUT value : INT; END_VAR
                CASE value OF
                    1, 2: Classify := 10;
                    5..9: Classify := 20;
                ELSE
                    Classify := 0;
                END_CASE;
            END_FUNCTION
            """,
            tmp_path,
            "gen_case",
        )
        assert "in (" in code and "<=" in code
        assert [module.Classify(v) for v in (1, 2, 3, 7, 20)] == [10, 10, 0, 20, 0]

    def test_repeat_until_runs_at_least_once(self, tmp_path) -> None:
        module, _ = generate(
            """
            FUNCTION Countdown : DINT
            VAR_INPUT start : DINT; END_VAR
            VAR n : DINT; END_VAR
                n := start;
                REPEAT
                    n := n - 1;
                    Countdown := Countdown + 1;
                UNTIL n <= 0 END_REPEAT;
            END_FUNCTION
            """,
            tmp_path,
            "gen_repeat",
        )
        assert module.Countdown(0) == 1, "REPEAT always executes the body once"
        assert module.Countdown(3) == 3

    def test_enumerations_and_structures_survive(self, tmp_path) -> None:
        module, _ = generate(
            """
            TYPE Mode : (Manual, Auto); END_TYPE
            TYPE Limits :
            STRUCT
                high : REAL := 80.0;
                low  : REAL := 20.0;
            END_STRUCT
            END_TYPE
            FUNCTION_BLOCK Controller
            VAR_OUTPUT mode : Mode; span : REAL; END_VAR
            VAR limits : Limits; END_VAR
                mode := Auto;
                span := limits.high - limits.low;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
            "gen_types",
        )
        block = module.Controller()()
        assert block.mode is module.Mode.Auto
        assert block.span == 60.0
        assert module.Limits().to_dict() == {"high": 80.0, "low": 20.0}

    def test_identifiers_colliding_with_python_keywords_are_mangled(self, tmp_path) -> None:
        module, _ = generate(
            """
            FUNCTION_BLOCK Valve
            VAR_INPUT class : INT; lambda : BOOL; END_VAR
            VAR_OUTPUT result : INT; END_VAR
                IF lambda THEN result := class; END_IF;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
            "gen_keywords",
        )
        block = module.Valve()(class_=5, lambda_=True)
        assert block.result == 5

    def test_direct_addresses_become_readable_names(self, tmp_path) -> None:
        _, code = generate(
            """
            FUNCTION_BLOCK Io
            VAR_INPUT start AT %IX0.0 : BOOL; END_VAR
            VAR_OUTPUT lamp : BOOL; END_VAR
                lamp := start;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
            "gen_addresses",
        )
        assert "at %IX0.0" in code, "the hardware address stays visible as a comment"

    def test_global_variables_become_one_shared_instance(self, tmp_path) -> None:
        module, _ = generate(
            """
            VAR_GLOBAL
                emergencyStop : BOOL := FALSE;
            END_VAR
            FUNCTION_BLOCK Guard
            VAR_OUTPUT safe : BOOL; END_VAR
                safe := NOT emergencyStop;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
            "gen_globals",
        )
        assert module.Guard()().safe is True
        module.GLOBALS.emergencyStop = True
        assert module.Guard()().safe is False


class TestTiming:
    def test_a_generated_timer_keeps_iec_timing(self, tmp_path) -> None:
        module, _ = generate(
            """
            FUNCTION_BLOCK Delay
            VAR_INPUT go : BOOL; END_VAR
            VAR_OUTPUT ready : BOOL; END_VAR
            VAR timer : TON; END_VAR
                timer(IN := go, PT := T#1s500ms);
                ready := timer.Q;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
            "gen_timer",
        )
        clock = SimulatedClock()
        with use_clock(clock):
            block = module.Delay()
            assert block(go=True).ready is False
            clock.advance(1.499)
            assert block(go=True).ready is False
            clock.advance(0.001)
            assert block(go=True).ready is True

    def test_output_parameters_are_read_back_after_the_call(self, tmp_path) -> None:
        module, code = generate(
            """
            FUNCTION_BLOCK Edge
            VAR_INPUT signal : BOOL; END_VAR
            VAR_OUTPUT pulse : BOOL; END_VAR
            VAR detector : R_TRIG; END_VAR
                detector(CLK := signal, Q => pulse);
            END_FUNCTION_BLOCK
            """,
            tmp_path,
            "gen_output_param",
        )
        assert "self.pulse = self.detector.Q" in code
        block = module.Edge()
        assert block(signal=True).pulse is True
        assert block(signal=True).pulse is False


def test_strict_integers_wraps_like_the_controller(tmp_path) -> None:
    module, code = generate(
        """
        FUNCTION_BLOCK Accumulator
        VAR_OUTPUT total : INT; END_VAR
            total := total + 30000;
        END_FUNCTION_BLOCK
        """,
        tmp_path,
        "gen_strict",
        PythonOptions(strict_integers=True),
    )
    assert 'wrap(' in code
    block = module.Accumulator()
    block()
    block()
    assert block.total == -5536, "an INT rolls over at 32767 on the controller"


def test_generated_module_is_valid_python_with_no_undefined_names(tmp_path) -> None:
    project, diagnostics = build(
        """
        FUNCTION_BLOCK Everything
        VAR_INPUT a : INT; b : REAL; c : STRING[10]; END_VAR
        VAR_OUTPUT r : REAL; END_VAR
        VAR t : TON; grid : ARRAY [1..2, 1..2] OF INT; END_VAR
            t(IN := a > 0, PT := T#1s);
            r := SQRT(ABS(b)) + INT_TO_REAL(a) + INT_TO_REAL(LEN(c));
            grid[1, 2] := a;
        END_FUNCTION_BLOCK
        """,
        tmp_path,
    )
    code = emit_project(project, None, diagnostics)
    module = load_generated(code, "gen_everything")
    block = module.Everything()(a=4, b=-9.0, c="abcd")
    assert block.r == pytest.approx(3.0 + 4.0 + 4.0)
    assert block.grid[1, 2] == 4
