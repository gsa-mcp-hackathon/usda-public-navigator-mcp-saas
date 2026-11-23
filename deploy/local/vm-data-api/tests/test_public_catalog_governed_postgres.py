from __future__ import annotations

import importlib.util
import json
import sys
import threading
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import httpx
import psycopg
import pytest
from fastmcp import Client as MCPClient

from example_server import core_app
from example_server.core_app import create_core_server
from example_server.core_source_policy import HttpPublicCatalogRegistrationRepository

API_DIRECTORY = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(0, str(API_DIRECTORY))
API_SPEC = importlib.util.spec_from_file_location(
    "governed_public_catalog_test_api", API_DIRECTORY / "api.py"
)
assert API_SPEC is not None and API_SPEC.loader is not None
api = importlib.util.module_from_spec(API_SPEC)
API_SPEC.loader.exec_module(api)


class LoopbackDataApiTransport(httpx.BaseTransport):
    """Bridge Core's HTTPS-only repository client to a loopback API test server."""

    def __init__(self, api_server: ThreadingHTTPServer) -> None:
        self.api_server = api_server

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.raw_path.decode("ascii")
        upstream = Request(
            f"http://127.0.0.1:{self.api_server.server_port}{path}",
            data=request.read(),
            headers={
                key: value
                for key, value in request.headers.items()
                if key.casefold() not in {"host", "connection"}
            },
            method=request.method,
        )
        try:
            with urlopen(upstream, timeout=5) as response:
                status_code = response.status
                headers = dict(response.headers.items())
                content = response.read()
        except HTTPError as response:
            status_code = response.code
            headers = dict(response.headers.items())
            content = response.read()
        return httpx.Response(
            status_code, headers=headers, content=content, request=request
        )


