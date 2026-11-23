"""Contracts for explicitly named synthetic evaluation fixture sets."""

from __future__ import annotations

import json
import re
from pathlib import Path

FIXTURE_SET_ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
FIXTURE_ID = re.compile(r"fixture-[a-z0-9]{4,32}\Z")
USE_CASE_ID = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")
PROFILE_ID = re.compile(r"synthetic-eval-[a-z0-9][a-z0-9-]{0,47}\Z")
SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")


def load_fixture_sets(path: Path) -> dict[tuple[str, str], frozenset[str]]:
    """Load the reviewed set/use-case allowlist; it contains no profile values."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {
        "manifest_version",
        "fixture_sets",
    }:
        raise ValueError("Invalid fixture-set manifest envelope.")
    if payload["manifest_version"] != "1.0.0" or not isinstance(
        payload["fixture_sets"], list
    ):
        raise ValueError("Unsupported fixture-set manifest version.")
    configured: dict[tuple[str, str], frozenset[str]] = {}
    for item in payload["fixture_sets"]:
        if not isinstance(item, dict) or set(item) != {
            "fixture_set_id",
            "fixture_set_version",
            "description",
            "allowed_use_case_ids",
        }:
            raise ValueError("Invalid fixture-set definition.")
        identity = (item["fixture_set_id"], item["fixture_set_version"])
        use_cases = item["allowed_use_case_ids"]
        if (
            not isinstance(identity[0], str)
            or FIXTURE_SET_ID.fullmatch(identity[0]) is None
            or not isinstance(identity[1], str)
            or SEMVER.fullmatch(identity[1]) is None
            or not isinstance(item["description"], str)
            or not item["description"].strip()
            or not isinstance(use_cases, list)
            or not use_cases
            or any(
                not isinstance(use_case, str) or USE_CASE_ID.fullmatch(use_case) is None
                for use_case in use_cases
            )
            or len(set(use_cases)) != len(use_cases)
            or identity in configured
        ):
            raise ValueError("Invalid or duplicate fixture-set definition.")
        configured[identity] = frozenset(use_cases)
    return configured


def valid_registration_command(
    value: object,
    *,
    profile_id: object,
    updating: bool,
    fixture_sets: dict[tuple[str, str], frozenset[str]],
) -> dict[str, str] | None:
    """Validate the explicit create-only command against reviewed set policy."""
    if value is None:
        return None
    if (
        updating
        or not isinstance(profile_id, str)
        or PROFILE_ID.fullmatch(profile_id) is None
    ):
        return None
    if not isinstance(value, dict) or set(value) != {
        "operation",
        "fixture_set_id",
        "fixture_set_version",
        "fixture_id",
        "use_case_id",
    }:
        return None
    set_id = value["fixture_set_id"]
    set_version = value["fixture_set_version"]
    fixture_id = value["fixture_id"]
    use_case_id = value["use_case_id"]
    if (
        value["operation"] != "register_evaluation_fixture"
        or not isinstance(set_id, str)
        or FIXTURE_SET_ID.fullmatch(set_id) is None
        or not isinstance(set_version, str)
        or SEMVER.fullmatch(set_version) is None
        or not isinstance(fixture_id, str)
        or FIXTURE_ID.fullmatch(fixture_id) is None
    ):
        return None
    allowed_use_cases = fixture_sets.get((set_id, set_version))
    if (
        allowed_use_cases is None
        or not isinstance(use_case_id, str)
        or USE_CASE_ID.fullmatch(use_case_id) is None
        or use_case_id not in allowed_use_cases
    ):
        return None
    return {
        "operation": "register_evaluation_fixture",
        "fixture_set_id": set_id,
        "fixture_set_version": set_version,
        "fixture_id": fixture_id,
        "use_case_id": use_case_id,
    }
