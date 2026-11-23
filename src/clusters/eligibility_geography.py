"""County-name comparison that tolerates common formatting differences."""

from __future__ import annotations

import re

_SUFFIXES = (" county", " parish")


def county_key(name: str | None) -> str:
    """Return a comparison key for a county or county-equivalent name.

    Ignores case, punctuation (including _ and -), spacing, a trailing
    "County" or "Parish", and the St./Saint and Ste./Sainte spellings. "City"
    is deliberately kept, because independent cities (for example St. Louis
    City) are distinct from the county of the same name.
    """
    text = re.sub(r"[.,_\-]", " ", (name or "").casefold())
    text = re.sub(r"\s+", " ", text).strip()
    match = re.fullmatch(r"city of (.+)", text)
    if match:
        text = f"{match.group(1)} city"
    text = re.sub(r"^st ", "saint ", text)
    text = re.sub(r"^ste ", "sainte ", text)
    for suffix in _SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text


def same_county(left: str | None, right: str | None) -> bool:
    """Return whether two county names refer to the same jurisdiction."""
    key = county_key(left)
    return bool(key) and key == county_key(right)
