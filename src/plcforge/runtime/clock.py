"""Time sources for the scan cycle.

Generated code never calls :func:`time.monotonic` directly.  Everything that
needs the clock goes through this module, which means a whole control program
can be driven by a simulated clock and tested without waiting in real time.
"""

from __future__ import annotations

import threading
import time
from typing import Protocol

__all__ = [
    "Clock",
    "MonotonicClock",
    "SimulatedClock",
    "get_clock",
    "now",
    "set_clock",
    "use_clock",
]


class Clock(Protocol):
    """Anything that can report elapsed seconds."""

    def now(self) -> float:  # pragma: no cover - protocol definition
        ...


class MonotonicClock:
    """Wall clock time, immune to system clock adjustments."""

    __slots__ = ("_origin",)

    def __init__(self) -> None:
        self._origin = time.monotonic()

    def now(self) -> float:
        return time.monotonic() - self._origin

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"MonotonicClock(now={self.now():.3f}s)"


class SimulatedClock:
    """A clock that only moves when the test tells it to.

    This is what makes timer behaviour testable: advance by 999 ms and the
    on-delay timer must still be low, advance one more and it must be high.
    """

    __slots__ = ("_seconds",)

    def __init__(self, start: float = 0.0) -> None:
        self._seconds = float(start)

    def now(self) -> float:
        return self._seconds

    def advance(self, seconds: float) -> float:
        if seconds < 0:
            raise ValueError("a simulated clock cannot run backwards")
        self._seconds += float(seconds)
        return self._seconds

    def set(self, seconds: float) -> None:
        self._seconds = float(seconds)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"SimulatedClock(now={self._seconds:.3f}s)"


_local = threading.local()
_default_clock: Clock = MonotonicClock()


def get_clock() -> Clock:
    """Return the clock in effect for the calling thread."""
    return getattr(_local, "clock", _default_clock)


def set_clock(clock: Clock | None) -> None:
    """Install *clock* for the calling thread, or restore the default."""
    if clock is None:
        if hasattr(_local, "clock"):
            del _local.clock
    else:
        _local.clock = clock


def now() -> float:
    return get_clock().now()


class use_clock:
    """Context manager that swaps the clock for the duration of a block.

    >>> sim = SimulatedClock()
    >>> with use_clock(sim):
    ...     _ = now()
    """

    __slots__ = ("_clock", "_had_previous", "_previous")

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._previous: Clock | None = None
        self._had_previous = False

    def __enter__(self) -> Clock:
        self._had_previous = hasattr(_local, "clock")
        self._previous = getattr(_local, "clock", None)
        _local.clock = self._clock
        return self._clock

    def __exit__(self, *exc_info: object) -> None:
        if self._had_previous:
            _local.clock = self._previous
        elif hasattr(_local, "clock"):
            del _local.clock
