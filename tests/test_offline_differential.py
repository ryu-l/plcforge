"""Compare actual Python/C++ traces AND independent expected values."""

from __future__ import annotations

import random

import pytest

from offline_support import (
    CompilationError,
    checked_project,
    cpp_output,
    python_module,
    requires_compiler,
    roundtrip,
)
from plcforge.runtime import SimulatedClock, use_clock

NUMERIC = """
FUNCTION_BLOCK Arithmetic
VAR_INPUT value : DINT; divisor : DINT; real_value : REAL; END_VAR
VAR_OUTPUT quotient : DINT; remainder : DINT; bounded : DINT;
    bits : WORD; total : DINT; fraction : REAL;
END_VAR
VAR i : DINT; END_VAR
    quotient := value / divisor;
    remainder := value MOD divisor;
    bounded := LIMIT(-10, value, 10);
    bits := WORD#16#1234 AND WORD#16#00FF;
    total := 0;
    FOR i := 5 TO 1 BY -2 DO total := total + i; END_FOR;
    fraction := real_value / 4.0 + 0.5;
END_FUNCTION_BLOCK
"""


def inputs():
    # Fixed boundary rows plus a reproducible stream; no INT overflow or /0.
    rng = random.Random(20261029)
    return [(-9, 4, -2.5), (9, -4, 2.5), (-9, -4, 0.0), (0, 3, 100.0)] + [
        (rng.randint(-10000, 10000), rng.choice([-7, -3, 2, 5]), rng.randint(-100, 100) / 2)
        for _ in range(512)
    ]


