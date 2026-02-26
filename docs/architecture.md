# Architecture

plcforge is a compiler with an unusually wide waist: many input languages, many
output languages, and one intermediate representation in the middle that both
sides agree on.

```
              frontends                 core                    backends
  ┌───────────────────────┐   ┌────────────────────┐   ┌──────────────────────┐
  │ SCL / ST      lexer   │   │  IR (typed)        │   │ Python  emitter      │
  │               parser  │──▶│  link user types   │──▶│ C++     emitter      │
  │ PLCopen XML   reader  │   │  resolve types     │   │ ST      emitter      │
  │ Python        lifter  │   │  diagnostics       │   │ scaffold (project)   │
  └───────────────────────┘   └────────────────────┘   └──────────────────────┘
```

Adding a language costs exactly one module. Nothing else changes.

## Module map

| Module | Responsibility |
|---|---|
| `plcforge.ir.nodes` | The IR: expressions, statements, declarations, POUs |
| `plcforge.ir.types` | The IEC type system and the widening rules |
| `plcforge.ir.stdlib` | Signatures of the standard blocks and functions |
| `plcforge.ir.link` | Resolves user type names across files |
| `plcforge.ir.validate` | Annotates expressions with types, reports semantic problems |
| `plcforge.frontends.st` | Lexer and recursive descent parser for SCL/ST |
| `plcforge.frontends.plcopen` | PLCopen XML reader for Ladder and FBD |
| `plcforge.frontends.python` | Lifts Python back into the IR |
| `plcforge.backends.python` | Python emitter and project scaffolding |
| `plcforge.backends.cpp` | C++17 emitter |
| `plcforge.backends.st` | Structured Text writer |
| `plcforge.backends.naming` | Identifier mapping shared by every backend |
| `plcforge.runtime` | Scan engine, clocks, standard blocks, IEC functions |
| `plcforge.pipeline` | Wires the stages together |
| `plcforge.cli` | Command line interface |

## The intermediate representation

The IR is close to Structured Text, not to a compiler's SSA form. That is a
deliberate trade: optimisation is not the goal, and a control engineer has to be
able to read the generated code and recognise their own program in a review.

```python
Pou(
    name="ConveyorSegment",
    kind=PouKind.FUNCTION_BLOCK,
    var_blocks=[VarBlock(kind=VarKind.INPUT, declarations=[VarDecl(...)])],
    body=[Invoke(...), Case(...), Assign(...)],
    source_language="ST",
)
```

Three properties matter downstream:

- **Comments are nodes, not whitespace.** `VarDecl.comment` and `Stmt.comment`
  carry the engineer's own annotations into every target language.
- **Every node knows where it came from.** `SourceSpan` makes a diagnostic point
  at the original line, even after the program has crossed two languages.
- **Expressions carry their type.** `Expr.inferred_type` is filled in by the
  resolver and is what lets a backend choose between `and` and `&`.

## The type resolver

The resolver is not decoration. It is the reason the output is correct rather
than merely plausible:

| Decision | Needs to know |
|---|---|
| `AND` → `and` or `&` | whether the operands are `BOOL` or a bit string |
| `/` → `/` or `idiv()` | whether both operands are integers |
| `MOD` → `%` or `imod()` | the same |
| `timer.Q` → `BOOL` | the signature of `TON` |
| `TO_REAL(x)` → `REAL` | the conversion family |

Where the resolver cannot decide it leaves `inferred_type` as `None`, and the
backends fall back to the boolean reading — because that is what the
overwhelming majority of PLC code means, and because the result stays readable.

Unresolved names are reported once per name, not once per use. One missing
library file should produce one diagnostic, not two hundred.

## Diagnostics

Nothing raises on the first problem. A translation collects diagnostics, keeps
going where it can, and lets the caller decide whether the result is good enough.
For a real project that matters: one unsupported block should not hide the two
hundred that convert cleanly.

```python
diagnostics = DiagnosticBag()
project = load_sources([path], diagnostics)
prepare(project, diagnostics)

if diagnostics.has_errors:
    diagnostics.report()
```

Codes are grouped by stage, which makes them easy to filter in CI:

| Range | Stage |
|---|---|
| `PF0xx` | Pipeline and file handling |
| `PF1xx` | Frontends (lexing, parsing, XML, Python) |
| `PF2xx` | Linking and type resolution |
| `PF3xx` | Python backend |
| `PF4xx` | Structured Text backend |
| `PF5xx` | C++ backend |

## Graphical languages

A ladder network is a small directed graph. plcforge indexes the elements by
`localId` and walks **backwards** from each output:

1. Collect the sinks: coils, output variables, jumps, returns, and blocks whose
   outputs nothing reads.
2. Order them by `executionOrderId` where the exporter provided one, otherwise
   top to bottom by position.
3. For each sink, resolve its `connectionPointIn`:
   - a **contact** is `AND(incoming, variable)`, negated where marked,
   - several connections on one input point are **parallel branches**, so `OR`,
   - a **left power rail** is `TRUE` and disappears from the expression,
   - a **block** with an instance name emits a call statement first and then
     contributes `instance.PIN` as the expression.

The result is one assignment per coil, in the shape an engineer would have
written by hand:

```pascal
motor := (startButton OR motor) AND NOT stopButton AND NOT overload;
```

Function block calls are memoised per rung, so a timer read from two places is
still called once.

## Adding a language

**A new frontend** needs one function:

```python
def parse_file(path: str, diagnostics: DiagnosticBag) -> Project: ...
```

Register the extension in `pipeline.SOURCE_EXTENSIONS` and add the reader to
`pipeline._read_frontend`. The linker, the resolver and every backend then work
without changes.

**A new backend** needs one function:

```python
def emit_project(project: Project, options, diagnostics) -> str | dict[str, str]: ...
```

Add a branch to `pipeline.translate` and an entry to `available_targets()`.
Reuse `backends.naming` so identifiers are mangled the same way everywhere.

The thing to get right in a backend is not the syntax — it is the handful of
places where the target language disagrees with IEC 61131-3. Those are catalogued
in [semantics.md](semantics.md), and each has a test that runs the generated
program.

## Testing strategy

The test suite **executes what it generates**. A golden file proves the text has
not changed; running the program proves it still computes the same thing.

- `test_python_backend.py` compiles and runs each generated module
- `test_cpp_backend.py` compiles with `-Wall -Wextra -Werror` and runs the binary
- `test_roundtrip.py` drives the same scenario through SCL → Python → ST → Python
  and asserts the two traces are identical
- `test_runtime_blocks.py` pins the timer and counter semantics against a
  simulated clock

That last point is why the runtime takes an injectable clock: a `TON` test asserts
that `Q` is low at 999 ms and high at 1000 ms, deterministically, in microseconds
of wall time.
