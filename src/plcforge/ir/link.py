"""Resolve user defined type names across a whole project.

A frontend parsing one file cannot know whether ``Setpoints`` is a structure, an
enumeration or a function block - the declaration may live in another file.  The
parser therefore records every user type as an instance reference, and this pass
reclassifies them once all sources are loaded.
"""

from __future__ import annotations

from ..diagnostics import DiagnosticBag
from .nodes import Pou, Project, VarDecl
from .stdlib import STANDARD_BLOCKS
from .types import IecType, TypeKind, derived

__all__ = ["instance_owner", "link_types"]


def _reclassify(declared: IecType, user_types: set[str], blocks: set[str]) -> IecType:
    """Return *declared* with user type references resolved, arrays included."""
    if declared.kind is TypeKind.ARRAY and declared.element is not None:
        element = _reclassify(declared.element, user_types, blocks)
        if element is declared.element:
            return declared
        return IecType(
            declared.name,
            declared.kind,
            element=element,
            dimensions=declared.dimensions,
            length=declared.length,
        )
    if declared.kind is not TypeKind.INSTANCE:
        return declared
    if declared.name.lower() in user_types:
        return derived(declared.name)
    return declared


def _base_type(declared: IecType) -> IecType:
    while declared.kind is TypeKind.ARRAY and declared.element is not None:
        declared = declared.element
    return declared


def link_types(project: Project, diagnostics: DiagnosticBag | None = None) -> DiagnosticBag:
    """Turn instance references into structures or enumerations where they are.

    Names that match nothing keep their instance classification and are reported
    once, together with the POU they appear in, so a missing library file is easy
    to spot in the diagnostics.
    """
    diagnostics = diagnostics if diagnostics is not None else DiagnosticBag()
    user_types = {s.name.lower() for s in project.structs} | {e.name.lower() for e in project.enums}
    blocks = {p.name.lower() for p in project.pous} | {name.lower() for name in STANDARD_BLOCKS}
    reported: set[str] = set()

    def visit(declaration: VarDecl, owner: str) -> None:
        declaration.type = _reclassify(declaration.type, user_types, blocks)
        target = _base_type(declaration.type)
        if target.kind is not TypeKind.INSTANCE or target.name.lower() in blocks:
            return
        key = target.name.lower()
        if key in reported:
            return
        reported.add(key)
        diagnostics.warning(
            f"unknown type '{target.name}' used by {owner}.{declaration.name}",
            span=declaration.span,
            code="PF210",
            hint="Add the file that declares it; until then it is translated as an opaque instance.",
        )

    for pou in project.pous:
        for declaration in pou.declarations():
            visit(declaration, pou.name)
    for struct in project.structs:
        for declaration in struct.fields:
            visit(declaration, struct.name)
    for block in project.globals:
        for declaration in block.declarations:
            visit(declaration, "VAR_GLOBAL")
    return diagnostics


def instance_owner(project: Project, block_name: str) -> Pou | None:
    """Return the POU that declares *block_name*, if the project contains it."""
    return project.pou(block_name)
