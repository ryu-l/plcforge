"""Ladder Diagram and Function Block Diagram, read through PLCopen XML.

Graphical networks are the part of the standard where a translator is most
likely to be quietly wrong, so these tests check the resulting logic rather than
the shape of the tree: a seal-in circuit must produce a seal-in expression.
"""

from __future__ import annotations

import textwrap

import pytest

from conftest import load_generated
from plcforge.backends.python.emitter import emit_project as emit_python
from plcforge.backends.st.emitter import emit_project as emit_st
from plcforge.diagnostics import DiagnosticBag
from plcforge.frontends.plcopen.reader import parse_source
from plcforge.ir.link import link_types
from plcforge.ir.validate import resolve_types
from plcforge.runtime import SimulatedClock, use_clock

HEADER = '<project xmlns="http://www.plcopen.org/xml/tc6_0201"><types><pous>'
FOOTER = "</pous></types></project>"


def document(interface: str, body: str, name: str = "Unit", kind: str = "functionBlock") -> str:
    return (
        f"{HEADER}<pou name='{name}' pouType='{kind}'>"
        f"<interface>{textwrap.dedent(interface)}</interface>"
        f"<body>{textwrap.dedent(body)}</body>"
        f"</pou>{FOOTER}"
    )


def translate(xml: str):
    diagnostics = DiagnosticBag()
    project = parse_source(xml, "unit.xml", diagnostics)
    link_types(project, diagnostics)
    resolve_types(project, diagnostics)
    return project, diagnostics


BOOL_INTERFACE = """
    <inputVars>
      <variable name="a"><type><BOOL/></type></variable>
      <variable name="b"><type><BOOL/></type></variable>
    </inputVars>
    <outputVars>
      <variable name="out"><type><BOOL/></type></variable>
    </outputVars>
"""


