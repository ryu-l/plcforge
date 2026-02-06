"""IEC 61131-3 data types expressed in Python.

Elementary types map onto Python builtins so that generated code stays readable
and fast.  What the mapping cannot express - fixed width wrap-around, duration
literals, structure defaults - lives here as small helpers the generated code
calls explicitly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields, is_dataclass
from typing import Any, ClassVar, TypeAlias

__all__ = [
    "BOOL",
    "BYTE",
    "DINT",
    "DWORD",
    "INT",
    "INT_LIMITS",
    "LINT",
    "LREAL",
    "LWORD",
    "REAL",
    "SINT",
    "STRING",
    "TIME",
    "UDINT",
    "UINT",
    "ULINT",
    "USINT",
    "WORD",
    "Array",
    "Struct",
    "T",
    "filled",
    "format_time",
    "wrap",
]

# Elementary type aliases.  They are aliases on purpose: a DINT is a Python int,
# so arithmetic keeps full speed and no wrapper object is allocated per scan.
# Declaring them as TypeAlias rather than plain assignment keeps them usable in
# annotations, which is how the generated code spells its declarations.
BOOL: TypeAlias = bool
SINT: TypeAlias = int
INT: TypeAlias = int
DINT: TypeAlias = int
LINT: TypeAlias = int
USINT: TypeAlias = int
UINT: TypeAlias = int
UDINT: TypeAlias = int
ULINT: TypeAlias = int
BYTE: TypeAlias = int
WORD: TypeAlias = int
DWORD: TypeAlias = int
LWORD: TypeAlias = int
REAL: TypeAlias = float
LREAL: TypeAlias = float
#: Durations are seconds as a float, matching the units used by every timer.
TIME: TypeAlias = float
STRING: TypeAlias = str

#: Value ranges of the fixed width integer types, used by :func:`wrap`.
INT_LIMITS: dict[str, tuple[int, int]] = {
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


def wrap(value: int, type_name: str) -> int:
    """Truncate *value* to the width of *type_name*, like a real controller.

    Python integers are unbounded, so an accumulator that would roll over on a
    PLC keeps growing here.  Call this where the overflow behaviour matters.

    >>> wrap(32768, "INT")
    -32768
    """
    limits = INT_LIMITS.get(type_name.upper())
    if limits is None:
        return value
    low, high = limits
    span = high - low + 1
    return (value - low) % span + low


_DURATION_UNITS = {
    "d": 86400.0,
    "h": 3600.0,
    "m": 60.0,
    "s": 1.0,
    "ms": 1e-3,
    "us": 1e-6,
    "ns": 1e-9,
}
_UNIT_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)(d|h|ms|us|ns|m|s)", re.IGNORECASE)


def T(literal: str) -> TIME:
    """Parse an IEC duration literal into seconds.

    Accepts the spelling used in Structured Text, with or without the prefix.

    >>> T("T#1s500ms")
    1.5
    >>> T("2m30s")
    150.0
    """
    text = literal.strip()
    for prefix in ("TIME#", "LTIME#", "T#", "LT#"):
        if text.upper().startswith(prefix):
            text = text[len(prefix) :]
            break
    sign = 1.0
    if text.startswith("-"):
        sign, text = -1.0, text[1:]
    matches = _UNIT_RE.findall(text.replace("_", ""))
    if not matches:
        raise ValueError(f"not a duration literal: {literal!r}")
    return sign * sum(float(amount) * _DURATION_UNITS[unit.lower()] for amount, unit in matches)


def format_time(seconds: TIME) -> str:
    """Render seconds back as an IEC duration literal.

    >>> format_time(1.5)
    'T#1s500ms'
    >>> format_time(0.0)
    'T#0ms'
    """
    if seconds < 0:
        return "-" + format_time(-seconds)
    remaining = seconds
    parts: list[str] = []
    for unit, size in (("d", 86400.0), ("h", 3600.0), ("m", 60.0), ("s", 1.0)):
        count = int(remaining // size)
        if count:
            parts.append(f"{count}{unit}")
            remaining -= count * size
    milliseconds = round(remaining * 1000)
    if milliseconds or not parts:
        parts.append(f"{milliseconds}ms")
    return "T#" + "".join(parts)


class Array:
    """A multi-dimensional array with IEC index bounds.

    ``ARRAY [1..5] OF REAL`` is indexed from one, and ``ARRAY [-10..10]`` from
    minus ten.  Translating those to a zero-based Python list would silently
    shift every index in the program, so the bounds are kept instead.

    >>> a = Array([(1, 3)], 0.0)
    >>> a[1] = 4.5
    >>> a[1], len(a)
    (4.5, 3)
    >>> grid = Array([(0, 1), (1, 2)], 0)
    >>> grid[1, 2] = 7
    >>> grid[1, 2]
    7
    """

    __slots__ = ("_bounds", "_data", "_strides")

    def __init__(self, bounds: Any, fill: Any = 0) -> None:
        normalised: list[tuple[int, int]] = []
        for entry in bounds:
            low, high = entry
            if high < low:
                raise ValueError(f"array bound {low}..{high} is empty")
            normalised.append((int(low), int(high)))
        self._bounds: tuple[tuple[int, int], ...] = tuple(normalised)
        sizes = [high - low + 1 for low, high in self._bounds]
        strides: list[int] = []
        running = 1
        for size in reversed(sizes):
            strides.append(running)
            running *= size
        self._strides = tuple(reversed(strides))
        total = running
        maker = fill if callable(fill) else None
        self._data: list[Any] = [maker() for _ in range(total)] if maker else [fill] * total

    # -- bounds ------------------------------------------------------------ #

    @property
    def bounds(self) -> tuple[tuple[int, int], ...]:
        return self._bounds

    @property
    def lower(self) -> int:
        return self._bounds[0][0]

    @property
    def upper(self) -> int:
        return self._bounds[0][1]

    def __len__(self) -> int:
        return len(self._data)

    # -- element access ---------------------------------------------------- #

    def _offset(self, key: Any) -> int:
        indices = key if isinstance(key, tuple) else (key,)
        if len(indices) != len(self._bounds):
            raise IndexError(
                f"array has {len(self._bounds)} dimension(s), got {len(indices)} index/indices"
            )
        offset = 0
        for index, (low, high), stride in zip(indices, self._bounds, self._strides, strict=True):
            if not low <= index <= high:
                raise IndexError(f"index {index} outside {low}..{high}")
            offset += (index - low) * stride
        return offset

    def __getitem__(self, key: Any) -> Any:
        return self._data[self._offset(key)]

    def __setitem__(self, key: Any, value: Any) -> None:
        self._data[self._offset(key)] = value

    def __iter__(self):
        return iter(self._data)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Array):
            return self._bounds == other._bounds and self._data == other._data
        if isinstance(other, list):
            return self._data == other
        return NotImplemented

    def to_list(self) -> list[Any]:
        """Flat copy of the contents, in row-major order."""
        return list(self._data)

    def fill(self, value: Any) -> Array:
        for index in range(len(self._data)):
            self._data[index] = value() if callable(value) else value
        return self

    def __repr__(self) -> str:
        shown = ", ".join(f"{low}..{high}" for low, high in self._bounds)
        return f"Array([{shown}], {self._data!r})"


def filled(array: Array, values: Any) -> Array:
    """Write *values* into *array* in declaration order and return it.

    Generated code uses this for ``ARRAY [1..3] OF INT := [10, 20, 30]``, where
    the initialiser is a flat list but the array keeps its IEC index bounds.

    >>> filled(Array([(1, 3)], 0), [10, 20, 30])[1]
    10
    """
    data = list(values)
    if len(data) > len(array):
        raise ValueError(f"initialiser has {len(data)} values, array holds {len(array)}")
    for offset, value in enumerate(data):
        array._data[offset] = value
    return array


@dataclass
class Struct:
    """Base class for translated ``TYPE ... STRUCT ... END_STRUCT`` types.

    Generated structures are plain dataclasses; this base only adds the two
    conveniences that make them pleasant at a REPL and in tests.
    """

    #: Filled in by generated subclasses so tooling can report the origin.
    __iec_name__: ClassVar[str] = "STRUCT"

    def to_dict(self) -> dict[str, Any]:
        if not is_dataclass(self):  # pragma: no cover - defensive
            return {}
        result: dict[str, Any] = {}
        for item in fields(self):
            value = getattr(self, item.name)
            result[item.name] = value.to_dict() if isinstance(value, Struct) else value
        return result

    def update(self, **values: Any) -> Struct:
        for key, value in values.items():
            if not hasattr(self, key):
                raise AttributeError(f"{type(self).__name__} has no member {key!r}")
            setattr(self, key, value)
        return self
