from __future__ import annotations

import copy
import importlib.util
import json
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

API_DIRECTORY = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(0, str(API_DIRECTORY))
API_SPEC = importlib.util.spec_from_file_location(
    "source_policy_test_api", API_DIRECTORY / "api.py"
)
assert API_SPEC is not None and API_SPEC.loader is not None
api = importlib.util.module_from_spec(API_SPEC)
API_SPEC.loader.exec_module(api)

from source_policy_manifests import (  # noqa: E402
    InvalidSourcePolicyManifest,
    source_policy_manifest_sha256,
    validate_manifest_registration,
    validate_source_policy_activation,
    validate_source_policy_decision,
    validate_source_policy_manifest,
)


def candidate_manifest(suffix: str = "unit") -> dict[str, object]:
    return {
        "manifest_id": f"aitest-source-policy-{suffix}",
        "manifest_version": "1.0.0",
        "candidate_id": f"aitest-candidate-{suffix}",
        "source": {
            "source_id": f"aitest.source-{suffix}",
            "name": "AITest official source candidate",
            "canonical_url": "https://example.gov/programs/listing",
            "authority": {"type": "federal", "name": "AITest agency"},
            "jurisdiction": {"level": "federal", "code": "US"},
            "host_path_scope": {
                "host": "example.gov",
                "path_prefixes": ["/programs"],
            },
            "permitted_use": ["source_discovery", "program_listing"],
        },
        "normalized_fields": [
            {
                "field_id": "program.title",
                "source_field": "listingTitle",
                "data_type": "string",
                "mapping_status": "mapped",
            }
        ],
        "criteria_semantics": [
            {
                "criterion_id": "program.open_date",
                "description": "Published application opening date.",
                "source_field": "openDate",
                "interpretation_status": "not_assessed",
            }
        ],
        "intended_effect": {
            "cluster_id": "eligibility",
            "tenant_ids": [],
            "publication_scope": "shared_cluster_inventory",
        },
        "verification_evidence": [
            {
                "evidence_type": "official_page",
                "reference_url": "https://example.gov/programs/listing",
                "observed_at_utc": "2026-10-24T12:30:00Z",
                "result": "AITest evidence for contract verification.",
            }
        ],
        "review_status": "pending_review",
        "approval": None,
    }


def search_policy_bundle_manifest() -> dict[str, object]:
    entry = {
        "entry_id": "aitest-federal-programs",
        "source_id": "aitest.federal-programs",
        "host": "programs.example.gov",
        "path_prefixes": ["/programs"],
        "authority_class": "federal",
        "owner": "AITest Federal Agency",
        "authority_jurisdiction": "US",
        "permitted_uses": ["discovery", "criteria"],
        "verification_evidence": "AITest official authority evidence.",
        "review_status": "approved_for_bounded_discovery",
        "reviewed_at": "2026-10-25",
    }
    state_entry = {
        **entry,
        "entry_id": "aitest-state-programs",
        "source_id": "aitest.state-programs",
        "host": "programs.example.state.us",
        "authority_class": "state",
        "owner": "AITest State Agency",
        "authority_jurisdiction": "US-OH",
        "permitted_uses": ["discovery"],
    }
    return {
        "manifest_id": "aitest-public-search-policy",
        "manifest_version": "1.0.0",
        "candidate_id": "aitest-public-search-policy-candidate",
        "manifest_kind": "public_search_policy_bundle",
        "policy_bundle": {
            "reviewed_at": "2026-10-25",
            "entries": [entry, state_entry],
        },
        "review_status": "pending_review",
        "approval": None,
    }


def test_manifest_validation_and_canonical_digest_are_stable() -> None:
    manifest = candidate_manifest()
    validated = validate_source_policy_manifest(manifest)
    reversed_keys = dict(reversed(list(manifest.items())))

    assert validated == manifest
    assert source_policy_manifest_sha256(manifest) == source_policy_manifest_sha256(
        reversed_keys
    )


def test_multisource_search_policy_bundle_is_digest_pinned() -> None:
    manifest = search_policy_bundle_manifest()
    assert validate_source_policy_manifest(manifest) == manifest
    assert source_policy_manifest_sha256(manifest) == source_policy_manifest_sha256(
        dict(reversed(list(manifest.items())))
    )