class TestLadder:
    def test_contacts_in_series_are_a_conjunction(self) -> None:
        project, diagnostics = translate(
            document(
                BOOL_INTERFACE,
                """
                <LD>
                  <leftPowerRail localId="1"><connectionPointOut/></leftPowerRail>
                  <contact localId="2">
                    <connectionPointIn><connection refLocalId="1"/></connectionPointIn>
                    <variable>a</variable>
                  </contact>
                  <contact localId="3" negated="true">
                    <connectionPointIn><connection refLocalId="2"/></connectionPointIn>
                    <variable>b</variable>
                  </contact>
                  <coil localId="4">
                    <connectionPointIn><connection refLocalId="3"/></connectionPointIn>
                    <variable>out</variable>
                  </coil>
                </LD>
                """,
            )
        )
        assert not diagnostics.has_errors
        assert "out := a AND NOT b;" in emit_st(project, None, diagnostics)

    def test_parallel_branches_are_a_disjunction(self) -> None:
        project, diagnostics = translate(
            document(
                BOOL_INTERFACE,
                """
                <LD>
                  <leftPowerRail localId="1"><connectionPointOut/></leftPowerRail>
                  <contact localId="2">
                    <connectionPointIn><connection refLocalId="1"/></connectionPointIn>
                    <variable>a</variable>
                  </contact>
                  <contact localId="3">
                    <connectionPointIn><connection refLocalId="1"/></connectionPointIn>
                    <variable>b</variable>
                  </contact>
                  <coil localId="4">
                    <connectionPointIn>
                      <connection refLocalId="2"/>
                      <connection refLocalId="3"/>
                    </connectionPointIn>
                    <variable>out</variable>
                  </coil>
                </LD>
                """,
            )
        )
        assert "out := a OR b;" in emit_st(project, None, diagnostics)

    def test_set_and_reset_coils_become_conditional_assignments(self) -> None:
        project, diagnostics = translate(
            document(
                BOOL_INTERFACE,
                """
                <LD>
                  <leftPowerRail localId="1"><connectionPointOut/></leftPowerRail>
                  <contact localId="2">
                    <connectionPointIn><connection refLocalId="1"/></connectionPointIn>
                    <variable>a</variable>
                  </contact>
                  <coil localId="3" storage="set">
                    <connectionPointIn><connection refLocalId="2"/></connectionPointIn>
                    <variable>out</variable>
                  </coil>
                  <contact localId="4">
                    <connectionPointIn><connection refLocalId="1"/></connectionPointIn>
                    <variable>b</variable>
                  </contact>
                  <coil localId="5" storage="reset">
                    <connectionPointIn><connection refLocalId="4"/></connectionPointIn>
                    <variable>out</variable>
                  </coil>
                </LD>
                """,
            )
        )
        text = emit_st(project, None, diagnostics)
        assert "IF a THEN" in text and "out := TRUE;" in text
        assert "IF b THEN" in text and "out := FALSE;" in text

    def test_an_edge_contact_creates_an_implicit_trigger_instance(self) -> None:
        project, diagnostics = translate(
            document(
                BOOL_INTERFACE,
                """
                <LD>
                  <leftPowerRail localId="1"><connectionPointOut/></leftPowerRail>
                  <contact localId="2" edge="rising">
                    <connectionPointIn><connection refLocalId="1"/></connectionPointIn>
                    <variable>a</variable>
                  </contact>
                  <coil localId="3">
                    <connectionPointIn><connection refLocalId="2"/></connectionPointIn>
                    <variable>out</variable>
                  </coil>
                </LD>
                """,
            )
        )
        pou = project.pou("Unit")
        implicit = [d for d in pou.declarations() if d.type.name == "R_TRIG"]
        assert len(implicit) == 1
        text = emit_st(project, None, diagnostics)
        assert "CLK := a" in text and ".Q;" in text

    def test_the_worked_example_produces_a_seal_in_circuit(self, examples) -> None:
        from plcforge.frontends.plcopen.reader import parse_file

        diagnostics = DiagnosticBag()
        project = parse_file(str(examples / "plcopen" / "MotorStarter.ld.xml"), diagnostics)
        link_types(project, diagnostics)
        resolve_types(project, diagnostics)
        text = emit_st(project, None, diagnostics)

        assert not diagnostics.has_errors
        assert "motor := (startButton OR motor) AND NOT stopButton AND NOT overload;" in text
        assert "warmUpTimer(IN := motor, PT := T#5s);" in text

    def test_the_worked_example_runs_as_python(self, examples) -> None:
        from plcforge.frontends.plcopen.reader import parse_file

        diagnostics = DiagnosticBag()
        project = parse_file(str(examples / "plcopen" / "MotorStarter.ld.xml"), diagnostics)
        link_types(project, diagnostics)
        resolve_types(project, diagnostics)
        module = load_generated(emit_python(project, None, diagnostics), "gen_motor_starter")

        clock = SimulatedClock()
        with use_clock(clock):
            starter = module.MotorStarter()
            assert starter(startButton=True, stopButton=False, overload=False).motor is True
            assert starter(startButton=False).motor is True, "the seal-in holds the contactor"
            assert starter.warmedUp is False

            clock.advance(5.0)
            starter(startButton=False)
            assert starter.warmedUp is True

            assert starter(stopButton=True).motor is False