@pytest.mark.asyncio
async def test_governed_public_catalog_edit_applies_replays_and_rejects_stale_state(
    database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    suffix = uuid.uuid4().hex[:16]
    source_id = f"aitest.public-catalog-{suffix}"
    dataset_id = f"aitest-catalog-{suffix}"
    registry_id = f"aitest-public-catalog-{suffix}"
    transaction_id = str(uuid.uuid4())
    stale_transaction_id = str(uuid.uuid4())
    registration = {
        "contract_version": "public-source-registration-v1",
        "source": {
            "source_id": source_id,
            "source_name": "AITest public source",
            "source_url": "https://example.gov/programs",
            "access_status": "available",
            "source_context": {
                "authority": {
                    "level": "federal",
                    "owner_name": "AITest agency",
                    "verification_status": "verified",
                    "evidence_url": "https://example.gov/about",
                }
            },
        },
        "datasets": [
            {
                "dataset_id": dataset_id,
                "source_dataset_id": "program-index",
                "title": "AITest program index",
                "landing_page": "https://example.gov/programs",
                "schema_version": "1.0.0",
                "namespace_ids": ["aitest"],
                "metadata": {
                    "catalog_namespace": "aitest",
                    "criteria_coverage": "not_assessed",
                    "record_contract_version": "normalized-public-record-v1",
                    "visibility_scope": "cluster_public",
                    "schema_registry_ids": {"aitest": registry_id},
                    "schema_versions": {"aitest": "1.0.0"},
                },
            }
        ],
    }
    schema_definition = {
        "namespaces": {"aitest": {"description": "Synthetic integration test"}},
        "attributes": {"aitest.program.title": {"type": "string"}},
    }
    proposal_digest = api.canonical_registration_digest(registration)

    with psycopg.connect(database_url) as connection:
        connection.execute(
            """INSERT INTO mcp_saas.schema_registry
               (manifest_id, registry_id, schema_version, lifecycle_state,
                definition, git_revision)
               VALUES (%s, %s, '1.0.0', 'active', %s::jsonb, 'test')""",
            (
                f"aitest-manifest-{suffix}",
                registry_id,
                json.dumps(schema_definition),
            ),
        )

    monkeypatch.setattr(
        api, "connect_public_catalog_ingest_db", lambda: psycopg.connect(database_url)
    )
    monkeypatch.setattr(api, "read_secret", lambda _path: "AITest Data API writer")
    api_server = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
    api_thread = threading.Thread(target=api_server.serve_forever, daemon=True)
    api_thread.start()
    token_file = tmp_path / "data-api-writer-token"
    token_file.write_text("AITest Data API writer", encoding="utf-8")
    repository = HttpPublicCatalogRegistrationRepository(
        "https://public-catalog.test",
        token_file,
        transport=LoopbackDataApiTransport(api_server),
    )
    core = create_core_server(
        None,
        operator_token="AITest Core operator",
        public_catalog_registration_repository=repository,
    )
    try:
        monkeypatch.setattr(
            core_app,
            "get_http_request",
            lambda: SimpleNamespace(headers={"x-mcp-saas-operator-token": "wrong"}),
        )
        async with MCPClient(core) as client:
            denied = await client.call_tool(
                "preview_public_catalog_registration",
                {"registration": registration},
            )
            monkeypatch.setattr(
                core_app,
                "get_http_request",
                lambda: SimpleNamespace(
                    headers={"x-mcp-saas-operator-token": "AITest Core operator"}
                ),
            )
            preview = await client.call_tool(
                "preview_public_catalog_registration",
                {"registration": registration},
            )
            envelope = {
                "registration": registration,
                "expected_before_sha256": preview.data["before_sha256"],
                "expected_proposed_sha256": proposal_digest,
                "transaction_id": transaction_id,
                "approval_reference": "AITest approved synthetic metadata correction",
            }
            applied = await client.call_tool(
                "apply_public_catalog_registration", envelope
            )
            replay = await client.call_tool(
                "apply_public_catalog_registration", envelope
            )
            stale = await client.call_tool(
                "apply_public_catalog_registration",
                {
                    **envelope,
                    "transaction_id": stale_transaction_id,
                    "approval_reference": "AITest stale metadata correction",
                },
            )
        denied, preview, applied, replay, stale = (
            denied.data,
            preview.data,
            applied.data,
            replay.data,
            stale.data,
        )
        assert denied == {"status": "operator_authorization_required"}
        assert preview["status"] == "preview_ready"
        assert preview["write_performed"] is False
        assert preview["proposed_sha256"] == proposal_digest
        assert preview["changes"]
        assert applied["status"] == "applied"
        assert applied["before_sha256"] == preview["before_sha256"]
        assert applied["after_sha256"] != applied["before_sha256"]
        assert applied["audit_readback_verified"] is True
        applied_audit = applied["audit_readback"]
        assert applied_audit["status"] == "recorded"
        assert applied_audit["outcome"] == "applied"
        assert applied_audit["transaction_id"] == transaction_id
        assert len(applied_audit["operator_audit_context"]["mcp_session_sha256"]) == 64
        assert len(applied_audit["operator_audit_context"]["mcp_request_sha256"]) == 64

        assert replay["status"] == applied["status"]
        assert replay["after_sha256"] == applied["after_sha256"]
        assert replay["idempotent_replay"] is True
        assert replay["audit_readback_verified"] is True
        assert (
            replay["audit_readback"]["operator_audit_context"]
            == applied_audit["operator_audit_context"]
        )

        assert stale["status"] == "stale_before_state"
        assert stale["after_sha256"] == applied["after_sha256"]
        assert stale["audit_readback_verified"] is True
        assert stale["audit_readback"]["outcome"] == "stale_before_state"

        with psycopg.connect(database_url) as connection:
            source = connection.execute(
                """SELECT source_name FROM mcp_saas.public_data_sources
                   WHERE source_id = %s""",
                (source_id,),
            ).fetchone()
            dataset = connection.execute(
                """SELECT title FROM mcp_saas.public_datasets
                   WHERE dataset_id = %s""",
                (dataset_id,),
            ).fetchone()
            receipts = connection.execute(
                """SELECT outcome,
                          operator_audit_context->>'operator_credential_label',
                          operator_audit_context->>'mcp_session_sha256',
                          operator_audit_context->>'mcp_request_sha256'
                   FROM mcp_saas.public_catalog_registration_events
                   WHERE transaction_id IN (%s, %s) ORDER BY recorded_at""",
                (transaction_id, stale_transaction_id),
            ).fetchall()
        assert source == ("AITest public source",)
        assert dataset == ("AITest program index",)
        assert [receipt[:2] for receipt in receipts] == [
            ("applied", "core_operator_token"),
            ("stale_before_state", "core_operator_token"),
        ]
        assert len(receipts[0][2]) == len(receipts[1][2]) == 64
        assert len(receipts[0][3]) == len(receipts[1][3]) == 64
        assert receipts[0][3] != receipts[1][3]
    finally:
        api_server.shutdown()
        api_server.server_close()
        api_thread.join(timeout=5)
        repository._client.close()
        with psycopg.connect(database_url) as connection:
            connection.execute(
                """DELETE FROM mcp_saas.public_catalog_registration_events
                   WHERE transaction_id IN (%s, %s)""",
                (transaction_id, stale_transaction_id),
            )
            connection.execute(
                "DELETE FROM mcp_saas.public_datasets WHERE dataset_id = %s",
                (dataset_id,),
            )
            connection.execute(
                "DELETE FROM mcp_saas.public_data_sources WHERE source_id = %s",
                (source_id,),
            )
            connection.execute(
                "DELETE FROM mcp_saas.schema_registry WHERE registry_id = %s",
                (registry_id,),
            )


@pytest.mark.asyncio
async def test_governed_public_catalog_record_jurisdiction_roundtrip(
    database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    suffix = uuid.uuid4().hex[:16]
    source_id = "sam.gov.assistance_listings"
    dataset_id = f"aitest-jurisdiction-{suffix}"
    registry_id = f"aitest-listing-v4-{suffix}"
    record_id = "sam.gov.assistance_listings.10.539"
    transaction_id = str(uuid.uuid4())
    evidence_url = "https://www.fns.usda.gov/nap/cnmi-summary"
    jurisdiction = {"level": "territory", "country_code": "US", "state_code": "MP"}
    schema = {
        "namespaces": {"core": "Core geography", "listing": "Public listing"},
        "attributes": {
            "core.geography.jurisdiction_level": {
                "type": "string",
                "allowed_values": ["federal", "state", "territory", "local", "tribal"],
            },
            "core.geography.country_code": {"type": "string"},
            "core.geography.state_code": {"type": "string"},
            "listing.title": {"type": "string"},
        },
    }
    observed_at = "2026-11-04T12:00:00+00:00"
    before_record = {
        "source_record_id": record_id,
        "record_type": "public_listing",
        "canonical_attributes": {
            "listing.title": "CNMI Nutrition Assistance",
            "core.geography.jurisdiction_level": "federal",
            "core.geography.country_code": "US",
        },
        "semantic_context": {
            "catalog_namespace": "listing",
            "criteria_coverage": "not_assessed",
            "jurisdiction": {"level": "federal", "country_code": "US"},
        },
        "source_metadata": {
            "source_id": source_id,
            "schema_version": "public-listing-v4",
            "retrieved_at": observed_at,
            "criteria_status": "not_assessed",
        },
        "source_url": evidence_url,
        "content_hash": "a" * 64,
        "schema_version": "public-listing-v4",
        "observed_at": observed_at,
    }
    with psycopg.connect(database_url) as connection:
        connection.execute(
            """INSERT INTO mcp_saas.schema_registry
               (manifest_id, registry_id, schema_version, lifecycle_state,
                definition, git_revision)
               VALUES (%s, %s, 'public-listing-v4', 'active', %s::jsonb, 'test')""",
            (f"aitest-manifest-{suffix}", registry_id, json.dumps(schema)),
        )
        connection.execute(
            """INSERT INTO mcp_saas.public_data_sources
               (source_id, source_name, source_url, access_status)
               VALUES (%s, 'SAM Assistance Listings', %s, 'available')
               ON CONFLICT (source_id) DO NOTHING""",
            (source_id, evidence_url),
        )
        connection.execute(
            """INSERT INTO mcp_saas.public_datasets
               (dataset_id, source_id, source_dataset_id, title, landing_page,
                schema_version, namespace_ids, metadata)
               VALUES (%s, %s, %s, 'AITest SAM listings', %s,
                       'public-listing-v4', ARRAY['core','listing'], %s::jsonb)""",
            (
                dataset_id,
                source_id,
                suffix,
                evidence_url,
                json.dumps(
                    {
                        "catalog_namespace": "listing",
                        "criteria_coverage": "not_assessed",
                        "schema_registry_ids": {
                            "core": registry_id,
                            "listing": registry_id,
                        },
                        "schema_versions": {
                            "core": "public-listing-v4",
                            "listing": "public-listing-v4",
                        },
                    }
                ),
            ),
        )
        connection.execute(
            """INSERT INTO mcp_saas.public_dataset_records
               (dataset_id, source_record_id, record_type, canonical_attributes,
                semantic_context, source_metadata, source_url, content_hash,
                schema_version, observed_at)
               VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s)""",
            (
                dataset_id,
                record_id,
                before_record["record_type"],
                json.dumps(before_record["canonical_attributes"]),
                json.dumps(before_record["semantic_context"]),
                json.dumps(before_record["source_metadata"]),
                evidence_url,
                before_record["content_hash"],
                before_record["schema_version"],
                observed_at,
            ),
        )
    monkeypatch.setattr(
        api, "connect_public_catalog_ingest_db", lambda: psycopg.connect(database_url)
    )
    monkeypatch.setattr(api, "read_secret", lambda _path: "AITest Data API writer")
    api_server = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
    api_thread = threading.Thread(target=api_server.serve_forever, daemon=True)
    api_thread.start()
    token_file = tmp_path / "data-api-writer-token"
    token_file.write_text("AITest Data API writer", encoding="utf-8")
    repository = HttpPublicCatalogRegistrationRepository(
        "https://public-catalog.test",
        token_file,
        transport=LoopbackDataApiTransport(api_server),
    )
    core = create_core_server(
        None,
        operator_token="AITest Core operator",
        public_catalog_registration_repository=repository,
    )
    request = {
        "dataset_id": dataset_id,
        "source_record_id": record_id,
        "proposed_jurisdiction": jurisdiction,
        "evidence_url": evidence_url,
        "evidence_observed_at": observed_at,
    }
    try:
        monkeypatch.setattr(
            core_app,
            "get_http_request",
            lambda: SimpleNamespace(
                headers={"x-mcp-saas-operator-token": "AITest Core operator"}
            ),
        )
        async with MCPClient(core) as client:
            preview_call = await client.call_tool(
                "preview_public_catalog_record_jurisdiction", {"request": request}
            )
            preview = preview_call.data
            applied_call = await client.call_tool(
                "apply_public_catalog_record_jurisdiction",
                {
                    "request": request,
                    "expected_before_sha256": preview["before_sha256"],
                    "expected_proposed_sha256": preview["proposed_sha256"],
                    "transaction_id": transaction_id,
                    "approval_reference": (
                        "AITest approved record jurisdiction correction"
                    ),
                },
            )
        applied = applied_call.data
        assert preview["status"] == "preview_ready"
        assert preview["write_performed"] is False
        assert preview["changes"][0]["proposed_value"] == jurisdiction
        assert applied["status"] == "applied"
        assert applied["audit_readback_verified"] is True
        assert applied["readback"]["jurisdiction"] == jurisdiction
        assert applied["readback"]["criteria_status"] == "not_assessed"
        with psycopg.connect(database_url) as connection:
            record = connection.execute(
                """SELECT semantic_context->'jurisdiction',
                          canonical_attributes->>'core.geography.state_code'
                   FROM mcp_saas.public_dataset_records
                   WHERE dataset_id = %s AND source_record_id = %s""",
                (dataset_id, record_id),
            ).fetchone()
            connection.execute(
                """DELETE FROM mcp_saas.public_catalog_registration_events
                   WHERE transaction_id = %s""",
                (transaction_id,),
            )
            connection.execute(
                "DELETE FROM mcp_saas.public_datasets WHERE dataset_id = %s",
                (dataset_id,),
            )
            connection.execute(
                "DELETE FROM mcp_saas.schema_registry WHERE registry_id = %s",
                (registry_id,),
            )
        assert record == (jurisdiction, "MP")
    finally:
        api_server.shutdown()
        api_server.server_close()
        api_thread.join(timeout=5)
        repository._client.close()
        with psycopg.connect(database_url) as connection:
            connection.execute(
                "DELETE FROM mcp_saas.public_datasets WHERE dataset_id = %s",
                (dataset_id,),
            )
            connection.execute(
                "DELETE FROM mcp_saas.schema_registry WHERE registry_id = %s",
                (registry_id,),
            )