@pytest.mark.parametrize(
    "entry",
    [
        {
            "entry_id": "aitest-domain-class",
            "source_id": "aitest.public-domain.gov",
            "host": "gov",
            "path_prefixes": ["/"],
            "scope_kind": "domain_suffix",
            "authority_class": "unverified_domain_class",
            "owner": "Publisher not verified",
            "authority_jurisdiction": "unknown",
            "permitted_uses": ["discovery"],
            "verification_evidence": (
                "Operator-approved discovery-only government domain class."
            ),
            "review_status": "approved_for_bounded_discovery",
            "reviewed_at": "2026-10-29",
        },
        {
            "entry_id": "aitest-related-com",
            "source_id": "aitest.related-partner",
            "host": "partner.example.com",
            "path_prefixes": ["/"],
            "scope_kind": "related_domain",
            "authority_class": "unverified_related_domain",
            "owner": "Publisher not verified",
            "authority_jurisdiction": "US-MD",
            "permitted_uses": ["discovery"],
            "verification_evidence": "Partner relationship lead: https://agency.gov/partners/example.",
            "review_status": "approved_for_bounded_discovery",
            "reviewed_at": "2026-10-29",
        },
    ],
)
def test_public_search_policy_accepts_unverified_discovery_scopes(entry) -> None:
    manifest = search_policy_bundle_manifest()
    manifest["policy_bundle"]["entries"] = [entry]
    assert validate_source_policy_manifest(manifest) == manifest


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda item: item["policy_bundle"]["entries"][0].update(
                scope_kind="domain_suffix",
                host="gov",
                authority_class="unverified_domain_class",
                authority_jurisdiction="unknown",
                permitted_uses=["discovery", "criteria"],
            ),
            "entry is invalid",
        ),
        (
            lambda item: item["policy_bundle"]["entries"][0].update(
                scope_kind="domain_suffix", host="com"
            ),
            "entry is invalid",
        ),
        (
            lambda item: item["policy_bundle"]["entries"][0].update(
                scope_kind="domain_suffix", host=["gov"]
            ),
            "entry is invalid",
        ),
    ],
)
def test_public_search_policy_rejects_broad_scopes_for_non_discovery(
    mutation, message
) -> None:
    manifest = search_policy_bundle_manifest()
    mutation(manifest)
    with pytest.raises(InvalidSourcePolicyManifest, match=message):
        validate_source_policy_manifest(manifest)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda item: item["policy_bundle"]["entries"][0].update(host="*.gov"),
            "entry is invalid",
        ),
        (
            lambda item: item["policy_bundle"]["entries"][0].update(
                path_prefixes=["/programs/../private"]
            ),
            "entry is invalid",
        ),
        (
            lambda item: item["policy_bundle"]["entries"].append(
                {
                    **copy.deepcopy(item["policy_bundle"]["entries"][0]),
                    "entry_id": "aitest-duplicate-scope",
                    "source_id": "aitest.duplicate-scope",
                }
            ),
            "duplicate host/path",
        ),
        (
            lambda item: item["policy_bundle"]["entries"][0].update(
                review_status="approved"
            ),
            "entry is invalid",
        ),
    ],
)
def test_multisource_search_policy_bundle_rejects_unsafe_scope(
    mutation, message
) -> None:
    manifest = search_policy_bundle_manifest()
    mutation(manifest)
    with pytest.raises(InvalidSourcePolicyManifest, match=message):
        validate_source_policy_manifest(manifest)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda item: item.pop("verification_evidence"), "fields do not match"),
        (lambda item: item.update(review_status="approved"), "pending and unapproved"),
        (
            lambda item: item.update(approval={"actor": "self"}),
            "pending and unapproved",
        ),
        (
            lambda item: item["source"]["host_path_scope"].update(host="other.gov"),
            "Host and path scope",
        ),
        (
            lambda item: item["source"].update(
                canonical_url="http://example.gov/programs/listing"
            ),
            "HTTPS",
        ),
    ],
)
def test_manifest_rejects_invalid_or_self_approved_candidates(
    mutation, message
) -> None:
    manifest = copy.deepcopy(candidate_manifest("invalid"))
    mutation(manifest)
    with pytest.raises(InvalidSourcePolicyManifest, match=message):
        validate_source_policy_manifest(manifest)


def test_registration_requires_exact_validated_digest() -> None:
    manifest = candidate_manifest("digest")
    digest = source_policy_manifest_sha256(manifest)
    assert validate_manifest_registration(
        {"manifest": manifest, "expected_content_sha256": digest}
    ) == (manifest, digest)
    with pytest.raises(InvalidSourcePolicyManifest, match="does not match"):
        validate_manifest_registration(
            {"manifest": manifest, "expected_content_sha256": "0" * 64}
        )


