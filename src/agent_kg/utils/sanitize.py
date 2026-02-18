"""String sanitisation utilities for class names and identifiers."""

from __future__ import annotations

import re
import unicodedata


def sanitize_for_identifier(name: str, style: str = "upper") -> str:
    """Clean a string to produce a valid Python identifier.

    Removes accents, replaces invalid characters with underscores,
    and applies the requested casing style.

    Args:
        name: Raw string to sanitise.
        style: One of ``"upper"``, ``"lower"``, ``"title"``.

    Returns:
        A sanitised identifier string.
    """
    if not isinstance(name, str):
        raise TypeError(f"Expected str, got {type(name).__name__}")

    # Decompose unicode → strip combining marks (accents)
    s = unicodedata.normalize("NFD", name)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")

    # Normalise whitespace and special chars
    s = s.strip().replace("&", "_AND_")
    s = re.sub(r"[^a-zA-Z0-9_]+", "_", s)

    # Ensure it doesn't start with a digit
    if s and s[0].isdigit():
        s = "_" + s

    # Clean up underscores
    s = s.strip("_")
    s = re.sub(r"_+", "_", s)

    # Apply casing
    if style == "title":
        return s.title()
    elif style == "upper":
        return s.upper()
    elif style == "lower":
        return s.lower()
    return s
