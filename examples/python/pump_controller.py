"""A control program written in Python, ready to be translated to Structured Text.

This is the other direction: prototype in Python where the tooling is fast, then
run ``plcforge lift pump_controller.py -o PumpController.st`` and paste the
result into the engineering tool.

The conventions the lifter expects are the ones this file follows:

* a POU is a class deriving from ``FunctionBlock`` or ``Program``,
* its variables are annotated attributes assigned in ``__init__``, grouped by a
  ``# VAR_INPUT`` style comment,
* its logic lives in ``body()`` for a function block and ``scan()`` for a
  program,
* a ``FUNCTION`` is a module level function with annotated parameters.
"""

from __future__ import annotations

from enum import IntEnum

from plcforge.runtime import BOOL, INT, LIMIT, REAL, TIME, TON, Clock, FunctionBlock, Program, T


class PumpState(IntEnum):
    """Operating state of a single pump."""

    Stopped = 0
    Starting = 1
    Running = 2
    Locked = 3


def duty_percent(measured: REAL, target: REAL) -> REAL:
    """Proportional duty command, clamped to a sensible operating window."""
    error = target - measured
    return LIMIT(0.0, 50.0 + error * 2.0, 100.0)


class Pump(FunctionBlock):
    """One pump with a start delay and a dry-run lockout."""

    def __init__(self, clock: Clock | None = None) -> None:
        super().__init__(clock)
        # VAR_INPUT
        self.demand: BOOL = False
        self.suctionPressure: REAL = 0.0
        self.minPressure: REAL = 0.5
        self.startDelay: TIME = T("T#2s")
        self.reset: BOOL = False
        # VAR_OUTPUT
        self.pumpRunning: BOOL = False
        self.state: PumpState = PumpState.Stopped
        self.dryRuns: INT = 0
        # VAR
        self.delayTimer: TON = TON(clock)

    def body(self) -> None:
        dry = self.suctionPressure < self.minPressure
        self.delayTimer(IN=self.demand and not dry, PT=self.startDelay)

        if self.reset:
            self.state = PumpState.Stopped
            self.dryRuns = 0

        if self.state == PumpState.Stopped:
            if self.demand and not dry:
                self.state = PumpState.Starting
        elif self.state == PumpState.Starting:
            if self.delayTimer.Q:
                self.state = PumpState.Running
            elif not self.demand:
                self.state = PumpState.Stopped
        elif self.state == PumpState.Running:
            if dry:
                self.dryRuns = self.dryRuns + 1
                self.state = PumpState.Locked
            elif not self.demand:
                self.state = PumpState.Stopped
        elif self.state == PumpState.Locked:
            pass

        if self.dryRuns >= 3:
            self.state = PumpState.Locked

        self.pumpRunning = self.state == PumpState.Running


class PumpStation(Program):
    """Lead/standby pump station holding a pressure setpoint."""

    def __init__(self, clock: Clock | None = None) -> None:
        super().__init__(clock)
        # VAR
        self.lead: Pump = Pump(clock)
        self.standby: Pump = Pump(clock)
        self.setpoint: REAL = 4.0
        self.measured: REAL = 0.0
        self.suction: REAL = 1.0
        self.resetButton: BOOL = False
        self.duty: REAL = 0.0
        self.stationRunning: BOOL = False

    def scan(self) -> None:
        self.duty = duty_percent(self.measured, self.setpoint)

        demand = self.measured < self.setpoint
        self.lead(
            demand=demand,
            suctionPressure=self.suction,
            reset=self.resetButton,
        )
        # The standby pump only runs once the lead pump has locked out.
        self.standby(
            demand=demand and self.lead.state == PumpState.Locked,
            suctionPressure=self.suction,
            reset=self.resetButton,
        )

        self.stationRunning = self.lead.pumpRunning or self.standby.pumpRunning