def test_decision_validation_is_digest_bound_and_rejects_caller_identity() -> None:
    manifest = candidate_manifest("decision-contract")
    digest = source_policy_manifest_sha256(manifest)
    payload = {
        "expected_content_sha256": digest,
        "decision": "approved",
        "rationale": "Official source and mapping reviewed.",
    }
    assert validate_source_policy_decision(
        payload, str(manifest["manifest_id"]), str(manifest["manifest_version"])
    ) == (digest, "approved", "Official source and mapping reviewed.")

    audit = {
        "mcp_session_sha256": "3" * 64,
        "mcp_request_sha256": "4" * 64,
        "mcp_client_identity_status": "unavailable",
        "operator_credential_label": "core_operator_token",
    }
    assert validate_source_policy_decision(
        {**payload, "operator_audit_context": audit},
        str(manifest["manifest_id"]),
        str(manifest["manifest_version"]),
    ) == (digest, "approved", "Official source and mapping reviewed.")

    for invalid in (
        {**payload, "decision": "activated"},
        {**payload, "expected_content_sha256": "not-a-digest"},
        {**payload, "rationale": "  "},
        {**payload, "approver_principal": "caller-controlled"},
        {
            **payload,
            "operator_audit_context": {**audit, "client_id": "caller-claimed"},
        },
    ):
        with pytest.raises(InvalidSourcePolicyManifest):
            validate_source_policy_decision(
                invalid,
                str(manifest["manifest_id"]),
                str(manifest["manifest_version"]),
            )


def test_source_policy_decision_persists_and_replays_original_audit_context(
    monkeypatch,
) -> None:
    manifest = candidate_manifest("decision-audit")
    digest = source_policy_manifest_sha256(manifest)
    first_audit = {
        "mcp_session_sha256": "3" * 64,
        "mcp_request_sha256": "4" * 64,
        "mcp_client_identity_status": "unavailable",
        "operator_credential_label": "core_operator_token",
    }
    replay_audit = {**first_audit, "mcp_session_sha256": "5" * 64}

    class Connection:
        row = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params):
            if "FROM mcp_saas.source_policy_manifests" in sql:
                return type(
                    "Result", (), {"fetchone": lambda _self: (digest, manifest)}
                )()
            if sql.lstrip().startswith("INSERT"):
                if self.row is None:
                    self.row = (
                        params[0],
                        digest,
                        params[4],
                        params[5],
                        params[6],
                        datetime(2026, 10, 31, tzinfo=timezone.utc),
                        *params[7:11],
                    )
                    inserted = self.row
                else:
                    inserted = None
                return type("Result", (), {"fetchone": lambda _self: inserted})()
            return type("Result", (), {"fetchone": lambda _self: self.row})()

    connection = Connection()
    monkeypatch.setattr(api, "connect_db", lambda: connection)
    payload = {
        "expected_content_sha256": digest,
        "decision": "approved",
        "rationale": "AITest source review decision.",
        "operator_audit_context": first_audit,
    }
    first, created = api.record_source_policy_decision(
        str(manifest["manifest_id"]), str(manifest["manifest_version"]), payload
    )
    replay, replay_created = api.record_source_policy_decision(
        str(manifest["manifest_id"]),
        str(manifest["manifest_version"]),
        {**payload, "operator_audit_context": replay_audit},
    )

    assert created is True
    assert replay_created is False
    assert replay == first
    assert replay["operator_audit_context"] == first_audit


