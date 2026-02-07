"""Lexing and parsing Structured Text."""

from __future__ import annotations

import pytest

from conftest import build
from plcforge.diagnostics import DiagnosticBag
from plcforge.frontends.st.lexer import TokenType, parse_duration, tokenize
from plcforge.frontends.st.parser import parse_source
from plcforge.ir.nodes import Assign, Binary, Case, For, If, Invoke, PouKind, Repeat, VarKind


class TestLexer:
    @pytest.mark.parametrize(
        ("literal", "seconds"),
        [
            ("T#5s", 5.0),
            ("T#1s500ms", 1.5),
            ("T#1h30m", 5400.0),
            ("TIME#2d", 172800.0),
            ("T#-250ms", -0.25),
            ("t#100ms", 0.1),
        ],
    )
    def test_duration_literals(self, literal: str, seconds: float) -> None:
        assert parse_duration(literal) == pytest.approx(seconds)

    @pytest.mark.parametrize(
        ("source", "value"),
        [("16#FF", 255), ("2#1010_1010", 170), ("8#17", 15), ("1_000", 1000), ("INT#42", 42)],
    )
    def test_based_and_typed_integers(self, source: str, value: int) -> None:
        tokens = tokenize(source)
        assert tokens[0].payload == value

    def test_nested_block_comments_are_one_token(self) -> None:
        tokens = tokenize("(* outer (* inner *) still outer *) x")
        kinds = [token.type for token in tokens]
        assert kinds[0] is TokenType.COMMENT
        assert tokens[1].value == "x"

    def test_string_escapes_are_decoded(self) -> None:
        tokens = tokenize("'line$Nnext$$done'")
        assert tokens[0].payload == "line\nnext$done"

    def test_direct_addresses_are_recognised(self) -> None:
        tokens = tokenize("%IX0.0 := %QW12;")
        assert tokens[0].type is TokenType.ADDRESS
        assert tokens[0].value == "%IX0.0"

    def test_an_unterminated_comment_is_reported_not_raised(self) -> None:
        bag = DiagnosticBag()
        tokenize("(* never closed", "unit.st", bag)
        assert any(d.code == "PF102" for d in bag.errors)


