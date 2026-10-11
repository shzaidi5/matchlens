"""The grounding guard: no number reaches the screen unless the pipeline computed it.

Used on everything a model writes (overlay copy, analyst briefs, answers to
viewer questions) and, in the tests, on the built-in templates as well.
"""

from __future__ import annotations

import json
import re
from typing import Any

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
SCALE_CONSTANTS = {99.0, 100.0}


# ------------------------------------------------------------------ grounding
def numbers_in(value: Any) -> set[float]:
    """Every number that appears anywhere in a JSON-like value.

    Strings are scanned too, so minute labels such as "45+2'" contribute 45
    and 2, and a shirt number inside a name string would count.
    """
    found: set[float] = set()
    if isinstance(value, bool) or value is None:
        return found
    if isinstance(value, (int, float)):
        found.add(float(value))
    elif isinstance(value, str):
        found.update(float(m.replace(",", ".")) for m in _NUMBER.findall(value))
    elif isinstance(value, dict):
        for v in value.values():
            found |= numbers_in(v)
    elif isinstance(value, (list, tuple, set)):
        for v in value:
            found |= numbers_in(v)
    return found


def ungrounded_numbers(text: str, allowed: set[float]) -> list[str]:
    """Numbers in `text` that match no allowed value (after sensible rounding)."""
    pool = allowed | SCALE_CONSTANTS
    bad = []
    for token in _NUMBER.findall(text):
        n = float(token.replace(",", "."))
        if not any(abs(n - a) < 0.051 or n == round(a) for a in pool):
            bad.append(token)
    return bad


def parse_json_object(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None
