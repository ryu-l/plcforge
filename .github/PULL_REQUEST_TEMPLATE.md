## What this changes

<!-- The why. The diff already shows the what. -->

## How it was verified

<!-- For a translation change: which test runs the generated program? -->

- [ ] `pytest` passes
- [ ] `ruff check .` is clean
- [ ] `mypy` is clean
- [ ] A test covers the change, and it would have failed before
- [ ] `CHANGELOG.md` has a line under `Unreleased`

## If this changes a translation

- [ ] The relevant IEC 61131-3 rule is named in the test or the comment
- [ ] [`docs/semantics.md`](docs/semantics.md) is updated if a new disagreement
      between IEC and a target language is involved
- [ ] [`docs/language-support.md`](docs/language-support.md) is updated if the
      supported surface changed
