from __future__ import annotations

import importlib.util
import json
import sys
import threading
import uuid
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

API_DIRECTORY = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
API_SPEC = importlib.util.spec_from_file_location(
    "program_candidate_test_api", API_DIRECTORY / "api.py"
)
assert API_SPEC is not None and API_SPEC.loader is not None
api = importlib.util.module_from_spec(API_SPEC)
sys.path.insert(0, str(API_DIRECTORY))
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
API_SPEC.loader.exec_module(api)

from clusters.eligibility_candidate import (  # noqa: E402
    ProgramCandidateNormalizer,
    ProgramCandidateRequest,
)


def pending_candidate() -> dict[str, object]:
    return {
        "contract_version": "program-candidate-v1",
        "status": "candidate_ready_for_review",
        "candidate_id": "candidate-" + "a" * 32,
        "source_lineage": {
            "source_id": "aitest.source",
            "source_record_id": "program-1",
            "source_version": "2026-10-25",
            "source_url": "https://example.gov/programs/1",
            "retrieved_at": "2026-10-25T12:00:00+00:00",
            "source_content_sha256": None,
        },
        "listing": {"title": "Synthetic test candidate"},
        "field_lineage": {},
        "omitted_fields": ["agency"],
        "criteria_mapping": {
            "status": "not_assessed",
            "review_status": "not_reviewed",
            "proposed_criteria": [],
            "unavailable_reason": None,
        },
        "validation_findings": [],
        "governance": {
            "source_verified": False,
            "review_status": "review_required",
            "catalog_status": "not_published",
            "approval_status": "not_reviewed",
            "persistence_performed": False,
            "tenant_semantics_changed": False,
            "eligibility_outcome": None,
        },
    }


def test_program_candidate_requires_pending_unpublished_governance():
    candidate = pending_candidate()
    candidate["governance"] = dict(candidate["governance"])
    candidate["governance"]["catalog_status"] = "published"

    with pytest.raises(api.InvalidProgramCandidate):
        api.validate_program_candidate(candidate)


def test_program_candidate_accepts_v2_identity_and_rejects_invalid_continuity_fields():
    candidate = pending_candidate()
    candidate.update(
        {
            "contract_version": "program-candidate-v2",
            "candidate_ref": "candidate-ref-" + "c" * 32,
            "candidate_identity_basis": "publisher_program_number",
        }
    )

    assert api.validate_program_candidate(candidate) == candidate

    for field, invalid in (
        ("candidate_ref", "candidate-ref-not-a-digest"),
        ("candidate_identity_basis", "title"),
        ("candidate_identity_basis", {"untyped": "value"}),
    ):
        invalid_candidate = {**candidate, field: invalid}
        with pytest.raises(api.InvalidProgramCandidate):
            api.validate_program_candidate(invalid_candidate)


def test_program_candidate_v3_requires_valid_reusable_program_identity():
    candidate = pending_candidate()
    candidate.update(
        {
            "contract_version": "program-candidate-v3",
            "candidate_ref": "candidate-ref-" + "c" * 32,
            "candidate_identity_basis": "canonical_url",
            "program_id": "program-ref-" + "d" * 32,
            "program_identity_basis": "canonical_url",
        }
    )

    assert api.validate_program_candidate(candidate) == candidate

    for field, invalid in (
        ("program_id", "bad id"),
        ("program_identity_basis", "title"),
        ("program_identity_basis", {"untyped": "value"}),
    ):
        invalid_candidate = {**candidate, field: invalid}
        with pytest.raises(api.InvalidProgramCandidate):
            api.validate_program_candidate(invalid_candidate)


def test_registration_binds_canonical_digest_to_exact_candidate():
    candidate = pending_candidate()
    digest = api.program_candidate_sha256(candidate)

    validated, actual_digest = api.validate_program_candidate_registration(
        {"candidate": candidate, "expected_content_sha256": digest}
    )

    assert validated == candidate
    assert actual_digest == digest
    with pytest.raises(api.InvalidProgramCandidate):
        api.validate_program_candidate_registration(
            {"candidate": candidate, "expected_content_sha256": "0" * 64}
        )


def test_registration_accepts_review_only_normalized_requirement_mappings():
    candidate = pending_candidate()
    requirement = {
        "requirement_id": "farm.years-in-operation",
        "statement": "Applicant must have operated a farm for at least three years.",
        "requirement_type": "eligibility",
        "mapping_status": "mapped",
        "concept_id": "farm.years_in_operation",
        "profile_attribute": "core.farm_business.years_farming",
        "operator": "gte",
        "value": 3,
        "unit": "years",
        "citation": {
            "source_url": "https://example.gov/programs/1",
            "source_record_id": "program-1",
            "retrieved_at": "2026-10-25T12:00:00+00:00",
            "source_id": "aitest.source",
            "locator": "Eligibility, paragraph 2",
        },
    }
    candidate["criteria_mapping"] = {
        "status": "candidate_requires_review",
        "review_status": "not_reviewed",
        "proposed_criteria": [],
        "requirement_candidates": [requirement],
        "coverage": {
            "source_completeness": "not_proven",
            "requirement_count": 1,
            "mapped_count": 1,
            "partial_count": 0,
            "unmapped_or_context_only_count": 0,
            "active_rule_count": 0,
        },
        "unavailable_reason": None,
    }
    digest = api.program_candidate_sha256(candidate)

    stored_candidate, stored_digest = api.validate_program_candidate_registration(
        {"candidate": candidate, "expected_content_sha256": digest}
    )

    assert stored_candidate == candidate
    assert stored_digest == digest
    assert stored_candidate["criteria_mapping"]["coverage"]["active_rule_count"] == 0


