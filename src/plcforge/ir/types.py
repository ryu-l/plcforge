"""IEC 61131-3 type system used by the plcforge intermediate representation.

The type model is deliberately small: everything a control program can declare is
either an elementary type, a string, an array, a user defined type (struct or
enumeration) or a function block instance.  Backends translate this model into
their own type universe, so keeping it narrow keeps the backends honest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "ANY_BIT",
    "ANY_INT",
    "ANY_NUM",
    "ANY_REAL",
    "ANY_STRING",
    "ANY_TIME",
    "BOOL",
    "VOID",
    "IecType",
    "TypeKind",
    "array_of",
    "common_type",
    "derived",
    "elementary",
    "instance_of",
    "is_bit_string",
    "is_bool",
    "is_integer",
    "is_numeric",
    "is_real",
    "is_string",
    "is_time",
    "string_of",
]


class TypeKind(Enum):
    """Broad category a type belongs to."""

    ELEMENTARY = "elementary"
    STRING = "string"
    ARRAY = "array"
    DERIVED = "derived"
    INSTANCE = "instance"
    VOID = "void"


@dataclass(frozen=True)
class IecType:
    """A resolved (or partially resolved) IEC 61131-3 type."""

    name: str
    kind: TypeKind = TypeKind.ELEMENTARY
    element: IecType | None = None
    dimensions: tuple[tuple[int, int], ...] = ()
    length: int | None = None
    fields: tuple[tuple[str, IecType], ...] = field(default=(), compare=False)

    def __str__(self) -> str:  # pragma: no cover - debugging helper
        if self.kind is TypeKind.ARRAY and self.element is not None:
            dims = ", ".join(f"{lo}..{hi}" for lo, hi in self.dimensions)
            return f"ARRAY [{dims}] OF {self.element}"
        if self.kind is TypeKind.STRING and self.length is not None:
            return f"{self.name}[{self.length}]"
        return self.name

    @property
    def rank(self) -> int:
        """Number of array dimensions (``0`` for scalars)."""
        return len(self.dimensions)


# --------------------------------------------------------------------------- #
# Elementary type groups (IEC 61131-3, table 10)
# --------------------------------------------------------------------------- #

SIGNED_INT_TYPES = frozenset({"SINT", "INT", "DINT", "LINT"})
UNSIGNED_INT_TYPES = frozenset({"USINT", "UINT", "UDINT", "ULINT"})
BIT_STRING_TYPES = frozenset({"BYTE", "WORD", "DWORD", "LWORD"})
REAL_TYPES = frozenset({"REAL", "LREAL"})
TIME_TYPES = frozenset({"TIME", "LTIME"})
DATE_TYPES = frozenset({"DATE", "TIME_OF_DAY", "TOD", "DATE_AND_TIME", "DT"})
STRING_TYPES = frozenset({"STRING", "WSTRING", "CHAR", "WCHAR"})

ANY_INT = SIGNED_INT_TYPES | UNSIGNED_INT_TYPES
ANY_BIT = BIT_STRING_TYPES | {"BOOL"}
ANY_REAL = REAL_TYPES
ANY_TIME = TIME_TYPES
ANY_STRING = STRING_TYPES
ANY_NUM = ANY_INT | ANY_REAL | BIT_STRING_TYPES

ELEMENTARY_TYPES = (
    ANY_INT
    | BIT_STRING_TYPES
    | REAL_TYPES
    | TIME_TYPES
    | DATE_TYPES
    | STRING_TYPES
    | {"BOOL"}
)

#: Integer ranges used by the validator and by the C++ backend.
INT_RANGES: dict[str, tuple[int, int]] = {
    "SINT": (-(2**7), 2**7 - 1),
    "INT": (-(2**15), 2**15 - 1),
    "DINT": (-(2**31), 2**31 - 1),
    "LINT": (-(2**63), 2**63 - 1),
    "USINT": (0, 2**8 - 1),
    "UINT": (0, 2**16 - 1),
    "UDINT": (0, 2**32 - 1),
    "ULINT": (0, 2**64 - 1),
    "BYTE": (0, 2**8 - 1),
    "WORD": (0, 2**16 - 1),
    "DWORD": (0, 2**32 - 1),
    "LWORD": (0, 2**64 - 1),
}

#: Implicit widening order used when two operands disagree.
_NUMERIC_RANK = [
    "SINT",
    "USINT",
    "BYTE",
    "INT",
    "UINT",
    "WORD",
    "DINT",
    "UDINT",
    "DWORD",
    "LINT",
    "ULINT",
    "LWORD",
    "REAL",
    "LREAL",
]


def elementary(name: str) -> IecType:
    """Return the elementary type called *name* (case insensitive)."""
    return IecType(name.upper(), TypeKind.ELEMENTARY)


def string_of(name: str, length: int | None = None) -> IecType:
    return IecType(name.upper(), TypeKind.STRING, length=length)


def array_of(element: IecType, dimensions: tuple[tuple[int, int], ...]) -> IecType:
    return IecType("ARRAY", TypeKind.ARRAY, element=element, dimensions=dimensions)


def derived(name: str, fields: tuple[tuple[str, IecType], ...] = ()) -> IecType:
    return IecType(name, TypeKind.DERIVED, fields=fields)


def instance_of(block_name: str) -> IecType:
    """A function block instance variable, e.g. ``delay : TON;``."""
    return IecType(block_name, TypeKind.INSTANCE)


BOOL = elementary("BOOL")
VOID = IecType("VOID", TypeKind.VOID)
UNKNOWN = IecType("ANY", TypeKind.ELEMENTARY)


def is_bool(t: IecType | None) -> bool:
    return t is not None and t.kind is TypeKind.ELEMENTARY and t.name == "BOOL"


def is_integer(t: IecType | None) -> bool:
    return t is not None and t.kind is TypeKind.ELEMENTARY and t.name in ANY_INT


def is_bit_string(t: IecType | None) -> bool:
    return t is not None and t.kind is TypeKind.ELEMENTARY and t.name in BIT_STRING_TYPES


def is_real(t: IecType | None) -> bool:
    return t is not None and t.kind is TypeKind.ELEMENTARY and t.name in REAL_TYPES


def is_time(t: IecType | None) -> bool:
    return t is not None and t.kind is TypeKind.ELEMENTARY and t.name in TIME_TYPES


def is_string(t: IecType | None) -> bool:
    return t is not None and (t.kind is TypeKind.STRING or t.name in STRING_TYPES)


def is_numeric(t: IecType | None) -> bool:
    return is_integer(t) or is_real(t) or is_bit_string(t)


def common_type(left: IecType | None, right: IecType | None) -> IecType | None:
    """Return the wider of two numeric types, or ``None`` when not comparable.

    IEC 61131-3 requires explicit conversion between different elementary types.
    Real controllers are more forgiving than the standard, and so is plcforge:
    mixed arithmetic widens instead of failing, and the validator reports it as
    a warning rather than an error.
    """
    if left is None or right is None:
        return left or right
    if left == right:
        return left
    if is_numeric(left) and is_numeric(right):
        try:
            rank = max(_NUMERIC_RANK.index(left.name), _NUMERIC_RANK.index(right.name))
        except ValueError:
            return None
        return elementary(_NUMERIC_RANK[rank])
    if is_time(left) and is_time(right):
        return left
    if is_string(left) and is_string(right):
        return left
    return None