def numeric_expected(rows):
    expected = []
    for value, divisor, real in rows:
        # Independently derive signed quotient/remainder from positive magnitudes.
        q = (abs(value) // abs(divisor)) * (-1 if (value < 0) != (divisor < 0) else 1)
        expected.append((q, value - q * divisor, min(10, max(-10, value)), 52, 9, real / 4 + 0.5))
    return expected


def assert_numeric(actual, expected):
    assert len(actual) == len(expected)
    for scan, (got, want) in enumerate(zip(actual, expected, strict=True)):
        assert tuple(got[:5]) == want[:5], f"scan {scan}"
        assert got[5] == pytest.approx(want[5], rel=1e-6, abs=1e-6), f"scan {scan}"


@requires_compiler
@pytest.mark.parametrize("via_roundtrip", [False, True], ids=["direct", "python-st-roundtrip"])
def test_numeric_matrix_against_oracle_and_cpp(tmp_path, via_roundtrip):
    project = checked_project(NUMERIC)
    if via_roundtrip:
        project = roundtrip(project)
    module = python_module(project)
    block = module.Arithmetic()
    rows = inputs()
    trace = []
    for value, divisor, real in rows:
        block(value=value, divisor=divisor, real_value=real)
        trace.append(
            (block.quotient, block.remainder, block.bounded, block.bits, block.total, block.fraction)
        )
    calls = "\n".join(
        f"b.value={value}; b.divisor={divisor}; b.real_value={real}; b(); "
        'std::cout << b.quotient << " " << b.remainder << " " << b.bounded << " " '
        '<< b.bits << " " << b.total << " " << b.fraction << "\\n";'
        for value, divisor, real in rows
    )
    output = cpp_output(
        project,
        """
#include "offline.hpp"
#include <iostream>
#include <iomanip>
int main() {
    plant::Arithmetic b;
    std::cout << std::setprecision(9);
"""
        + calls
        + "\n}",
        tmp_path,
    )
    cpp_trace = [tuple(map(float, line.split())) for line in output.splitlines()]
    expected = numeric_expected(rows)
    assert_numeric(trace, expected)
    assert_numeric(cpp_trace, expected)
    assert_numeric(cpp_trace, trace)


STATEFUL = """
FUNCTION_BLOCK Channel
VAR_INPUT signal : BOOL; reset : BOOL; END_VAR
VAR_OUTPUT pulses : DINT; ready : BOOL; elapsed : TIME; END_VAR
VAR edge : R_TRIG; delay : TON; history : ARRAY [-1..1] OF DINT; END_VAR
    edge(CLK := signal);
    delay(IN := signal, PT := T#1500ms);
    IF reset THEN pulses := 0;
    ELSIF edge.Q THEN pulses := pulses + 1;
    END_IF;
    history[-1] := pulses;
    ready := delay.Q;
    elapsed := delay.ET;
END_FUNCTION_BLOCK
"""
# Absolute milliseconds avoid accumulated floating-point boundary ambiguity.
SCENARIO = [
    (0, False, False, True, False),
    (500, True, False, True, False),
    (1499, True, False, False, False),
    (1500, True, False, True, False),
    (1999, True, False, True, False),
    (2000, True, False, True, False),
    (2500, False, False, True, True),
    (3000, True, True, False, False),
    (4500, True, False, True, False),
]
# (pulses, ready, elapsed), first and second independent instance at each scan.
EXPECTED_STATE = [
    ((0, 0, 0), (1, 0, 0)),
    ((1, 0, 0), (1, 0, 0.5)),
    ((1, 0, 0.999), (1, 0, 0)),
    ((1, 0, 1), (2, 0, 0)),
    ((1, 0, 1.499), (2, 0, 0.499)),
    ((1, 1, 1.5), (2, 0, 0.5)),
    ((1, 0, 0), (0, 0, 1)),
    ((0, 0, 0), (0, 0, 0)),
    ((0, 1, 1.5), (1, 0, 0)),
]


def assert_state(actual):
    assert len(actual) == len(EXPECTED_STATE)
    for got, pair in zip(actual, EXPECTED_STATE, strict=True):
        expected = (*pair[0], *pair[1])
        assert got == pytest.approx(expected, abs=1e-9)


@requires_compiler
@pytest.mark.parametrize("via_roundtrip", [False, True], ids=["direct", "python-st-roundtrip"])
def test_timer_edges_reset_and_instance_isolation(tmp_path, via_roundtrip):
    project = checked_project(STATEFUL)
    if via_roundtrip:
        project = roundtrip(project)
    module = python_module(project)
    clock = SimulatedClock()
    trace = []
    with use_clock(clock):
        a, b = module.Channel(), module.Channel()
        assert a.delay is not b.delay and a.edge is not b.edge and a.history is not b.history
        for ms, sa, ra, sb, rb in SCENARIO:
            clock.set(ms / 1000)
            a(signal=sa, reset=ra)
            b(signal=sb, reset=rb)
            assert a.history[-1] == a.pulses and b.history[-1] == b.pulses
            trace.append((a.pulses, int(a.ready), a.elapsed, b.pulses, int(b.ready), b.elapsed))
        a.history[0] = 99
        assert b.history[0] == 0
    calls = []
    for ms, sa, ra, sb, rb in SCENARIO:
        calls.append(f"sim.set({ms / 1000});")
        calls.append(f"a.signal={int(sa)}; a.reset={int(ra)}; b.signal={int(sb)}; b.reset={int(rb)};")
        calls.append(
            'a(); b(); std::cout << a.pulses << " " << a.ready << " " << a.elapsed '
            '<< " " << b.pulses << " " << b.ready << " " << b.elapsed << "\\n";'
        )
    output = cpp_output(
        project,
        """
#include "offline.hpp"
#include <iostream>
#include <iomanip>
int main() {
    plcforge::SimulatedClock sim;
    plcforge::set_clock(&sim);
    plant::Channel a, b;
    std::cout << std::setprecision(12);
"""
        + "\n".join(calls)
        + """
    a.history[0] = 99;
    if (b.history[0] != 0) return 1;
}
""",
        tmp_path,
    )
    cpp_trace = [tuple(map(float, line.split())) for line in output.splitlines()]
    assert_state(trace)
    assert_state(cpp_trace)
    for cpp_row, python_row in zip(cpp_trace, trace, strict=True):
        assert cpp_row == pytest.approx(python_row, abs=1e-9)


@requires_compiler
@pytest.mark.xfail(
    strict=True,
    raises=CompilationError,
    reason="OFFLINE-002: C++ emits dependent FB before its definition; see offline-expanded.md",
)
def test_cpp_forward_function_block_dependency(tmp_path):
    project = checked_project("""
PROGRAM Factory
VAR child : Cell; total : DINT; END_VAR
    child();
    total := child.total;
END_PROGRAM
FUNCTION_BLOCK Cell
VAR_OUTPUT total : DINT; END_VAR
    total := total + 1;
END_FUNCTION_BLOCK
""")
    module = python_module(project)
    block = module.Factory()
    block.scan()
    assert block.total == 1
    output = cpp_output(
        project,
        """
#include "offline.hpp"
#include <iostream>
int main() {
    plant::Factory block;
    block.scan();
    std::cout << block.total;
}
""",
        tmp_path,
    )
    assert output == "1"


@requires_compiler
@pytest.mark.xfail(
    strict=True,
    raises=CompilationError,
    reason="OFFLINE-003: function roundtrip emits self-assignment under -Werror; see offline-expanded.md",
)
def test_roundtrip_function_cpp_is_warning_free(tmp_path):
    project = roundtrip(
        checked_project("""
FUNCTION Increment : DINT
VAR_INPUT value : DINT; END_VAR
    Increment := value + 1;
END_FUNCTION
""")
    )
    assert python_module(project).Increment(2) == 3
    output = cpp_output(
        project,
        """
#include "offline.hpp"
#include <iostream>
int main() { std::cout << plant::Increment(2); }
""",
        tmp_path,
    )
    assert output == "3"
