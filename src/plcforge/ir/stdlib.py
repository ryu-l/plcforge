"""Signatures of the IEC 61131-3 standard library.

Both the type resolver and the backends need to know what ``TON`` looks like and
what ``REAL_TO_INT`` returns.  Keeping that knowledge in one table means the
Python runtime, the C++ runtime and the Structured Text writer cannot drift
apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .types import IecType, elementary, is_integer, is_real, string_of

__all__ = [
    "STANDARD_BLOCKS",
    "STANDARD_FUNCTIONS",
    "BlockSignature",
    "block_signature",
    "function_return_type",
    "is_standard_block",
    "is_standard_function",
]

BOOL = elementary("BOOL")
INT = elementary("INT")
DINT = elementary("DINT")
REAL = elementary("REAL")
LREAL = elementary("LREAL")
TIME = elementary("TIME")
WORD = elementary("WORD")


@dataclass(frozen=True)
class BlockSignature:
    """Interface of a standard function block."""

    name: str
    inputs: tuple[tuple[str, IecType], ...] = ()
    outputs: tuple[tuple[str, IecType], ...] = ()
    #: Short description used in generated docstrings.
    summary: str = ""

    def output_type(self, name: str) -> IecType | None:
        lowered = name.lower()
        for field_name, field_type in self.outputs:
            if field_name.lower() == lowered:
                return field_type
        return None

    def input_type(self, name: str) -> IecType | None:
        lowered = name.lower()
        for field_name, field_type in self.inputs:
            if field_name.lower() == lowered:
                return field_type
        return None

    def member_type(self, name: str) -> IecType | None:
        return self.output_type(name) or self.input_type(name)


def _sig(
    name: str,
    inputs: tuple[tuple[str, IecType], ...],
    outputs: tuple[tuple[str, IecType], ...],
    summary: str,
) -> tuple[str, BlockSignature]:
    return name, BlockSignature(name, inputs, outputs, summary)


STANDARD_BLOCKS: dict[str, BlockSignature] = dict(
    (
        _sig(
            "TON",
            (("IN", BOOL), ("PT", TIME)),
            (("Q", BOOL), ("ET", TIME)),
            "On-delay timer: Q follows IN after PT has elapsed.",
        ),
        _sig(
            "TOF",
            (("IN", BOOL), ("PT", TIME)),
            (("Q", BOOL), ("ET", TIME)),
            "Off-delay timer: Q drops PT after IN goes low.",
        ),
        _sig(
            "TP",
            (("IN", BOOL), ("PT", TIME)),
            (("Q", BOOL), ("ET", TIME)),
            "Pulse timer: Q stays high for PT after a rising edge on IN.",
        ),
        _sig(
            "CTU",
            (("CU", BOOL), ("R", BOOL), ("PV", INT)),
            (("Q", BOOL), ("CV", INT)),
            "Up counter.",
        ),
        _sig(
            "CTD",
            (("CD", BOOL), ("LD", BOOL), ("PV", INT)),
            (("Q", BOOL), ("CV", INT)),
            "Down counter.",
        ),
        _sig(
            "CTUD",
            (("CU", BOOL), ("CD", BOOL), ("R", BOOL), ("LD", BOOL), ("PV", INT)),
            (("QU", BOOL), ("QD", BOOL), ("CV", INT)),
            "Up/down counter.",
        ),
        _sig("R_TRIG", (("CLK", BOOL),), (("Q", BOOL),), "Rising edge detector."),
        _sig("F_TRIG", (("CLK", BOOL),), (("Q", BOOL),), "Falling edge detector."),
        _sig("SR", (("S1", BOOL), ("R", BOOL)), (("Q1", BOOL),), "Set dominant bistable."),
        _sig("RS", (("S", BOOL), ("R1", BOOL)), (("Q1", BOOL),), "Reset dominant bistable."),
        _sig(
            "RTC",
            (("IN", BOOL), ("PDT", elementary("DATE_AND_TIME"))),
            (("Q", BOOL), ("CDT", elementary("DATE_AND_TIME"))),
            "Real time clock.",
        ),
    )
)

#: Functions whose result type equals the type of their first argument.
_SAME_AS_FIRST = {
    "ABS",
    "MIN",
    "MAX",
    "LIMIT",
    "MUX",
    "SEL",
    "ADD",
    "SUB",
    "MUL",
    "DIV",
    "MOD",
    "EXPT",
    "SHL",
    "SHR",
    "ROL",
    "ROR",
    "AND",
    "OR",
    "XOR",
    "NOT",
    "MOVE",
}

#: Functions with a fixed result type.
_FIXED_RESULT: dict[str, IecType] = {
    "SQRT": REAL,
    "LN": REAL,
    "LOG": REAL,
    "EXP": REAL,
    "SIN": REAL,
    "COS": REAL,
    "TAN": REAL,
    "ASIN": REAL,
    "ACOS": REAL,
    "ATAN": REAL,
    "ATAN2": REAL,
    "TRUNC": DINT,
    "ROUND": DINT,
    "LEN": INT,
    "FIND": INT,
    "GT": BOOL,
    "GE": BOOL,
    "EQ": BOOL,
    "LE": BOOL,
    "LT": BOOL,
    "NE": BOOL,
    "LEFT": string_of("STRING"),
    "RIGHT": string_of("STRING"),
    "MID": string_of("STRING"),
    "CONCAT": string_of("STRING"),
    "INSERT": string_of("STRING"),
    "DELETE": string_of("STRING"),
    "REPLACE": string_of("STRING"),
}

STANDARD_FUNCTIONS = frozenset(_SAME_AS_FIRST | set(_FIXED_RESULT))


def is_standard_block(name: str) -> bool:
    return name.upper() in STANDARD_BLOCKS


def is_standard_function(name: str) -> bool:
    return name.upper() in STANDARD_FUNCTIONS or _conversion_target(name) is not None


def block_signature(name: str) -> BlockSignature | None:
    return STANDARD_BLOCKS.get(name.upper())


def _conversion_target(name: str) -> IecType | None:
    """Recognise the conversion family.

    Both spellings are accepted: ``INT_TO_REAL`` from the second edition and the
    shorter ``TO_REAL`` introduced in the third.
    """
    upper = name.upper()
    if upper.startswith("TO_") and len(upper) > 3:
        return elementary(upper[3:])
    if "_TO_" not in upper:
        return None
    _, _, target = upper.partition("_TO_")
    if not target:
        return None
    return elementary(target)


def function_return_type(name: str, arg_types: list[IecType | None]) -> IecType | None:
    """Best effort result type for a standard function call."""
    upper = name.upper()
    converted = _conversion_target(upper)
    if converted is not None:
        return converted
    if upper in _FIXED_RESULT:
        result = _FIXED_RESULT[upper]
        # SQRT and the trigonometric family widen with an LREAL argument.
        if result == REAL and arg_types and is_real(arg_types[0]):
            return arg_types[0]
        return result
    if upper == "SEL" and len(arg_types) >= 3:
        return arg_types[1] or arg_types[2]
    if upper in _SAME_AS_FIRST:
        for candidate in arg_types:
            if candidate is not None:
                return candidate
        return None
    return None


@dataclass
class LibraryUsage:
    """Which parts of the standard library a translated project touches."""

    blocks: set[str] = field(default_factory=set)
    functions: set[str] = field(default_factory=set)

    def note_call(self, name: str) -> None:
        upper = name.upper()
        if upper in STANDARD_BLOCKS:
            self.blocks.add(upper)
        elif is_standard_function(upper):
            self.functions.add(upper)


def integer_division_used(left: IecType | None, right: IecType | None) -> bool:
    """True when ``/`` between these operands must truncate rather than divide."""
    return is_integer(left) and is_integer(right)
