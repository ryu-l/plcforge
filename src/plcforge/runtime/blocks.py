"""The IEC 61131-3 standard function blocks.

Timers read the scan clock from :mod:`plcforge.runtime.clock`, so a program can
be exercised in a test with a simulated clock and behave exactly as it does on
the controller - no sleeping, no flakiness.

Every block follows the same protocol: call the instance with its inputs, read
the outputs off the instance afterwards.  That mirrors the Structured Text
notation ``delay(IN := start, PT := T#5s); IF delay.Q THEN ...``.
"""

from __future__ import annotations

from .clock import Clock, get_clock
from .types import BOOL, INT, TIME

__all__ = [
    "CTD",
    "CTU",
    "CTUD",
    "F_TRIG",
    "RS",
    "R_TRIG",
    "SR",
    "TOF",
    "TON",
    "TP",
    "FunctionBlock",
]


class FunctionBlock:
    """Base class for stateful blocks.

    Holding state across calls is the whole point of a function block, so the
    base class deliberately offers only what every block needs: an optional
    private clock and a readable representation.
    """

    __slots__ = ("_clock",)

    #: Names printed by ``repr`` - generated subclasses override this.
    _repr_fields: tuple[str, ...] = ()

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock

    @property
    def clock(self) -> Clock:
        return self._clock if self._clock is not None else get_clock()

    def reset(self) -> None:
        """Return the block to its power-on state."""

    def __repr__(self) -> str:
        shown = ", ".join(f"{name}={getattr(self, name, None)!r}" for name in self._repr_fields)
        return f"{type(self).__name__}({shown})"


# --------------------------------------------------------------------------- #
# Timers
# --------------------------------------------------------------------------- #


class TON(FunctionBlock):
    """On-delay timer.

    ``Q`` goes high once ``IN`` has been high continuously for ``PT``.

    >>> from .clock import SimulatedClock, use_clock
    >>> sim = SimulatedClock()
    >>> with use_clock(sim):
    ...     t = TON()
    ...     _ = t(IN=True, PT=1.0); _ = sim.advance(0.999); _ = t(IN=True)
    ...     first = t.Q
    ...     _ = sim.advance(0.001); _ = t(IN=True)
    ...     (first, t.Q)
    (False, True)
    """

    __slots__ = ("ET", "IN", "PT", "Q", "_started")
    _repr_fields = ("IN", "PT", "Q", "ET")

    def __init__(self, clock: Clock | None = None) -> None:
        super().__init__(clock)
        self.IN: BOOL = False
        self.PT: TIME = 0.0
        self.Q: BOOL = False
        self.ET: TIME = 0.0
        self._started: float | None = None

    def __call__(self, IN: BOOL | None = None, PT: TIME | None = None) -> TON:
        if IN is not None:
            self.IN = IN
        if PT is not None:
            self.PT = PT
        if self.IN:
            now = self.clock.now()
            if self._started is None:
                self._started = now
            self.ET = min(now - self._started, self.PT)
            self.Q = self.ET >= self.PT
        else:
            self._started = None
            self.ET = 0.0
            self.Q = False
        return self

    def reset(self) -> None:
        self._started = None
        self.ET = 0.0
        self.Q = False


class TOF(FunctionBlock):
    """Off-delay timer: ``Q`` drops ``PT`` after ``IN`` goes low."""

    __slots__ = ("ET", "IN", "PT", "Q", "_released")
    _repr_fields = ("IN", "PT", "Q", "ET")

    def __init__(self, clock: Clock | None = None) -> None:
        super().__init__(clock)
        self.IN: BOOL = False
        self.PT: TIME = 0.0
        self.Q: BOOL = False
        self.ET: TIME = 0.0
        self._released: float | None = None

    def __call__(self, IN: BOOL | None = None, PT: TIME | None = None) -> TOF:
        if IN is not None:
            self.IN = IN
        if PT is not None:
            self.PT = PT
        if self.IN:
            self._released = None
            self.ET = 0.0
            self.Q = True
        elif self._released is None:
            # First scan with IN low: only start timing if Q was actually set.
            if self.Q:
                self._released = self.clock.now()
            else:
                self.ET = 0.0
        if self._released is not None:
            self.ET = min(self.clock.now() - self._released, self.PT)
            if self.ET >= self.PT:
                self.Q = False
        return self

    def reset(self) -> None:
        self._released = None
        self.ET = 0.0
        self.Q = False


