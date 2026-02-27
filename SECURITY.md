# Security policy

## Supported versions

The `main` branch is the supported version. Fixes go there first, and into the
next release.

## Reporting a vulnerability

Email **alexanderhahn.br@gmail.com**. Please do not open a public issue for a
security problem.

Include:

- what the problem is and what it lets an attacker do
- a minimal input that reproduces it
- the plcforge version and Python version

You will get an acknowledgement within five working days and an assessment within
ten. If the report is valid you will be credited in the fix unless you would
rather not be.

## What counts as a vulnerability here

plcforge is a translator: it reads source files and writes source files. The
realistic attack surface is a **hostile input file**, so these are in scope:

- a source file that causes arbitrary code execution during translation
- a source file that causes plcforge to write outside the requested output
  directory (path traversal in a project or POU name)
- XML that triggers entity expansion, external entity resolution or another XML
  parser attack
- a source file that causes unbounded memory use or a hang rather than a
  diagnostic

Out of scope:

- vulnerabilities in the *control program you translate* — plcforge does not
  analyse your logic for safety or security properties
- generated code that behaves unexpectedly because the source did; that is a
  correctness bug, and a normal issue is the right place for it
- running plcforge on files you do not trust while also running the output
  without reading it

## Notes on the design

- Translation is **static**. plcforge never imports or executes a source file:
  the Python frontend uses `ast.parse` and walks the tree, it does not `exec`.
- XML is parsed with the standard library's `xml.etree.ElementTree`, which does
  not resolve external entities.
- The generated code has **no dependencies**, so it adds no supply chain of its
  own to whatever runs it.

## A word about control systems

Generated code is a translation, not a certification. plcforge is not qualified
for safety instrumented functions, and nothing it produces should be trusted in
one. Review the output the way you would review any other change to a control
system.
