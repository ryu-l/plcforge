# Where IEC 61131-3 and the target languages disagree

This is the document that matters. Mapping `IF` onto `if` is trivial; the value
of a translator is in the places where the obvious mapping compiles cleanly and
computes the wrong answer.

Every rule below is enforced by a test that **runs the generated program**.

---

## 1. Integer division truncates toward zero

IEC 61131-3 specifies truncation toward zero. Python's `//` floors, so the two
disagree for every division with exactly one negative operand.

| Expression | IEC | Python `//` | plcforge emits |
|---|---|---|---|
| `7 / 2` | `3` | `3` | `idiv(7, 2)` → `3` |
| `-7 / 2` | `-3` | `-4` ❌ | `idiv(-7, 2)` → `-3` |
| `7 / -2` | `-3` | `-4` ❌ | `idiv(7, -2)` → `-3` |

C++ has truncated integer division since C++11, so the C++ backend emits `/`
directly.

A silent off-by-one in a scaling calculation is the kind of defect that shows up
as a millimetre of drift per cycle. It is worth the helper call.

---

## 2. `MOD` follows the sign of the dividend

| Expression | IEC | Python `%` | plcforge emits |
|---|---|---|---|
| `7 MOD 2` | `1` | `1` | `imod(7, 2)` |
| `-7 MOD 2` | `-1` | `1` ❌ | `imod(-7, 2)` → `-1` |
| `7 MOD -2` | `1` | `-1` ❌ | `imod(7, -2)` → `1` |

The runtime guarantees the identity that IEC requires:

```
dividend = idiv(dividend, divisor) * divisor + imod(dividend, divisor)
```

C++ `%` already follows the dividend, so the C++ backend emits `%`.

---

## 3. `FOR` bounds are inclusive

`FOR i := 1 TO 4 DO` runs **four** times. `range(1, 4)` runs three. The Python
backend emits `iec_range`, which is inclusive and handles a negative step:

```pascal
FOR i := 10 TO 1 BY -3 DO ... END_FOR;
```

```python
for self.i in iec_range(10, 1, -3):    # 10, 7, 4, 1
```

The C++ backend folds a literal step at translation time and picks the matching
comparison:

```cpp
for (i = 10; i >= 1; i += -3) { ... }
```

With a non-constant step the comparison has to follow the sign at run time:

```cpp
for (i = start; (step > 0 ? i <= stop : i >= stop); i += step) { ... }
```

---

## 4. Arrays keep their declared index origin

`ARRAY [1..5] OF REAL` is indexed from one. `ARRAY [-10..10]` is indexed from
minus ten. Shifting everything to a zero-based list would move every index in the
program by a silent constant, so plcforge keeps the bounds:

```python
self.history: Array = Array([(1, 5)], 0.0)
self.history[1]        # the first element, as declared
self.history[0]        # IndexError: index 0 outside 1..5
```

```cpp
plcforge::Array<plcforge::REAL, 1, 5> history;
history[0];            // throws std::out_of_range
```

An out-of-bounds access raises rather than silently reading a neighbouring
element. On a controller this is a fault; it should be a fault here too.

Multi-dimensional arrays are row-major and indexed with a tuple:
`grid[1, 2]` for `ARRAY [0..1, 1..3] OF INT`.

---

## 5. `AND`, `OR`, `XOR` are logical or bitwise depending on the type

IEC overloads them: on `BOOL` they are logical, on `BYTE`/`WORD`/`DWORD`/`LWORD`
they are bitwise. The resolver decides from the declared types:

| Operand types | Python | C++ |
|---|---|---|
| `BOOL AND BOOL` | `and` | `&&` |
| `WORD AND WORD` | `&` | `&` |
| `BOOL XOR BOOL` | `^` | `!=` |
| `WORD XOR WORD` | `^` | `^` |
| `NOT BOOL` | `not` | `!` |
| `NOT WORD` | `~` | `~` |

When the resolver cannot determine the type — usually a variable from a library
plcforge has not seen — it falls back to the **logical** reading and the code
stays readable. That is the right default: almost all PLC boolean logic is
`BOOL`, and a wrong bitwise guess on booleans would be both wrong and unreadable.

---

## 6. Integer types have a fixed width

