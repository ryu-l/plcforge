"""The scan cycle.

A PLC does the same three things forever: read the inputs, run the program,
write the outputs.  :class:`ScanEngine` reproduces that loop with an explicit
cycle time, reports how well the loop is keeping up, and can be driven either in
real time or by a simulated clock.
"""

from __future__ import annotations

import contextlib
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .blocks import FunctionBlock
from .clock import Clock, SimulatedClock, get_clock, use_clock
from .types import TIME

__all__ = ["IoHook", "Program", "ScanEngine", "ScanStatistics"]

IoHook = Callable[["Program"], None]


class Program(FunctionBlock):
    """Base class for a translated ``PROGRAM``.

    A program is a function block that takes no parameters and is called once
    per scan.  Generated subclasses override :meth:`scan`.
    """

    __slots__ = ()

    def scan(self) -> None:  # pragma: no cover - overridden by generated code
        """Execute one scan of the program logic."""
        raise NotImplementedError(f"{type(self).__name__} does not implement scan()")

    def __call__(self) -> Program:
        self.scan()
        return self

    def variables(self) -> dict[str, object]:
        """Every public variable of the program, for logging and debugging."""
        names: list[str] = []
        for klass in reversed(type(self).__mro__):
            names.extend(getattr(klass, "__slots__", ()) or ())
        names.extend(getattr(self, "__dict__", {}))
        seen: dict[str, object] = {}
        for name in names:
            if name.startswith("_") or name in seen:
                continue
            value = getattr(self, name, None)
            if not callable(value):
                seen[name] = value
        return seen


@dataclass
class ScanStatistics:
    """How the scan loop is behaving - the numbers an operator would ask for."""

    cycles: int = 0
    overruns: int = 0
    last_duration: float = 0.0
    total_duration: float = 0.0
    max_duration: float = 0.0
    min_duration: float = field(default=float("inf"))

    def record(self, duration: float, cycle_time: float) -> None:
        self.cycles += 1
        self.last_duration = duration
        self.total_duration += duration
        self.max_duration = max(self.max_duration, duration)
        self.min_duration = min(self.min_duration, duration)
        if duration > cycle_time:
            self.overruns += 1

    @property
    def average_duration(self) -> float:
        return self.total_duration / self.cycles if self.cycles else 0.0

    def load(self, cycle_time: float) -> float:
        """Average scan duration as a fraction of the configured cycle time."""
        return self.average_duration / cycle_time if cycle_time else 0.0

    def summarise(self, cycle_time: float) -> str:
        if not self.cycles:
            return "no scans executed"
        return (
            f"{self.cycles} scans, "
            f"avg {self.average_duration * 1e3:.3f} ms, "
            f"max {self.max_duration * 1e3:.3f} ms, "
            f"load {self.load(cycle_time) * 100:.1f}%, "
            f"{self.overruns} overrun{'s' if self.overruns != 1 else ''}"
        )


class ScanEngine:
    """Runs a :class:`Program` on a fixed cycle.

    >>> class Blink(Program):
    ...     def __init__(self):
    ...         super().__init__()
    ...         self.count = 0
    ...     def scan(self):
    ...         self.count += 1
    >>> engine = ScanEngine(Blink(), cycle_time=0.01)
    >>> engine.run_cycles(5).count
    5
    """

    def __init__(
        self,
        program: Program,
        cycle_time: TIME = 0.01,
        clock: Clock | None = None,
        on_read_inputs: IoHook | None = None,
        on_write_outputs: IoHook | None = None,
    ) -> None:
        if cycle_time <= 0:
            raise ValueError("cycle_time must be positive")
        self.program = program
        self.cycle_time = float(cycle_time)
        self.clock = clock or get_clock()
        self.on_read_inputs = on_read_inputs
        self.on_write_outputs = on_write_outputs
        self.statistics = ScanStatistics()
        self._stop = False

    # -- one cycle --------------------------------------------------------- #

    def scan_once(self) -> Program:
        """Read inputs, run the program once, write outputs."""
        started = time.perf_counter()
        if self.on_read_inputs is not None:
            self.on_read_inputs(self.program)
        self.program.scan()
        if self.on_write_outputs is not None:
            self.on_write_outputs(self.program)
        self.statistics.record(time.perf_counter() - started, self.cycle_time)
        return self.program

    # -- driving the loop -------------------------------------------------- #

    def run_cycles(self, count: int) -> Program:
        """Run exactly *count* scans.

        With a :class:`~plcforge.runtime.clock.SimulatedClock` the clock is
        advanced by one cycle time per scan, so timers behave as they would on
        the controller without the test having to wait.
        """
        simulated = self.clock if isinstance(self.clock, SimulatedClock) else None
        with use_clock(self.clock):
            for _ in range(count):
                self.scan_once()
                if simulated is not None:
                    simulated.advance(self.cycle_time)
        return self.program

    def run_for(self, duration: TIME) -> Program:
        """Run scans until *duration* seconds of scan time have elapsed."""
        return self.run_cycles(max(round(duration / self.cycle_time), 0))

    def run_forever(self, install_signal_handlers: bool = True) -> Program:
        """Run until interrupted, holding the configured cycle time.

        The loop sleeps for whatever is left of the cycle after the program has
        run.  If a scan takes longer than the cycle time the overrun is counted
        and the next scan starts immediately, which is what a controller does
        rather than drifting further behind every cycle.
        """
        self._stop = False
        if install_signal_handlers:
            self._install_signal_handlers()
        next_deadline = time.perf_counter()
        with use_clock(self.clock):
            while not self._stop:
                self.scan_once()
                next_deadline += self.cycle_time
                remaining = next_deadline - time.perf_counter()
                if remaining > 0:
                    time.sleep(remaining)
                else:
                    # Behind schedule: give up the missed slots instead of
                    # trying to catch up with a burst of back-to-back scans.
                    next_deadline = time.perf_counter()
        return self.program

    def stop(self, *_args: object) -> None:
        """Ask :meth:`run_forever` to finish the current scan and return."""
        self._stop = True

    def _install_signal_handlers(self) -> None:
        for name in ("SIGINT", "SIGTERM"):
            handler = getattr(signal, name, None)
            if handler is None:
                continue
            # Installing a handler fails outside the main thread; that is fine,
            # the caller can still stop the loop with stop().
            with contextlib.suppress(ValueError, OSError):
                signal.signal(handler, self.stop)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"ScanEngine({type(self.program).__name__}, "
            f"cycle_time={self.cycle_time * 1e3:.1f}ms, "
            f"{self.statistics.summarise(self.cycle_time)})"
        )
