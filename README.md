# plcforge

**Translate IEC 61131-3 control programs into real Python projects — and back again.**

PLC code is locked inside the engineering tool. You cannot unit test a ladder
rung in CI, you cannot diff a function block diagram in a pull request, and you
cannot reuse a proven control algorithm in a simulation without rewriting it by
hand — which is exactly where the behaviour quietly drifts.

plcforge translates the program instead.

```bash
plcforge convert Conveyor.scl --target python --out ./generated
plcforge check   ./plc_src --summary
```

## Why the translation is trustworthy

Mapping `IF` onto `if` is trivial. The value is in the places where the obvious
mapping compiles cleanly and computes the wrong answer:

| IEC 61131-3 says | The naive Python is | plcforge emits |
|---|---|---|
| `-7 / 2` is `-3` (truncates toward zero) | `-7 // 2` → `-4` ❌ | `idiv(-7, 2)` → `-3` ✅ |
| `-7 MOD 2` is `-1` (sign of the dividend) | `-7 % 2` → `1` ❌ | `imod(-7, 2)` → `-1` ✅ |
| `FOR i := 1 TO 4` runs four times | `range(1, 4)` → three ❌ | `iec_range(1, 4)` ✅ |
| `ARRAY [1..5]` is indexed from one | `list[0]` is the first ❌ | `Array([(1, 5)], …)` ✅ |
| `AND` is bitwise on `WORD`, logical on `BOOL` | always `and` ❌ | `&` or `and`, by type ✅ |

Each is covered by a test that runs the generated program.

## Testing control logic like software

Generated code runs on a simulated clock, so timer behaviour is exact and
nothing waits in real time:

```python
clock = SimulatedClock()
engine = ScanEngine(ConveyorLine(clock), cycle_time=0.05, clock=clock)
engine.run_cycles(42)          # 2.1 s of plant time, instantly
```

## Status

Early. Structured Text in, Python out. Ladder and Function Block Diagram are
next, and a C++ target after that.

## Installation

```bash
pip install -e ".[dev]"
pytest
```

Python 3.10 or newer. No runtime dependencies, and neither has the generated
code.

## Licence

[PolyForm Noncommercial License 1.0.0](LICENSE.md) — free for personal use,
research, education and non-profits. Commercial use requires a separate licence;
see [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md).