class TestParser:
    def test_pou_kinds_and_variable_sections(self, tmp_path) -> None:
        project, diagnostics = build(
            """
            FUNCTION_BLOCK Gate
            VAR_INPUT  open : BOOL; END_VAR
            VAR_OUTPUT moving : BOOL; END_VAR
            VAR CONSTANT limit : INT := 7; END_VAR
                moving := open;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )
        assert not diagnostics.has_errors
        pou = project.pou("Gate")
        assert pou is not None and pou.kind is PouKind.FUNCTION_BLOCK
        assert [block.kind for block in pou.var_blocks] == [
            VarKind.INPUT,
            VarKind.OUTPUT,
            VarKind.LOCAL,
        ]
        assert pou.var_blocks[2].constant is True

    def test_operator_precedence_follows_iec(self, tmp_path) -> None:
        # AND binds looser than '=', so this is (a = b) AND (c = d).
        project, _ = build(
            """
            FUNCTION_BLOCK P
            VAR a, b, c, d : INT; r : BOOL; END_VAR
                r := a = b AND c = d;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )
        statement = project.pou("P").body[0]
        assert isinstance(statement, Assign)
        assert isinstance(statement.value, Binary) and statement.value.op == "AND"
        assert statement.value.left.op == "="

    def test_power_is_right_associative(self, tmp_path) -> None:
        project, _ = build(
            "FUNCTION_BLOCK P VAR r : REAL; END_VAR r := 2.0 ** 3.0 ** 2.0; END_FUNCTION_BLOCK",
            tmp_path,
        )
        value = project.pou("P").body[0].value
        assert value.op == "**" and value.right.op == "**"

    def test_case_with_ranges_and_multiple_labels(self, tmp_path) -> None:
        project, diagnostics = build(
            """
            FUNCTION_BLOCK P
            VAR step : INT; out : INT; END_VAR
                CASE step OF
                    1, 2: out := 10;
                    5..9: out := 20;
                ELSE
                    out := 0;
                END_CASE;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )
        assert not diagnostics.has_errors
        case = project.pou("P").body[0]
        assert isinstance(case, Case)
        assert len(case.branches[0].labels) == 2
        assert case.branches[1].labels[0].high is not None
        assert len(case.orelse) == 1

    def test_loops_and_jumps(self, tmp_path) -> None:
        project, diagnostics = build(
            """
            FUNCTION_BLOCK P
            VAR i, total : INT; END_VAR
                FOR i := 10 TO 1 BY -1 DO
                    total := total + i;
                    IF total > 30 THEN EXIT; END_IF;
                END_FOR;
                REPEAT
                    total := total - 1;
                UNTIL total <= 0 END_REPEAT;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )
        assert not diagnostics.has_errors
        body = project.pou("P").body
        assert isinstance(body[0], For) and body[0].step is not None
        assert isinstance(body[0].body[1], If)
        assert isinstance(body[1], Repeat)

    def test_function_block_call_with_named_and_output_parameters(self, tmp_path) -> None:
        project, _ = build(
            """
            FUNCTION_BLOCK P
            VAR delay : TON; go, done : BOOL; END_VAR
                delay(IN := go, PT := T#1s, Q => done);
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )
        call = project.pou("P").body[0]
        assert isinstance(call, Invoke)
        assert [a.name for a in call.call.args] == ["IN", "PT", "Q"]
        assert call.call.args[2].is_output is True

    def test_comments_are_carried_into_the_ir(self, tmp_path) -> None:
        project, _ = build(
            """
            (* Door interlock. *)
            FUNCTION_BLOCK Door
            VAR_INPUT
                closed : BOOL;   (* reed contact *)
            END_VAR
                (* latch the state *)
                closed := closed;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )
        pou = project.pou("Door")
        assert pou.comment == "Door interlock."
        assert pou.find("closed").comment == "reed contact"
        assert pou.body[0].comment == "latch the state"

    def test_arrays_structures_and_enumerations(self, tmp_path) -> None:
        project, diagnostics = build(
            """
            TYPE Mode : (Manual, Auto := 5, Service); END_TYPE
            TYPE Axis :
            STRUCT
                position : REAL;
                grid : ARRAY [0..1, 1..3] OF INT;
            END_STRUCT
            END_TYPE
            FUNCTION_BLOCK P
            VAR
                buffer : ARRAY [1..4] OF REAL := [4(1.5)];
                mode : Mode;
            END_VAR
                mode := Auto;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )
        assert not diagnostics.has_errors
        assert project.enums[0].members == [("Manual", 0), ("Auto", 5), ("Service", 6)]
        grid = project.structs[0].fields[1]
        assert grid.type.dimensions == ((0, 1), (1, 3))
        buffer = project.pou("P").find("buffer")
        assert len(buffer.initial.elements) == 4

    def test_a_broken_statement_does_not_lose_the_rest_of_the_file(self, tmp_path) -> None:
        project, diagnostics = build(
            """
            FUNCTION_BLOCK Broken
            VAR a : INT; END_VAR
                a := ;
                a := 1;
            END_FUNCTION_BLOCK
            FUNCTION_BLOCK Fine
            VAR b : INT; END_VAR
                b := 2;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )
        assert diagnostics.has_errors, "the malformed assignment must be reported"
        assert project.pou("Fine") is not None, "parsing continues past the error"
        assert len(project.pou("Fine").body) == 1

    def test_vendor_inheritance_keywords_are_reported_not_fatal(self, tmp_path) -> None:
        project, diagnostics = build(
            """
            FUNCTION_BLOCK Child EXTENDS Base
            VAR x : INT; END_VAR
                x := 1;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )
        assert not diagnostics.has_errors
        assert any(d.code == "PF115" for d in diagnostics.warnings)
        assert len(project.pou("Child").body) == 1

    def test_pragmas_are_skipped(self, tmp_path) -> None:
        project, diagnostics = build(
            """
            FUNCTION_BLOCK P
            VAR
                {attribute 'hidden'}
                secret : INT;
            END_VAR
                secret := 1;
            END_FUNCTION_BLOCK
            """,
            tmp_path,
        )
        assert not diagnostics.has_errors
        assert project.pou("P").find("secret") is not None


def test_utf8_bom_and_crlf_are_handled(tmp_path) -> None:
    path = tmp_path / "bom.scl"
    path.write_bytes(
        "﻿FUNCTION_BLOCK P\r\nVAR a : INT; END_VAR\r\n    a := 1;\r\nEND_FUNCTION_BLOCK\r\n".encode()
    )
    from plcforge.frontends.st.parser import parse_file

    bag = DiagnosticBag()
    project = parse_file(str(path), bag)
    assert not bag.has_errors
    assert project.pou("P") is not None


def test_parse_source_accepts_a_bare_string() -> None:
    project = parse_source("FUNCTION_BLOCK P VAR a : INT; END_VAR a := 1; END_FUNCTION_BLOCK")
    assert project.pou("P") is not None
