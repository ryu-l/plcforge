# The runtime

The runtime is what makes generated code behave like a controller rather than
like a script that happens to contain the same arithmetic. It is deliberately
small, has no dependencies, and is vendored into any project produced by
`plcforge scaffold` so the result does not depend on plcforge at all.

## The scan cycle

A PLC does the same three things forever: read the inputs, run the program,
write the outputs. `ScanEngine` reproduces that loop.

```python
from plant import ConveyorLine
from plant.runtime import ScanEngine

engine = ScanEngine(ConveyorLine(), cycle_time=0.01)   # 10 ms
engine.on_read_inputs  = lambda program: read_from_fieldbus(program)
engine.on_write_outputs = lambda program: write_to_fieldbus(program)
engine.run_forever()
```

`run_forever` holds the configured cycle time and installs handlers for `SIGINT`
and `SIGTERM`, so Ctrl+C and `docker stop` both finish the current scan and
return cleanly.

When a scan overruns, the engine counts it and starts the next scan immediately
rather than trying to catch up with a burst — which is what a controller does,
and what keeps a slow scan from cascading.

```python
engine.statistics.summarise(engine.cycle_time)
# '4210 scans, avg 0.062 ms, max 0.310 ms, load 0.6%, 0 overruns'
```

## Clocks

Every timer reads the clock through `plcforge.runtime.clock`, never
`time.monotonic` directly. That indirection is what makes control logic testable.

```python
from plant.runtime import ScanEngine, SimulatedClock

clock = SimulatedClock()
engine = ScanEngine(ConveyorLine(clock), cycle_time=0.05, clock=clock)

engine.run_cycles(42)        # 2.1 seconds of plant time, in microseconds of real time
```

`run_cycles` advances a simulated clock by exactly one cycle per scan, so a two
second ramp is two seconds of plant time regardless of how fast the test machine
is. Timer assertions become exact:

```python
with use_clock(clock):
    timer = TON()
    timer(IN=True, PT=1.0)
    clock.advance(0.999); timer(IN=True); assert timer.Q is False
    clock.advance(0.001); timer(IN=True); assert timer.Q is True
```

Three clocks are available:

| Clock | Use |
|---|---|
| `MonotonicClock` | production; immune to system clock adjustments |
| `SimulatedClock` | tests; moves only when `advance()` is called |
| any object with `now() -> float` | your own, for replay or hardware time |

The active clock is thread-local, so two programs can run on different clocks in
the same process. A block can also be given its own clock at construction, which
overrides the ambient one.

## Standard function blocks

All of them follow the same protocol: call the instance with its inputs, read the
outputs off the instance. That mirrors the Structured Text notation exactly.

```python
delay = TON()
delay(IN=start, PT=T("T#5s"))
if delay.Q:
    ...
```

| Block | Inputs | Outputs |
|---|---|---|
| `TON` on-delay | `IN`, `PT` | `Q`, `ET` |
| `TOF` off-delay | `IN`, `PT` | `Q`, `ET` |
| `TP` pulse | `IN`, `PT` | `Q`, `ET` |
| `CTU` up counter | `CU`, `R`, `PV` | `Q`, `CV` |
| `CTD` down counter | `CD`, `LD`, `PV` | `Q`, `CV` |
| `CTUD` up/down counter | `CU`, `CD`, `R`, `LD`, `PV` | `QU`, `QD`, `CV` |
| `R_TRIG` rising edge | `CLK` | `Q` |
| `F_TRIG` falling edge | `CLK` | `Q` |
| `SR` set dominant | `S1`, `R` | `Q1` |
| `RS` reset dominant | `S`, `R1` | `Q1` |

Counters count **edges**, not levels: holding `CU` high increments once, as on a
controller. `R_TRIG` and `F_TRIG` produce exactly one scan of output. `reset()`
returns any block to its power-on state.

## Types

| IEC | Python | C++ |
|---|---|---|
| `BOOL` | `bool` | `bool` |
| `SINT` `INT` `DINT` `LINT` | `int` | `int8_t` … `int64_t` |
| `USINT` `UINT` `UDINT` `ULINT` | `int` | `uint8_t` … `uint64_t` |
| `BYTE` `WORD` `DWORD` `LWORD` | `int` | `uint8_t` … `uint64_t` |
| `REAL` / `LREAL` | `float` | `float` / `double` |
| `TIME` | `float` (seconds) | `double` (seconds) |
| `STRING` | `str` | `std::string` |
| `ARRAY [a..b] OF T` | `Array` | `plcforge::Array<T, a, b>` |
| `STRUCT` | `@dataclass(Struct)` | `struct` |
| enumeration | `IntEnum` | `enum class` |

The Python aliases are aliases on purpose: a `DINT` is a Python `int`, so
arithmetic runs at full speed and no wrapper object is allocated per scan.

Where the fixed width matters, `wrap(value, "INT")` reproduces controller
overflow; `plcforge convert --strict-integers` emits it automatically.

## Arrays

`Array` keeps the declared index origin and checks bounds:

```python
history = Array([(1, 5)], 0.0)     # ARRAY [1..5] OF REAL
history[1] = 1.5                   # the first element
history[0]                         # IndexError: index 0 outside 1..5

grid = Array([(0, 1), (1, 3)], 0)  # ARRAY [0..1, 1..3] OF INT
grid[1, 3] = 7
```

`filled(array, values)` writes an initialiser list in declaration order, which is
what a generated `ARRAY [1..3] OF INT := [10, 20, 30]` turns into.

## Functions

`plcforge.runtime.functions` implements the standard library, including the
handful of operators where Python disagrees with IEC — `idiv`, `imod`,
`iec_range` — and the one-based string functions. See
[semantics.md](semantics.md) for why each exists.

## Wiring to real I/O

The engine does not know what a fieldbus is, on purpose. Two hooks are the whole
interface:

```python
def read_inputs(program: ConveyorLine) -> None:
    program.lineEnable  = plc_input(0, 0)
    program.rawSpeedPot = analog_input(1)

def write_outputs(program: ConveyorLine) -> None:
    plc_output(0, 0, program.infeed.active)

engine = ScanEngine(ConveyorLine(), cycle_time=0.01)
engine.on_read_inputs, engine.on_write_outputs = read_inputs, write_outputs
engine.run_forever()
```

Keeping I/O outside the program is also what lets the same logic run unchanged
in a test, in a simulation and against hardware.

## A note on scope

This runtime reproduces IEC semantics faithfully enough to test and simulate
control logic. It is not a real-time system: CPython gives no scheduling
guarantees, and neither does a general purpose operating system. Treat the
overrun counter as a diagnostic, not as a guarantee, and do not put this in the
path of a safety function.
