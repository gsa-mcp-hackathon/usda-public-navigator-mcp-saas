"""Compatibility checks for the Eligibility 0.1 runtime line."""

from __future__ import annotations

import re

_VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_ELIGIBILITY_RUNTIME_LINE = (0, 1)

REQUEST_SCOPED_CANDIDATE_VERSION = "0.1.28"
CRITERIA_PERSISTENCE_VERSION = "0.1.30"
ACTIVE_RULESET_READINESS_VERSION = "0.1.35"
INVENTORY_RECOVERY_VERSION = "0.1.41"


def parse_eligibility_version(version: str) -> tuple[int, int, int] | None:
    """Return a numeric version only for the supported Eligibility 0.1 line."""
    match = _VERSION_PATTERN.fullmatch(version)
    if match is None:
        return None
    parsed = tuple(int(part) for part in match.groups())
    if parsed[:2] != _ELIGIBILITY_RUNTIME_LINE:
        return None
    return parsed


def supports_eligibility_feature(version: str, minimum: str) -> bool:
    """Check a feature's first compatible release without pinning every patch."""
    current = parse_eligibility_version(version)
    required = parse_eligibility_version(minimum)
    if current is None or required is None:
        return False
    return current >= required
