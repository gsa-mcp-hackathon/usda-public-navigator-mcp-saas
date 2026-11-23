"""Provider-neutral MCP client for the synthetic Profile Service contract."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from example_server.persistence_api_client import persistence_api_headers

_PROFILE_REFERENCE = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_PROFILE_RESUME_CLAIM = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_PROFILE_SCOPE_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ProfileFieldWrite(BaseModel):
    """One typed field value with optional active-schema derivation reference."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    attribute_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+$")
    value: object
    derivation: ProfileDerivationReference | None = None


class ProfileDerivationReference(BaseModel):
    """Identity of a registered method; the active schema owns its definition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    method_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    method_version: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")


class ProfileWriteRequest(BaseModel):
    """Confirmed synthetic profile values and the exact active schema identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str | None = Field(
        default=None,
        pattern=r"^(?:demo|synthetic)-[a-z0-9][a-z0-9-]{0,62}$",
    )
    profile_kind: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    schema_registry_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    schema_version: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    attributes: list[ProfileFieldWrite] = Field(min_length=1, max_length=64)
    command: EvaluationFixtureCommand | None = None
    confirmed: Literal[True]


class SavedProgramInterestRequest(BaseModel):
    """Confirmed private link to a selected source-backed candidate snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lead_id: str = Field(pattern=r"^[0-9a-f]{24}$")
    candidate_ref: str = Field(pattern=r"^candidate-ref-[0-9a-f]{32}$")
    candidate_id: str = Field(pattern=r"^candidate-[0-9a-f]{32}$")
    candidate_identity_basis: Literal[
        "publisher_program_number", "canonical_url", "source_record_id"
    ]
    source_id: str = Field(pattern=r"^[a-z][a-z0-9._-]{0,127}$")
    source_record_id: str = Field(min_length=1, max_length=200)
    source_version: str = Field(min_length=1, max_length=120)
    source_content_sha256: str | None = Field(
        default=None, pattern=r"^[a-fA-F0-9]{64}$"
    )
    source_entry_id: str = Field(pattern=r"^[a-z][a-z0-9._-]{0,127}$")
    canonical_source_url: str = Field(min_length=9, max_length=2048)
    policy_id: str = Field(pattern=r"^[a-z][a-z0-9.-]{0,127}$")
    policy_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    retrieved_at_utc: str = Field(min_length=20, max_length=40)
    selected: bool = Field(strict=True)
    expected_revision: int = Field(ge=1, strict=True)
    confirmed: Literal[True]


class EvaluationFixtureCommand(BaseModel):
    """Explicit create-only command for registering synthetic evaluation data."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: Literal["register_evaluation_fixture"]
    fixture_set_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    fixture_set_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    fixture_id: str = Field(pattern=r"^fixture-[a-z0-9]{4,32}$")
    use_case_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,127}$")


class ProfileServiceUnavailable(RuntimeError):
    """The Persistence Access API could not serve a synthetic profile request."""


