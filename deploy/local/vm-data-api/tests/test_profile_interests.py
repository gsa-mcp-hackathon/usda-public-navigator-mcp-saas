from __future__ import annotations

import hashlib
import sys
from pathlib import Path

API_DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIRECTORY))

from profile_interests import validate_saved_interest  # noqa: E402


def _active_policy() -> dict[str, object]:
    return {
        "status": "active",
        "active_manifest": {
            "manifest_id": "approved-search-policy",
            "manifest_version": "1.0.0",
            "content_sha256": "b" * 64,
            "manifest": {
                "policy_bundle": {
                    "entries": [
                        {
                            "entry_id": "federal-dol-training",
                            "source_id": "federal.dol",
                            "host": "dol.gov",
                            "path_prefixes": ["/career"],
                            "permitted_uses": ["discovery"],
                            "review_status": "approved_for_bounded_discovery",
                            "owner": "U.S. Department of Labor",
                        }
                    ]
                }
            },
        },
    }


def _payload(**overrides: object) -> dict[str, object]:
    source_entry_id = "federal-dol-training"
    canonical_url = "https://dol.gov/career/training"
    return {
        "lead_id": hashlib.sha256(
            f"{source_entry_id}\n{canonical_url}".encode("utf-8")
        ).hexdigest()[:24],
        "candidate_ref": "candidate-ref-" + "c" * 32,
        "candidate_id": "candidate-" + "d" * 32,
        "candidate_identity_basis": "publisher_program_number",
        "source_id": "federal.dol",
        "source_record_id": "program-record-2026",
        "source_version": "snapshot-2026-10",
        "source_content_sha256": "e" * 64,
        "source_entry_id": source_entry_id,
        "canonical_source_url": canonical_url,
        "policy_id": "approved-search-policy",
        "policy_version": "1.0.0",
        "retrieved_at_utc": "2026-10-25T12:00:00Z",
        "selected": True,
        "expected_revision": 1,
        "confirmed": True,
        **overrides,
    }


def test_saves_only_leads_matched_to_current_approved_policy() -> None:
    interest, status = validate_saved_interest(_payload(), _active_policy())

    assert status == "ok"
    assert interest is not None
    assert interest["lead_id"] == _payload()["lead_id"]
    assert interest["policy_content_sha256"] == "b" * 64
    assert interest["selected"] is True


def test_rejects_unapproved_url_forged_lead_and_policy_version() -> None:
    outside_url = "https://dol.gov/grants/other"
    outside_id = hashlib.sha256(
        f"federal-dol-training\n{outside_url}".encode("utf-8")
    ).hexdigest()[:24]
    _, status = validate_saved_interest(
        _payload(canonical_source_url=outside_url, lead_id=outside_id),
        _active_policy(),
    )
    assert status == "source_not_approved"

    _, status = validate_saved_interest(_payload(lead_id="a" * 24), _active_policy())
    assert status == "invalid_interest"

    _, status = validate_saved_interest(
        _payload(policy_version="0.9.0"), _active_policy()
    )
    assert status == "source_policy_changed"


def test_refuses_unconfirmed_or_unavailable_policy_requests() -> None:
    _, status = validate_saved_interest(_payload(confirmed=False), _active_policy())
    assert status == "invalid_interest"
    _, status = validate_saved_interest(_payload(), {"status": "inactive"})
    assert status == "source_policy_unavailable"


def test_rejects_noncanonical_redirect_urls() -> None:
    url = "https://dol.gov/career/training?next=https://attacker.invalid"
    lead_id = hashlib.sha256(
        f"federal-dol-training\n{url}".encode("utf-8")
    ).hexdigest()[:24]
    _, status = validate_saved_interest(
        _payload(canonical_source_url=url, lead_id=lead_id), _active_policy()
    )
    assert status == "invalid_interest"