def test_activation_request_requires_exact_refs_and_typed_lifecycle_action() -> None:
    reference = {
        "manifest_id": "approved-source-policy",
        "manifest_version": "1.0.0",
        "content_sha256": "a" * 64,
    }
    request_id = str(uuid.uuid4())
    activation = {
        "request_id": request_id,
        "action": "activate",
        "expected_current": None,
        "target": reference,
        "rationale": "Approved exact source policy for controlled discovery.",
    }
    assert validate_source_policy_activation(activation) == activation
    audit = {
        "mcp_session_sha256": "3" * 64,
        "mcp_request_sha256": "4" * 64,
        "mcp_client_identity_status": "unavailable",
        "operator_credential_label": "core_operator_token",
    }
    audited_activation = validate_source_policy_activation(
        {**activation, "operator_audit_context": audit}
    )
    assert audited_activation["operator_audit_context"] == audit
    with pytest.raises(InvalidSourcePolicyManifest):
        validate_source_policy_activation(
            {
                **activation,
                "operator_audit_context": {
                    **audit,
                    "client_id": "caller-asserted-identity",
                },
            }
        )

    deactivation = {
        **activation,
        "action": "deactivate",
        "expected_current": reference,
        "target": None,
    }
    assert validate_source_policy_activation(deactivation) == deactivation

    for invalid in (
        {**activation, "action": []},
        {**activation, "request_id": "invalid"},
        {**activation, "target": {**reference, "content_sha256": "A" * 64}},
        {**activation, "target": {**reference, "approver": "caller"}},
        {**activation, "operator_principal": "caller-controlled"},
        {**activation, "action": "deactivate"},
        {**deactivation, "target": reference},
        {**activation, "rationale": "  "},
    ):
        with pytest.raises(InvalidSourcePolicyManifest):
            validate_source_policy_activation(invalid)


