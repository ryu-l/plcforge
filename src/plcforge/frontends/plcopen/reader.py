"""PLCopen XML frontend: Ladder Diagram (KOP/LD) and Function Block Diagram (FUP/FBD).

Graphical languages have no text to parse, so the way in is the interchange
format every serious engineering tool can export: PLCopen TC6 XML, standardised
as IEC 61131-10.  A ladder network is a small directed graph, and this module
walks it backwards from each output to build the expression that drives it.

    leftPowerRail --- start ---+--- NOT jam --- ( motor )
                               |
                    motor -----+

becomes ``motor := (start OR motor) AND NOT jam;`` - one assignment, in the same
shape a control engineer would write by hand.
"""

from __future__ import annotations

import pathlib
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field

from ...diagnostics import DiagnosticBag
from ...ir import stdlib
from ...ir.nodes import (
    Argument,
    Assign,
    Binary,
    Branch,
    Call,
    Expr,
    If,
    Invoke,
    Literal,
    Member,
    Pou,
    PouKind,
    Project,
    Return,
    SourceSpan,
    Stmt,
    StructType,
    Unary,
    VarBlock,
    VarDecl,
    VarKind,
    VarRef,
)
from ...ir.types import BOOL, IecType, array_of, derived, elementary, instance_of, string_of
from ..st.parser import parse_expression_text

__all__ = ["PlcOpenReader", "parse_file", "parse_source"]

_POU_TYPES = {
    "program": PouKind.PROGRAM,
    "functionblock": PouKind.FUNCTION_BLOCK,
    "function": PouKind.FUNCTION,
}

_VAR_SECTIONS = {
    "inputvars": VarKind.INPUT,
    "outputvars": VarKind.OUTPUT,
    "inoutvars": VarKind.IN_OUT,
    "localvars": VarKind.LOCAL,
    "tempvars": VarKind.TEMP,
    "externalvars": VarKind.EXTERNAL,
    "globalvars": VarKind.GLOBAL,
}

#: Graphical elements that produce a value rather than consume one.
_SOURCE_ELEMENTS = {"leftpowerrail", "contact", "block", "invariable", "inoutvariable", "connector"}
#: Graphical elements that terminate a rung.
_SINK_ELEMENTS = {"coil", "outvariable", "rightpowerrail", "jump", "return"}


def _tag(element: ElementTree.Element) -> str:
    """Local element name, lowercased, with any XML namespace removed."""
    tag = element.tag
    return tag.rsplit("}", 1)[-1].lower() if "}" in tag else tag.lower()


def _find(element: ElementTree.Element, name: str) -> ElementTree.Element | None:
    return next((child for child in element if _tag(child) == name), None)


