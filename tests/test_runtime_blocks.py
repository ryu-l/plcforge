"""The standard function blocks must behave as IEC 61131-3 specifies.

Every test drives a simulated clock, so the assertions are about the standard's
timing rules rather than about how fast the machine running the test happens to
be.
"""

from __future__ import annotations

import pytest

from plcforge.runtime import (
    CTD,
    CTU,
    CTUD,
    F_TRIG,
    R_TRIG,
    RS,
    SR,
    TOF,
    TON,
    TP,
    SimulatedClock,
    use_clock,
)


@pytest.fixture
def clock() -> SimulatedClock:
    return SimulatedClock()


class TestTON:
    def test_q_rises_only_after_the_full_preset(self, clock: SimulatedClock) -> None:
        with use_clock(clock):
            timer = TON()
            timer(IN=True, PT=1.0)
            assert timer.Q is False

            clock.advance(0.999)
            timer(IN=True)
            assert timer.Q is False, "Q must stay low one millisecond short of PT"

            clock.advance(0.001)
            timer(IN=True)
            assert timer.Q is True
            assert timer.ET == pytest.approx(1.0)

    def test_elapsed_time_is_clamped_to_the_preset(self, clock: SimulatedClock) -> None:
        with use_clock(clock):
            timer = TON()
            timer(IN=True, PT=0.5)
            clock.advance(10.0)
            timer(IN=True)
            assert timer.ET == pytest.approx(0.5)

    def test_dropping_the_input_resets_the_timer(self, clock: SimulatedClock) -> None:
        with use_clock(clock):
            timer = TON()
            timer(IN=True, PT=1.0)
            clock.advance(0.6)
            timer(IN=True)
            timer(IN=False)
            assert (timer.Q, timer.ET) == (False, 0.0)

            timer(IN=True)
            clock.advance(0.6)
            timer(IN=True)
            assert timer.Q is False, "timing restarts from zero after the input drops"

    def test_zero_preset_is_immediate(self, clock: SimulatedClock) -> None:
        with use_clock(clock):
            timer = TON()
            timer(IN=True, PT=0.0)
            assert timer.Q is True


class TestTOF:
    def test_q_follows_input_up_and_lags_down(self, clock: SimulatedClock) -> None:
        with use_clock(clock):
            timer = TOF()
            timer(IN=False, PT=1.0)
            assert timer.Q is False, "a TOF starts low, it does not switch on by itself"

            timer(IN=True)
            assert timer.Q is True

            timer(IN=False)
            assert timer.Q is True, "Q holds while the off delay runs"

            clock.advance(0.999)
            timer(IN=False)
            assert timer.Q is True

            clock.advance(0.001)
            timer(IN=False)
            assert timer.Q is False

    def test_input_returning_early_cancels_the_delay(self, clock: SimulatedClock) -> None:
        with use_clock(clock):
            timer = TOF()
            timer(IN=True, PT=1.0)
            timer(IN=False)
            clock.advance(0.5)
            timer(IN=False)
            timer(IN=True)
            clock.advance(1.0)
            timer(IN=True)
            assert timer.Q is True


class TestTP:
    def test_pulse_holds_for_the_preset_regardless_of_the_input(self, clock: SimulatedClock) -> None:
        with use_clock(clock):
            timer = TP()
            timer(IN=True, PT=1.0)
            assert timer.Q is True

            timer(IN=False)
            clock.advance(0.5)
            timer(IN=False)
            assert timer.Q is True, "the pulse ignores the input dropping"

            clock.advance(0.5)
            timer(IN=False)
            assert timer.Q is False

    def test_retriggering_needs_a_new_rising_edge(self, clock: SimulatedClock) -> None:
        with use_clock(clock):
            timer = TP()
            timer(IN=True, PT=0.1)
            clock.advance(0.2)
            timer(IN=True)
            assert timer.Q is False, "holding IN high does not restart the pulse"

            timer(IN=False)
            timer(IN=True)
            assert timer.Q is True


class TestCounters:
    def test_ctu_counts_edges_not_levels(self) -> None:
        counter = CTU()
        for _ in range(5):
            counter(CU=True, PV=3)
        assert counter.CV == 1, "a level held high is a single edge"

        counter(CU=False)
        counter(CU=True)
        assert counter.CV == 2
        assert counter.Q is False

        counter(CU=False)
        counter(CU=True)
        assert (counter.CV, counter.Q) == (3, True)

        counter(R=True)
        assert (counter.CV, counter.Q) == (0, False)

    def test_ctd_loads_and_counts_down(self) -> None:
        counter = CTD()
        counter(LD=True, PV=2)
        assert counter.CV == 2

        counter(LD=False, CD=True)
        counter(CD=False)
        counter(CD=True)
        assert (counter.CV, counter.Q) == (0, True)

    def test_ctud_tracks_both_directions(self) -> None:
        counter = CTUD()
        counter(PV=2)
        assert (counter.QU, counter.QD) == (False, True)

        for _ in range(2):
            counter(CU=False)
            counter(CU=True)
        assert (counter.CV, counter.QU) == (2, True)

        counter(CU=False, CD=True)
        assert counter.CV == 1


class TestEdgesAndBistables:
    def test_r_trig_pulses_for_one_scan(self) -> None:
        trigger = R_TRIG()
        assert trigger(CLK=False).Q is False
        assert trigger(CLK=True).Q is True
        assert trigger(CLK=True).Q is False, "the edge lasts exactly one scan"
        assert trigger(CLK=False).Q is False
        assert trigger(CLK=True).Q is True

    def test_f_trig_pulses_on_the_falling_edge(self) -> None:
        trigger = F_TRIG()
        assert trigger(CLK=True).Q is False
        assert trigger(CLK=False).Q is True
        assert trigger(CLK=False).Q is False

    def test_sr_is_set_dominant(self) -> None:
        latch = SR()
        assert latch(S1=True, R=True).Q1 is True
        assert latch(S1=False, R=False).Q1 is True
        assert latch(S1=False, R=True).Q1 is False

    def test_rs_is_reset_dominant(self) -> None:
        latch = RS()
        assert latch(S=True, R1=True).Q1 is False
        assert latch(S=True, R1=False).Q1 is True
        assert latch(S=False, R1=False).Q1 is True


def test_instance_clock_overrides_the_ambient_one() -> None:
    ambient, private = SimulatedClock(), SimulatedClock()
    with use_clock(ambient):
        timer = TON(clock=private)
        timer(IN=True, PT=1.0)
        ambient.advance(5.0)
        timer(IN=True)
        assert timer.Q is False

        private.advance(1.0)
        timer(IN=True)
        assert timer.Q is True


def test_reset_returns_a_block_to_its_power_on_state(clock: SimulatedClock) -> None:
    with use_clock(clock):
        timer = TON()
        timer(IN=True, PT=0.1)
        clock.advance(0.2)
        timer(IN=True)
        assert timer.Q is True

        timer.reset()
        assert (timer.Q, timer.ET) == (False, 0.0)
