# Changelog

All notable changes are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-02-27

### Added
- `plcforge scaffold`: generates a complete, installable Python repository with a
  vendored runtime, an entry point that runs the scan cycle, tests and packaging
  metadata. The result has no dependency on plcforge.
- Python frontend: lifts Python control code back into the IR, so a project can
  go SCL to Python and back and still be recognisable.
- Implicit Python locals are declared in a `VAR_TEMP` block with the type the
  resolver derived, rather than being dropped.
- `plcforge lift` as a direct route from Python to Structured Text.

### Fixed
- A `return <value>` inside a lifted function is assigned to the function result
  instead of being lost.
- The section markers that group variables (`# VAR_INPUT`) are read from the
  source that was handed in, not re-read from disk, so lifting a string works.

## [0.3.0] - 2026-02-21

### Added
- C++17 backend with a header-only runtime: types, standard blocks, bounds
  checked arrays and a scan engine.
- `CASE` over single labels becomes a `switch`; ranges become a branch chain.
- `--namespace` and `--no-runtime` for the C++ target.

### Fixed
- `LIMIT`, `MIN`, `MAX` and `SEL` deduce a common type, so mixing `REAL` and a
  `double` literal compiles.
- A negated literal loop step (`BY -1`) is folded at translation time, so the
  loop comparison is chosen statically rather than at run time.
- An undeclared `FOR` counter is declared in the generated C++, which Structured
  Text permits and C++ does not.

## [0.2.0] - 2026-02-14

### Added
- PLCopen XML frontend for Ladder Diagram and Function Block Diagram: contacts,
  coils, set/reset and negated coils, parallel branches, operator blocks,
  function block instances and `executionOrderId`.
- Edge sensitive contacts create an implicit `R_TRIG`/`F_TRIG` instance.
- Structured Text backend, which is what makes a ladder rung reviewable as text.
- Diagnostics carry a code and a hint.

### Fixed
- A block whose outputs nothing reads is still called, instead of being skipped.
- Dangling connections and unsupported body languages are reported rather than
  producing an empty POU with no explanation.

## [0.1.0] - 2026-02-07

### Added
- Structured Text / SCL frontend: full statement set, IEC operator precedence,
  structures, enumerations, multi-dimensional arrays, based and duration
  literals, direct addresses, nested comments and pragmas.
- Typed intermediate representation with a resolver that annotates every
  expression.
- Python backend with a scan-cycle runtime: injectable clocks, the standard
  function blocks and the IEC functions where Python's operators disagree.
- Error recovery: a malformed statement is reported and parsing continues.
- Command line interface: `convert`, `check`, `info`.

[Unreleased]: https://github.com/alex-hahn/plcforge/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/alex-hahn/plcforge/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/alex-hahn/plcforge/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/alex-hahn/plcforge/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/alex-hahn/plcforge/releases/tag/v0.1.0