class ProfileServiceRepository:
    """Read and write synthetic profiles through separately scoped API tokens."""

    def __init__(
        self,
        api_url: str,
        read_token_file: Path,
        write_token_file: Path,
        *,
        timeout_seconds: float = 3.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(api_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Persistence API URL must be an HTTPS origin.")
        self._base_url = api_url.rstrip("/")
        self._read_client = httpx.Client(
            headers=persistence_api_headers(self._read_token(read_token_file)),
            timeout=timeout_seconds,
            transport=transport,
        )
        self._write_client = httpx.Client(
            headers=persistence_api_headers(self._read_token(write_token_file)),
            timeout=timeout_seconds,
            transport=transport,
        )

    @staticmethod
    def _read_token(path: Path) -> str:
        try:
            token = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError("Profile API credential is unavailable.") from exc
        if not token:
            raise ValueError("Profile API credential is empty.")
        return token

    def read_profile(
        self,
        profile_ref: str,
        profile_scope_sha256: str,
        correlation_id: str | None = None,
    ) -> dict[str, object]:
        """Read a profile through its opaque, session-scoped reference."""
        if not _PROFILE_REFERENCE.fullmatch(profile_ref):
            return {"status": "profile_not_found_in_scope"}
        if not _PROFILE_SCOPE_SHA256.fullmatch(profile_scope_sha256):
            return {"status": "profile_scope_required"}
        try:
            response = self._read_client.get(
                f"{self._base_url}/v1/profile-sessions/current",
                headers={
                    "X-Profile-Reference": profile_ref,
                    "X-Profile-Scope": profile_scope_sha256,
                    **(
                        {"X-MCP-Correlation-Id": correlation_id}
                        if correlation_id
                        else {}
                    ),
                },
            )
            if response.status_code in {401, 403, 404, 410}:
                result = response.json()
                if isinstance(result, dict):
                    return result
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("status") != "available":
                raise ValueError("Profile response has an invalid structure.")
            return payload
        except (httpx.HTTPError, ValueError) as exc:
            raise ProfileServiceUnavailable("Profile read is unavailable.") from exc

    def list_evaluation_fixtures(
        self, fixture_set_id: str, fixture_set_version: str
    ) -> dict[str, object]:
        """List opaque fixtures from the explicit synthetic evaluation allowlist."""
        path = (
            f"{self._base_url}/v1/evaluation/fixture-sets/"
            f"{quote(fixture_set_id, safe='')}/"
            f"{quote(fixture_set_version, safe='')}/fixtures"
        )
        return self._evaluation_fixture_get(path)

    def read_evaluation_fixture(
        self, fixture_set_id: str, fixture_set_version: str, fixture_id: str
    ) -> dict[str, object]:
        """Read only an explicitly allowlisted fixture by opaque fixture ID."""
        path = (
            f"{self._base_url}/v1/evaluation/fixture-sets/"
            f"{quote(fixture_set_id, safe='')}/{quote(fixture_set_version, safe='')}"
            f"/fixtures/{quote(fixture_id, safe='')}"
        )
        return self._evaluation_fixture_get(path)

    def _evaluation_fixture_get(self, path: str) -> dict[str, object]:
        try:
            response = self._read_client.get(path)
            if response.status_code in {401, 403, 404}:
                result = response.json()
                if isinstance(result, dict):
                    return result
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or result.get("status") != "available":
                raise ValueError("Fixture response has an invalid structure.")
            return result
        except (httpx.HTTPError, ValueError) as exc:
            raise ProfileServiceUnavailable("Fixture read is unavailable.") from exc

    def read_profile_schema(
        self, registry_id: str, schema_version: str
    ) -> dict[str, object]:
        """Read one active profile schema through the fixed Persistence API route."""
        try:
            response = self._read_client.get(
                f"{self._base_url}/v1/profile-schemas/"
                f"{quote(registry_id, safe='')}/{quote(schema_version, safe='')}"
            )
            if response.status_code == 404:
                return {"status": "schema_not_found"}
            response.raise_for_status()
            payload = response.json()
            if (
                not isinstance(payload, dict)
                or payload.get("registry_id") != registry_id
                or payload.get("schema_version") != schema_version
                or not isinstance(payload.get("attributes"), dict)
            ):
                raise ValueError("Profile schema response is invalid.")
            return {"status": "available", **payload}
        except (httpx.HTTPError, ValueError) as exc:
            raise ProfileServiceUnavailable("Profile schema is unavailable.") from exc

    def create_profile(
        self,
        request: ProfileWriteRequest,
        profile_scope_sha256: str | None = None,
    ) -> dict[str, object]:
        """Create a synthetic profile; ordinary profiles receive an opaque ref."""
        headers: dict[str, str] = {}
        if request.command is None:
            if not profile_scope_sha256 or not _PROFILE_SCOPE_SHA256.fullmatch(
                profile_scope_sha256
            ):
                return {"status": "profile_scope_required"}
            headers["X-Profile-Scope"] = profile_scope_sha256
        return self._write(
            "post",
            request.model_dump(mode="json", exclude_none=True),
            headers=headers,
        )

    def update_profile(
        self,
        request: ProfileWriteRequest,
        profile_ref: str,
        profile_scope_sha256: str,
        expected_revision: int,
    ) -> dict[str, object]:
        """Correct one profile only through its matching session reference."""
        if expected_revision < 1:
            raise ValueError("Expected profile revision must be positive.")
        if not _PROFILE_REFERENCE.fullmatch(
            profile_ref
        ) or not _PROFILE_SCOPE_SHA256.fullmatch(profile_scope_sha256):
            return {"status": "profile_not_found_in_scope"}
        payload = request.model_dump(mode="json", exclude_none=True)
        payload["expected_revision"] = expected_revision
        return self._write(
            "revision",
            payload,
            headers={
                "X-Profile-Reference": profile_ref,
                "X-Profile-Scope": profile_scope_sha256,
            },
        )

    def save_program_interest(
        self,
        request: SavedProgramInterestRequest,
        profile_ref: str,
        profile_scope_sha256: str,
    ) -> dict[str, object]:
        """Save one lead only under the matching live profile reference and scope."""
        if not _PROFILE_REFERENCE.fullmatch(
            profile_ref
        ) or not _PROFILE_SCOPE_SHA256.fullmatch(profile_scope_sha256):
            return {"status": "profile_not_found_in_scope"}
        try:
            response = self._write_client.post(
                f"{self._base_url}/v1/profile-sessions/current/interests",
                json=request.model_dump(mode="json"),
                headers={
                    "X-Profile-Reference": profile_ref,
                    "X-Profile-Scope": profile_scope_sha256,
                },
            )
            if response.status_code in {400, 401, 403, 404, 409, 410, 422}:
                result = response.json()
                if isinstance(result, dict):
                    return result
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or result.get("status") != "available":
                raise ValueError("Saved-interest response has an invalid structure.")
            return result
        except (httpx.HTTPError, ValueError) as exc:
            raise ProfileServiceUnavailable(
                "Profile interest write is unavailable."
            ) from exc

    def create_resume_claim(
        self, profile_ref: str, profile_scope_sha256: str
    ) -> dict[str, object]:
        """Issue a one-time cross-session claim for the current synthetic profile."""
        if not _PROFILE_REFERENCE.fullmatch(
            profile_ref
        ) or not _PROFILE_SCOPE_SHA256.fullmatch(profile_scope_sha256):
            return {"status": "profile_not_found_in_scope"}
        try:
            response = self._write_client.post(
                f"{self._base_url}/v1/profile-resume-claims",
                json={"confirmed": True},
                headers={
                    "X-Profile-Reference": profile_ref,
                    "X-Profile-Scope": profile_scope_sha256,
                },
            )
            if response.status_code in {400, 401, 403, 404, 410, 422}:
                result = response.json()
                if isinstance(result, dict):
                    return result
            response.raise_for_status()
            result = response.json()
            if (
                not isinstance(result, dict)
                or result.get("status") != "available"
                or not isinstance(result.get("resume_claim"), str)
                or not _PROFILE_RESUME_CLAIM.fullmatch(result["resume_claim"])
                or not isinstance(result.get("expires_at"), str)
            ):
                raise ValueError("Resume-claim response is invalid.")
            return result
        except (httpx.HTTPError, ValueError) as exc:
            raise ProfileServiceUnavailable(
                "Profile resume-claim creation is unavailable."
            ) from exc

    def resume_profile(
        self, resume_claim: str, profile_scope_sha256: str
    ) -> dict[str, object]:
        """Exchange a one-time claim for a new session ref and rotated claim."""
        if not _PROFILE_RESUME_CLAIM.fullmatch(
            resume_claim
        ) or not _PROFILE_SCOPE_SHA256.fullmatch(profile_scope_sha256):
            return {"status": "profile_resume_unavailable"}
        try:
            response = self._write_client.post(
                f"{self._base_url}/v1/profile-sessions/resume",
                json={"resume_claim": resume_claim, "confirmed": True},
                headers={"X-Profile-Scope": profile_scope_sha256},
            )
            if response.status_code in {400, 401, 403, 404, 409, 410, 422}:
                result = response.json()
                if isinstance(result, dict):
                    return result
            response.raise_for_status()
            result = response.json()
            if (
                not isinstance(result, dict)
                or result.get("status") != "available"
                or not isinstance(result.get("profile_ref"), str)
                or not _PROFILE_REFERENCE.fullmatch(result["profile_ref"])
                or not isinstance(result.get("resume_claim"), str)
                or not _PROFILE_RESUME_CLAIM.fullmatch(result["resume_claim"])
                or not isinstance(result.get("revision"), int)
                or isinstance(result.get("revision"), bool)
            ):
                raise ValueError("Profile-resume response is invalid.")
            return result
        except (httpx.HTTPError, ValueError) as exc:
            raise ProfileServiceUnavailable("Profile resume is unavailable.") from exc

    def revoke_resume_claims(
        self, profile_ref: str, profile_scope_sha256: str
    ) -> dict[str, object]:
        """Revoke active claims through the current authorized profile reference."""
        if not _PROFILE_REFERENCE.fullmatch(
            profile_ref
        ) or not _PROFILE_SCOPE_SHA256.fullmatch(profile_scope_sha256):
            return {"status": "profile_not_found_in_scope"}
        try:
            response = self._write_client.post(
                f"{self._base_url}/v1/profile-sessions/current/resume-claims/revoke",
                json={"confirmed": True},
                headers={
                    "X-Profile-Reference": profile_ref,
                    "X-Profile-Scope": profile_scope_sha256,
                },
            )
            if response.status_code in {400, 401, 403, 404, 410, 422}:
                result = response.json()
                if isinstance(result, dict):
                    return result
            response.raise_for_status()
            result = response.json()
            if (
                not isinstance(result, dict)
                or result.get("status") != "available"
                or result.get("resume_claims_revoked") is not True
            ):
                raise ValueError("Resume-claim revocation response is invalid.")
            return result
        except (httpx.HTTPError, ValueError) as exc:
            raise ProfileServiceUnavailable(
                "Profile resume-claim revocation is unavailable."
            ) from exc

    def _write(
        self,
        method: Literal["post", "revision"],
        payload: dict[str, object],
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        try:
            if method == "post":
                response = self._write_client.post(
                    f"{self._base_url}/v1/profiles", json=payload, headers=headers
                )
            else:
                response = self._write_client.post(
                    f"{self._base_url}/v1/profile-sessions/current/revisions",
                    json=payload,
                    headers=headers,
                )
            if response.status_code in {400, 401, 403, 404, 409, 410, 422}:
                result = response.json()
                if isinstance(result, dict):
                    return result
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("Profile write response has an invalid structure.")
            return result
        except (httpx.HTTPError, ValueError) as exc:
            raise ProfileServiceUnavailable("Profile write is unavailable.") from exc