class TestFunctionBlockDiagram:
    def test_operator_blocks_become_expressions(self) -> None:
        project, diagnostics = translate(
            document(
                """
                <inputVars>
                  <variable name="x"><type><REAL/></type></variable>
                  <variable name="limit"><type><REAL/></type></variable>
                </inputVars>
                <outputVars>
                  <variable name="high"><type><BOOL/></type></variable>
                </outputVars>
                """,
                """
                <FBD>
                  <inVariable localId="1"><connectionPointOut/><expression>x</expression></inVariable>
                  <inVariable localId="2"><connectionPointOut/><expression>limit</expression></inVariable>
                  <block localId="3" typeName="GT">
                    <inputVariables>
                      <variable formalParameter="IN1">
                        <connectionPointIn><connection refLocalId="1"/></connectionPointIn>
                      </variable>
                      <variable formalParameter="IN2">
                        <connectionPointIn><connection refLocalId="2"/></connectionPointIn>
                      </variable>
                    </inputVariables>
                    <outputVariables>
                      <variable formalParameter="OUT"><connectionPointOut/></variable>
                    </outputVariables>
                  </block>
                  <outVariable localId="4">
                    <connectionPointIn><connection refLocalId="3" formalParameter="OUT"/></connectionPointIn>
                    <expression>high</expression>
                  </outVariable>
                </FBD>
                """,
            )
        )
        assert "high := x > limit;" in emit_st(project, None, diagnostics)

    def test_a_block_whose_outputs_nobody_reads_is_still_called(self) -> None:
        """A timer driven purely for its side effect must not be dropped."""
        project, diagnostics = translate(
            document(
                """
                <inputVars>
                  <variable name="run"><type><BOOL/></type></variable>
                </inputVars>
                <localVars>
                  <variable name="hours"><type><derived name="TON"/></type></variable>
                </localVars>
                """,
                """
                <FBD>
                  <inVariable localId="1"><connectionPointOut/><expression>run</expression></inVariable>
                  <inVariable localId="2"><connectionPointOut/><expression>T#1h</expression></inVariable>
                  <block localId="3" typeName="TON" instanceName="hours">
                    <inputVariables>
                      <variable formalParameter="IN">
                        <connectionPointIn><connection refLocalId="1"/></connectionPointIn>
                      </variable>
                      <variable formalParameter="PT">
                        <connectionPointIn><connection refLocalId="2"/></connectionPointIn>
                      </variable>
                    </inputVariables>
                    <outputVariables>
                      <variable formalParameter="Q"><connectionPointOut/></variable>
                    </outputVariables>
                  </block>
                </FBD>
                """,
            )
        )
        assert not diagnostics.has_errors
        assert "hours(IN := run, PT := T#1h);" in emit_st(project, None, diagnostics)

    def test_the_worked_example_latches_and_alarms(self, examples) -> None:
        from plcforge.frontends.plcopen.reader import parse_file

        diagnostics = DiagnosticBag()
        project = parse_file(str(examples / "plcopen" / "TankControl.fbd.xml"), diagnostics)
        link_types(project, diagnostics)
        resolve_types(project, diagnostics)
        module = load_generated(emit_python(project, None, diagnostics), "gen_tank_control")

        clock = SimulatedClock()
        with use_clock(clock):
            tank = module.TankControl()
            assert tank(level=10.0, lowSetpoint=20.0, highSetpoint=80.0).pump is True
            assert tank(level=50.0).pump is True, "the latch holds between the setpoints"
            assert tank(level=90.0).pump is False
            assert tank.overfillAlarm is False

            clock.advance(3.0)
            tank(level=90.0)
            assert tank.overfillAlarm is True


class TestRobustness:
    def test_malformed_xml_is_reported_not_raised(self) -> None:
        _, diagnostics = translate("<project><types><pous>")
        assert any(d.code == "PF120" for d in diagnostics.errors)

    def test_an_unsupported_body_language_keeps_the_interface(self) -> None:
        project, diagnostics = translate(
            document(BOOL_INTERFACE, "<IL>LD a\nST out</IL>")
        )
        pou = project.pou("Unit")
        assert pou is not None and pou.find("a") is not None
        assert pou.body == []
        assert any(d.code == "PF124" for d in diagnostics.warnings)

    def test_a_dangling_connection_is_reported(self) -> None:
        _, diagnostics = translate(
            document(
                BOOL_INTERFACE,
                """
                <LD>
                  <coil localId="1">
                    <connectionPointIn><connection refLocalId="99"/></connectionPointIn>
                    <variable>out</variable>
                  </coil>
                </LD>
                """,
            )
        )
        assert any(d.code == "PF128" for d in diagnostics.warnings)

    def test_a_structured_text_body_inside_plcopen_is_parsed(self) -> None:
        project, diagnostics = translate(
            document(BOOL_INTERFACE, "<ST><![CDATA[out := a AND b;]]></ST>")
        )
        assert not diagnostics.has_errors
        assert "out := a AND b;" in emit_st(project, None, diagnostics)

    @pytest.mark.parametrize("namespace", ["", ' xmlns="http://www.plcopen.org/xml/tc6_0200"'])
    def test_namespace_variants_are_accepted(self, namespace: str) -> None:
        xml = (
            f"<project{namespace}><types><pous>"
            "<pou name='N' pouType='functionBlock'>"
            "<interface><outputVars><variable name='out'><type><BOOL/></type></variable></outputVars></interface>"
            "<body><ST>out := TRUE;</ST></body>"
            "</pou></pous></types></project>"
        )
        project, diagnostics = translate(xml)
        assert not diagnostics.has_errors
        assert project.pou("N") is not None
