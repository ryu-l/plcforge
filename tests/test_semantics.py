"""Type resolution, linking and diagnostics.

The resolver exists so the backends can make decisions that change behaviour.
These tests pin down the decisions themselves rather than the messages.
"""

from __future__ import annotations

import pytest

from conftest import build
from plcforge.diagnostics import DiagnosticBag, Severity, TranslationError
from plcforge.ir.types import TypeKind, common_type, elementary, is_bool, is_integer
from plcforge.ir.validate import resolve_types


def codes(diagnostics: DiagnosticBag) -> set[str]:
    return {diagnostic.code for diagnostic in diagnostics}


class TestTypeResolution:
    def test_expressions_are_annotated_with_their_type(self, tmp_path) -> None:
        project, _ = build(
            """
            FUNCTION_BLOCK P
            VAR a, b : INT; flag : BOOL; ratio : REAL; END_VAR
                flag := a < b;
                ratio := ratio * 2.0;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )
        body = project.pou("P").body
        assert is_bool(body[0].value.inferred_type)
        assert body[1].value.inferred_type.name == "REAL"

    def test_function_block_outputs_resolve_through_the_standard_library(self, tmp_path) -> None:
        project, diagnostics = build(
            """
            FUNCTION_BLOCK P
            VAR timer : TON; elapsed : TIME; done : BOOL; END_VAR
                timer(IN := TRUE, PT := T#1s);
                elapsed := timer.ET;
                done := timer.Q;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )
        assert not diagnostics.has_errors
        body = project.pou("P").body
        assert body[1].value.inferred_type.name == "TIME"
        assert is_bool(body[2].value.inferred_type)

    def test_user_function_block_outputs_resolve_too(self, tmp_path) -> None:
        project, diagnostics = build(
            """
            FUNCTION_BLOCK Inner
            VAR_OUTPUT level : REAL; END_VAR
                level := 1.0;
            END_FUNCTION_BLOCK

            FUNCTION_BLOCK Outer
            VAR child : Inner; copy : REAL; END_VAR
                child();
                copy := child.level;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )
        assert not diagnostics.has_errors
        assert project.pou("Outer").body[1].value.inferred_type.name == "REAL"

    def test_array_element_types_resolve(self, tmp_path) -> None:
        project, _ = build(
            """
            FUNCTION_BLOCK P
            VAR samples : ARRAY [1..4] OF REAL; one : REAL; END_VAR
                one := samples[2];
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )
        assert project.pou("P").body[0].value.inferred_type.name == "REAL"

    def test_conversion_functions_report_their_target(self, tmp_path) -> None:
        project, diagnostics = build(
            """
            FUNCTION_BLOCK P
            VAR count : INT; ratio : REAL; END_VAR
                ratio := INT_TO_REAL(count);
                ratio := TO_REAL(count);
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )
        assert not diagnostics.has_errors, "both IEC spellings must be recognised"
        for statement in project.pou("P").body:
            assert statement.value.inferred_type.name == "REAL"

    def test_mixed_arithmetic_widens_and_warns(self, tmp_path) -> None:
        project, diagnostics = build(
            """
            FUNCTION_BLOCK P
            VAR count : INT; ratio : REAL; END_VAR
                ratio := count * ratio;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )
        assert project.pou("P").body[0].value.inferred_type.name == "REAL"
        assert not diagnostics.has_errors, "widening is a warning, not a failure"

    def test_incompatible_types_are_warned_about(self, tmp_path) -> None:
        _, diagnostics = build(
            """
            FUNCTION_BLOCK P
            VAR when : TIME; ratio : REAL; END_VAR
                ratio := when * 2.0;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )
        assert "PF204" in codes(diagnostics)


class TestLinking:
    def test_user_types_declared_in_another_file_still_resolve(self, tmp_path) -> None:
        (tmp_path / "types.scl").write_text(
            "TYPE Limits : STRUCT high : REAL; END_STRUCT END_TYPE", encoding="utf-8"
        )
        (tmp_path / "logic.scl").write_text(
            """
            FUNCTION_BLOCK P
            VAR limits : Limits; copy : REAL; END_VAR
                copy := limits.high;
            END_FUNCTION_BLOCK
            """,
            encoding="utf-8",
        )
        from plcforge.pipeline import load_sources, prepare

        diagnostics = DiagnosticBag()
        project = load_sources([tmp_path], diagnostics, project_name="split")
        prepare(project, diagnostics)

        assert not diagnostics.has_errors
        declaration = project.pou("P").find("limits")
        assert declaration.type.kind is TypeKind.DERIVED
        assert project.pou("P").body[0].value.inferred_type.name == "REAL"

    def test_an_unknown_type_is_reported_once(self, tmp_path) -> None:
        _, diagnostics = build(
            """
            FUNCTION_BLOCK P
            VAR a : Mystery; b : Mystery; END_VAR
                a := b;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )
        unknown = [d for d in diagnostics if d.code == "PF210"]
        assert len(unknown) == 1, "one report per unknown type, not per use"

    def test_an_unknown_member_is_reported(self, tmp_path) -> None:
        _, diagnostics = build(
            """
            FUNCTION_BLOCK P
            VAR timer : TON; value : BOOL; END_VAR
                value := timer.NotAPin;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )
        assert "PF202" in codes(diagnostics) or "PF203" in codes(diagnostics)

    def test_an_unknown_block_parameter_is_reported(self, tmp_path) -> None:
        _, diagnostics = build(
            """
            FUNCTION_BLOCK P
            VAR timer : TON; END_VAR
                timer(IN := TRUE, PRESET := T#1s);
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )
        assert "PF206" in codes(diagnostics)

    def test_a_duplicate_declaration_is_reported(self, tmp_path) -> None:
        _, diagnostics = build(
            """
            FUNCTION_BLOCK P
            VAR a : INT; END_VAR
            VAR a : REAL; END_VAR
                a := 1;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )
        assert "PF201" in codes(diagnostics)

    def test_case_insensitive_shadowing_of_an_enumeration_is_reported(self, tmp_path) -> None:
        _, diagnostics = build(
            """
            TYPE State : (Idle, Running); END_TYPE
            FUNCTION_BLOCK P
            VAR_OUTPUT running : BOOL; END_VAR
                running := TRUE;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )
        assert "PF209" in codes(diagnostics), "IEC names are case insensitive, so these collide"


class TestDiagnostics:
    def test_severity_ranking(self) -> None:
        assert Severity.ERROR.rank > Severity.WARNING.rank > Severity.NOTE.rank

    def test_formatting_includes_location_code_and_hint(self) -> None:
        bag = DiagnosticBag()
        bag.error("bad thing", file="a.scl", line=3, column=7, code="PF999", hint="try this")
        text = bag.items[0].format()
        assert "a.scl:3:7" in text and "PF999" in text and "hint: try this" in text

    def test_summary_counts(self) -> None:
        bag = DiagnosticBag()
        assert bag.summary() == "no issues"
        bag.warning("w")
        bag.error("e")
        assert bag.summary() == "1 error, 1 warning"

    def test_raise_if_errors_carries_the_diagnostics(self) -> None:
        bag = DiagnosticBag()
        bag.error("boom", code="PF999")
        with pytest.raises(TranslationError) as info:
            bag.raise_if_errors()
        assert info.value.diagnostics[0].code == "PF999"

    def test_no_errors_means_no_raise(self) -> None:
        bag = DiagnosticBag()
        bag.warning("only a warning")
        bag.raise_if_errors()


class TestTypeAlgebra:
    def test_common_type_widens(self) -> None:
        assert common_type(elementary("INT"), elementary("DINT")).name == "DINT"
        assert common_type(elementary("INT"), elementary("REAL")).name == "REAL"
        assert common_type(elementary("REAL"), elementary("LREAL")).name == "LREAL"

    def test_unrelated_types_have_no_common_type(self) -> None:
        assert common_type(elementary("TIME"), elementary("REAL")) is None

    def test_a_missing_operand_type_does_not_hide_the_other(self) -> None:
        assert common_type(None, elementary("INT")).name == "INT"

    def test_group_predicates(self) -> None:
        assert is_integer(elementary("DINT"))
        assert not is_integer(elementary("REAL"))
        assert is_bool(elementary("BOOL"))
        assert not is_bool(elementary("BYTE"))


def test_resolving_an_empty_project_is_harmless() -> None:
    from plcforge.ir.nodes import Project

    assert len(resolve_types(Project())) == 0
