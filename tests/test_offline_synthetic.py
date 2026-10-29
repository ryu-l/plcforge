"""Controlled multi-file projects with independently calculated state checks.

No vendor files, device IO, wall-clock sleeps, or timing performance thresholds.
"""

from __future__ import annotations

import json
import time

import pytest

from offline_support import cpp_output, python_module, requires_compiler, roundtrip
from plcforge.diagnostics import DiagnosticBag
from plcforge.pipeline import load_sources, prepare
from plcforge.runtime import ScanEngine, SimulatedClock


def synthetic_project(tmp_path, blocks: int, width: int, depth: int):
    sources = tmp_path / "sources"
    sources.mkdir()
    paths = []
    for level in range(depth):
        expr = "value + 1" if level == 0 else f"Chain{level - 1}(value) + 1"
        path = sources / f"chain_{level:04d}.st"
        path.write_text(
            f"""
FUNCTION Chain{level} : DINT
VAR_INPUT value : DINT; END_VAR
    Chain{level} := {expr};
END_FUNCTION
""",
            encoding="utf-8",
        )
        paths.append(path)
    for index in range(blocks):
        path = sources / f"cell_{index:04d}.st"
        path.write_text(
            f"""
FUNCTION_BLOCK Cell{index}
VAR_INPUT enabled : BOOL; END_VAR
VAR_OUTPUT total : DINT; checksum : DINT; END_VAR
VAR ticks : DINT; samples : ARRAY [-2..{width - 3}] OF DINT; i : DINT; END_VAR
    IF enabled THEN
        total := total + Chain{depth - 1}({index + 1});
        samples[(ticks MOD {width}) - 2] := total;
        ticks := ticks + 1;
    END_IF;
    checksum := 0;
    FOR i := -2 TO {width - 3} DO checksum := checksum + samples[i]; END_FOR;
END_FUNCTION_BLOCK
""",
            encoding="utf-8",
        )
        paths.append(path)
    declarations = "\n".join(f"cell{i} : Cell{i};" for i in range(blocks))
    calls = "\n".join(
        f"cell{i}(enabled := enabled); total := total + cell{i}.total; "
        f"checksum := checksum + cell{i}.checksum;"
        for i in range(blocks)
    )
    path = sources / "plant.st"
    path.write_text(
        f"""
PROGRAM Factory
VAR enabled : BOOL := TRUE; total : DINT; checksum : DINT; {declarations} END_VAR
    total := 0;
    checksum := 0;
    {calls}
END_PROGRAM
""",
        encoding="utf-8",
    )
    paths.append(path)
    # Dependency-ordered inputs exercise supported C++ emission.
    bag = DiagnosticBag()
    project = load_sources(paths, bag, project_name="offline")
    prepare(project, bag)
    assert not bag.has_errors, bag.summary()
    assert len(project.pous) == blocks + depth + 1
    assert len(project.source_files) == len(paths)
    return project


def expected_state(blocks, width, depth, enabled_scans):
    # Each cell j adds j+depth each enabled scan; its ring retains the last w totals.
    step_sum = blocks * (blocks + 1) // 2 + blocks * depth
    retained = min(width, enabled_scans)
    last_totals = retained * (2 * enabled_scans - retained + 1) // 2
    return step_sum * enabled_scans, step_sum * last_totals


def cpp_main(scans):
    return f"""
#include "offline.hpp"
#include <iostream>
int main() {{
    plant::Factory a, b;
    for (int scan=0; scan < {scans}; ++scan) {{
        a.enabled = scan % 3 != 0;
        b.enabled = scan % 5 == 0;
        a.scan(); b.scan();
        std::cout << a.total << " " << a.checksum << " "
                  << b.total << " " << b.checksum << "\\n";
    }}
}}
"""


