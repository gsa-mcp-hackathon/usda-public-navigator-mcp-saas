from __future__ import annotations

import hashlib
import importlib.util
import secrets
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest

API_DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIRECTORY))
API_SPEC = importlib.util.spec_from_file_location(
    "profile_resume_api", API_DIRECTORY / "api.py"
)
assert API_SPEC is not None and API_SPEC.loader is not None
api = importlib.util.module_from_spec(API_SPEC)
API_SPEC.loader.exec_module(api)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _seed_profile(database_url: str) -> tuple[str, str, str]:
    suffix = uuid.uuid4().hex[:20]
    profile_id = f"synthetic-{suffix}"
    profile_ref = secrets.token_urlsafe(32)
    scope = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
    purpose = "core-schema-registry@core-v1"
    with psycopg.connect(database_url) as connection:
        connection.execute(
            """INSERT INTO mcp_saas.synthetic_profiles
               (profile_id, profile_kind, schema_registry_id, schema_version,
                provenance, created_at, revision)
               VALUES (%s, 'person', 'core-schema-registry', 'core-v1',
                       '{"record_type":"synthetic"}'::jsonb, now(), 1)""",
            (profile_id,),
        )
        connection.execute(
            """INSERT INTO mcp_saas.profile_access_references
               (reference_sha256, profile_id, scope_sha256, purpose_id)
               VALUES (%s, %s, %s, %s)""",
            (_digest(profile_ref), profile_id, scope, purpose),
        )
    return profile_id, profile_ref, scope


def _delete_profile(database_url: str, profile_id: str) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute(
            "DELETE FROM mcp_saas.synthetic_profiles WHERE profile_id = %s",
            (profile_id,),
        )


def test_resume_claim_rotates_once_and_revocation_and_expiry_fail_closed(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "connect_db", lambda: psycopg.connect(database_url))
    profile_id, profile_ref, first_scope = _seed_profile(database_url)
    try:
        created = api.create_profile_resume_claim(profile_ref, first_scope)
        assert created["status"] == "available"
        first_claim = created["resume_claim"]
        assert isinstance(first_claim, str)

        with psycopg.connect(database_url) as connection:
            stored = connection.execute(
                """SELECT claim_sha256, profile_id, purpose_id, expires_at,
                          consumed_at, revoked_at
                   FROM mcp_saas.profile_resume_claims WHERE claim_sha256 = %s""",
                (api._profile_resume_claim_digest(first_claim),),
            ).fetchone()
        assert stored is not None
        assert stored[0].strip() == api._profile_resume_claim_digest(first_claim)
        assert stored[1] == profile_id
        assert stored[2] == "core-schema-registry@core-v1"
        assert stored[3] is not None
        assert stored[4] is None and stored[5] is None

        second_scope = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
        resumed = api.resume_synthetic_profile(first_claim, second_scope)
        assert resumed["status"] == "available"
        assert resumed["revision"] == 1
        assert resumed["profile_ref"] != profile_ref
        assert resumed["resume_claim"] != first_claim
        readback = api.read_scoped_synthetic_profile(
            resumed["profile_ref"], second_scope
        )
        assert readback["status"] == "available"
        assert readback["revision"] == 1
        assert api.resume_synthetic_profile(first_claim, second_scope) == {
            "status": "profile_resume_unavailable"
        }

        third_scope = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
        third_session = api.resume_synthetic_profile(
            resumed["resume_claim"], third_scope
        )
        assert third_session["status"] == "available"
        assert api.revoke_profile_resume_claims(
            third_session["profile_ref"], third_scope
        ) == {"status": "available", "resume_claims_revoked": True}
        assert api.resume_synthetic_profile(
            third_session["resume_claim"],
            hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        ) == {"status": "profile_resume_unavailable"}

        fourth_scope = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
        fourth_ref = third_session["profile_ref"]
        fourth_claim = api.create_profile_resume_claim(fourth_ref, third_scope)
        expired_digest = api._profile_resume_claim_digest(fourth_claim["resume_claim"])
        with psycopg.connect(database_url) as connection:
            connection.execute(
                """UPDATE mcp_saas.profile_resume_claims
                   SET expires_at = now() - interval '1 second'
                   WHERE claim_sha256 = %s""",
                (expired_digest,),
            )
        assert api.resume_synthetic_profile(
            fourth_claim["resume_claim"], fourth_scope
        ) == {"status": "profile_resume_unavailable"}
        assert api.resume_synthetic_profile("malformed", fourth_scope) == {
            "status": "profile_resume_unavailable"
        }
    finally:
        _delete_profile(database_url, profile_id)


def test_concurrent_claim_replay_has_exactly_one_success(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "connect_db", lambda: psycopg.connect(database_url))
    profile_id, profile_ref, first_scope = _seed_profile(database_url)
    try:
        created = api.create_profile_resume_claim(profile_ref, first_scope)
        claim = created["resume_claim"]

        def attempt(_: int) -> dict[str, object]:
            scope = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
            return api.resume_synthetic_profile(claim, scope)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        assert sum(result.get("status") == "available" for result in results) == 1
        assert (
            sum(
                result.get("status") == "profile_resume_unavailable"
                for result in results
            )
            == 1
        )
    finally:
        _delete_profile(database_url, profile_id)
