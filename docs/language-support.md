# Language support

What translates, what does not, and what happens when plcforge meets something
it cannot handle. The short answer to the last one: it reports a diagnostic and
keeps going. Nothing is dropped silently.

## Input languages

| Language | Extensions | Route in |
|---|---|---|
| Structured Text / SCL | `.st` `.scl` `.iecst` `.exp` | Native lexer and parser |
| Ladder Diagram (KOP / LD) | `.xml` | PLCopen TC6 XML (IEC 61131-10) |
| Function Block Diagram (FUP / FBD) | `.xml` | PLCopen TC6 XML |
| Python | `.py` | Lifted from the runtime conventions |

Graphical languages have no text to parse, so the way in is the interchange
format the engineering tools export. In TIA Portal, CODESYS, TwinCAT and
Beremiz this is *Export → PLCopen XML*.

## Structured Text

### Declarations

| Construct | Status |
|---|---|
| `PROGRAM`, `FUNCTION_BLOCK`, `FUNCTION` (with return type) | ✅ |
| `VAR`, `VAR_INPUT`, `VAR_OUTPUT`, `VAR_IN_OUT`, `VAR_TEMP`, `VAR_STAT`, `VAR_GLOBAL`, `VAR_EXTERNAL` | ✅ |
| `CONSTANT`, `RETAIN`, `NON_RETAIN`, `PERSISTENT` qualifiers | ✅ |
| Multiple names per declaration (`a, b : INT;`) | ✅ |
| Initial values, including structure and array initialisers | ✅ |
| Repetition in array initialisers (`[5(0.0)]`) | ✅ |
| `AT %IX0.0` direct addresses | ✅ |
| `TYPE ... STRUCT ... END_STRUCT ... END_TYPE` | ✅ |
| `TYPE ... : (A, B := 5, C); END_TYPE` enumerations | ✅ |
| Alias types (`TYPE Level : INT := 0; END_TYPE`) | ✅ translated as a one-field structure |
| Multi-dimensional `ARRAY [0..1, 1..3] OF T` | ✅ |
| `STRING[80]`, `WSTRING` | ✅ |
| `EXTENDS`, `IMPLEMENTS` | ⚠️ reported (`PF115`), members not inherited |
| `INTERFACE`, `METHOD`, `PROPERTY` | ❌ not parsed |

### Statements and expressions

| Construct | Status |
|---|---|
| Assignment, function block invocation | ✅ |
| Named parameters (`IN :=`) and output parameters (`Q =>`) | ✅ |
| `IF` / `ELSIF` / `ELSE` | ✅ |
| `CASE` with lists, ranges (`5..9`) and enumeration labels | ✅ |
| `FOR ... TO ... BY ... DO`, including a negative step | ✅ |
| `WHILE`, `REPEAT ... UNTIL` | ✅ |
| `EXIT`, `CONTINUE`, `RETURN` | ✅ |
| Full IEC operator precedence, `**` right-associative | ✅ |
| Member access, array indexing, nested calls | ✅ |
| Pointer dereference `^` | ⚠️ parsed, the base expression is kept |
| Labels and `GOTO` | ❌ not parsed |

### Literals

| Form | Example | Status |
|---|---|---|
| Integer, with digit separators | `1_000_000` | ✅ |
| Based | `16#FF`, `2#1010_1010`, `8#17` | ✅ notation preserved |
| Typed | `INT#5`, `WORD#16#FF` | ✅ |
| Real, with exponent | `1.5`, `2.5e-3` | ✅ |
| Duration | `T#1h30m`, `TIME#500ms`, `T#-250ms` | ✅ |
| Boolean | `TRUE`, `FALSE` | ✅ |
| String with `$` escapes | `'line$Nnext$$'` | ✅ |
| Date and time of day | `DATE#2026-02-14`, `TOD#09:15:00` | ⚠️ kept as text |

### Comments and pragmas

| Form | Status |
|---|---|
| `(* block *)`, including nesting | ✅ carried into the output |
| `// line` | ✅ carried into the output |
| `{attribute 'hidden'}` pragmas | ✅ skipped |

## Ladder Diagram