def test_registration_rejects_candidate_coverage_that_claims_active_rules():
    candidate = pending_candidate()
    candidate["criteria_mapping"] = {
        "status": "candidate_requires_review",
        "review_status": "not_reviewed",
        "proposed_criteria": [],
        "requirement_candidates": [
            {
                "requirement_id": "farm.years-in-operation",
                "statement": (
                    "Applicant must have operated a farm for at least three years."
                ),
                "requirement_type": "eligibility",
                "mapping_status": "mapped",
                "concept_id": "farm.years_in_operation",
                "profile_attribute": "core.farm_business.years_farming",
                "operator": "gte",
                "value": 3,
                "citation": {
                    "source_url": "https://example.gov/programs/1",
                    "source_record_id": "program-1",
                    "retrieved_at": "2026-10-25T12:00:00+00:00",
                },
            }
        ],
        "coverage": {
            "source_completeness": "not_proven",
            "requirement_count": 1,
            "mapped_count": 1,
            "partial_count": 0,
            "unmapped_or_context_only_count": 0,
            "active_rule_count": 1,
        },
        "unavailable_reason": None,
    }

    with pytest.raises(api.InvalidProgramCandidate):
        api.validate_program_candidate(candidate)


def test_cluster_normalizer_output_registers_without_projection_workarounds():
    normalized = ProgramCandidateNormalizer().normalize(
        ProgramCandidateRequest.model_validate(
            {
                "source_id": "aitest.source",
                "source_record_id": "program-1",
                "source_version": "2026-10-25",
                "source_url": "https://example.gov/programs/1",
                "retrieved_at": "2026-10-25T12:00:00+00:00",
                "listing": {"title": "Synthetic test candidate"},
                "requirement_candidates": [
                    {
                        "requirement_id": "farm.years-in-operation",
                        "statement": (
                            "Applicant must have operated a farm for three years."
                        ),
                        "requirement_type": "eligibility",
                        "mapping_status": "mapped",
                        "concept_id": "farm.years_in_operation",
                        "profile_attribute": "core.farm_business.years_farming",
                        "operator": "gte",
                        "value": 3,
                        "unit": "years",
                        "citation": {
                            "source_url": "https://example.gov/programs/1",
                            "source_record_id": "program-1",
                            "retrieved_at": "2026-10-25T12:00:00+00:00",
                            "source_id": "aitest.source",
                            "locator": "Eligibility, paragraph 2",
                        },
                    }
                ],
            }
        )
    )
    digest = api.program_candidate_sha256(normalized)

    stored_candidate, stored_digest = api.validate_program_candidate_registration(
        {"candidate": normalized, "expected_content_sha256": digest}
    )

    assert stored_candidate == normalized
    assert stored_digest == digest
    assert stored_candidate["criteria_mapping"]["status"] == "candidate_requires_review"
    assert stored_candidate["criteria_mapping"]["coverage"]["active_rule_count"] == 0


def test_candidate_decision_requires_exact_digest_choice_and_rationale():
    candidate_id = "candidate-" + "a" * 32
    valid = {
        "expected_content_sha256": "b" * 64,
        "decision": "approved",
        "rationale": "Reviewed official source evidence.",
    }
    assert api.validate_program_candidate_decision(valid, candidate_id) == (
        "b" * 64,
        "approved",
        "Reviewed official source evidence.",
        None,
        None,
    )
    audit = {
        "mcp_session_sha256": "3" * 64,
        "mcp_request_sha256": "4" * 64,
        "mcp_client_identity_status": "unavailable",
        "operator_credential_label": "core_operator_token",
    }
    assert api.validate_program_candidate_decision(
        {**valid, "operator_audit_context": audit}, candidate_id
    ) == ("b" * 64, "approved", "Reviewed official source evidence.", None, None)
    governed = {
        **valid,
        "transaction_id": "3de3e57d-53c9-455e-b901-9aecaf829cee",
        "approval_reference": "approved test scope",
    }
    assert api.validate_program_candidate_decision(governed, candidate_id) == (
        "b" * 64,
        "approved",
        "Reviewed official source evidence.",
        "3de3e57d-53c9-455e-b901-9aecaf829cee",
        "approved test scope",
    )
    for invalid in (
        {**valid, "expected_content_sha256": "not-a-digest"},
        {**valid, "decision": "published"},
        {**valid, "rationale": "  "},
        {**governed, "transaction_id": "not-a-uuid"},
        {**governed, "approval_reference": ""},
        {**governed, "approval_reference": "bad\nreference"},
        {**valid, "approver_principal": "caller-controlled"},
        {
            **valid,
            "operator_audit_context": {**audit, "client_id": "caller-claimed"},
        },
    ):
        with pytest.raises(api.InvalidProgramCandidate):
            api.validate_program_candidate_decision(invalid, candidate_id)


class DecisionConnection:
    def __init__(self, candidate: dict[str, object], digest: str, existing=None):
        self.candidate = candidate
        self.digest = digest
        self.existing = existing
        self.inserted = None
        self.transaction_conflict = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql: str, params: tuple[object, ...]):
        if "FROM mcp_saas.program_candidate_records" in sql:
            return type(
                "Result",
                (),
                {"fetchone": lambda _self: (self.digest, self.candidate)},
            )()
        if (
            "FROM mcp_saas.program_candidate_review_decisions" in sql
            and self.transaction_conflict
        ):
            return type("Result", (), {"fetchone": lambda _self: None})()
        if sql.lstrip().startswith("INSERT"):
            if (
                self.transaction_conflict
                or self.existing is not None
                or self.inserted is not None
            ):
                inserted = None
            else:
                self.inserted = (
                    params[0],
                    self.digest,
                    params[3],
                    params[4],
                    params[5],
                    datetime(2026, 10, 25, tzinfo=timezone.utc),
                    *params[6:10],
                    params[10],
                    params[11],
                )
                inserted = self.inserted
            return type("Result", (), {"fetchone": lambda _self: inserted})()
        return type(
            "Result",
            (),
            {"fetchone": lambda _self: self.existing or self.inserted},
        )()


