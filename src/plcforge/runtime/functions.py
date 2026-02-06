"""The IEC 61131-3 standard functions.

Several of these exist because Python's own operators disagree with IEC in ways
that silently change program behaviour:

* ``/`` between integers truncates toward zero in IEC, while Python's ``//``
  floors - the two differ for negative operands (``-7 / 2`` is ``-3``, not
  ``-4``),
* ``MOD`` takes the sign of the dividend in IEC and the sign of the divisor in
  Python (``-7 MOD 2`` is ``-1``, not ``1``),
* ``FOR`` counts inclusively, ``range`` does not,
* string positions are 1-based.

Generated code calls these helpers wherever that difference is observable.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from typing import Any, TypeVar

from .types import INT_LIMITS, TIME, format_time, wrap

__all__ = [
    "ABS",
    "ACOS",
    "ASIN",
    "ATAN",
    "ATAN2",
    "CONCAT",
    "CONVERSIONS",
    "COS",
    "DELETE",
    "EXP",
    "FIND",
    "INSERT",
    "LEFT",
    "LEN",
    "LIMIT",
    "LN",
    "LOG",
    "MAX",
    "MID",
    "MIN",
    "MOVE",
    "MUX",
    "REPLACE",
    "RIGHT",
    "ROL",
    "ROR",
    "ROUND",
    "SEL",
    "SHL",
    "SHR",
    "SIN",
    "SQRT",
    "TAN",
    "TRUNC",
    "convert",
    "idiv",
    "iec_range",
    "imod",
]

_T = TypeVar("_T")


# --------------------------------------------------------------------------- #
# Operators whose Python spelling would change behaviour
# --------------------------------------------------------------------------- #


def idiv(dividend: int, divisor: int) -> int:
    """Integer division that truncates toward zero, as IEC 61131-3 requires.

    >>> idiv(-7, 2), -7 // 2
    (-3, -4)
    """
    if divisor == 0:
        raise ZeroDivisionError("integer division by zero")
    quotient = abs(dividend) // abs(divisor)
    return -quotient if (dividend < 0) != (divisor < 0) else quotient


def imod(dividend: int, divisor: int) -> int:
    """Remainder that follows the sign of the dividend, as IEC requires.

    >>> imod(-7, 2), -7 % 2
    (-1, 1)
    """
    if divisor == 0:
        raise ZeroDivisionError("modulo by zero")
    return dividend - divisor * idiv(dividend, divisor)


def iec_range(start: int, stop: int, step: int = 1) -> Iterator[int]:
    """The inclusive counting of ``FOR i := start TO stop BY step``.

    >>> list(iec_range(1, 3))
    [1, 2, 3]
    >>> list(iec_range(10, 1, -3))
    [10, 7, 4, 1]
    """
    if step == 0:
        raise ValueError("FOR loop step must not be zero")
    return iter(range(start, stop + (1 if step > 0 else -1), step))


# --------------------------------------------------------------------------- #
# Numeric functions
# --------------------------------------------------------------------------- #


def ABS(value: _T) -> _T:
    return abs(value)  # type: ignore[arg-type,return-value]


def SQRT(value: float) -> float:
    return math.sqrt(value)


def LN(value: float) -> float:
    return math.log(value)


def LOG(value: float) -> float:
    return math.log10(value)


def EXP(value: float) -> float:
    return math.exp(value)


def SIN(value: float) -> float:
    return math.sin(value)


def COS(value: float) -> float:
    return math.cos(value)


def TAN(value: float) -> float:
    return math.tan(value)


def ASIN(value: float) -> float:
    return math.asin(value)


def ACOS(value: float) -> float:
    return math.acos(value)


def ATAN(value: float) -> float:
    return math.atan(value)


def ATAN2(y: float, x: float) -> float:
    return math.atan2(y, x)


def MIN(*values: _T) -> _T:
    return min(values)  # type: ignore[type-var]


def MAX(*values: _T) -> _T:
    return max(values)  # type: ignore[type-var]


def LIMIT(minimum: _T, value: _T, maximum: _T) -> _T:
    """Clamp *value* into ``[minimum, maximum]`` (IEC argument order)."""
    return min(max(value, minimum), maximum)  # type: ignore[type-var,call-overload]


def SEL(selector: bool, on_false: _T, on_true: _T) -> _T:
    return on_true if selector else on_false


def MUX(selector: int, *values: _T) -> _T:
    """Select the ``selector``-th value, counting from zero."""
    if not 0 <= selector < len(values):
        raise IndexError(f"MUX selector {selector} outside 0..{len(values) - 1}")
    return values[selector]


def MOVE(value: _T) -> _T:
    return value


def TRUNC(value: float) -> int:
    return math.trunc(value)


def ROUND(value: float) -> int:
    """Round half away from zero, the way controllers do."""
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)


# --------------------------------------------------------------------------- #
# Bit string functions
# --------------------------------------------------------------------------- #


def _width(type_name: str) -> int:
    low, high = INT_LIMITS.get(type_name.upper(), (0, 2**32 - 1))
    return (high - low + 1).bit_length() - 1


def SHL(value: int, count: int, type_name: str = "DWORD") -> int:
    """Shift left, discarding bits pushed out of the type's width."""
    width = _width(type_name)
    return (value << count) & ((1 << width) - 1)


