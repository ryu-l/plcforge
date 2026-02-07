"""Runtime pieces that are not function blocks: types, functions, scan engine."""

from __future__ import annotations

import pytest

from plcforge.runtime import (
    ABS,
    CONCAT,
    FIND,
    LEFT,
    LIMIT,
    MID,
    MUX,
    RIGHT,
    ROL,
    ROR,
    SEL,
    SHL,
    SHR,
    Array,
    MonotonicClock,
    Program,
    ScanEngine,
    SimulatedClock,
    Struct,
    T,
    convert,
    filled,
    format_time,
    idiv,
    iec_range,
    imod,
    use_clock,
    wrap,
)


class TestOperatorsThatDifferFromPython:
    @pytest.mark.parametrize(
        ("dividend", "divisor", "expected"),
        [(7, 2, 3), (-7, 2, -3), (7, -2, -3), (-7, -2, 3)],
    )
    def test_integer_division_truncates_toward_zero(self, dividend, divisor, expected) -> None:
        assert idiv(dividend, divisor) == expected

    @pytest.mark.parametrize(
        ("dividend", "divisor", "expected"),
        [(7, 2, 1), (-7, 2, -1), (7, -2, 1), (-7, -2, -1)],
    )
    def test_modulo_follows_the_dividend(self, dividend, divisor, expected) -> None:
        assert imod(dividend, divisor) == expected

    def test_division_by_zero_is_an_error_not_a_silent_zero(self) -> None:
        with pytest.raises(ZeroDivisionError):
            idiv(1, 0)
        with pytest.raises(ZeroDivisionError):
            imod(1, 0)

    def test_the_identity_dividend_equals_quotient_times_divisor_plus_remainder(self) -> None:
        for dividend in range(-9, 10):
            for divisor in (-3, -2, -1, 1, 2, 3):
                assert dividend == idiv(dividend, divisor) * divisor + imod(dividend, divisor)

    @pytest.mark.parametrize(
        ("arguments", "expected"),
        [((1, 3), [1, 2, 3]), ((3, 1, -1), [3, 2, 1]), ((10, 1, -3), [10, 7, 4, 1]), ((5, 5), [5])],
    )
    def test_for_loop_counting_is_inclusive(self, arguments, expected) -> None:
        assert list(iec_range(*arguments)) == expected

    def test_a_zero_step_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            list(iec_range(1, 10, 0))


class TestSelectionAndBits:
    def test_limit_uses_the_iec_argument_order(self) -> None:
        assert LIMIT(0, 15, 10) == 10
        assert LIMIT(0, -5, 10) == 0
        assert LIMIT(0, 5, 10) == 5

    def test_sel_and_mux(self) -> None:
        assert SEL(False, "off", "on") == "off"
        assert SEL(True, "off", "on") == "on"
        assert MUX(1, "a", "b", "c") == "b"
        with pytest.raises(IndexError):
            MUX(9, "a")

    def test_abs_keeps_the_type(self) -> None:
        assert ABS(-3) == 3
        assert ABS(-2.5) == 2.5

    def test_shifts_stay_inside_the_declared_width(self) -> None:
        assert SHL(0x80, 1, "BYTE") == 0
        assert SHL(0x80, 1, "WORD") == 0x100
        assert SHR(0x80, 4, "BYTE") == 0x08

    def test_rotations_wrap_around(self) -> None:
        assert ROL(0x81, 1, "BYTE") == 0x03
        assert ROR(0x03, 1, "BYTE") == 0x81
        assert ROL(0xAB, 8, "BYTE") == 0xAB


class TestStringsAreOneBased:
    def test_left_right_and_mid(self) -> None:
        assert LEFT("conveyor", 3) == "con"
        assert RIGHT("conveyor", 2) == "or"
        assert MID("conveyor", 3, 4) == "vey", "MID counts from position 1"

    def test_find_returns_a_one_based_position_or_zero(self) -> None:
        assert FIND("conveyor", "vey") == 4
        assert FIND("conveyor", "missing") == 0

    def test_concat(self) -> None:
        assert CONCAT("a", "b", "c") == "abc"


class TestTypes:
    @pytest.mark.parametrize(
        ("literal", "seconds"),
        [("T#5s", 5.0), ("T#1s500ms", 1.5), ("2m30s", 150.0), ("TIME#1h", 3600.0)],
    )
    def test_duration_literals_parse(self, literal, seconds) -> None:
        assert T(literal) == pytest.approx(seconds)

    def test_an_unparsable_duration_is_an_error(self) -> None:
        with pytest.raises(ValueError):
            T("not a duration")

    @pytest.mark.parametrize(
        ("seconds", "text"),
        [(1.5, "T#1s500ms"), (0.0, "T#0ms"), (3661.0, "T#1h1m1s"), (-0.25, "-T#250ms")],
    )
    def test_durations_render_back(self, seconds, text) -> None:
        assert format_time(seconds) == text

    def test_duration_formatting_round_trips(self) -> None:
        for seconds in (0.001, 1.5, 90.0, 3600.0, 86400.5):
            assert T(format_time(seconds)) == pytest.approx(seconds)

    def test_fixed_width_integers_wrap(self) -> None:
        assert wrap(32768, "INT") == -32768
        assert wrap(-32769, "INT") == 32767
        assert wrap(256, "BYTE") == 0
        assert wrap(5, "UNKNOWN") == 5, "an unknown type is left alone"

    def test_conversions(self) -> None:
        assert convert(3.7, "INT") == 3, "narrowing to an integer truncates"
        assert convert(-3.7, "INT") == -3
        assert convert(2, "REAL") == 2.0
        assert convert(0, "BOOL") is False
        assert convert(300, "BYTE") == 44, "conversion respects the target width"