`INT` rolls over at 32767 on a controller. A Python `int` grows forever, so a
counter that would wrap in the field keeps counting in the translation.

This is off by default, because most translated code never approaches the limit
and the wrapping calls add noise. Turn it on when the overflow behaviour is part
of the design:

```bash
plcforge convert Accumulator.scl --strict-integers
```

```python
self.total = wrap(self.total + 30000, "INT")     # 30000, then -5536
```

The C++ backend needs no flag: `plcforge::INT` is `std::int16_t` and wraps by
construction.

---

## 7. Strings are indexed from one

`MID`, `LEFT`, `RIGHT`, `INSERT`, `DELETE`, `REPLACE` and `FIND` all count from
one, and `FIND` returns `0` when the needle is absent rather than a negative
index. The runtime implements the IEC convention, not the Python one:

```python
MID("conveyor", 3, 4)     # 'vey'   - four is the position, three the length
FIND("conveyor", "vey")   # 4       - one-based
FIND("conveyor", "x")     # 0       - not found
```

---

## 8. Durations are seconds

`TIME` maps to a float in seconds in both backends. `T#1s500ms` becomes
`T("T#1s500ms")` in Python (evaluated once, at construction) and `1.5` with a
comment in C++.

Seconds were chosen over milliseconds because every timer comparison then stays
in the same unit as the scan clock, and because a `REAL` computed against a `TIME`
does not need a scale factor nobody remembers.

`format_time` renders seconds back into IEC notation, so a value read from a
running program can be pasted straight into the source.

---

## 9. Identifiers are case insensitive in IEC and case sensitive in the targets

`Motor` and `motor` are the same variable in Structured Text. In Python and C++
they are two different names. plcforge always emits the spelling used in the
**declaration**, whatever spelling the reference used.

Where a name genuinely collides — an enumeration member `Running` and a variable
`running` — the source is ambiguous in IEC too, and plcforge reports it:

```
warning [PF209]: 'running' shadows enumeration member 'ConveyorState.Running'
    hint: IEC identifiers are case insensitive, so the two names collide.
```

Names that collide with a target language keyword get a trailing underscore:
`class` becomes `class_`, `lambda` becomes `lambda_`.

---

## 10. Direct addresses become readable names

`%IX0.0` is not an identifier in any target language. plcforge maps the area and
the offset into a name and keeps the original in a comment:

| Address | Name |
|---|---|
| `%IX0.0` | `input_x0_0` |
| `%QW12` | `output_w12` |
| `%MD100` | `memory_d100` |

```python
self.start: BOOL = False    # at %IX0.0
```

The comment is what lets a reviewer match the translation against the hardware
configuration.

---

## 11. Function results are assigned to a variable named after the function

Structured Text has no value-carrying `RETURN`; a function assigns to its own
name. The Python backend keeps that shape, and the lifter turns a Python `return`
back into it:

```pascal
FUNCTION ScaleAnalog : REAL
    ScaleAnalog := minimum + span * clamped;
END_FUNCTION
```

```python
def ScaleAnalog(...) -> REAL:
    ScaleAnalog = 0.0
    ScaleAnalog = minimum + span * clamped
    return ScaleAnalog
```

---

## 12. Python locals are implicit; Structured Text needs declarations

Assigning to a new name creates a variable in Python. In IEC it is an error. When
lifting Python back to ST, plcforge runs the type resolver first and then declares
what the Python code created:

```python
def body(self):
    dry = self.suctionPressure < self.minPressure    # implicit in Python
```

```pascal
VAR_TEMP
    dry : BOOL;  (* Local introduced by the Python source. *)
END_VAR
    dry := suctionPressure < minPressure;
```

The type comes from the resolved expression, not from a guess.

---

## What is deliberately not translated

- **Instruction List and SFC bodies.** The interface is preserved and a
  diagnostic names the POU. Guessing at a state chart is worse than saying so.
- **`EXTENDS` / `IMPLEMENTS`.** Vendor inheritance is reported, not flattened.
- **Pointers and references.** `^` dereference is parsed and the base is kept;
  pointer arithmetic has no IEC-portable meaning to translate.
- **`JMP` to a label in ladder.** Reported with a hint to restructure with
  `IF`/`CASE` or export the POU as ST.

In every case the rule is the same: report it, keep translating everything else,
and never pretend.