def test_candidate_decision_is_append_only_idempotent_and_digest_bound():
    candidate = pending_candidate()
    digest = api.program_candidate_sha256(candidate)
    connection = DecisionConnection(candidate, digest)
    payload = {
        "expected_content_sha256": digest,
        "decision": "rejected",
        "rationale": "Source record is not authoritative for this use.",
        "transaction_id": "3de3e57d-53c9-455e-b901-9aecaf829cee",
        "approval_reference": "bounded operator test",
        "operator_audit_context": {
            "mcp_session_sha256": "3" * 64,
            "mcp_request_sha256": "4" * 64,
            "mcp_client_identity_status": "unavailable",
            "operator_credential_label": "core_operator_token",
        },
    }
    with patch.object(api, "connect_db", return_value=connection):
        decision, created = api.record_program_candidate_decision(
            candidate["candidate_id"], payload
        )
        replay_payload = {
            **payload,
            "operator_audit_context": {
                **payload["operator_audit_context"],
                "mcp_session_sha256": "5" * 64,
            },
        }
        replay, replay_created = api.record_program_candidate_decision(
            candidate["candidate_id"], replay_payload
        )
    assert created is True
    assert replay_created is False
    assert replay == decision
    assert decision["decision"] == "rejected"
    assert decision["authorization_capability"] == "registry_writer"
    assert decision["transaction_id"] == payload["transaction_id"]
    assert decision["approval_reference"] == payload["approval_reference"]
    assert decision["operator_audit_context"] == payload["operator_audit_context"]
    assert decision["catalog_status"] == "not_published"

    connection.existing = (
        decision["decision_id"],
        digest,
        "rejected",
        payload["rationale"],
        "registry_writer",
        datetime.fromisoformat(decision["decided_at"]),
        *tuple(payload["operator_audit_context"].values()),
        uuid.UUID(payload["transaction_id"]),
        payload["approval_reference"],
    )
    connection.inserted = None
    conflicting = {**payload, "decision": "approved"}
    with patch.object(api, "connect_db", return_value=connection):
        with pytest.raises(api.ProgramCandidateDecisionConflict):
            api.record_program_candidate_decision(
                candidate["candidate_id"], conflicting
            )

    connection.existing = None
    connection.inserted = None
    connection.transaction_conflict = True
    with patch.object(api, "connect_db", return_value=connection):
        with pytest.raises(
            api.ProgramCandidateDecisionConflict, match="another candidate"
        ):
            api.record_program_candidate_decision(candidate["candidate_id"], payload)


def test_candidate_readback_surfaces_decision_without_changing_candidate():
    candidate = pending_candidate()
    digest = api.program_candidate_sha256(candidate)
    created = datetime(2026, 10, 25, tzinfo=timezone.utc)
    decision = (
        "decision-1",
        digest,
        "approved",
        "Evidence checked.",
        "registry_writer",
        created,
        "3" * 64,
        "4" * 64,
        "unavailable",
        "core_operator_token",
        uuid.UUID("3de3e57d-53c9-455e-b901-9aecaf829cee"),
        "operator decision",
    )

    class ReadConnection(DecisionConnection):
        def execute(self, sql: str, params: tuple[object, ...]):
            if "program_candidate_registration_events" in sql:
                return type("Result", (), {"fetchone": lambda _self: None})()
            if "FROM mcp_saas.program_candidate_records" in sql:
                return type(
                    "Result",
                    (),
                    {"fetchone": lambda _self: (digest, candidate, created)},
                )()
            if "program_candidate_publications" in sql:
                return type("Result", (), {"fetchone": lambda _self: None})()
            return type("Result", (), {"fetchone": lambda _self: decision})()

    with patch.object(
        api, "connect_db", return_value=ReadConnection(candidate, digest)
    ):
        readback = api.read_program_candidate_record(candidate["candidate_id"])
    assert readback is not None
    assert readback["review_status"] == "approved"
    assert readback["review_decision"]["decision"] == "approved"
    assert readback["candidate"] == candidate
    assert readback["catalog_status"] == "not_published"
    assert readback["publication_keys"] == []