class TP(FunctionBlock):
    """Pulse timer: a rising edge on ``IN`` holds ``Q`` high for ``PT``."""

    __slots__ = ("ET", "IN", "PT", "Q", "_previous_in", "_started")
    _repr_fields = ("IN", "PT", "Q", "ET")

    def __init__(self, clock: Clock | None = None) -> None:
        super().__init__(clock)
        self.IN: BOOL = False
        self.PT: TIME = 0.0
        self.Q: BOOL = False
        self.ET: TIME = 0.0
        self._started: float | None = None
        self._previous_in: BOOL = False

    def __call__(self, IN: BOOL | None = None, PT: TIME | None = None) -> TP:
        if IN is not None:
            self.IN = IN
        if PT is not None:
            self.PT = PT
        now = self.clock.now()
        if self.IN and not self._previous_in and self._started is None:
            self._started = now
        if self._started is not None:
            self.ET = min(now - self._started, self.PT)
            self.Q = self.ET < self.PT
            if not self.Q and not self.IN:
                # The pulse is over; rearm only once IN has gone low again.
                self._started = None
                self.ET = 0.0
        else:
            self.Q = False
            self.ET = 0.0
        self._previous_in = self.IN
        return self

    def reset(self) -> None:
        self._started = None
        self._previous_in = False
        self.ET = 0.0
        self.Q = False


# --------------------------------------------------------------------------- #
# Counters
# --------------------------------------------------------------------------- #


class CTU(FunctionBlock):
    """Up counter: ``CV`` increments on every rising edge of ``CU``."""

    __slots__ = ("CU", "CV", "PV", "Q", "R", "_previous_cu")
    _repr_fields = ("CV", "PV", "Q")

    def __init__(self, clock: Clock | None = None) -> None:
        super().__init__(clock)
        self.CU: BOOL = False
        self.R: BOOL = False
        self.PV: INT = 0
        self.Q: BOOL = False
        self.CV: INT = 0
        self._previous_cu: BOOL = False

    def __call__(
        self,
        CU: BOOL | None = None,
        R: BOOL | None = None,
        PV: INT | None = None,
    ) -> CTU:
        if CU is not None:
            self.CU = CU
        if R is not None:
            self.R = R
        if PV is not None:
            self.PV = PV
        if self.R:
            self.CV = 0
        elif self.CU and not self._previous_cu and self.CV < 2**31 - 1:
            self.CV += 1
        self.Q = self.CV >= self.PV
        self._previous_cu = self.CU
        return self

    def reset(self) -> None:
        self.CV = 0
        self.Q = False
        self._previous_cu = False


class CTD(FunctionBlock):
    """Down counter: ``CV`` decrements on every rising edge of ``CD``."""

    __slots__ = ("CD", "CV", "LD", "PV", "Q", "_previous_cd")
    _repr_fields = ("CV", "PV", "Q")

    def __init__(self, clock: Clock | None = None) -> None:
        super().__init__(clock)
        self.CD: BOOL = False
        self.LD: BOOL = False
        self.PV: INT = 0
        self.Q: BOOL = False
        self.CV: INT = 0
        self._previous_cd: BOOL = False

    def __call__(
        self,
        CD: BOOL | None = None,
        LD: BOOL | None = None,
        PV: INT | None = None,
    ) -> CTD:
        if CD is not None:
            self.CD = CD
        if LD is not None:
            self.LD = LD
        if PV is not None:
            self.PV = PV
        if self.LD:
            self.CV = self.PV
        elif self.CD and not self._previous_cd and self.CV > -(2**31):
            self.CV -= 1
        self.Q = self.CV <= 0
        self._previous_cd = self.CD
        return self

    def reset(self) -> None:
        self.CV = 0
        self.Q = False
        self._previous_cd = False