def SHR(value: int, count: int, type_name: str = "DWORD") -> int:
    width = _width(type_name)
    return (value & ((1 << width) - 1)) >> count


def ROL(value: int, count: int, type_name: str = "DWORD") -> int:
    width = _width(type_name)
    mask = (1 << width) - 1
    count %= width
    value &= mask
    return ((value << count) | (value >> (width - count))) & mask if count else value


def ROR(value: int, count: int, type_name: str = "DWORD") -> int:
    width = _width(type_name)
    mask = (1 << width) - 1
    count %= width
    value &= mask
    return ((value >> count) | (value << (width - count))) & mask if count else value


# --------------------------------------------------------------------------- #
# String functions (positions are 1-based, as in IEC)
# --------------------------------------------------------------------------- #


def LEN(value: Sequence[Any]) -> int:
    return len(value)


def LEFT(value: str, count: int) -> str:
    return value[: max(count, 0)]


def RIGHT(value: str, count: int) -> str:
    return value[len(value) - max(count, 0) :] if count > 0 else ""


def MID(value: str, count: int, position: int) -> str:
    start = max(position - 1, 0)
    return value[start : start + max(count, 0)]


def CONCAT(*parts: str) -> str:
    return "".join(parts)


def INSERT(target: str, insertion: str, position: int) -> str:
    index = max(position, 0)
    return target[:index] + insertion + target[index:]


def DELETE(target: str, count: int, position: int) -> str:
    start = max(position - 1, 0)
    return target[:start] + target[start + max(count, 0) :]


def REPLACE(target: str, replacement: str, count: int, position: int) -> str:
    start = max(position - 1, 0)
    return target[:start] + replacement + target[start + max(count, 0) :]


def FIND(haystack: str, needle: str) -> int:
    """Return the 1-based position of *needle*, or 0 when it is absent."""
    return haystack.find(needle) + 1


# --------------------------------------------------------------------------- #
# Type conversion (the <SOURCE>_TO_<TARGET> family)
# --------------------------------------------------------------------------- #


def _to_bool(value: Any) -> bool:
    return bool(value)


def _to_string(value: Any) -> str:
    return format_time(value) if isinstance(value, float) else str(value)


def convert(value: Any, target: str) -> Any:
    """Apply the conversion named by the ``_TO_<TARGET>`` suffix."""
    target = target.upper()
    if target == "BOOL":
        return _to_bool(value)
    if target in ("REAL", "LREAL"):
        return float(value)
    if target in ("STRING", "WSTRING"):
        return _to_string(value)
    if target in ("TIME", "LTIME"):
        return float(value)
    if target in INT_LIMITS:
        if isinstance(value, float):
            value = math.trunc(value)
        return wrap(int(value), target)
    return value


#: Every conversion the Python backend may emit, resolved lazily by name so that
#: adding a target type does not need a new function here.
class _ConversionTable(dict):
    def __missing__(self, name: str):
        if "_TO_" not in name.upper():
            raise KeyError(name)
        target = name.upper().split("_TO_", 1)[1]

        def conversion(value: Any, _target: str = target) -> Any:
            return convert(value, _target)

        conversion.__name__ = name
        conversion.__doc__ = f"IEC conversion function {name}."
        self[name] = conversion
        return conversion


CONVERSIONS = _ConversionTable()


def __getattr__(name: str) -> Any:
    """Resolve ``INT_TO_REAL`` and friends on first use."""
    if "_TO_" in name:
        return CONVERSIONS[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def TIME_TO_STRING(value: TIME) -> str:
    """Render a duration using the IEC literal notation."""
    return format_time(value)
