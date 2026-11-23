"""Validation for user-selected public program references saved to profiles."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

from program_candidate_publications import approved_policy_entry_for_url

_LEAD_ID = re.compile(r"[0-9a-f]{24}\Z")
_CANDIDATE_REF = re.compile(r"candidate-ref-[0-9a-f]{32}\Z")
_CANDIDATE_ID = re.compile(r"candidate-[0-9a-f]{32}\Z")
_IDENTIFIER = re.compile(r"[a-z][a-z0-9._-]{0,127}\Z")
_SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REDIRECT_KEYS = {
    "continue",
    "destination",
    "dest",
    "next",
    "redirect",
    "redirect_uri",
    "return",
    "returnto",
    "target",
    "url",
}


def validate_saved_interest(
    payload: object, active_policy_state: object
) -> tuple[dict[str, object] | None, str]:
    """Validate a selected lead against the active approved source policy."""
    required = {
        "lead_id",
        "candidate_ref",
        "candidate_id",
        "candidate_identity_basis",
        "source_id",
        "source_record_id",
        "source_version",
        "source_content_sha256",
        "source_entry_id",
        "canonical_source_url",
        "policy_id",
        "policy_version",
        "retrieved_at_utc",
        "selected",
        "expected_revision",
        "confirmed",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        return None, "invalid_interest"
    if (
        not isinstance(payload["lead_id"], str)
        or not _LEAD_ID.fullmatch(payload["lead_id"])
        or not isinstance(payload["candidate_ref"], str)
        or not _CANDIDATE_REF.fullmatch(payload["candidate_ref"])
        or not isinstance(payload["candidate_id"], str)
        or not _CANDIDATE_ID.fullmatch(payload["candidate_id"])
        or not isinstance(payload["candidate_identity_basis"], str)
        or payload["candidate_identity_basis"]
        not in {
            "publisher_program_number",
            "canonical_url",
            "source_record_id",
        }
        or not isinstance(payload["source_id"], str)
        or not _IDENTIFIER.fullmatch(payload["source_id"])
        or not isinstance(payload["source_record_id"], str)
        or not payload["source_record_id"].strip()
        or len(payload["source_record_id"]) > 200
        or not isinstance(payload["source_version"], str)
        or not payload["source_version"].strip()
        or len(payload["source_version"]) > 120
        or (
            payload["source_content_sha256"] is not None
            and (
                not isinstance(payload["source_content_sha256"], str)
                or not re.fullmatch(
                    r"[a-fA-F0-9]{64}", payload["source_content_sha256"]
                )
            )
        )
        or not isinstance(payload["source_entry_id"], str)
        or not _IDENTIFIER.fullmatch(payload["source_entry_id"])
        or not isinstance(payload["canonical_source_url"], str)
        or len(payload["canonical_source_url"]) > 2048
        or not isinstance(payload["policy_id"], str)
        or not _IDENTIFIER.fullmatch(payload["policy_id"])
        or not isinstance(payload["policy_version"], str)
        or not _SEMVER.fullmatch(payload["policy_version"])
        or not isinstance(payload["retrieved_at_utc"], str)
        or not _utc_timestamp(payload["retrieved_at_utc"])
        or not isinstance(payload["selected"], bool)
        or isinstance(payload["expected_revision"], bool)
        or not isinstance(payload["expected_revision"], int)
        or payload["expected_revision"] < 1
        or payload["confirmed"] is not True
    ):
        return None, "invalid_interest"

    if not isinstance(active_policy_state, dict):
        return None, "source_policy_unavailable"
    if active_policy_state.get("status") != "active":
        return None, "source_policy_unavailable"
    active = active_policy_state.get("active_manifest")
    if not isinstance(active, dict):
        return None, "source_policy_unavailable"
    manifest = active.get("manifest")
    bundle = manifest.get("policy_bundle") if isinstance(manifest, dict) else None
    entries = bundle.get("entries") if isinstance(bundle, dict) else None
    digest = active.get("content_sha256")
    if (
        not isinstance(entries, list)
        or not isinstance(digest, str)
        or not _SHA256.fullmatch(digest)
    ):
        return None, "source_policy_unavailable"
    if payload["policy_id"] != active.get("manifest_id") or payload[
        "policy_version"
    ] != active.get("manifest_version"):
        return None, "source_policy_changed"

    canonical_url = payload["canonical_source_url"]
    if not _is_canonical_https_url(canonical_url):
        return None, "invalid_interest"
    entry = approved_policy_entry_for_url(canonical_url, entries, payload["source_id"])
    if (
        entry is None
        or entry.get("entry_id") != payload["source_entry_id"]
        or "discovery" not in entry.get("permitted_uses", [])
    ):
        return None, "source_not_approved"
    expected_lead_id = hashlib.sha256(
        f"{payload['source_entry_id']}\n{canonical_url}".encode("utf-8")
    ).hexdigest()[:24]
    if payload["lead_id"] != expected_lead_id:
        return None, "invalid_interest"

    return (
        {
            "lead_id": payload["lead_id"],
            "candidate_ref": payload["candidate_ref"],
            "candidate_id": payload["candidate_id"],
            "candidate_identity_basis": payload["candidate_identity_basis"],
            "source_id": payload["source_id"],
            "source_record_id": payload["source_record_id"],
            "source_version": payload["source_version"],
            "source_content_sha256": (
                payload["source_content_sha256"].lower()
                if isinstance(payload["source_content_sha256"], str)
                else None
            ),
            "source_entry_id": payload["source_entry_id"],
            "canonical_source_url": canonical_url,
            "policy_id": str(active["manifest_id"]),
            "policy_version": str(active["manifest_version"]),
            "policy_content_sha256": digest,
            "retrieved_at_utc": payload["retrieved_at_utc"],
            "selected": payload["selected"],
            "expected_revision": payload["expected_revision"],
        },
        "ok",
    )


def _is_canonical_https_url(value: str) -> bool:
    """Require the exact safe URL representation returned by bounded search."""
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        decoded_path = unquote(parsed.path or "/")
        if (
            parsed.scheme != "https"
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or parsed.fragment
            or "\\" in decoded_path
            or any(part in {".", ".."} for part in decoded_path.split("/"))
            or re.search(r"%(?:2f|5c)", parsed.path, re.IGNORECASE)
            or any(key.lower() in _REDIRECT_KEYS for key, _ in parse_qsl(parsed.query))
        ):
            return False
        return value == urlunsplit(
            ("https", host, parsed.path or "/", parsed.query, "")
        )
    except ValueError:
        return False


def _utc_timestamp(value: str) -> bool:
    """Accept a timezone-aware ISO timestamp normalized to UTC."""
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        timestamp = datetime.fromisoformat(normalized)
        offset = timestamp.utcoffset()
        return offset is not None and offset.total_seconds() == 0
    except (TypeError, ValueError, AttributeError):
        return False