| Element | Status |
|---|---|
| `leftPowerRail`, `rightPowerRail` | ✅ |
| Contacts, normally closed contacts | ✅ |
| Rising and falling edge contacts | ✅ an `R_TRIG`/`F_TRIG` instance is declared implicitly |
| Coils | ✅ |
| Set and reset coils | ✅ become conditional assignments |
| Negated coils | ✅ |
| Series and parallel branches | ✅ `AND` and `OR` |
| Function and function block boxes in a rung | ✅ |
| `inVariable` boxes holding an expression | ✅ parsed as Structured Text |
| `return` with a condition | ✅ |
| `jump` to a label | ⚠️ reported (`PF126`), not translated |
| Connectors and continuations | ✅ |

## Function Block Diagram

| Element | Status |
|---|---|
| `inVariable`, `outVariable`, `inOutVariable` | ✅ |
| Operator blocks (`AND` `OR` `XOR` `NOT` `ADD` `SUB` `MUL` `DIV` `MOD` `GT` `GE` `LT` `LE` `EQ` `NE` `EXPT` `MOVE` `SEL` `LIMIT` `MUX`) | ✅ folded into expressions |
| Standard function block boxes | ✅ call plus output reference |
| User function block instances | ✅ |
| Negated pins | ✅ |
| `executionOrderId` | ✅ respected; otherwise ordered by position |
| Blocks whose outputs nothing reads | ✅ still called |

## Python

The lifter expects the conventions the Python backend emits, which are also
reasonable conventions for hand-written code:

| Convention | Meaning |
|---|---|
| Class deriving from `FunctionBlock` | a `FUNCTION_BLOCK` |
| Class deriving from `Program` | a `PROGRAM` |
| Module level function | a `FUNCTION` |
| Class deriving from `IntEnum` | an enumeration type |
| `@dataclass` deriving from `Struct` | a structure type |
| Annotated attributes in `__init__` | the variable declarations |
| `# VAR_INPUT` style comments | the variable sections |
| `body()` / `scan()` | the POU body |

| Construct | Status |
|---|---|
| `if` / `elif` / `else` | ✅ |
| `while`, and `while True: ... if c: break` | ✅ the second becomes `REPEAT ... UNTIL` |
| `for x in iec_range(...)` / `range(...)` | ✅ the exclusive bound is corrected |
| `break`, `continue`, `return`, `return <value>` | ✅ |
| Augmented assignment (`x += 1`) | ✅ expanded |
| Chained comparison (`a < b < c`) | ✅ split with `AND` |
| Conditional expression (`a if c else b`) | ✅ becomes `SEL` |
| Implicit locals | ✅ declared in a `VAR_TEMP` block with the resolved type |
| `idiv` / `imod` / `T()` / `convert()` calls | ✅ folded back to operators and literals |
| Comprehensions, generators, `with`, `try`, `lambda`, `yield` | ❌ reported (`PF145`) |
| Decorators other than `@dataclass` | ❌ ignored |

## Output targets

| Target | Produces |
|---|---|
| `python` | One module using the plcforge runtime |
| `cpp` | One header plus `plcforge_runtime.hpp` |
| `st` | Normalised Structured Text |
| `scaffold` | A complete Python repository with a vendored runtime |

## Standard library

Function blocks: `TON` `TOF` `TP` `CTU` `CTD` `CTUD` `R_TRIG` `F_TRIG` `SR` `RS`
(`RTC` is recognised by the type resolver but has no runtime implementation).

Functions: `ABS` `SQRT` `LN` `LOG` `EXP` `SIN` `COS` `TAN` `ASIN` `ACOS` `ATAN`
`ATAN2` `MIN` `MAX` `LIMIT` `SEL` `MUX` `MOVE` `TRUNC` `ROUND` `SHL` `SHR` `ROL`
`ROR` `LEN` `LEFT` `RIGHT` `MID` `CONCAT` `INSERT` `DELETE` `REPLACE` `FIND`,
and the full conversion family in both spellings (`INT_TO_REAL` and `TO_REAL`).

## What happens at the edges

Every limitation above produces a diagnostic with a code and, where there is one,
a way forward:

```
MotorLogic.xml: warning [PF124]: POU 'Sequencer' uses SFC, which plcforge does
    not translate; the interface is kept and the body is left empty
    hint: Export the POU as ST, LD or FBD from the engineering tool.
```

Use `plcforge check --strict` in CI to turn those warnings into a build failure
once your project is clean.