class TestArray:
    def test_bounds_are_kept(self) -> None:
        array = Array([(1, 3)], 0.0)
        array[1], array[3] = 1.5, 3.5
        assert array.to_list() == [1.5, 0.0, 3.5]
        assert (array.lower, array.upper, len(array)) == (1, 3, 3)

    def test_out_of_range_access_is_reported(self) -> None:
        array = Array([(1, 3)], 0)
        for index in (0, 4, -1):
            with pytest.raises(IndexError):
                array[index]

    def test_multiple_dimensions_are_row_major(self) -> None:
        grid = Array([(0, 1), (1, 3)], 0)
        grid[0, 1], grid[1, 3] = 10, 20
        assert grid.to_list() == [10, 0, 0, 0, 0, 20]
        with pytest.raises(IndexError):
            grid[0]

    def test_an_empty_range_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            Array([(5, 1)], 0)

    def test_a_factory_fill_gives_each_element_its_own_object(self) -> None:
        array = Array([(1, 2)], list)
        array[1].append("x")
        assert array[2] == []

    def test_filled_writes_in_declaration_order(self) -> None:
        array = filled(Array([(1, 3)], 0), [10, 20])
        assert array.to_list() == [10, 20, 0]
        with pytest.raises(ValueError):
            filled(Array([(1, 2)], 0), [1, 2, 3])


class TestClock:
    def test_a_simulated_clock_only_moves_when_told(self) -> None:
        clock = SimulatedClock()
        assert clock.now() == 0.0
        clock.advance(2.5)
        assert clock.now() == 2.5
        with pytest.raises(ValueError):
            clock.advance(-1.0)

    def test_the_ambient_clock_is_restored_after_the_context(self) -> None:
        from plcforge.runtime import get_clock

        before = get_clock()
        with use_clock(SimulatedClock()):
            assert isinstance(get_clock(), SimulatedClock)
        assert get_clock() is before

    def test_a_monotonic_clock_never_goes_backwards(self) -> None:
        clock = MonotonicClock()
        readings = [clock.now() for _ in range(50)]
        assert readings == sorted(readings)


class Counter(Program):
    def __init__(self, clock=None) -> None:
        super().__init__(clock)
        self.count = 0
        self.inputs_seen = 0

    def scan(self) -> None:
        self.count += 1


class TestScanEngine:
    def test_run_cycles_advances_a_simulated_clock_one_cycle_per_scan(self) -> None:
        clock = SimulatedClock()
        engine = ScanEngine(Counter(), cycle_time=0.02, clock=clock)
        engine.run_cycles(5)
        assert engine.program.count == 5
        assert clock.now() == pytest.approx(0.1)

    def test_run_for_converts_a_duration_into_scans(self) -> None:
        engine = ScanEngine(Counter(), cycle_time=0.01, clock=SimulatedClock())
        engine.run_for(0.1)
        assert engine.program.count == 10

    def test_io_hooks_run_around_the_program(self) -> None:
        order: list[str] = []
        engine = ScanEngine(Counter(), cycle_time=0.01, clock=SimulatedClock())
        engine.on_read_inputs = lambda program: order.append("read")
        engine.on_write_outputs = lambda program: order.append("write")
        engine.run_cycles(2)
        assert order == ["read", "write", "read", "write"]

    def test_statistics_are_reported(self) -> None:
        engine = ScanEngine(Counter(), cycle_time=0.01, clock=SimulatedClock())
        engine.run_cycles(3)
        assert engine.statistics.cycles == 3
        assert "3 scans" in engine.statistics.summarise(0.01)
        assert engine.statistics.load(0.01) >= 0.0

    def test_a_non_positive_cycle_time_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            ScanEngine(Counter(), cycle_time=0.0)

    def test_program_variables_are_readable(self) -> None:
        program = Counter()
        program.scan()
        assert program.variables() == {"count": 1, "inputs_seen": 0}


def test_struct_helpers() -> None:
    from dataclasses import dataclass

    @dataclass
    class Limits(Struct):
        high: float = 80.0
        low: float = 20.0

    limits = Limits()
    assert limits.to_dict() == {"high": 80.0, "low": 20.0}
    limits.update(high=90.0)
    assert limits.high == 90.0
    with pytest.raises(AttributeError):
        limits.update(missing=1)
