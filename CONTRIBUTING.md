# Contributing

Thank you for looking. Bug reports, failing example files and small focused
patches are all genuinely useful.

## The most valuable contribution

**A PLC source file that plcforge gets wrong.** A minimal `.scl` or PLCopen XML
export that reproduces the problem is worth more than a paragraph describing it,
because it can go straight into the test suite.

Please strip anything confidential first — a ten line reproduction is better than
a real project file anyway.

## Setup

```bash
git clone https://github.com/alex-hahn/plcforge.git
cd plcforge
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Python 3.10 or newer. A C++ compiler is optional: the C++ tests skip themselves
when there is none on `PATH`, but they are the ones that catch generated code
that does not compile, so install one if you are touching that backend.

## Before opening a pull request

```bash
ruff check .        # style and common mistakes
mypy                # type check, must be clean
pytest              # the whole suite
```

CI runs the same three on Linux, macOS and Windows across Python 3.10 to 3.13,
plus a job that translates every example, scaffolds a project, runs its tests and
compiles the C++ output with `-Wall -Wextra -Werror`.

## How the tests are written

The suite **runs what it generates**. A golden file proves the text has not
changed; running the program proves it still computes the same thing. A new
backend feature needs a test that executes the output:

```python
def test_integer_division_truncates_toward_zero(self, tmp_path) -> None:
    module, code = generate("... FUNCTION Halve : DINT ...", tmp_path, "gen_idiv")
    assert "idiv(" in code
    assert module.Halve(-7) == -3, "IEC truncates toward zero; // would give -4"
```

For anything with timing, use `SimulatedClock` so the assertion is exact and the
test does not wait.

## Adding a language

Both directions are one module. See
[docs/architecture.md](docs/architecture.md#adding-a-language) for the interface;
the short version:

- **A frontend** implements `parse_file(path, diagnostics) -> Project` and gets
  registered in `pipeline.SOURCE_EXTENSIONS`.
- **A backend** implements `emit_project(project, options, diagnostics)` and gets
  a branch in `pipeline.translate`.

The hard part of a backend is never the syntax. It is the handful of places where
the target language disagrees with IEC 61131-3 — see
[docs/semantics.md](docs/semantics.md), and add to that document if you find a
new one.

## Style

- Follow the code that is already there. `ruff` settles the mechanical questions.
- Comments explain *why*, not *what*. If a line needs a comment to say what it
  does, rewrite the line.
- Diagnostics get a code (`PFnnn`), a message that says what is wrong, and a hint
  that says what to do about it wherever there is one.
- Never drop a construct silently. Report it and keep translating the rest.
- Generated code is read by control engineers. Keep their variable names, keep
  their comments, keep the structure recognisable.

## Commits and pull requests

- One concern per pull request.
- Explain the *why* in the description; the diff already shows the *what*.
- If behaviour changes, add the test that would have failed before.
- Add a line to `CHANGELOG.md` under `Unreleased`.

## Licensing of contributions

plcforge is licensed under the [PolyForm Noncommercial License
1.0.0](LICENSE.md) with commercial licences available separately. By
contributing you agree that your contribution may be distributed under those
terms, including in commercially licensed copies.

Contributing does not grant you a commercial licence, and it is not a condition
of obtaining one. See [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md).

## Code of conduct

Be decent to each other. The full text is in
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
