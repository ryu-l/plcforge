"""plcforge - translate IEC 61131-3 control programs to and from general purpose code.

    from plcforge import translate
    result = translate([pathlib.Path("Conveyor.scl")], target="python")
    print(result.files["Conveyor.py"])
"""

from .diagnostics import Diagnostic, DiagnosticBag, Severity, TranslationError
from .pipeline import TranslationResult, available_targets, load_sources, prepare, translate

__version__ = "0.3.0"

__all__ = [
    "Diagnostic",
    "DiagnosticBag",
    "Severity",
    "TranslationError",
    "TranslationResult",
    "__version__",
    "available_targets",
    "load_sources",
    "prepare",
    "translate",
]