def _findall(element: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [child for child in element if _tag(child) == name]


def _is_true(value: str | None) -> bool:
    return (value or "").strip().lower() in ("true", "1")


def _documentation(element: ElementTree.Element) -> str | None:
    """Extract the plain text of a ``<documentation>`` child, if there is one."""
    node = _find(element, "documentation")
    if node is None:
        return None
    text = " ".join(part.strip() for part in node.itertext() if part.strip())
    return text or None


@dataclass
class _Network:
    """One graphical body, indexed for backwards traversal."""

    elements: dict[str, ElementTree.Element] = field(default_factory=dict)
    language: str = "LD"
    #: Expressions already computed for a block output, keyed by "localId:pin".
    values: dict[str, Expr] = field(default_factory=dict)
    #: Statements that must run before the current rung (function block calls).
    prelude: list[Stmt] = field(default_factory=list)
    #: Blocks already invoked this body, so a shared timer is called once.
    invoked: set[str] = field(default_factory=set)


class PlcOpenReader:
    """Reads a PLCopen XML document into the plcforge IR."""

    def __init__(self, filename: str = "<plcopen>", diagnostics: DiagnosticBag | None = None) -> None:
        self.filename = filename
        self.diagnostics = diagnostics if diagnostics is not None else DiagnosticBag()
        self._depth = 0

    # ------------------------------------------------------------------ #
    # Document level
    # ------------------------------------------------------------------ #

    def parse(self, source: str, project_name: str | None = None) -> Project:
        try:
            root = ElementTree.fromstring(source)
        except ElementTree.ParseError as error:
            self.diagnostics.error(
                f"malformed XML: {error}",
                file=self.filename,
                line=getattr(error, "position", (0, 0))[0],
                code="PF120",
            )
            return Project(name=project_name or "project", source_files=[self.filename])

        project = Project(name=project_name or "project", source_files=[self.filename])
        types = _find(root, "types")
        container = types if types is not None else root

        data_types = _find(container, "datatypes")
        if data_types is not None:
            for node in _findall(data_types, "datatype"):
                self._read_data_type(node, project)

        pous = _find(container, "pous")
        for node in _findall(pous if pous is not None else container, "pou"):
            pou = self._read_pou(node)
            if pou is not None:
                project.pous.append(pou)

        instances = _find(root, "instances")
        if instances is not None:
            configurations = _find(instances, "configurations")
            for node in _findall(configurations if configurations is not None else instances, "configuration"):
                block = self._read_globals(node)
                if block.declarations:
                    project.globals.append(block)
        return project

    def _read_data_type(self, node: ElementTree.Element, project: Project) -> None:
        name = node.get("name") or "Unnamed"
        base = _find(node, "basetype")
        if base is None:
            return
        struct = _find(base, "struct")
        if struct is None:
            self.diagnostics.note(
                f"data type '{name}' is not a structure; translated as an alias",
                file=self.filename,
                code="PF121",
            )
            return
        fields = [self._read_variable(child, VarKind.LOCAL) for child in _findall(struct, "variable")]
        project.structs.append(
            StructType(name=name, fields=[f for f in fields if f], comment=_documentation(node))
        )

    def _read_globals(self, node: ElementTree.Element) -> VarBlock:
        block = VarBlock(kind=VarKind.GLOBAL)
        for section in _findall(node, "globalvars"):
            for variable in _findall(section, "variable"):
                declaration = self._read_variable(variable, VarKind.GLOBAL)
                if declaration is not None:
                    block.declarations.append(declaration)
        for resource in _findall(node, "resource"):
            nested = self._read_globals(resource)
            block.declarations.extend(nested.declarations)
        return block

    # ------------------------------------------------------------------ #
    # POU interface
    # ------------------------------------------------------------------ #

    def _read_pou(self, node: ElementTree.Element) -> Pou | None:
        name = node.get("name")
        if not name:
            self.diagnostics.error("pou element without a name", file=self.filename, code="PF122")
            return None
        kind = _POU_TYPES.get((node.get("pouType") or "functionBlock").lower(), PouKind.FUNCTION_BLOCK)
        pou = Pou(
            name=name,
            kind=kind,
            comment=_documentation(node),
            span=SourceSpan(self.filename, 0, 0),
        )

        interface = _find(node, "interface")
        if interface is not None:
            for section in interface:
                section_name = _tag(section)
                if section_name == "returntype":
                    pou.return_type = self._read_type(section)
                    continue
                var_kind = _VAR_SECTIONS.get(section_name)
                if var_kind is None:
                    continue
                block = VarBlock(
                    kind=var_kind,
                    constant=_is_true(section.get("constant")),
                    retain=_is_true(section.get("retain")),
                )
                for variable in _findall(section, "variable"):
                    declaration = self._read_variable(variable, var_kind)
                    if declaration is not None:
                        block.declarations.append(declaration)
                if block.declarations:
                    pou.var_blocks.append(block)

        body = _find(node, "body")
        if body is None:
            self.diagnostics.warning(
                f"POU '{name}' has no body",
                file=self.filename,
                code="PF123",
            )
            return pou

        for child in body:
            language = _tag(child)
            if language in ("ld", "fbd"):
                pou.source_language = language.upper()
                pou.body = self._read_network(child, pou, language.upper())
                return pou
            if language == "st":
                pou.source_language = "ST"
                pou.body = self._read_st_body(child, name)
                return pou
            if language in ("il", "sfc"):
                pou.source_language = language.upper()
                self.diagnostics.warning(
                    f"POU '{name}' uses {language.upper()}, which plcforge does not translate; "
                    "the interface is kept and the body is left empty",
                    file=self.filename,
                    code="PF124",
                    hint="Export the POU as ST, LD or FBD from the engineering tool.",
                )
                return pou
        return pou

    def _read_st_body(self, node: ElementTree.Element, pou_name: str) -> list[Stmt]:
        from ..st.parser import Parser

        text = "".join(node.itertext())
        parser = Parser(text, f"{self.filename}:{pou_name}", self.diagnostics)
        return parser.parse_statements(frozenset())

    def _read_variable(self, node: ElementTree.Element, kind: VarKind) -> VarDecl | None:
        name = node.get("name")
        if not name:
            return None
        type_node = _find(node, "type")
        declared = self._read_type(type_node) if type_node is not None else BOOL
        initial = None
        initial_node = _find(node, "initialvalue")
        if initial_node is not None:
            initial = self._read_initial_value(initial_node, declared)
        return VarDecl(
            name=name,
            type=declared,
            initial=initial,
            address=node.get("address"),
            comment=_documentation(node),
            span=SourceSpan(self.filename, 0, 0),
        )

    def _read_type(self, node: ElementTree.Element) -> IecType:
        for child in node:
            name = _tag(child)
            if name == "derived":
                return instance_of(child.get("name") or "UNKNOWN")
            if name == "array":
                dimensions = tuple(
                    (int(d.get("lower") or 0), int(d.get("upper") or 0))
                    for d in _findall(child, "dimension")
                )
                base = _find(child, "basetype")
                element = self._read_type(base) if base is not None else BOOL
                return array_of(element, dimensions or ((0, 0),))
            if name in ("string", "wstring"):
                length = child.get("length")
                return string_of(name.upper(), int(length) if length and length.isdigit() else None)
            if name == "struct":
                return derived("STRUCT")
            return elementary(child.tag.rsplit("}", 1)[-1])
        return BOOL

    def _read_initial_value(self, node: ElementTree.Element, declared: IecType) -> Expr | None:
        simple = _find(node, "simplevalue")
        if simple is not None:
            text = simple.get("value")
            if text is None:
                return None
            return parse_expression_text(text, self.filename, self.diagnostics)
        # Array and structure initialisers are stored as nested value elements;
        # rendering them back to ST text lets the normal parser handle them.
        array = _find(node, "arrayvalue")
        if array is not None:
            values = [v.get("value") or "0" for v in _findall(array, "value")]
            return parse_expression_text(f"[{', '.join(values)}]", self.filename, self.diagnostics)
        return None

    # ------------------------------------------------------------------ #
    # Graphical bodies
    # ------------------------------------------------------------------ #

    def _read_network(self, node: ElementTree.Element, pou: Pou, language: str) -> list[Stmt]:
        network = _Network(language=language)
        for element in node:
            local_id = element.get("localId")
            if local_id:
                network.elements[local_id] = element

        sinks = [
            element
            for element in node
            if _tag(element) in _SINK_ELEMENTS
            or (_tag(element) == "block" and self._is_pure_sink(element, node))
        ]
        sinks.sort(key=self._execution_key)

        statements: list[Stmt] = []
        for sink in sinks:
            network.prelude = []
            produced = self._read_sink(sink, network, pou)
            statements.extend(network.prelude)
            statements.extend(produced)
        if not statements:
            self.diagnostics.warning(
                f"{language} body of '{pou.name}' produced no statements",
                file=self.filename,
                code="PF125",
                hint="plcforge translates coils, output variables, jumps and returns.",
            )
        return statements

    @staticmethod
    def _is_pure_sink(element: ElementTree.Element, body: ElementTree.Element) -> bool:
        """True for a block whose outputs nothing else reads - a timer, usually."""
        local_id = element.get("localId")
        if not local_id:
            return False
        for other in body:
            for connection in other.iter():
                if _tag(connection) == "connection" and connection.get("refLocalId") == local_id:
                    return False
        return True

    @staticmethod
    def _execution_key(element: ElementTree.Element) -> tuple[int, int, int]:
        """Order sinks by explicit execution order, then top to bottom."""
        order = element.get("executionOrderId")
        position = _find(element, "position")
        x = int(float(position.get("x") or 0)) if position is not None else 0
        y = int(float(position.get("y") or 0)) if position is not None else 0
        return (int(order) if order and order.isdigit() else 1 << 20, y, x)

    def _read_sink(self, element: ElementTree.Element, network: _Network, pou: Pou) -> list[Stmt]:
        kind = _tag(element)
        span = SourceSpan(self.filename, 0, 0)

        if kind in ("coil", "outvariable"):
            value = self._resolve_input(element, network, pou)
            if value is None:
                return []
            target = self._sink_target(element, network, pou)
            if target is None:
                return []
            if kind == "coil":
                return self._coil_statement(element, target, value, span)
            return [Assign(target=target, value=value, span=span)]

        if kind == "return":
            condition = self._resolve_input(element, network, pou)
            if condition is None:
                return [Return(span=span)]
            return [If(branches=[Branch(condition=condition, body=[Return(span=span)])], span=span)]

        if kind == "jump":
            self.diagnostics.warning(
                f"JMP to '{element.get('label')}' in '{pou.name}' is not translated",
                file=self.filename,
                code="PF126",
                hint="Restructure the network with IF/CASE, or export the POU as ST.",
            )
            return []

        if kind == "block":
            # A block whose outputs nothing reads - a timer driven for its side
            # effect, typically.  Resolving it emits the call into the prelude.
            self._resolve_block(element, network, pou, None, span)
            return []

        if kind == "rightpowerrail":
            return []

        return []

    def _coil_statement(self, element: ElementTree.Element, target: Expr, value: Expr, span: SourceSpan) -> list[Stmt]:
        storage = (element.get("storage") or "none").lower()
        negated = _is_true(element.get("negated"))
        if negated:
            value = Unary(op="NOT", operand=value, span=span)
        if storage == "set":
            body: list[Stmt] = [Assign(target=target, value=Literal(value=True, type=BOOL), span=span)]
            return [If(branches=[Branch(condition=value, body=body)], span=span)]
        if storage == "reset":
            body = [Assign(target=target, value=Literal(value=False, type=BOOL), span=span)]
            return [If(branches=[Branch(condition=value, body=body)], span=span)]
        return [Assign(target=target, value=value, span=span)]

    def _sink_target(self, element: ElementTree.Element, network: _Network, pou: Pou) -> Expr | None:
        for tag in ("variable", "expression"):
            node = _find(element, tag)
            text = (node.text or "").strip() if node is not None else ""
            if text:
                return parse_expression_text(text, self.filename, self.diagnostics)
        self.diagnostics.warning(
            f"output element {element.get('localId')} in '{pou.name}' has no variable",
            file=self.filename,
            code="PF127",
        )
        return None

    # -- backwards traversal ------------------------------------------- #

    def _resolve_input(self, element: ElementTree.Element, network: _Network, pou: Pou) -> Expr | None:
        """Value arriving at an element's ``connectionPointIn``.

        Several connections on one input point mean parallel branches in ladder
        logic, which is an OR.
        """
        point = _find(element, "connectionpointin")
        if point is None:
            return None
        return self._resolve_point(point, network, pou)

    def _resolve_point(
        self,
        point: ElementTree.Element,
        network: _Network,
        pou: Pou,
    ) -> Expr | None:
        branches: list[Expr] = []
        for connection in _findall(point, "connection"):
            value = self._resolve_connection(connection, network, pou)
            if value is not None:
                branches.append(value)
        if not branches:
            return None
        result = branches[0]
        for extra in branches[1:]:
            result = Binary(op="OR", left=result, right=extra, span=result.span)
        if _is_true(point.get("negated")):
            result = Unary(op="NOT", operand=result, span=result.span)
        return result

    def _resolve_connection(
        self,
        connection: ElementTree.Element,
        network: _Network,
        pou: Pou,
    ) -> Expr | None:
        reference = connection.get("refLocalId")
        if not reference:
            return None
        source = network.elements.get(reference)
        if source is None:
            self.diagnostics.warning(
                f"connection to unknown element {reference} in '{pou.name}'",
                file=self.filename,
                code="PF128",
            )
            return None
        return self._resolve_element(source, network, pou, connection.get("formalParameter"))

    def _resolve_element(
        self,
        element: ElementTree.Element,
        network: _Network,
        pou: Pou,
        pin: str | None = None,
    ) -> Expr | None:
        if self._depth > 200:  # pragma: no cover - malformed, cyclic document
            self.diagnostics.error(
                f"network in '{pou.name}' contains a cycle",
                file=self.filename,
                code="PF129",
            )
            return None
        kind = _tag(element)
        span = SourceSpan(self.filename, 0, 0)
        key = f"{element.get('localId')}:{pin or ''}"
        if key in network.values:
            return network.values[key]

        self._depth += 1
        try:
            result = self._resolve_element_inner(element, network, pou, pin, kind, span)
        finally:
            self._depth -= 1
        if result is not None:
            network.values[key] = result
        return result

    def _resolve_element_inner(
        self,
        element: ElementTree.Element,
        network: _Network,
        pou: Pou,
        pin: str | None,
        kind: str,
        span: SourceSpan,
    ) -> Expr | None:
        if kind == "leftpowerrail":
            return Literal(value=True, type=BOOL, text="TRUE", span=span)

        if kind == "contact":
            variable = _find(element, "variable")
            name = (variable.text or "").strip() if variable is not None else ""
            if not name:
                return self._resolve_input(element, network, pou)
            operand: Expr = parse_expression_text(name, self.filename, self.diagnostics)
            edge = (element.get("edge") or "").lower()
            if edge in ("rising", "falling"):
                operand = self._edge_expression(edge, operand, element, network, pou, span)
            elif _is_true(element.get("negated")):
                operand = Unary(op="NOT", operand=operand, span=span)
            incoming = self._resolve_input(element, network, pou)
            if incoming is None or self._is_true_literal(incoming):
                return operand
            return Binary(op="AND", left=incoming, right=operand, span=span)

        if kind in ("invariable", "inoutvariable"):
            expression = _find(element, "expression")
            text = (expression.text or "").strip() if expression is not None else ""
            if not text:
                return None
            value: Expr = parse_expression_text(text, self.filename, self.diagnostics)
            if _is_true(element.get("negated")):
                value = Unary(op="NOT", operand=value, span=span)
            return value

        if kind == "connector":
            return self._resolve_input(element, network, pou)

        if kind == "block":
            return self._resolve_block(element, network, pou, pin, span)

        self.diagnostics.warning(
            f"graphical element '{kind}' in '{pou.name}' is not translated",
            file=self.filename,
            code="PF130",
        )
        return None

    @staticmethod
    def _is_true_literal(expr: Expr) -> bool:
        return isinstance(expr, Literal) and expr.value is True

    def _edge_expression(
        self,
        edge: str,
        operand: Expr,
        element: ElementTree.Element,
        network: _Network,
        pou: Pou,
        span: SourceSpan,
    ) -> Expr:
        """Turn an edge-sensitive contact into an R_TRIG/F_TRIG instance."""
        block_type = "R_TRIG" if edge == "rising" else "F_TRIG"
        instance = self._ensure_instance(pou, f"_{block_type.lower()}_{element.get('localId')}", block_type)
        network.prelude.append(
            Invoke(
                call=Call(
                    callee=VarRef(name=instance, span=span),
                    args=[Argument(value=operand, name="CLK")],
                    span=span,
                ),
                span=span,
            )
        )
        return Member(base=VarRef(name=instance, span=span), field_name="Q", span=span)

    def _resolve_block(
        self,
        element: ElementTree.Element,
        network: _Network,
        pou: Pou,
        pin: str | None,
        span: SourceSpan,
    ) -> Expr | None:
        type_name = element.get("typeName") or ""
        instance_name = element.get("instanceName")
        arguments = self._block_arguments(element, network, pou)

        if instance_name or stdlib.is_standard_block(type_name):
            instance = instance_name or f"_{type_name.lower()}_{element.get('localId')}"
            self._ensure_instance(pou, instance, type_name)
            self._invoke_once(element, instance, arguments, network, span)
            output = pin or self._default_output(type_name)
            return Member(base=VarRef(name=instance, span=span), field_name=output, span=span)

        operator = self._operator_expression(type_name, arguments, span)
        if operator is not None:
            return operator

        return Call(
            callee=VarRef(name=type_name, span=span),
            args=[Argument(value=value, name=name) for name, value in arguments],
            span=span,
        )

    def _block_arguments(
        self,
        element: ElementTree.Element,
        network: _Network,
        pou: Pou,
    ) -> list[tuple[str, Expr]]:
        arguments: list[tuple[str, Expr]] = []
        inputs = _find(element, "inputvariables")
        if inputs is None:
            return arguments
        for index, variable in enumerate(_findall(inputs, "variable"), start=1):
            point = _find(variable, "connectionpointin")
            value = self._resolve_point(point, network, pou) if point is not None else None
            if value is None:
                continue
            if _is_true(variable.get("negated")):
                value = Unary(op="NOT", operand=value, span=value.span)
            arguments.append((variable.get("formalParameter") or f"IN{index}", value))
        return arguments

    def _invoke_once(
        self,
        element: ElementTree.Element,
        instance: str,
        arguments: list[tuple[str, Expr]],
        network: _Network,
        span: SourceSpan,
    ) -> None:
        """Emit the call for a function block instance at most once per rung."""
        local_id = element.get("localId") or instance
        if local_id in network.invoked:
            return
        network.invoked.add(local_id)
        network.prelude.append(
            Invoke(
                call=Call(
                    callee=VarRef(name=instance, span=span),
                    args=[Argument(value=value, name=name) for name, value in arguments],
                    span=span,
                ),
                span=span,
            )
        )

    @staticmethod
    def _default_output(type_name: str) -> str:
        signature = stdlib.block_signature(type_name)
        if signature is not None and signature.outputs:
            return signature.outputs[0][0]
        return "OUT"

    def _operator_expression(
        self,
        type_name: str,
        arguments: list[tuple[str, Expr]],
        span: SourceSpan,
    ) -> Expr | None:
        """Render a standard function block box as a plain expression."""
        upper = type_name.upper()
        values = [value for _, value in arguments]
        if not values:
            return None
        binary = {
            "AND": "AND",
            "OR": "OR",
            "XOR": "XOR",
            "ADD": "+",
            "SUB": "-",
            "MUL": "*",
            "DIV": "/",
            "MOD": "MOD",
            "GT": ">",
            "GE": ">=",
            "LT": "<",
            "LE": "<=",
            "EQ": "=",
            "NE": "<>",
            "EXPT": "**",
        }
        if upper in binary:
            result = values[0]
            for extra in values[1:]:
                result = Binary(op=binary[upper], left=result, right=extra, span=span)
            return result
        if upper == "NOT":
            return Unary(op="NOT", operand=values[0], span=span)
        if upper == "MOVE":
            return values[0]
        if stdlib.is_standard_function(upper):
            return Call(
                callee=VarRef(name=upper, span=span),
                args=[Argument(value=value) for value in values],
                span=span,
            )
        return None

    def _ensure_instance(self, pou: Pou, name: str, block_type: str) -> str:
        """Declare an implicit function block instance if it is not declared yet."""
        if pou.find(name) is not None:
            return name
        target = next((b for b in pou.var_blocks if b.kind is VarKind.LOCAL), None)
        if target is None:
            target = VarBlock(kind=VarKind.LOCAL)
            pou.var_blocks.append(target)
        target.declarations.append(
            VarDecl(
                name=name,
                type=instance_of(block_type),
                comment="Implicit instance created from the graphical body.",
                span=SourceSpan(self.filename, 0, 0),
            )
        )
        return name


def parse_source(
    source: str,
    filename: str = "<plcopen>",
    diagnostics: DiagnosticBag | None = None,
    project_name: str | None = None,
) -> Project:
    return PlcOpenReader(filename, diagnostics).parse(source, project_name)


def parse_file(path: str, diagnostics: DiagnosticBag | None = None) -> Project:
    file = pathlib.Path(path)
    return parse_source(
        file.read_text(encoding="utf-8-sig"),
        str(file),
        diagnostics,
        project_name=file.stem,
    )