class CTUD(FunctionBlock):
    """Combined up/down counter."""

    __slots__ = (
        "CD",
        "CU",
        "CV",
        "LD",
        "PV",
        "QD",
        "QU",
        "R",
        "_previous_cd",
        "_previous_cu",
    )
    _repr_fields = ("CV", "PV", "QU", "QD")

    def __init__(self, clock: Clock | None = None) -> None:
        super().__init__(clock)
        self.CU: BOOL = False
        self.CD: BOOL = False
        self.R: BOOL = False
        self.LD: BOOL = False
        self.PV: INT = 0
        self.QU: BOOL = False
        self.QD: BOOL = True
        self.CV: INT = 0
        self._previous_cu: BOOL = False
        self._previous_cd: BOOL = False

    def __call__(
        self,
        CU: BOOL | None = None,
        CD: BOOL | None = None,
        R: BOOL | None = None,
        LD: BOOL | None = None,
        PV: INT | None = None,
    ) -> CTUD:
        if CU is not None:
            self.CU = CU
        if CD is not None:
            self.CD = CD
        if R is not None:
            self.R = R
        if LD is not None:
            self.LD = LD
        if PV is not None:
            self.PV = PV
        rising_up = self.CU and not self._previous_cu
        rising_down = self.CD and not self._previous_cd
        if self.R:
            self.CV = 0
        elif self.LD:
            self.CV = self.PV
        else:
            if rising_up and not rising_down and self.CV < 2**31 - 1:
                self.CV += 1
            elif rising_down and not rising_up and self.CV > -(2**31):
                self.CV -= 1
        self.QU = self.CV >= self.PV
        self.QD = self.CV <= 0
        self._previous_cu = self.CU
        self._previous_cd = self.CD
        return self

    def reset(self) -> None:
        self.CV = 0
        self.QU = False
        self.QD = True
        self._previous_cu = False
        self._previous_cd = False


# --------------------------------------------------------------------------- #
# Edge detection and bistables
# --------------------------------------------------------------------------- #


class R_TRIG(FunctionBlock):
    """Rising edge detector: ``Q`` is high for exactly one scan."""

    __slots__ = ("CLK", "Q", "_previous")
    _repr_fields = ("CLK", "Q")

    def __init__(self, clock: Clock | None = None) -> None:
        super().__init__(clock)
        self.CLK: BOOL = False
        self.Q: BOOL = False
        self._previous: BOOL = False

    def __call__(self, CLK: BOOL | None = None) -> R_TRIG:
        if CLK is not None:
            self.CLK = CLK
        self.Q = self.CLK and not self._previous
        self._previous = self.CLK
        return self

    def reset(self) -> None:
        self.Q = False
        self._previous = False


class F_TRIG(FunctionBlock):
    """Falling edge detector: ``Q`` is high for exactly one scan."""

    __slots__ = ("CLK", "Q", "_previous")
    _repr_fields = ("CLK", "Q")

    def __init__(self, clock: Clock | None = None) -> None:
        super().__init__(clock)
        self.CLK: BOOL = False
        self.Q: BOOL = False
        self._previous: BOOL = True

    def __call__(self, CLK: BOOL | None = None) -> F_TRIG:
        if CLK is not None:
            self.CLK = CLK
        self.Q = (not self.CLK) and self._previous
        self._previous = self.CLK
        return self

    def reset(self) -> None:
        self.Q = False
        self._previous = True


class SR(FunctionBlock):
    """Set dominant bistable."""

    __slots__ = ("Q1", "R", "S1")
    _repr_fields = ("S1", "R", "Q1")

    def __init__(self, clock: Clock | None = None) -> None:
        super().__init__(clock)
        self.S1: BOOL = False
        self.R: BOOL = False
        self.Q1: BOOL = False

    def __call__(self, S1: BOOL | None = None, R: BOOL | None = None) -> SR:
        if S1 is not None:
            self.S1 = S1
        if R is not None:
            self.R = R
        self.Q1 = self.S1 or (self.Q1 and not self.R)
        return self

    def reset(self) -> None:
        self.Q1 = False


class RS(FunctionBlock):
    """Reset dominant bistable."""

    __slots__ = ("Q1", "R1", "S")
    _repr_fields = ("S", "R1", "Q1")

    def __init__(self, clock: Clock | None = None) -> None:
        super().__init__(clock)
        self.S: BOOL = False
        self.R1: BOOL = False
        self.Q1: BOOL = False

    def __call__(self, S: BOOL | None = None, R1: BOOL | None = None) -> RS:
        if S is not None:
            self.S = S
        if R1 is not None:
            self.R1 = R1
        self.Q1 = (not self.R1) and (self.S or self.Q1)
        return self

    def reset(self) -> None:
        self.Q1 = False