def test_candidate_decision_postgres_roundtrip_is_immutable(database_url: str):
    import psycopg

    candidate = pending_candidate()
    candidate["candidate_id"] = "candidate-" + uuid.uuid4().hex
    digest = api.program_candidate_sha256(candidate)
    api.connect_db = lambda: psycopg.connect(database_url)
    with psycopg.connect(database_url) as connection:
        database_name = connection.execute("SELECT current_database()").fetchone()[0]
        assert "test" in database_name.casefold(), (
            "Candidate lifecycle integration checks require an explicitly named "
            "disposable test database."
        )
    api.register_program_candidate_record(
        {
            "candidate": candidate,
            "expected_content_sha256": digest,
            "operator_audit_context": {
                "mcp_session_sha256": "1" * 64,
                "mcp_request_sha256": "2" * 64,
                "mcp_client_identity_status": "unavailable",
                "operator_credential_label": "core_operator_token",
            },
        }
    )
    decision_payload = {
        "expected_content_sha256": digest,
        "decision": "approved",
        "rationale": "AITest exact-digest review roundtrip.",
        "transaction_id": str(uuid.uuid4()),
        "approval_reference": "bounded operator test",
        "operator_audit_context": {
            "mcp_session_sha256": "3" * 64,
            "mcp_request_sha256": "4" * 64,
            "mcp_client_identity_status": "unavailable",
            "operator_credential_label": "core_operator_token",
        },
    }
    first, created = api.record_program_candidate_decision(
        candidate["candidate_id"], decision_payload
    )
    replay_payload = {
        **decision_payload,
        "operator_audit_context": {
            **decision_payload["operator_audit_context"],
            "mcp_session_sha256": "5" * 64,
        },
    }
    replay, replay_created = api.record_program_candidate_decision(
        candidate["candidate_id"], replay_payload
    )
    assert created is True
    assert replay_created is False
    assert replay == first
    readback = api.read_program_candidate_record(candidate["candidate_id"])
    assert readback is not None
    assert readback["review_decision"]["decision"] == "approved"
    assert readback["registration_audit"]["after_state_sha256"] == digest
    assert readback["registration_audit"]["before_state_sha256"] is None
    assert (
        readback["review_decision"]["transaction_id"]
        == decision_payload["transaction_id"]
    )
    assert (
        readback["review_decision"]["approval_reference"]
        == (decision_payload["approval_reference"])
    )
    assert (
        readback["review_decision"]["operator_audit_context"]
        == decision_payload["operator_audit_context"]
    )
    assert readback["candidate"] == candidate
    assert readback["catalog_status"] == "not_published"
    with pytest.raises(api.ProgramCandidateDecisionConflict):
        api.record_program_candidate_decision(
            candidate["candidate_id"],
            {**decision_payload, "decision": "rejected"},
        )