def test_runtime_image_includes_manifest_contract_module() -> None:
    dockerfile = (API_DIRECTORY / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (REPOSITORY_ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "deploy/local/vm-data-api/source_policy_manifests.py" in dockerfile
    assert "!deploy/local/vm-data-api/source_policy_manifests.py" in dockerignore


def test_runtime_image_includes_operator_audit_module() -> None:
    dockerfile = (API_DIRECTORY / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (REPOSITORY_ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "deploy/local/vm-data-api/operator_audit.py" in dockerfile
    assert "!deploy/local/vm-data-api/operator_audit.py" in dockerignore


def test_runtime_image_includes_public_catalog_contract_module() -> None:
    dockerfile = (API_DIRECTORY / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (REPOSITORY_ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "deploy/local/vm-data-api/public_catalog_registry.py" in dockerfile
    assert "!deploy/local/vm-data-api/public_catalog_registry.py" in dockerignore


def test_manifest_api_auth_and_validate_response() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    manifest = candidate_manifest("http")
    digest = source_policy_manifest_sha256(manifest)
    stored = {
        "status": "stored",
        "manifest_id": manifest["manifest_id"],
        "manifest_version": manifest["manifest_version"],
        "candidate_id": manifest["candidate_id"],
        "content_sha256": digest,
        "review_status": "pending_review",
        "created_at": "2026-10-24T12:30:00+00:00",
        "manifest": manifest,
    }
    with (
        patch.object(
            api,
            "read_secret",
            side_effect=lambda path: (
                "writer-token"
                if path == api.REGISTRY_WRITE_TOKEN_FILE
                else "reader-token"
            ),
        ),
        patch.object(
            api, "register_source_policy_manifest", return_value=(stored, True)
        ),
        patch.object(api, "read_source_policy_manifest", return_value=stored),
    ):
        thread.start()
        try:
            endpoint = (
                f"http://127.0.0.1:{server.server_port}"
                "/v1/registry/source-policy-manifests/validate"
            )
            denied = Request(
                endpoint,
                data=json.dumps(manifest).encode(),
                headers={"Authorization": "Bearer writer-token"},
                method="POST",
            )
            with pytest.raises(HTTPError) as error:
                urlopen(denied, timeout=2)
            assert error.value.code == 401

            allowed = Request(
                endpoint,
                data=json.dumps(manifest).encode(),
                headers={"Authorization": "Bearer reader-token"},
                method="POST",
            )
            with urlopen(allowed, timeout=2) as response:
                result = json.loads(response.read())
            assert result["status"] == "valid"
            assert result["review_status"] == "pending_review"
            assert result["content_sha256"] == digest

            registration_url = (
                f"http://127.0.0.1:{server.server_port}"
                "/v1/registry/source-policy-manifests"
            )
            registration_body = json.dumps(
                {"manifest": manifest, "expected_content_sha256": digest}
            ).encode()
            denied_registration = Request(
                registration_url,
                data=registration_body,
                headers={"Authorization": "Bearer reader-token"},
                method="POST",
            )
            with pytest.raises(HTTPError) as error:
                urlopen(denied_registration, timeout=2)
            assert error.value.code == 401

            allowed_registration = Request(
                registration_url,
                data=registration_body,
                headers={"Authorization": "Bearer writer-token"},
                method="POST",
            )
            with urlopen(allowed_registration, timeout=2) as response:
                registration = json.loads(response.read())
                assert response.status == 201
            assert registration["content_sha256"] == digest

            readback_url = (
                f"{registration_url}/{manifest['manifest_id']}"
                f"/{manifest['manifest_version']}"
            )
            with urlopen(
                Request(readback_url, headers={"Authorization": "Bearer reader-token"}),
                timeout=2,
            ) as response:
                readback = json.loads(response.read())
            assert readback["manifest"] == manifest

            decision_url = (
                f"{registration_url}/{manifest['manifest_id']}"
                f"/{manifest['manifest_version']}/decision"
            )
            decision = {
                "status": "recorded",
                "decision_id": str(uuid.uuid4()),
                "manifest_id": manifest["manifest_id"],
                "manifest_version": manifest["manifest_version"],
                "content_sha256": digest,
                "decision": "approved",
                "rationale": "Reviewed against official source evidence.",
                "authorization_capability": "registry_writer",
                "decided_at": "2026-10-25T12:00:00+00:00",
            }
            decision_payload = {
                "expected_content_sha256": digest,
                "decision": "approved",
                "rationale": decision["rationale"],
            }
            with (
                patch.object(
                    api,
                    "record_source_policy_decision",
                    return_value=(decision, True),
                ),
                patch.object(
                    api,
                    "read_secret",
                    side_effect=lambda path: (
                        "writer-token"
                        if path == api.REGISTRY_WRITE_TOKEN_FILE
                        else "reader-token"
                    ),
                ),
            ):
                denied_decision = Request(
                    decision_url,
                    data=json.dumps(decision_payload).encode(),
                    headers={"Authorization": "Bearer reader-token"},
                    method="POST",
                )
                with pytest.raises(HTTPError) as error:
                    urlopen(denied_decision, timeout=2)
                assert error.value.code == 401

            with (
                patch.object(
                    api,
                    "record_source_policy_decision",
                    return_value=(decision, True),
                ),
                patch.object(api, "read_secret", return_value="writer-token"),
            ):
                allowed_decision = Request(
                    decision_url,
                    data=json.dumps(decision_payload).encode(),
                    headers={"Authorization": "Bearer writer-token"},
                    method="POST",
                )
                with urlopen(allowed_decision, timeout=2) as response:
                    recorded = json.loads(response.read())
                    assert response.status == 201
                assert recorded["authorization_capability"] == "registry_writer"
                assert "approver_principal" not in recorded
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def test_activation_api_uses_writer_for_changes_and_reader_for_readback() -> None:
    result = {
        "status": "recorded",
        "event": {"action": "activate", "authorization_capability": "registry_writer"},
        "active_state": {"status": "active"},
    }
    server = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    with (
        patch.object(
            api,
            "read_secret",
            side_effect=lambda path: (
                "writer-token"
                if path == api.REGISTRY_WRITE_TOKEN_FILE
                else "reader-token"
            ),
        ),
        patch.object(
            api, "record_source_policy_activation", return_value=(result, True)
        ),
        patch.object(
            api, "read_active_source_policy", return_value={"status": "inactive"}
        ),
        patch.object(
            api,
            "list_source_policy_activation_events",
            return_value={
                "status": "available",
                "events": [],
                "next_before_generation": None,
            },
        ),
    ):
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}/v1/registry"
            payload = {
                "request_id": str(uuid.uuid4()),
                "action": "activate",
                "expected_current": None,
                "target": {
                    "manifest_id": "approved-source-policy",
                    "manifest_version": "1.0.0",
                    "content_sha256": "a" * 64,
                },
                "rationale": "Activate the exact reviewed source-policy digest.",
            }
            denied = Request(
                f"{base}/source-policy-activations",
                data=json.dumps(payload).encode(),
                headers={"Authorization": "Bearer reader-token"},
                method="POST",
            )
            with pytest.raises(HTTPError) as error:
                urlopen(denied, timeout=2)
            assert error.value.code == 401

            allowed = Request(
                f"{base}/source-policy-activations",
                data=json.dumps(payload).encode(),
                headers={"Authorization": "Bearer writer-token"},
                method="POST",
            )
            with urlopen(allowed, timeout=2) as response:
                recorded = json.loads(response.read())
                assert response.status == 201
            assert recorded["event"]["authorization_capability"] == "registry_writer"

            denied_read = Request(
                f"{base}/source-policy-active",
                headers={"Authorization": "Bearer writer-token"},
            )
            with pytest.raises(HTTPError) as error:
                urlopen(denied_read, timeout=2)
            assert error.value.code == 401

            with urlopen(
                Request(
                    f"{base}/source-policy-active",
                    headers={"Authorization": "Bearer reader-token"},
                ),
                timeout=2,
            ) as response:
                assert json.loads(response.read()) == {"status": "inactive"}

            with urlopen(
                Request(
                    f"{base}/source-policy-activation-events?limit=2",
                    headers={"Authorization": "Bearer reader-token"},
                ),
                timeout=2,
            ) as response:
                assert json.loads(response.read())["events"] == []
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def test_active_public_search_policy_projects_only_approved_bundle() -> None:
    bundle = search_policy_bundle_manifest()["policy_bundle"]
    active = {
        "status": "active",
        "active_manifest": {
            "status": "available",
            "manifest_id": "aitest-public-search-policy",
            "manifest_version": "1.0.0",
            "candidate_id": "aitest-public-search-policy-candidate",
            "content_sha256": "a" * 64,
            "manifest": {
                **search_policy_bundle_manifest(),
                "manifest_id": "aitest-public-search-policy",
                "manifest_version": "1.0.0",
                "candidate_id": "aitest-public-search-policy-candidate",
            },
            "review_decision": {"decision": "approved", "rationale": "private"},
        },
        "last_event": {"rationale": "private"},
    }
    with patch.object(api, "read_active_source_policy", return_value=active):
        result = api.read_active_public_search_policy()

    assert result == {
        "status": "active",
        "active_reference": {
            "manifest_id": "aitest-public-search-policy",
            "manifest_version": "1.0.0",
            "content_sha256": "a" * 64,
        },
        "policy_bundle": bundle,
    }
    assert "rationale" not in json.dumps(result)


def test_active_public_search_policy_api_uses_catalog_reader_only() -> None:
    projection = {"status": "inactive", "active_reference": None}
    server = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    with (
        patch.object(
            api,
            "read_secret",
            side_effect=lambda path: (
                "catalog-token"
                if path == api.CATALOG_READ_TOKEN_FILE
                else "registry-token"
            ),
        ),
        patch.object(api, "read_active_public_search_policy", return_value=projection),
    ):
        thread.start()
        try:
            endpoint = (
                f"http://127.0.0.1:{server.server_port}/v1/public-search-policy/active"
            )
            with pytest.raises(HTTPError) as error:
                urlopen(
                    Request(
                        endpoint,
                        headers={"Authorization": "Bearer registry-token"},
                    ),
                    timeout=2,
                )
            assert error.value.code == 401
            with urlopen(
                Request(
                    endpoint,
                    headers={"Authorization": "Bearer catalog-token"},
                ),
                timeout=2,
            ) as response:
                assert json.loads(response.read()) == projection
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def test_manifest_persistence_is_immutable_and_idempotent(database_url: str) -> None:
    import psycopg

    suffix = uuid.uuid4().hex[:16]
    manifest = candidate_manifest(suffix)
    payload = {
        "manifest": manifest,
        "expected_content_sha256": source_policy_manifest_sha256(manifest),
    }
    api.connect_db = lambda: psycopg.connect(database_url)
    try:
        first, created = api.register_source_policy_manifest(payload)
        replay, replay_created = api.register_source_policy_manifest(payload)
        assert created is True
        assert replay_created is False
        assert replay["content_sha256"] == first["content_sha256"]
        assert replay["review_status"] == "pending_review"

        readback = api.read_source_policy_manifest(
            str(manifest["manifest_id"]), str(manifest["manifest_version"])
        )
        assert readback is not None
        assert readback["manifest"] == manifest
        assert readback["content_sha256"] == payload["expected_content_sha256"]

        changed = copy.deepcopy(manifest)
        changed["source"]["name"] = "AITest changed after validation"
        changed_payload = {
            "manifest": changed,
            "expected_content_sha256": source_policy_manifest_sha256(changed),
        }
        with pytest.raises(api.SourcePolicyManifestConflict):
            api.register_source_policy_manifest(changed_payload)

        decision_payload = {
            "expected_content_sha256": payload["expected_content_sha256"],
            "decision": "approved",
            "rationale": "Reviewed against the official source.",
            "operator_audit_context": {
                "mcp_session_sha256": "3" * 64,
                "mcp_request_sha256": "4" * 64,
                "mcp_client_identity_status": "unavailable",
                "operator_credential_label": "core_operator_token",
            },
        }
        first_decision, decision_created = api.record_source_policy_decision(
            str(manifest["manifest_id"]),
            str(manifest["manifest_version"]),
            decision_payload,
        )
        replayed_decision, replayed_created = api.record_source_policy_decision(
            str(manifest["manifest_id"]),
            str(manifest["manifest_version"]),
            {
                **decision_payload,
                "operator_audit_context": {
                    **decision_payload["operator_audit_context"],
                    "mcp_session_sha256": "5" * 64,
                },
            },
        )
        assert decision_created is True
        assert replayed_created is False
        assert replayed_decision == first_decision
        assert first_decision["authorization_capability"] == "registry_writer"
        assert "approver_principal" not in first_decision

        with pytest.raises(api.SourcePolicyDecisionConflict):
            api.record_source_policy_decision(
                str(manifest["manifest_id"]),
                str(manifest["manifest_version"]),
                {
                    **decision_payload,
                    "decision": "rejected",
                },
            )

        decided_readback = api.read_source_policy_manifest(
            str(manifest["manifest_id"]), str(manifest["manifest_version"])
        )
        assert decided_readback is not None
        assert decided_readback["review_decision"] == {
            "decision_id": first_decision["decision_id"],
            "content_sha256": payload["expected_content_sha256"],
            "decision": "approved",
            "rationale": decision_payload["rationale"],
            "authorization_capability": "registry_writer",
            "decided_at": first_decision["decided_at"],
            "operator_audit_context": decision_payload["operator_audit_context"],
        }
    finally:
        with psycopg.connect(database_url) as connection:
            connection.execute(
                """DELETE FROM mcp_saas.source_policy_review_decisions
                   WHERE manifest_id = %s AND manifest_version = %s""",
                (manifest["manifest_id"], manifest["manifest_version"]),
            )
            connection.execute(
                "DELETE FROM mcp_saas.source_policy_manifests WHERE manifest_id = %s",
                (manifest["manifest_id"],),
            )


def test_source_policy_activation_is_approved_digest_bound_and_serialized(
    database_url: str, monkeypatch
) -> None:
    import psycopg

    monkeypatch.setattr(api, "connect_db", lambda: psycopg.connect(database_url))
    with psycopg.connect(database_url) as connection:
        database_name = connection.execute("SELECT current_database()").fetchone()[0]
        assert "test" in database_name.casefold(), (
            "Activation integration checks require an explicitly named "
            "disposable test database."
        )
        existing_events = connection.execute(
            "SELECT count(*) FROM mcp_saas.source_policy_activation_events"
        ).fetchone()[0]
        assert existing_events == 0, (
            "Activation integration checks require a fresh disposable database."
        )

    suffix = uuid.uuid4().hex[:16]
    manifests = [
        candidate_manifest(f"activation-{suffix}-{letter}") for letter in "abcd"
    ]
    references: dict[str, dict[str, str]] = {}

    def lifecycle(action, expected_current, target, *, request_id=None, rationale=None):
        return {
            "request_id": request_id or str(uuid.uuid4()),
            "action": action,
            "expected_current": expected_current,
            "target": target,
            "rationale": rationale or f"AITest {action} source-policy lifecycle event.",
        }

    try:
        for manifest in manifests:
            digest = source_policy_manifest_sha256(manifest)
            registered, created = api.register_source_policy_manifest(
                {"manifest": manifest, "expected_content_sha256": digest}
            )
            assert created is True
            references[str(manifest["manifest_id"])] = {
                "manifest_id": str(manifest["manifest_id"]),
                "manifest_version": str(manifest["manifest_version"]),
                "content_sha256": digest,
            }
            assert registered["content_sha256"] == digest

        first = references[str(manifests[0]["manifest_id"])]
        second = references[str(manifests[1]["manifest_id"])]
        never_active = references[str(manifests[2]["manifest_id"])]
        rejected = references[str(manifests[3]["manifest_id"])]

        with pytest.raises(api.SourcePolicyManifestNotFound):
            api.record_source_policy_activation(
                lifecycle(
                    "activate",
                    None,
                    {
                        "manifest_id": f"aitest-missing-{suffix}",
                        "manifest_version": "1.0.0",
                        "content_sha256": "f" * 64,
                    },
                )
            )
        with pytest.raises(api.SourcePolicyActivationConflict):
            api.record_source_policy_activation(lifecycle("activate", None, first))
        wrong_digest = {
            **first,
            "content_sha256": "0" * 64,
        }
        with pytest.raises(api.SourcePolicyActivationConflict):
            api.record_source_policy_activation(
                lifecycle("activate", None, wrong_digest)
            )

        for manifest, reference, decision in zip(
            manifests,
            references.values(),
            ("approved", "approved", "approved", "rejected"),
        ):
            api.record_source_policy_decision(
                str(manifest["manifest_id"]),
                str(manifest["manifest_version"]),
                {
                    "expected_content_sha256": reference["content_sha256"],
                    "decision": decision,
                    "rationale": f"AITest review outcome: {decision}.",
                },
            )

        rejected_request = lifecycle("activate", None, rejected)
        with pytest.raises(api.SourcePolicyActivationConflict):
            api.record_source_policy_activation(rejected_request)

        activation_request = lifecycle("activate", None, first)
        activation_request["operator_audit_context"] = {
            "mcp_session_sha256": "3" * 64,
            "mcp_request_sha256": "4" * 64,
            "mcp_client_identity_status": "unavailable",
            "operator_credential_label": "core_operator_token",
        }
        first_event, created = api.record_source_policy_activation(activation_request)
        replay_request = {
            **activation_request,
            "operator_audit_context": {
                **activation_request["operator_audit_context"],
                "mcp_session_sha256": "5" * 64,
            },
        }
        replay, replay_created = api.record_source_policy_activation(replay_request)
        assert created is True
        assert replay_created is False
        assert replay["event"] == first_event["event"]
        assert (
            first_event["event"]["operator_audit_context"]
            == activation_request["operator_audit_context"]
        )
        assert (
            replay["active_state"]["active_manifest"]["content_sha256"]
            == first["content_sha256"]
        )

        with pytest.raises(api.SourcePolicyActivationConflict):
            api.record_source_policy_activation(
                {**activation_request, "rationale": "AITest changed replay content."}
            )
        with pytest.raises(api.SourcePolicyActivationConflict):
            api.record_source_policy_activation(lifecycle("activate", None, second))

        api.record_source_policy_activation(lifecycle("activate", first, second))
        with pytest.raises(api.SourcePolicyActivationConflict):
            api.record_source_policy_activation(
                lifecycle("rollback", second, never_active)
            )

        rollback, _ = api.record_source_policy_activation(
            lifecycle("rollback", second, first)
        )
        assert (
            rollback["active_state"]["active_manifest"]["content_sha256"]
            == first["content_sha256"]
        )
        deactivation, _ = api.record_source_policy_activation(
            lifecycle("deactivate", first, None)
        )
        assert deactivation["active_state"]["status"] == "inactive"
        assert api.read_active_source_policy()["status"] == "inactive"

        def activate_after_deactivation(target):
            try:
                return api.record_source_policy_activation(
                    lifecycle("activate", None, target)
                )
            except api.SourcePolicyActivationConflict:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(
                executor.map(activate_after_deactivation, (second, never_active))
            )
        assert sum(outcome is not None for outcome in outcomes) == 1
        assert sum(outcome is None for outcome in outcomes) == 1

        first_page = api.list_source_policy_activation_events(limit=2)
        assert len(first_page["events"]) == 2
        assert first_page["next_before_generation"] is not None
        second_page = api.list_source_policy_activation_events(
            limit=2, before_generation=first_page["next_before_generation"]
        )
        assert len(second_page["events"]) == 2
        assert (
            first_page["events"][0]["generation"]
            > second_page["events"][0]["generation"]
        )
        active = api.read_active_source_policy()
        assert active["status"] == "active"
        assert active["active_manifest"]["review_decision"]["decision"] == "approved"
        assert (
            active["active_manifest"]["review_decision"]["content_sha256"]
            == active["active_manifest"]["content_sha256"]
        )
    finally:
        manifest_ids = list(references)
        with psycopg.connect(database_url) as connection:
            connection.execute(
                """DELETE FROM mcp_saas.source_policy_activation_events
                   WHERE expected_manifest_id = ANY(%s)
                      OR target_manifest_id = ANY(%s)""",
                (manifest_ids, manifest_ids),
            )
            connection.execute(
                """DELETE FROM mcp_saas.source_policy_review_decisions
                   WHERE manifest_id = ANY(%s)""",
                (manifest_ids,),
            )
            connection.execute(
                """DELETE FROM mcp_saas.source_policy_manifests
                   WHERE manifest_id = ANY(%s)""",
                (manifest_ids,),
            )
