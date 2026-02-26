"""Round-tripping: Structured Text out, Python back, and the reverse.

The bar these tests hold is behavioural, not textual.  A translation is correct
when the program still computes the same thing, not when the bytes match.
"""

from __future__ import annotations

import pytest

from conftest import load_generated
from plcforge.backends.python.emitter import emit_project as emit_python
from plcforge.backends.st.emitter import emit_project as emit_st
from plcforge.diagnostics import DiagnosticBag
from plcforge.frontends.python.lifter import lift_source
from plcforge.frontends.st.parser import parse_source
from plcforge.ir.link import link_types
from plcforge.ir.nodes import PouKind, VarKind
from plcforge.ir.validate import resolve_types
from plcforge.runtime import SimulatedClock, use_clock

SOURCE = """
TYPE Mode : (Off, Heating, Holding); END_TYPE

FUNCTION_BLOCK Oven
VAR_INPUT
    request     : BOOL;
    temperature : REAL;
    setpoint    : REAL := 180.0;
END_VAR
VAR_OUTPUT
    heater  : BOOL;
    mode    : Mode;
    cycles  : INT;
END_VAR
VAR
    soak    : TON;
    started : R_TRIG;
END_VAR
    started(CLK := request);
    IF started.Q THEN
        cycles := cycles + 1;
    END_IF;

    CASE mode OF
        Off:
            heater := FALSE;
            IF request THEN mode := Heating; END_IF;
        Heating:
            heater := temperature < setpoint;
            IF temperature >= setpoint THEN mode := Holding; END_IF;
        Holding:
            heater := temperature < setpoint - 2.0;
            IF NOT request THEN mode := Off; END_IF;
    END_CASE;

    soak(IN := mode = Holding, PT := T#4s);
END_FUNCTION_BLOCK
"""


def prepare_source(text: str, name: str):
    diagnostics = DiagnosticBag()
    project = parse_source(text, name, diagnostics, project_name="roundtrip")
    link_types(project, diagnostics)
    resolve_types(project, diagnostics)
    return project, diagnostics


def drive(oven_class, mode_enum):
    """Run one fixed scenario and record everything that can be observed."""
    clock = SimulatedClock()
    with use_clock(clock):
        oven = oven_class()
        trace = []
        for step, (request, temperature) in enumerate(
            [(False, 20.0), (True, 20.0), (True, 100.0), (True, 185.0), (True, 185.0), (False, 185.0)]
        ):
            oven(request=request, temperature=temperature)
            trace.append((step, oven.heater, int(oven.mode), oven.cycles))
            clock.advance(1.0)
        return trace


def test_structured_text_to_python_and_back(tmp_path) -> None:
    original, diagnostics = prepare_source(SOURCE, "oven.scl")
    assert not diagnostics.has_errors

    python_code = emit_python(original, None, diagnostics)
    first = load_generated(python_code, "rt_first")

    lifted = lift_source(python_code, "oven.py", DiagnosticBag(), "roundtrip")
    lift_diagnostics = DiagnosticBag()
    link_types(lifted, lift_diagnostics)
    resolve_types(lifted, lift_diagnostics)
    assert not lift_diagnostics.has_errors, lift_diagnostics.summary()

    structured_text = emit_st(lifted, None, lift_diagnostics)
    reparsed, reparse_diagnostics = prepare_source(structured_text, "oven-again.scl")
    assert not reparse_diagnostics.has_errors, reparse_diagnostics.summary()

    second = load_generated(emit_python(reparsed, None, reparse_diagnostics), "rt_second")

    assert drive(first.Oven, first.Mode) == drive(second.Oven, second.Mode)


def test_the_interface_survives_the_round_trip() -> None:
    original, _ = prepare_source(SOURCE, "oven.scl")
    python_code = emit_python(original, None, DiagnosticBag())
    lifted = lift_source(python_code, "oven.py", DiagnosticBag())

    before = original.pou("Oven")
    after = lifted.pou("Oven")
    assert after is not None and after.kind is PouKind.FUNCTION_BLOCK

    for kind in (VarKind.INPUT, VarKind.OUTPUT):
        expected = [(d.name, d.type.name) for d in before.declarations(kind)]
        actual = [(d.name, d.type.name) for d in after.declarations(kind)]
        assert actual == expected, f"{kind.value} changed across the round trip"

    assert [name for name, _ in lifted.enums[0].members] == ["Off", "Heating", "Holding"]


def test_structured_text_output_reparses_identically() -> None:
    """Writing ST and reading it again must be a fixed point."""
    first, _ = prepare_source(SOURCE, "oven.scl")
    text_once = emit_st(first, None, DiagnosticBag())
    second, diagnostics = prepare_source(text_once, "oven.st")
    text_twice = emit_st(second, None, DiagnosticBag())

    assert not diagnostics.has_errors
    assert _without_header(text_once) == _without_header(text_twice)


def _without_header(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if "Translated from" not in line)


def test_hand_written_python_lifts_to_valid_structured_text(examples) -> None:
    source = (examples / "python" / "pump_controller.py").read_text(encoding="utf-8")
    diagnostics = DiagnosticBag()
    lifted = lift_source(source, str(examples / "python" / "pump_controller.py"), diagnostics)
    link_types(lifted, diagnostics)
    resolve_types(lifted, diagnostics)
    assert not diagnostics.has_errors, diagnostics.summary()

    text = emit_st(lifted, None, diagnostics)
    # The function result is assigned to a variable named after the function.
    assert "duty_percent := LIMIT(" in text
    # Python locals are implicit; Structured Text needs them declared.
    assert "VAR_TEMP" in text and "dry : BOOL" in text

    reparsed, reparse_diagnostics = prepare_source(text, "pump.st")
    assert not reparse_diagnostics.has_errors, reparse_diagnostics.summary()
    module = load_generated(emit_python(reparsed, None, reparse_diagnostics), "rt_pump")

    clock = SimulatedClock()
    with use_clock(clock):
        station = module.PumpStation()
        station.measured, station.suction = 2.0, 1.0
        station.scan()
        assert station.duty == pytest.approx(54.0)
        assert station.lead.state == module.PumpState.Starting

        clock.advance(2.0)
        station.scan()
        assert station.lead.pumpRunning is True


def test_repeat_and_while_survive_the_round_trip(tmp_path) -> None:
    source = """
    FUNCTION_BLOCK Loops
    VAR_OUTPUT total : DINT; guard : DINT; END_VAR
    VAR n : DINT; END_VAR
        n := 5;
        REPEAT
            total := total + n;
            n := n - 1;
        UNTIL n <= 0 END_REPEAT;
        WHILE guard < 3 DO
            guard := guard + 1;
        END_WHILE;
    END_FUNCTION_BLOCK
    """
    project, diagnostics = prepare_source(source, "loops.scl")
    code = emit_python(project, None, diagnostics)
    lifted = lift_source(code, "loops.py", DiagnosticBag())
    link_types(lifted, DiagnosticBag())
    text = emit_st(lifted, None, DiagnosticBag())

    assert "REPEAT" in text and "UNTIL n <= 0" in text
    assert "WHILE guard < 3 DO" in text

    again, _ = prepare_source(text, "loops2.scl")
    module = load_generated(emit_python(again, None, DiagnosticBag()), "rt_loops")
    block = module.Loops()()
    assert (block.total, block.guard) == (15, 3)