def test_candidate_decision_endpoint_requires_registry_writer_capability():
    candidate_id = "candidate-" + "d" * 32
    response_body = {
        "status": "recorded",
        "decision_id": "decision-id",
        "candidate_id": candidate_id,
        "content_sha256": "a" * 64,
        "decision": "rejected",
        "rationale": "AITest authorization check.",
        "authorization_capability": "registry_writer",
        "decided_at": "2026-10-25T12:00:00+00:00",
        "catalog_status": "not_published",
    }
    payload = json.dumps(
        {
            "expected_content_sha256": "a" * 64,
            "decision": "rejected",
            "rationale": "AITest authorization check.",
        }
    ).encode()
    server = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    with (
        patch.object(
            api,
            "read_secret",
            side_effect=lambda path: (
                "writer-token" if path == api.REGISTRY_WRITE_TOKEN_FILE else ""
            ),
        ),
        patch.object(
            api,
            "record_program_candidate_decision",
            return_value=(response_body, True),
        ) as record,
    ):
        thread.start()
        try:
            endpoint = (
                f"http://127.0.0.1:{server.server_port}/v1/registry/"
                f"program-candidates/{candidate_id}/decision"
            )
            request = Request(endpoint, data=payload, method="POST")
            with pytest.raises(HTTPError) as error:
                urlopen(request, timeout=2)
            assert error.value.code == 401
            record.assert_not_called()

            authorized = Request(
                endpoint,
                data=payload,
                method="POST",
                headers={"Authorization": "Bearer writer-token"},
            )
            with urlopen(authorized, timeout=2) as result:
                assert result.status == 201
                assert json.loads(result.read()) == response_body
            record.assert_called_once()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def test_candidate_publication_endpoint_requires_registry_writer_capability():
    candidate_id = "candidate-" + "e" * 32
    publication = {
        "status": "published_inactive",
        "publication_key": "f" * 64,
        "candidate_id": candidate_id,
        "content_sha256": "a" * 64,
        "dataset_id": "shared-programs",
    }
    payload = json.dumps(
        {"expected_content_sha256": "a" * 64, "dataset_id": "shared-programs"}
    ).encode()
    server = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    with (
        patch.object(
            api,
            "read_secret",
            side_effect=lambda path: (
                "writer-token" if path == api.REGISTRY_WRITE_TOKEN_FILE else ""
            ),
        ),
        patch.object(
            api,
            "publish_program_candidate",
            return_value=(publication, True),
        ) as publish,
    ):
        thread.start()
        try:
            endpoint = (
                f"http://127.0.0.1:{server.server_port}/v1/registry/"
                f"program-candidates/{candidate_id}/publish"
            )
            unauthorized = Request(endpoint, data=payload, method="POST")
            with pytest.raises(HTTPError) as error:
                urlopen(unauthorized, timeout=2)
            assert error.value.code == 401
            publish.assert_not_called()

            authorized = Request(
                endpoint,
                data=payload,
                method="POST",
                headers={"Authorization": "Bearer writer-token"},
            )
            with urlopen(authorized, timeout=2) as response:
                assert response.status == 201
                assert json.loads(response.read()) == publication
            publish.assert_called_once()

            publish.side_effect = api.ProgramCandidatePublicationConflict(
                "internal detail must not be returned",
                reason_code="source_policy_not_active_or_approved",
            )
            conflict_request = Request(
                endpoint,
                data=payload,
                method="POST",
                headers={"Authorization": "Bearer writer-token"},
            )
            with pytest.raises(HTTPError) as conflict:
                urlopen(conflict_request, timeout=2)
            assert conflict.value.code == 409
            assert json.loads(conflict.value.read()) == {
                "error": "program_candidate_publication_conflict",
                "reason_code": "source_policy_not_active_or_approved",
            }
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def test_candidate_publication_database_lifecycle_and_record_immutability(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    import psycopg

    suffix = uuid.uuid4().hex
    candidate = pending_candidate()
    candidate_id = "candidate-" + suffix
    source_id = "aitest.source." + suffix
    dataset_id = "aitest-programs-" + suffix
    upstream_id = "program-" + suffix
    record_id = "candidate." + suffix
    candidate["candidate_id"] = candidate_id
    candidate["source_lineage"] = {
        **candidate["source_lineage"],
        "source_id": source_id,
        "source_record_id": upstream_id,
    }
    digest = api.program_candidate_sha256(candidate)
    publication_key = hashlib.sha256(
        f"{source_id}\n{upstream_id}".encode("utf-8")
    ).hexdigest()
    record_digest = "f" * 64
    target = {"candidate_id": candidate_id, "content_sha256": digest}
    monkeypatch.setattr(api, "connect_db", lambda: psycopg.connect(database_url))

    try:
        with psycopg.connect(database_url) as connection:
            database_name = connection.execute("SELECT current_database()").fetchone()[
                0
            ]
            assert "test" in database_name.casefold(), (
                "Publication lifecycle checks require an explicitly named "
                "disposable test database."
            )
            connection.execute(
                """INSERT INTO mcp_saas.public_data_sources
                   (source_id, source_name, source_url, access_status)
                   VALUES (%s, %s, %s, 'available')""",
                (
                    source_id,
                    "AITest source",
                    "https://example.gov",
                ),
            )
            connection.execute(
                """INSERT INTO mcp_saas.public_datasets
                   (dataset_id, source_id, source_dataset_id, title, schema_version,
                    namespace_ids, metadata)
                   VALUES (%s, %s, %s, %s, 'public-listing-v3', %s, %s::jsonb)""",
                (
                    dataset_id,
                    source_id,
                    dataset_id,
                    "AITest shared program inventory",
                    ["core", "listing"],
                    json.dumps({"catalog_namespace": "programs"}),
                ),
            )
            connection.execute(
                """INSERT INTO mcp_saas.program_candidate_records
                   (candidate_id, content_sha256, candidate)
                   VALUES (%s, %s, %s::jsonb)""",
                (candidate_id, digest, json.dumps(candidate)),
            )
            connection.execute(
                """INSERT INTO mcp_saas.program_candidate_review_decisions
                   (decision_id, candidate_id, content_sha256, decision, rationale,
                    authorization_capability, transaction_id, approval_reference)
                   VALUES (%s, %s, %s, 'approved', %s, 'registry_writer', %s, %s)""",
                (
                    uuid.uuid4(),
                    candidate_id,
                    digest,
                    "AITest exact digest approval.",
                    uuid.uuid4(),
                    "#test operator fixture",
                ),
            )
            record = {
                "source_record_id": record_id,
                "record_type": "public_listing",
                "canonical_attributes": {"listing.title": "AITest listing"},
                "semantic_context": {"catalog_namespace": "programs"},
                "source_metadata": {
                    "program_candidate_id": candidate_id,
                    "program_candidate_sha256": digest,
                    "program_candidate_publication_key": publication_key,
                },
                "source_url": "https://example.gov/programs/1",
                "content_hash": record_digest,
                "schema_version": "public-listing-v3",
                "observed_at": "2026-10-25T12:00:00+00:00",
            }
            connection.execute(
                """INSERT INTO mcp_saas.public_dataset_records
                   (dataset_id, source_record_id, record_type, canonical_attributes,
                    semantic_context, source_metadata, source_url, content_hash,
                    schema_version, observed_at)
                   VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s,
                           %s, %s)""",
                (
                    dataset_id,
                    record_id,
                    record["record_type"],
                    json.dumps(record["canonical_attributes"]),
                    json.dumps(record["semantic_context"]),
                    json.dumps(record["source_metadata"]),
                    record["source_url"],
                    record_digest,
                    record["schema_version"],
                    record["observed_at"],
                ),
            )
            connection.execute(
                """INSERT INTO mcp_saas.program_candidate_publications
                   (publication_key, candidate_id, candidate_content_sha256,
                    dataset_id, source_record_id, record_content_sha256)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    publication_key,
                    candidate_id,
                    digest,
                    dataset_id,
                    record_id,
                    record_digest,
                ),
            )

        assert api.read_program_candidate_publication_state(publication_key)[
            "status"
        ] == ("inactive")
        activate = {
            "request_id": str(uuid.uuid4()),
            "action": "activate",
            "expected_current": None,
            "target": target,
            "rationale": "AITest activate the exact approved listing.",
            "operator_audit_context": {
                "mcp_session_sha256": "3" * 64,
                "mcp_request_sha256": "4" * 64,
                "mcp_client_identity_status": "unavailable",
                "operator_credential_label": "core_operator_token",
            },
        }
        activation, created = api.record_program_candidate_publication_lifecycle(
            publication_key, activate
        )
        assert created is True
        assert (
            activation["event"]["operator_audit_context"]
            == activate["operator_audit_context"]
        )
        replay, replay_created = api.record_program_candidate_publication_lifecycle(
            publication_key,
            {
                **activate,
                "operator_audit_context": {
                    **activate["operator_audit_context"],
                    "mcp_session_sha256": "5" * 64,
                },
            },
        )
        assert replay_created is False
        assert replay["event"] == activation["event"]
        assert api.read_program_candidate_publication_state(publication_key)[
            "status"
        ] == ("active")
        with psycopg.connect(database_url) as connection:
            assert (
                connection.execute(
                    """SELECT count(*) FROM mcp_saas.active_public_dataset_records
                   WHERE dataset_id = %s AND source_record_id = %s""",
                    (dataset_id, record_id),
                ).fetchone()[0]
                == 1
            )
            with pytest.raises(psycopg.Error) as mutation_error:
                connection.execute(
                    """UPDATE mcp_saas.public_dataset_records
                       SET content_hash = %s
                       WHERE dataset_id = %s AND source_record_id = %s""",
                    ("e" * 64, dataset_id, record_id),
                )
            assert mutation_error.value.sqlstate == "55000"
            connection.rollback()

        deactivate = {
            "request_id": str(uuid.uuid4()),
            "action": "deactivate",
            "expected_current": target,
            "target": None,
            "rationale": "AITest hide the listing without deleting its version.",
        }
        api.record_program_candidate_publication_lifecycle(publication_key, deactivate)
        assert api.read_program_candidate_publication_state(publication_key)[
            "status"
        ] == ("inactive")
        with psycopg.connect(database_url) as connection:
            assert (
                connection.execute(
                    """SELECT count(*) FROM mcp_saas.active_public_dataset_records
                   WHERE dataset_id = %s AND source_record_id = %s""",
                    (dataset_id, record_id),
                ).fetchone()[0]
                == 0
            )
    finally:
        with psycopg.connect(database_url) as connection:
            connection.execute(
                "DELETE FROM mcp_saas.program_candidate_publication_events "
                "WHERE publication_key = %s",
                (publication_key,),
            )
            connection.execute(
                "DELETE FROM mcp_saas.program_candidate_publications "
                "WHERE publication_key = %s",
                (publication_key,),
            )
            connection.execute(
                "DELETE FROM mcp_saas.public_dataset_records "
                "WHERE dataset_id = %s AND source_record_id = %s",
                (dataset_id, record_id),
            )
            # Retain the candidate, decision, and registration receipt: these
            # are immutable audit records and candidate_id is unique per run.
            connection.execute(
                "DELETE FROM mcp_saas.public_datasets WHERE dataset_id = %s",
                (dataset_id,),
            )
            connection.execute(
                "DELETE FROM mcp_saas.public_data_sources WHERE source_id = %s",
                (source_id,),
            )


class FakeConnection:
    def __init__(self, candidate: dict[str, object], digest: str):
        self.candidate = candidate
        self.digest = digest
        self.created_at = datetime(2026, 10, 25, tzinfo=timezone.utc)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql: str, _params: tuple[object, ...]):
        if "program_candidate_registration_events" in sql:
            return type("Result", (), {"fetchone": lambda _self: None})()
        if "program_candidate_publications" in sql:
            return type("Result", (), {"fetchone": lambda _self: None})()
        if "program_candidate_review_decisions" in sql:
            return type("Result", (), {"fetchone": lambda _self: None})()
        if sql.lstrip().startswith("INSERT"):
            return type("Result", (), {"fetchone": lambda _self: None})()
        return type(
            "Result",
            (),
            {
                "fetchone": lambda _self: (
                    self.digest,
                    self.candidate,
                    self.created_at,
                )
            },
        )()


def test_register_is_idempotent_and_readback_digest_checked():
    candidate = pending_candidate()
    digest = api.program_candidate_sha256(candidate)
    audit_context = {
        "mcp_session_sha256": "1" * 64,
        "mcp_request_sha256": "2" * 64,
        "mcp_client_identity_status": "unavailable",
        "operator_credential_label": "core_operator_token",
    }
    connection = FakeConnection(candidate, digest)
    with patch.object(api, "connect_db", return_value=connection):
        result, created = api.register_program_candidate_record(
            {
                "candidate": candidate,
                "expected_content_sha256": digest,
                "operator_audit_context": audit_context,
            }
        )
        readback = api.read_program_candidate_record(candidate["candidate_id"])

    assert created is False
    assert result["status"] == "registered"
    assert result["content_sha256"] == digest
    assert result["catalog_status"] == "not_published"
    assert readback is not None
    assert readback["candidate"] == candidate
    assert readback["content_sha256"] == digest
    assert readback["registration_audit"] is None


def test_new_registration_persists_and_reads_immutable_audit_receipt():
    candidate = pending_candidate()
    candidate["candidate_id"] = "candidate-" + "b" * 32
    digest = api.program_candidate_sha256(candidate)
    audit_context = {
        "mcp_session_sha256": "1" * 64,
        "mcp_request_sha256": "2" * 64,
        "mcp_client_identity_status": "unavailable",
        "operator_credential_label": "core_operator_token",
    }
    created_at = datetime(2026, 10, 31, tzinfo=timezone.utc)

    class NewCandidateConnection:
        def __init__(self):
            self.event = None
            self.inserted = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql: str, params: tuple[object, ...]):
            if sql.lstrip().startswith("INSERT") and "program_candidate_records" in sql:
                row = None
                if not self.inserted:
                    self.inserted = True
                    row = (digest, candidate, created_at)
                return type("Result", (), {"fetchone": lambda _self: row})()
            if (
                sql.lstrip().startswith("INSERT")
                and "program_candidate_registration_events" in sql
            ):
                self.event = (
                    params[0],
                    "register",
                    None,
                    params[3],
                    json.loads(params[4]),
                    "registry_writer",
                    created_at,
                    *params[5:9],
                )
                return type("Result", (), {"fetchone": lambda _self: None})()
            if "program_candidate_registration_events" in sql:
                return type("Result", (), {"fetchone": lambda _self: self.event})()
            if "program_candidate_records" in sql:
                row = (digest, candidate, created_at)
                return type("Result", (), {"fetchone": lambda _self: row})()
            if "program_candidate_publications" in sql:
                return type("Result", (), {"fetchone": lambda _self: None})()
            if "program_candidate_review_decisions" in sql:
                return type("Result", (), {"fetchone": lambda _self: None})()
            raise AssertionError(f"Unexpected registration query: {sql}")

    connection = NewCandidateConnection()
    with patch.object(api, "connect_db", return_value=connection):
        result, created = api.register_program_candidate_record(
            {
                "candidate": candidate,
                "expected_content_sha256": digest,
                "operator_audit_context": audit_context,
            }
        )
        replay_context = {**audit_context, "mcp_session_sha256": "3" * 64}
        replay, replay_created = api.register_program_candidate_record(
            {
                "candidate": candidate,
                "expected_content_sha256": digest,
                "operator_audit_context": replay_context,
            }
        )
        readback = api.read_program_candidate_record(candidate["candidate_id"])

    assert created is True
    assert replay_created is False
    assert result["registration_audit"]["before_state_sha256"] is None
    assert result["registration_audit"]["after_state_sha256"] == digest
    assert result["registration_audit"]["operator_audit_context"] == audit_context
    assert (
        "/candidate/criteria_mapping" in result["registration_audit"]["changed_fields"]
    )
    assert readback is not None
    assert readback["registration_audit"] == result["registration_audit"]
    assert replay["registration_audit"] == result["registration_audit"]


def test_candidate_registration_requires_server_derived_audit_context():
    candidate = pending_candidate()
    digest = api.program_candidate_sha256(candidate)
    with pytest.raises(api.InvalidProgramCandidate):
        api.register_program_candidate_record(
            {"candidate": candidate, "expected_content_sha256": digest}
        )
    with pytest.raises(api.InvalidProgramCandidate):
        api.register_program_candidate_record(
            {
                "candidate": candidate,
                "expected_content_sha256": digest,
                "operator_audit_context": {
                    "mcp_session_sha256": "not-a-digest",
                    "mcp_request_sha256": "2" * 64,
                    "mcp_client_identity_status": "unavailable",
                    "operator_credential_label": "core_operator_token",
                },
            }
        )


def test_schema_and_container_package_include_candidate_storage():
    migration = (
        API_DIRECTORY.parents[2] / "db/migrations/025_program_candidate_records.sql"
    ).read_text()
    dockerfile = (API_DIRECTORY / "Dockerfile").read_text()
    dockerignore = (API_DIRECTORY.parents[2] / ".dockerignore").read_text()

    assert "program_candidate_records" in migration
    schema = (API_DIRECTORY / "schema.sql").read_text()
    assert "program-candidate-v3" in schema
    identity_migration = (
        API_DIRECTORY.parents[2] / "db/migrations/044_program_candidate_v3_identity.sql"
    ).read_text()
    assert "program-candidate-v3" in identity_migration
    assert (
        "DROP CONSTRAINT IF EXISTS program_candidate_records_check"
        in identity_migration
    )
    assert (
        "DROP CONSTRAINT IF EXISTS program_candidate_records_candidate_check"
        in identity_migration
    )
    assert "program_id" in identity_migration
    registration_audit_migration = (
        API_DIRECTORY.parents[2]
        / "db/migrations/039_program_candidate_registration_audit.sql"
    ).read_text()
    assert "program_candidate_registration_events" in registration_audit_migration
    assert "mcp_saas_core_api" in registration_audit_migration
    assert "mcp_saas_data_api" in registration_audit_migration
    assert (
        "trg_immutable_program_candidate_registration_event"
        in registration_audit_migration
    )
    assert (
        "program_candidate_registration_events TO {ROLE}"
        in (API_DIRECTORY / "provision_secrets.py").read_text()
    )
    decision_migration = (
        API_DIRECTORY.parents[2]
        / "db/migrations/026_program_candidate_review_decisions.sql"
    ).read_text()
    assert "program_candidate_review_decisions" in decision_migration
    decision_idempotency_migration = (
        API_DIRECTORY.parents[2] / "db/migrations/033_operator_decision_idempotency.sql"
    ).read_text()
    assert "transaction_id UUID" in decision_idempotency_migration
    assert "approval_reference TEXT" in decision_idempotency_migration
    assert "uq_program_candidate_decision_transaction" in decision_idempotency_migration
    publication_migration = (
        API_DIRECTORY.parents[2]
        / "db/migrations/027_program_candidate_publications.sql"
    ).read_text()
    assert "program_candidate_publications" in publication_migration
    assert "program_candidate_publication_events" in publication_migration
    assert "active_public_dataset_records" in publication_migration
    assert "uq_public_dataset_record_exact_content" in publication_migration
    assert "trg_immutable_published_candidate_record" in publication_migration
    initial_schema = (API_DIRECTORY / "schema.sql").read_text()
    assert "program_candidate_publications" in initial_schema
    assert "program_candidate_registration_events" in initial_schema
    assert "mcp_saas_core_api" in initial_schema
    assert "program_candidate_publication_events" in initial_schema
    assert "active_public_dataset_records" in initial_schema
    assert "program_candidate_records.py" in dockerfile
    assert "program_candidate_publications.py" in dockerfile
    assert "operator_audit.py" in dockerfile
    assert "!deploy/local/vm-data-api/program_candidate_records.py" in dockerignore
    assert "!deploy/local/vm-data-api/program_candidate_publications.py" in dockerignore
    assert "!deploy/local/vm-data-api/operator_audit.py" in dockerignore


def test_requirements_package_revisions_migration_matches_schema_and_is_scoped():
    repository = API_DIRECTORY.parents[2]
    migration = (
        repository / "db/migrations/046_eligibility_requirements_package_revisions.sql"
    ).read_text()
    schema = (API_DIRECTORY / "schema.sql").read_text()
    provisioner = (API_DIRECTORY / "provision_secrets.py").read_text()
    readme = (API_DIRECTORY / "PERSISTENCE-ACCESS-API.md").read_text(encoding="utf-8")

    assert (
        "CREATE TABLE IF NOT EXISTS "
        "mcp_saas.eligibility_requirements_package_revisions" in migration
    )
    assert "revision INTEGER NOT NULL CHECK (revision >= 2)" in migration
    assert "PRIMARY KEY (package_id, revision)" in migration
    assert "eligibility_requirements_package_revisions" in schema
    assert "eligibility_requirements_package_revisions TO {ROLE}" in provisioner
    assert "GRANT SELECT, INSERT" in migration
    assert "GRANT UPDATE" not in migration
    assert "GRANT DELETE" not in migration
    assert "046_eligibility_requirements_package_revisions.sql" in readme


def test_program_candidate_projection_is_shared_but_does_not_promote_criteria():
    candidate = pending_candidate()
    candidate.update(
        {
            "contract_version": "program-candidate-v3",
            "candidate_ref": "candidate-ref-" + "b" * 32,
            "candidate_identity_basis": "canonical_url",
            "program_id": "missouri-snap",
            "program_identity_basis": "selected_catalog_program_id",
        }
    )
    candidate["source_lineage"] = {
        **candidate["source_lineage"],
        "source_id": "usda.programs",
    }
    candidate["listing"] = {
        "title": "Public assistance listing",
        "listing_kind": "assistance_program",
        "jurisdiction_code": "US-VA",
        "program_number": "12.345",
        "canonical_url": "https://example.gov/programs/12.345",
        "summary": "Published program description.",
    }
    candidate["criteria_mapping"] = {
        "status": "candidate_requires_review",
        "review_status": "not_reviewed",
        "proposed_criteria": [
            {"attribute_id": "eligibility.income", "value": "under-review"}
        ],
        "unavailable_reason": None,
    }
    policy_entry = {
        "entry_id": "example-source-entry",
        "owner": "Example Agency",
    }

    publication_key, source_record_id, record = api.build_public_listing_record(
        candidate,
        dataset={
            "dataset_id": "shared-programs",
            "source_id": "usda.programs",
            "title": "Shared public program inventory",
            "schema_version": "public-listing-v3",
        },
        policy_entry=policy_entry,
        policy_reference={
            "manifest_id": "public-search-policy",
            "manifest_version": "1.0.0",
            "content_sha256": "c" * 64,
        },
        policy_entries=[
            {
                "entry_id": "example-source-entry",
                "source_id": "usda.programs",
                "review_status": "approved_for_bounded_discovery",
                "permitted_uses": ["discovery"],
                "host": "example.gov",
                "owner": "Example Agency",
                "path_prefixes": ["/programs"],
            }
        ],
    )

    assert len(publication_key) == 64
    assert source_record_id.startswith("candidate." + "a" * 32)
    assert record["canonical_attributes"]["core.geography.state_code"] == "VA"
    assert record["canonical_attributes"]["listing.record_kind"] == "assistance_program"
    assert record["source_metadata"]["criteria_status"] == "not_assessed"
    assert record["source_metadata"]["candidate_criteria_proposals_omitted"] is True
    assert "eligibility.income" not in record["canonical_attributes"]
    assert record["semantic_context"]["catalog_namespace"] == "programs"
    assert record["semantic_context"]["criteria_program_ids"] == ["missouri-snap"]
    assert record["source_metadata"]["source_policy_reference"] == {
        "manifest_id": "public-search-policy",
        "manifest_version": "1.0.0",
        "content_sha256": "c" * 64,
        "entry_id": "example-source-entry",
    }


def test_active_source_policy_match_requires_exact_approved_discovery_entry():
    entry = {
        "source_id": "usda.programs",
        "review_status": "approved_for_bounded_discovery",
        "permitted_uses": ["discovery"],
        "host": "example.gov",
        "owner": "Example Agency",
        "path_prefixes": ["/programs"],
    }
    assert (
        api.approved_policy_entry_for_url(
            "https://example.gov/programs/123", [entry], "usda.programs"
        )
        == entry
    )
    assert (
        api.approved_policy_entry_for_url(
            "https://example.gov.evil.test/programs/123", [entry], "usda.programs"
        )
        is None
    )
    assert (
        api.approved_policy_entry_for_url(
            "https://example.gov/other/123", [entry], "usda.programs"
        )
        is None
    )
    assert (
        api.approved_policy_entry_for_url(
            "https://example.gov/programs/123",
            [{**entry, "permitted_uses": "not-discovery"}],
            "usda.programs",
        )
        is None
    )


def test_publication_lifecycle_requires_exact_compare_and_set_references():
    publication_key = "1" * 64
    candidate_id = "candidate-" + "a" * 32
    reference = {"candidate_id": candidate_id, "content_sha256": "2" * 64}
    payload = {
        "request_id": "c9bb394e-ef1c-4bee-bce0-79e04972c38b",
        "action": "activate",
        "expected_current": None,
        "target": reference,
        "rationale": "Promote the reviewed shared catalog version.",
    }
    normalized = api.validate_program_candidate_publication_lifecycle(
        payload, publication_key
    )
    assert normalized["target"] == reference
    audit = {
        "mcp_session_sha256": "3" * 64,
        "mcp_request_sha256": "4" * 64,
        "mcp_client_identity_status": "unavailable",
        "operator_credential_label": "core_operator_token",
    }
    audited = api.validate_program_candidate_publication_lifecycle(
        {**payload, "operator_audit_context": audit}, publication_key
    )
    assert audited["operator_audit_context"] == audit
    with pytest.raises(api.InvalidProgramCandidatePublication):
        api.validate_program_candidate_publication_lifecycle(
            {**payload, "target": {**reference, "content_sha256": "bad"}},
            publication_key,
        )
    with pytest.raises(api.InvalidProgramCandidatePublication):
        api.validate_program_candidate_publication_lifecycle(
            {
                **payload,
                "operator_audit_context": {
                    **audit,
                    "client_id": "caller-asserted-identity",
                },
            },
            publication_key,
        )
