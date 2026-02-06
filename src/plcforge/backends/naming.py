"""Identifier mapping shared by the backends.

PLC identifiers are case insensitive, may collide with target language keywords
and may be direct addresses such as ``%IX0.0``.  Every backend needs the same
answer to "what is this called in the target language", so the mapping lives
here and is applied consistently.
"""

from __future__ import annotations

import keyword
import re

__all__ = ["class_name", "cpp_name", "module_name", "python_name", "sanitize", "unique"]

_PYTHON_RESERVED = set(keyword.kwlist) | set(getattr(keyword, "softkwlist", [])) | {
    "self",
    "cls",
    "None",
    "True",
    "False",
    "match",
    "case",
}

_CPP_RESERVED = {
    "alignas", "alignof", "and", "asm", "auto", "bitand", "bitor", "bool", "break",
    "case", "catch", "char", "class", "compl", "const", "constexpr", "continue",
    "decltype", "default", "delete", "do", "double", "dynamic_cast", "else", "enum",
    "explicit", "export", "extern", "false", "float", "for", "friend", "goto", "if",
    "inline", "int", "long", "mutable", "namespace", "new", "noexcept", "not",
    "nullptr", "operator", "or", "private", "protected", "public", "register",
    "return", "short", "signed", "sizeof", "static", "struct", "switch", "template",
    "this", "throw", "true", "try", "typedef", "typeid", "typename", "union",
    "unsigned", "using", "virtual", "void", "volatile", "while", "xor",
}

_ADDRESS_RE = re.compile(r"^%([IQM])([XBWDL])?([0-9.]+)$", re.IGNORECASE)
_AREA_NAMES = {"I": "input", "Q": "output", "M": "memory"}


def sanitize(name: str) -> str:
    """Turn any PLC identifier into a valid target language identifier.

    Direct addresses become readable names rather than mangled punctuation:

    >>> sanitize("%IX0.0")
    'input_x0_0'
    >>> sanitize("Motor Start")
    'Motor_Start'
    """
    match = _ADDRESS_RE.match(name)
    if match:
        area = _AREA_NAMES.get(match.group(1).upper(), "io")
        size = (match.group(2) or "").lower()
        offset = match.group(3).replace(".", "_")
        return f"{area}_{size}{offset}" if size else f"{area}_{offset}"
    cleaned = re.sub(r"[^0-9A-Za-z_]", "_", name)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    return cleaned


def python_name(name: str) -> str:
    """Sanitised name that is safe to use as a Python identifier."""
    cleaned = sanitize(name)
    if cleaned in _PYTHON_RESERVED or keyword.iskeyword(cleaned):
        return f"{cleaned}_"
    return cleaned


def cpp_name(name: str) -> str:
    cleaned = sanitize(name)
    if cleaned in _CPP_RESERVED:
        return f"{cleaned}_"
    return cleaned


def class_name(name: str) -> str:
    """POU name turned into a class name, keeping the author's spelling.

    ``ConveyorSegment`` stays ``ConveyorSegment``; ``conveyor_segment`` becomes
    ``ConveyorSegment``; ``FB_Motor`` stays ``FB_Motor``.
    """
    cleaned = sanitize(name)
    if "_" in cleaned and cleaned.islower():
        return "".join(part.title() for part in cleaned.split("_") if part)
    if cleaned[:1].islower():
        return cleaned[:1].upper() + cleaned[1:]
    return cleaned


def module_name(name: str) -> str:
    """POU name turned into a snake_case module name."""
    cleaned = sanitize(name)
    stepped = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", cleaned)
    stepped = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", stepped)
    lowered = re.sub(r"_+", "_", stepped).strip("_").lower()
    if not lowered or lowered[0].isdigit():
        lowered = f"m_{lowered}"
    return f"{lowered}_" if keyword.iskeyword(lowered) else lowered


def unique(name: str, taken: set[str]) -> str:
    """Return *name*, or *name* with a numeric suffix if it is already taken."""
    if name not in taken:
        taken.add(name)
        return name
    index = 2
    while f"{name}_{index}" in taken:
        index += 1
    result = f"{name}_{index}"
    taken.add(result)
    return result