@requires_compiler
@pytest.mark.parametrize(
    ("blocks", "width", "depth", "scans"),
    [(8, 8, 4, 20), (64, 32, 16, 40), (256, 128, 32, 64)],
    ids=["small", "medium", "large"],
)
def test_multifile_project_scales_and_matches_cpp(tmp_path, blocks, width, depth, scans):
    started = time.perf_counter()
    project = synthetic_project(tmp_path, blocks, width, depth)
    prepared = time.perf_counter()
    # Both original and Python -> ST -> reparse must retain every cell and its state.
    modules = [python_module(project, "synthetic_direct"), python_module(roundtrip(project), "synthetic_rt")]
    expected = []
    enabled_a = enabled_b = 0
    for scan in range(scans):
        enabled_a += scan % 3 != 0
        enabled_b += scan % 5 == 0
        expected.append(
            (
                *expected_state(blocks, width, depth, enabled_a),
                *expected_state(blocks, width, depth, enabled_b),
            )
        )
    for module in modules:
        a, b = module.Factory(), module.Factory()
        for scan, want in enumerate(expected):
            a.enabled, b.enabled = scan % 3 != 0, scan % 5 == 0
            a.scan()
            b.scan()
            assert (a.total, a.checksum, b.total, b.checksum) == want, f"scan {scan}"
        assert a.cell0.samples is not b.cell0.samples
    generated = time.perf_counter()
    directory = tmp_path / "cpp_direct"
    directory.mkdir()
    output = cpp_output(project, cpp_main(scans), directory)
    assert [tuple(map(int, line.split())) for line in output.splitlines()] == expected
    print(
        json.dumps(
            {
                "blocks": blocks,
                "array_width": width,
                "call_depth": depth,
                "pous": blocks + depth + 1,
                "files": blocks + depth + 1,
                "array_elements_per_program": blocks * width,
                "scans": scans,
                "prepare_seconds": round(prepared - started, 4),
                "python_and_roundtrip_seconds": round(generated - prepared, 4),
                "cpp_seconds": round(time.perf_counter() - generated, 4),
            },
            sort_keys=True,
        )
    )


def check_python_soak(module, scans):
    a, idle = module.Factory(), module.Factory()
    clock = SimulatedClock()
    count = 0

    def read_inputs(program):
        program.enabled = count % 3 != 0

    def check_outputs(program):
        nonlocal count
        enabled = count + 1 - (count // 3 + 1)
        assert (program.total, program.checksum) == expected_state(2, 4, 4, enabled)
        count += 1

    engine = ScanEngine(
        a, cycle_time=0.01, clock=clock, on_read_inputs=read_inputs, on_write_outputs=check_outputs
    )
    # Chunked continuation must preserve state and statistics.
    engine.run_cycles(40_000)
    engine.run_for(600.0)
    assert count == scans and engine.statistics.cycles == scans
    assert clock.now() == pytest.approx(1000.0, abs=1e-6)
    assert idle.total == 0 and idle.cell0.ticks == 0
    idle.scan()
    assert (idle.total, idle.checksum) == expected_state(2, 4, 4, 1)


@requires_compiler
def test_long_run_scan_engine_and_cpp(tmp_path):
    scans = 100_000
    project = synthetic_project(tmp_path, blocks=2, width=4, depth=4)
    started = time.perf_counter()
    for variant, candidate in enumerate((project, roundtrip(project))):
        module = python_module(candidate, f"soak_{variant}")
        check_python_soak(module, scans)
    directory = tmp_path / "soak_cpp_direct"
    directory.mkdir()
    output = cpp_output(project, cpp_main(scans), directory)
    lines = output.splitlines()
    assert len(lines) == scans
    for scan, line in enumerate(lines):
        enabled_a = scan + 1 - (scan // 3 + 1)
        enabled_b = scan // 5 + 1
        assert tuple(map(int, line.split())) == (
            *expected_state(2, 4, 4, enabled_a),
            *expected_state(2, 4, 4, enabled_b),
        ), f"scan {scan}"
    print(
        json.dumps(
            {
                "soak_scans_per_variant": scans,
                "simulated_seconds": 1000,
                "python_variants": 2,
                "cpp_variants": 1,
                "wall_seconds": round(time.perf_counter() - started, 4),
            }
        )
    )
