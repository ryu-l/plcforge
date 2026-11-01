"""Run bounded fault-injection controls in disposable copies, never this checkout.

Usage: <venv>/bin/python scripts/verify_offline_mutations.py
A killed mutant requires an assertion failure, not just any nonzero exit code.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]
NUMERIC = "tests/test_offline_differential.py::test_numeric_matrix_against_oracle_and_cpp[direct]"
TIMER = "tests/test_offline_differential.py::test_timer_edges_reset_and_instance_isolation[direct]"
NAMING = "tests/test_expected_logic.py::TestIdentifierMapping::test_collisions_are_numbered_from_two_without_disturbing_new_names"
MUTATIONS = [
    (
        "signed-division",
        "src/plcforge/runtime/functions.py",
        "return -quotient if (dividend < 0) != (divisor < 0) else quotient",
        "return dividend // divisor",
        NUMERIC,
    ),
    (
        "exclusive-loop",
        "src/plcforge/runtime/functions.py",
        "return iter(range(start, stop + (1 if step > 0 else -1), step))",
        "return iter(range(start, stop, step))",
        NUMERIC,
    ),
    (
        "timer-boundary",
        "src/plcforge/runtime/blocks.py",
        "self.Q = self.ET >= self.PT",
        "self.Q = self.ET > self.PT",
        TIMER,
    ),
    ("name-collision", "src/plcforge/backends/naming.py", "index = 2", "index = 3", NAMING),
]


def run(directory, selectors):
    env = dict(os.environ, PYTHONPATH=str(directory / "src"), PYTHONDONTWRITEBYTECODE="1")
    # Verify import origin so an editable installation cannot silently defeat injection.
    probe = subprocess.run(
        [sys.executable, "-c", "import plcforge; print(plcforge.__file__)"],
        cwd=directory,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert probe.returncode == 0 and str(directory / "src") in probe.stdout, probe
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=short", "--junitxml=result.xml", *selectors],
        cwd=directory,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    print(result.stdout, end="")
    print(result.stderr, end="")
    print(f"exit_code={result.returncode}")
    return result


def copy_checkout(directory):
    shutil.copytree(ROOT / "src", directory / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "tests", directory / "tests", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(ROOT / "pyproject.toml", directory / "pyproject.toml")


def main():
    with tempfile.TemporaryDirectory(prefix="plcforge-injection-") as temporary:
        base = pathlib.Path(temporary)
        control = base / "control"
        control.mkdir()
        copy_checkout(control)
        print("=== unmodified control ===", flush=True)
        result = run(control, list(dict.fromkeys(item[4] for item in MUTATIONS)))
        if result.returncode != 0 or "3 passed" not in result.stdout:
            raise SystemExit("control failed; no mutation result is valid")
        killed = 0
        for name, filename, original, replacement, selector in MUTATIONS:
            directory = base / name
            directory.mkdir()
            copy_checkout(directory)
            path = directory / filename
            text = path.read_text(encoding="utf-8")
            # TON expression also appears in other timers: mutate first occurrence only.
            assert original in text, f"stale mutation: {name}"
            path.write_text(text.replace(original, replacement, 1), encoding="utf-8")
            print(f"=== {name}: {filename}: {original!r} -> {replacement!r} ===", flush=True)
            result = run(directory, [selector])
            failures = ET.parse(directory / "result.xml").findall(".//testcase/failure")
            caught = (
                result.returncode == 1
                and len(failures) == 1
                and failures[0].get("message", "").startswith(("assert ", "AssertionError"))
                and "1 failed" in result.stdout
            )
            print(f"mutant={name} killed={caught}", flush=True)
            killed += caught
        print(f"SUMMARY: {killed}/{len(MUTATIONS)} mutants killed; control passed")
        if killed != len(MUTATIONS):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
