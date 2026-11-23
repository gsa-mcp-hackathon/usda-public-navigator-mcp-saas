"""Operator-gated source-policy candidate review through the Persistence API."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import quote, urlsplit

import httpx

from example_server.persistence_api_client import persistence_api_headers

_MANIFEST_ID = re.compile(r"^[a-z][a-z0-9.-]{0,127}$")
_PROGRAM_CANDIDATE_ID = re.compile(r"^candidate-[0-9a-f]{32}$")
_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OPERATOR_AUDIT_FIELDS = {
    "mcp_session_sha256",
    "mcp_request_sha256",
    "mcp_client_identity_status",
    "operator_credential_label",
}
_PROGRAM_CANDIDATE_PUBLICATION_CONFLICT_CODES = {
    "candidate_digest_or_approval_mismatch",
    "candidate_source_dataset_mismatch",
    "target_dataset_not_publishable",
    "source_policy_not_active_or_approved",
    "existing_record_content_mismatch",
    "publication_readback_mismatch",
    "publication_state_conflict",
}


def _valid_publication_reference(value: object) -> bool:
    if value is None:
        return True
    return (
        isinstance(value, dict)
        and set(value) == {"candidate_id", "content_sha256"}
        and isinstance(value.get("candidate_id"), str)
        and _PROGRAM_CANDIDATE_ID.fullmatch(value["candidate_id"]) is not None
        and isinstance(value.get("content_sha256"), str)
        and _SHA256.fullmatch(value["content_sha256"]) is not None
    )


def _validate_operator_audit_context(
    value: object,
) -> dict[str, str | None] | None:
    """Keep transport correlation hashes and the credential label tightly bounded."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != _OPERATOR_AUDIT_FIELDS:
        raise ValueError("Operator audit context is invalid.")
    session_hash = value["mcp_session_sha256"]
    request_hash = value["mcp_request_sha256"]
    client_identity_status = value["mcp_client_identity_status"]
    label = value["operator_credential_label"]
    if (
        any(
            item is not None
            and (not isinstance(item, str) or _SHA256.fullmatch(item) is None)
            for item in (session_hash, request_hash)
        )
        or client_identity_status != "unavailable"
        or label != "core_operator_token"
    ):
        raise ValueError("Operator audit context is invalid.")
    return {
        "mcp_session_sha256": session_hash,
        "mcp_request_sha256": request_hash,
        "mcp_client_identity_status": client_identity_status,
        "operator_credential_label": label,
    }


class SourcePolicyReviewUnavailable(RuntimeError):
    """The Persistence API could not complete a source-policy review."""


class SourcePolicyReviewConflict(RuntimeError):
    """The candidate already has a different immutable review decision."""


class SourcePolicyCandidateNotFound(RuntimeError):
    """The requested candidate is not present in the registry."""


class InvalidSourcePolicyReview(ValueError):
    """The exact candidate identity or review decision is invalid."""


class SourcePolicyLifecycleUnavailable(RuntimeError):
    """The Persistence API could not complete source-policy lifecycle work."""


class SourcePolicyLifecycleConflict(RuntimeError):
    """The active source-policy state changed or conflicts with the request."""


class SourcePolicyLifecycleNotFound(RuntimeError):
    """The requested source-policy version is not registered."""


class InvalidSourcePolicyLifecycle(ValueError):
    """The source-policy lifecycle request is malformed."""


class ProgramCandidateRegistrationUnavailable(RuntimeError):
    """The Persistence API could not register or read back a program candidate."""


class ProgramCandidateReadUnavailable(RuntimeError):
    """The Persistence API could not read a persisted program candidate."""


class ProgramCandidateNotFound(LookupError):
    """The requested persisted program candidate does not exist."""


class InvalidProgramCandidateRead(ValueError):
    """The requested program candidate identity is invalid."""


class ProgramCandidateRegistrationConflict(RuntimeError):
    """A candidate identity is already present with different immutable content."""


class InvalidProgramCandidateRegistration(ValueError):
    """The submitted normalized program candidate violates its contract."""


class ProgramCandidateDecisionUnavailable(RuntimeError):
    """The Persistence API could not complete candidate review or readback."""


class ProgramCandidateDecisionConflict(RuntimeError):
    """The candidate has a different immutable decision or digest."""


class ProgramCandidateDecisionNotFound(LookupError):
    """The program candidate is not registered."""


class InvalidProgramCandidateDecision(ValueError):
    """The candidate identity, digest, or review decision is malformed."""


class ProgramCandidatePublicationUnavailable(RuntimeError):
    """The Persistence API could not publish or read back a program candidate."""


class ProgramCandidatePublicationConflict(RuntimeError):
    """The candidate publication or activation pointer conflicts with current state."""

    def __init__(
        self,
        message: str = "Publication state conflicts with current data.",
        *,
        reason_code: str = "publication_state_conflict",
    ) -> None:
        self.reason_code = (
            reason_code
            if reason_code in _PROGRAM_CANDIDATE_PUBLICATION_CONFLICT_CODES
            else "publication_state_conflict"
        )
        super().__init__(message)


class ProgramCandidatePublicationNotFound(LookupError):
    """The program candidate publication is not registered."""


class InvalidProgramCandidatePublication(ValueError):
    """The candidate publication request or lifecycle command is malformed."""


class PublicCatalogRegistrationUnavailable(RuntimeError):
    """The Persistence API could not complete a governed catalog operation."""


class PublicCatalogRegistrationConflict(RuntimeError):
    """The catalog changed after preview or the transaction ID was reused."""


class InvalidPublicCatalogRegistration(ValueError):
    """The typed source/dataset registration or audit envelope is invalid."""


class PublicCatalogRegistrationRepository(Protocol):
    """Preview and apply operator-approved shared catalog metadata changes."""

    def preview_registration(
        self, registration: dict[str, object]
    ) -> dict[str, object]:
        """Return before and after digests for a registration without applying it."""

    def apply_registration(
        self,
        registration: dict[str, object],
        expected_before_sha256: str,
        expected_proposed_sha256: str,
        transaction_id: str,
        approval_reference: str,
        operator_audit_context: dict[str, str | None],
    ) -> dict[str, object]:
        """Apply a previewed registration if its digests still match."""

    def preview_record_jurisdiction(
        self, request: dict[str, object]
    ) -> dict[str, object]:
        """Return before and after digests for a jurisdiction change, unapplied."""

    def apply_record_jurisdiction(
        self,
        request: dict[str, object],
        expected_before_sha256: str,
        expected_proposed_sha256: str,
        transaction_id: str,
        approval_reference: str,
        operator_audit_context: dict[str, str | None],
    ) -> dict[str, object]:
        """Apply a previewed jurisdiction change if its digests still match."""


class SourcePolicyReviewRepository(Protocol):
    """Record and verify one immutable decision for a source-policy candidate."""

    def register_candidate(self, manifest: dict[str, object]) -> dict[str, object]:
        """Register a source-policy candidate manifest for review."""

    def register_program_candidate(
        self,
        candidate: dict[str, object],
        *,
        operator_audit_context: dict[str, str | None],
    ) -> dict[str, object]:
        """Register a discovered program lead as a pending review record."""

    def read_program_candidate(self, candidate_id: str) -> dict[str, object]:
        """Read a persisted program candidate for review."""

    def decide_program_candidate(
        self,
        candidate_id: str,
        expected_content_sha256: str,
        decision: Literal["approved", "rejected"],
        rationale: str,
        transaction_id: str,
        approval_reference: str,
        *,
        operator_audit_context: dict[str, str | None] | None = None,
    ) -> dict[str, object]:
        """Record one append-only approve or reject decision, bound to the digest."""

    def publish_program_candidate(
        self, candidate_id: str, expected_content_sha256: str, dataset_id: str
    ) -> dict[str, object]:
        """Publish an approved candidate to the shared inventory under a dataset id."""

    def read_program_candidate_publication(
        self, publication_key: str
    ) -> dict[str, object]:
        """Read a candidate publication record by its key."""

    def manage_program_candidate_publication(
        self,
        publication_key: str,
        request_id: str,
        action: Literal["activate", "deactivate", "rollback"],
        expected_current: dict[str, str] | None,
        target: dict[str, str] | None,
        rationale: str,
        *,
        operator_audit_context: dict[str, str | None] | None = None,
    ) -> dict[str, object]:
        """Activate, deactivate or roll back a candidate publication."""

    def decide(
        self,
        manifest_id: str,
        manifest_version: str,
        expected_content_sha256: str,
        decision: Literal["approved", "rejected"],
        rationale: str,
        *,
        operator_audit_context: dict[str, str | None] | None = None,
    ) -> dict[str, object]:
        """Record one append-only decision for a manifest, bound to its digest."""

    def read_active(self) -> dict[str, object]:
        """Read the currently active source-policy bundle."""

    def apply_lifecycle(
        self,
        request_id: str,
        action: Literal["activate", "deactivate", "rollback"],
        expected_current: dict[str, str] | None,
        target: dict[str, str] | None,
        rationale: str,
        *,
        operator_audit_context: dict[str, str | None] | None = None,
    ) -> dict[str, object]:
        """Activate, deactivate or roll back the active source-policy bundle."""


class HttpSourcePolicyReviewRepository:
    """Use separate API reader and registry-writer credentials kept server-side."""

    def __init__(
        self,
        api_url: str,
        read_token_file: Path,
        write_token_file: Path | None = None,
        *,
        timeout_seconds: float = 5.0,
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
        self._read_client = self._client(
            api_url, read_token_file, timeout_seconds, transport
        )
        self._write_client = (
            self._client(api_url, write_token_file, timeout_seconds, transport)
            if write_token_file is not None
            else None
        )

    @staticmethod
    def _client(
        api_url: str,
        token_file: Path,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None,
    ) -> httpx.Client:
        try:
            token = token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError("Persistence API credential is unavailable.") from exc
        if not token:
            raise ValueError("Persistence API credential is empty.")
        return httpx.Client(
            base_url=api_url.rstrip("/"),
            headers=persistence_api_headers(token),
            timeout=timeout_seconds,
            transport=transport,
        )

    def decide(
        self,
        manifest_id: str,
        manifest_version: str,
        expected_content_sha256: str,
        decision: Literal["approved", "rejected"],
        rationale: str,
        *,
        operator_audit_context: dict[str, str | None] | None = None,
    ) -> dict[str, object]:
        """Append an exact-digest decision, then verify it through readback."""
        _validate_request(
            manifest_id,
            manifest_version,
            expected_content_sha256,
            decision,
            rationale,
        )
        if self._write_client is None:
            raise SourcePolicyReviewUnavailable("Source-policy review is disabled.")
        try:
            validated_audit = _validate_operator_audit_context(operator_audit_context)
        except ValueError as exc:
            raise InvalidSourcePolicyReview(
                "Decision audit context is invalid."
            ) from exc

        base_path = (
            "/v1/registry/source-policy-manifests/"
            f"{quote(manifest_id, safe='')}/{quote(manifest_version, safe='')}"
        )
        try:
            response = self._write_client.post(
                f"{base_path}/decision",
                json={
                    "expected_content_sha256": expected_content_sha256,
                    "decision": decision,
                    "rationale": rationale,
                    **(
                        {"operator_audit_context": validated_audit}
                        if validated_audit is not None
                        else {}
                    ),
                },
            )
        except httpx.HTTPError as exc:
            raise SourcePolicyReviewUnavailable(
                "Source-policy review is unavailable."
            ) from exc
        if response.status_code == httpx.codes.NOT_FOUND:
            raise SourcePolicyCandidateNotFound(
                "Source-policy candidate was not found."
            )
        if response.status_code == httpx.codes.CONFLICT:
            raise SourcePolicyReviewConflict(
                "Candidate has a different review decision."
            )
        if response.status_code == httpx.codes.BAD_REQUEST:
            raise InvalidSourcePolicyReview("Source-policy review was rejected.")
        try:
            response.raise_for_status()
            submitted = response.json()
            if not isinstance(submitted, dict):
                raise ValueError
            readback_response = self._read_client.get(base_path)
            if readback_response.status_code == httpx.codes.NOT_FOUND:
                raise SourcePolicyCandidateNotFound(
                    "Source-policy candidate disappeared before readback."
                )
            readback_response.raise_for_status()
            candidate = readback_response.json()
            if not isinstance(candidate, dict):
                raise ValueError
        except SourcePolicyCandidateNotFound:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise SourcePolicyReviewUnavailable(
                "Source-policy decision readback is unavailable."
            ) from exc

        stored_decision = candidate.get("review_decision")
        expected_decision = {
            "content_sha256": expected_content_sha256,
            "decision": decision,
            "rationale": rationale,
        }
        if (
            candidate.get("manifest_id") != manifest_id
            or candidate.get("manifest_version") != manifest_version
            or candidate.get("content_sha256") != expected_content_sha256
            or not isinstance(stored_decision, dict)
            or any(
                stored_decision.get(key) != value
                for key, value in expected_decision.items()
            )
            or submitted.get("decision_id") != stored_decision.get("decision_id")
        ):
            raise SourcePolicyReviewUnavailable(
                "Source-policy decision readback did not match the requested digest."
            )
        if not submitted.get("idempotent_replay") and (
            stored_decision.get("operator_audit_context") != validated_audit
        ):
            raise SourcePolicyReviewUnavailable(
                "Source-policy decision audit readback did not match the request."
            )
        return {
            "status": (
                "idempotent_replay"
                if submitted.get("idempotent_replay")
                else "recorded"
            ),
            "manifest_id": manifest_id,
            "manifest_version": manifest_version,
            "content_sha256": expected_content_sha256,
            "review_decision": stored_decision,
        }

    def register_candidate(self, manifest: dict[str, object]) -> dict[str, object]:
        """Validate, register, and read back one immutable pending candidate."""
        if not isinstance(manifest, dict) or self._write_client is None:
            raise SourcePolicyReviewUnavailable(
                "Source-policy registration is disabled."
            )
        try:
            validation = self._read_client.post(
                "/v1/registry/source-policy-manifests/validate",
                json=manifest,
            )
            if validation.status_code == httpx.codes.BAD_REQUEST:
                raise InvalidSourcePolicyReview("Source-policy manifest is invalid.")
            validation.raise_for_status()
            validated = validation.json()
            canonical_manifest = validated.get("manifest")
            digest = validated.get("content_sha256")
            if (
                not isinstance(canonical_manifest, dict)
                or canonical_manifest != manifest
                or not isinstance(digest, str)
                or not _SHA256.fullmatch(digest)
            ):
                raise ValueError(
                    "Validated source-policy manifest readback is invalid."
                )
            response = self._write_client.post(
                "/v1/registry/source-policy-manifests",
                json={
                    "manifest": canonical_manifest,
                    "expected_content_sha256": digest,
                },
            )
            if response.status_code == httpx.codes.CONFLICT:
                raise SourcePolicyReviewConflict(
                    "Source-policy identity already has different content."
                )
            response.raise_for_status()
            submitted = response.json()
            if not isinstance(submitted, dict):
                raise ValueError("Source-policy registration response is invalid.")
            manifest_id = str(manifest.get("manifest_id", ""))
            manifest_version = str(manifest.get("manifest_version", ""))
            readback_response = self._read_client.get(
                "/v1/registry/source-policy-manifests/"
                f"{quote(manifest_id, safe='')}/{quote(manifest_version, safe='')}"
            )
            if readback_response.status_code == httpx.codes.NOT_FOUND:
                raise SourcePolicyCandidateNotFound(
                    "Registered source-policy manifest disappeared before readback."
                )
            readback_response.raise_for_status()
            readback = readback_response.json()
            if not isinstance(readback, dict):
                raise ValueError("Source-policy readback response is invalid.")
        except (
            InvalidSourcePolicyReview,
            SourcePolicyReviewConflict,
            SourcePolicyCandidateNotFound,
        ):
            raise
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
            raise SourcePolicyReviewUnavailable(
                "Source-policy registration or readback is unavailable."
            ) from exc
        if (
            submitted.get("content_sha256") != digest
            or readback.get("manifest_id") != manifest_id
            or readback.get("manifest_version") != manifest_version
            or readback.get("content_sha256") != digest
            or readback.get("manifest") != canonical_manifest
            or readback.get("review_status") != "pending_review"
            or readback.get("review_decision") is not None
        ):
            raise SourcePolicyReviewUnavailable(
                "Source-policy registration readback did not match the exact digest."
            )
        return {
            "status": "registered",
            "manifest_id": manifest_id,
            "manifest_version": manifest_version,
            "candidate_id": manifest.get("candidate_id"),
            "content_sha256": digest,
            "review_status": readback.get("review_status"),
            "review_decision": readback.get("review_decision"),
            "manifest": canonical_manifest,
        }

    def register_program_candidate(
        self,
        candidate: dict[str, object],
        *,
        operator_audit_context: dict[str, str | None],
    ) -> dict[str, object]:
        """Persist one unpublished candidate by exact digest and verify readback."""
        if not isinstance(candidate, dict) or self._write_client is None:
            raise ProgramCandidateRegistrationUnavailable(
                "Program candidate registration is disabled."
            )
        try:
            validation = self._read_client.post(
                "/v1/registry/program-candidates/validate", json=candidate
            )
            if validation.status_code == httpx.codes.BAD_REQUEST:
                raise InvalidProgramCandidateRegistration(
                    "Program candidate failed Persistence API validation."
                )
            validation.raise_for_status()
            validated = validation.json()
            canonical = validated.get("candidate")
            digest = validated.get("content_sha256")
            candidate_id = candidate.get("candidate_id")
            if (
                not isinstance(canonical, dict)
                or canonical != candidate
                or not isinstance(digest, str)
                or not _SHA256.fullmatch(digest)
                or validated.get("candidate_id") != candidate_id
            ):
                raise ValueError("Validated program candidate response is invalid.")

            response = self._write_client.post(
                "/v1/registry/program-candidates",
                json={
                    "candidate": canonical,
                    "expected_content_sha256": digest,
                    "operator_audit_context": operator_audit_context,
                },
            )
            if response.status_code == httpx.codes.CONFLICT:
                raise ProgramCandidateRegistrationConflict(
                    "Candidate identity already has different immutable content."
                )
            if response.status_code not in {
                httpx.codes.CREATED,
                httpx.codes.OK,
            }:
                response.raise_for_status()
                raise ProgramCandidateRegistrationUnavailable(
                    "Candidate registration returned an unexpected status."
                )
            created = response.status_code == httpx.codes.CREATED
            response.raise_for_status()
            submitted = response.json()
            readback_response = self._read_client.get(
                "/v1/registry/program-candidates/" + quote(str(candidate_id), safe="")
            )
            if readback_response.status_code == httpx.codes.NOT_FOUND:
                raise ProgramCandidateRegistrationUnavailable(
                    "Registered program candidate is missing on readback."
                )
            readback_response.raise_for_status()
            readback = readback_response.json()
        except (
            InvalidProgramCandidateRegistration,
            ProgramCandidateRegistrationConflict,
            ProgramCandidateRegistrationUnavailable,
        ):
            raise
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
            raise ProgramCandidateRegistrationUnavailable(
                "Program candidate registration or readback is unavailable."
            ) from exc

        if (
            not isinstance(submitted, dict)
            or not isinstance(readback, dict)
            or submitted.get("content_sha256") != digest
            or readback.get("candidate_id") != candidate_id
            or readback.get("content_sha256") != digest
            or readback.get("candidate") != canonical
            or readback.get("review_status") != "review_required"
            or readback.get("catalog_status") not in {"not_published", "published"}
            or submitted.get("registration_audit") != readback.get("registration_audit")
        ):
            raise ProgramCandidateRegistrationUnavailable(
                "Program candidate readback does not match the exact digest."
            )
        registration_audit = readback.get("registration_audit")
        if registration_audit is None:
            if created:
                raise ProgramCandidateRegistrationUnavailable(
                    "New candidate registration has no audit receipt."
                )
            return {
                "status": "legacy_record_without_registration_audit",
                "candidate_id": candidate_id,
                "content_sha256": digest,
                "review_status": "review_required",
                "catalog_status": readback["catalog_status"],
                "candidate": canonical,
            }
        if (
            not isinstance(registration_audit, dict)
            or registration_audit.get("action") != "register"
            or registration_audit.get("after_state_sha256") != digest
            or registration_audit.get("authorization_capability") != "registry_writer"
            or not isinstance(registration_audit.get("operator_audit_context"), dict)
            or created
            and registration_audit.get("operator_audit_context")
            != operator_audit_context
        ):
            raise ProgramCandidateRegistrationUnavailable(
                "Program candidate registration audit does not match its digest."
            )
        return {
            "status": "registered" if created else "idempotent_replay",
            "candidate_id": candidate_id,
            "content_sha256": digest,
            "review_status": "review_required",
            "catalog_status": readback["catalog_status"],
            "registration_audit": registration_audit,
            "candidate": canonical,
        }

    def read_program_candidate(self, candidate_id: str) -> dict[str, object]:
        """Read and verify one persisted immutable candidate and its review state."""
        if (
            not isinstance(candidate_id, str)
            or _PROGRAM_CANDIDATE_ID.fullmatch(candidate_id) is None
        ):
            raise InvalidProgramCandidateRead("Candidate identity is invalid.")
        try:
            response = self._read_client.get(
                "/v1/registry/program-candidates/" + quote(candidate_id, safe="")
            )
            if response.status_code == httpx.codes.NOT_FOUND:
                raise ProgramCandidateNotFound("Program candidate was not found.")
            response.raise_for_status()
            state = response.json()
            candidate = state.get("candidate") if isinstance(state, dict) else None
            digest = state.get("content_sha256") if isinstance(state, dict) else None
            expected_digest = (
                hashlib.sha256(
                    json.dumps(
                        candidate,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                if isinstance(candidate, dict)
                else None
            )
        except ProgramCandidateNotFound:
            raise
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
            raise ProgramCandidateReadUnavailable(
                "Program candidate read is unavailable."
            ) from exc
        if (
            not isinstance(state, dict)
            or state.get("status") != "available"
            or state.get("candidate_id") != candidate_id
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
            or digest != expected_digest
            or not isinstance(candidate, dict)
            or candidate.get("candidate_id") != candidate_id
            or state.get("review_status")
            not in {"review_required", "approved", "rejected"}
            or state.get("catalog_status") not in {"not_published", "published"}
            or not isinstance(state.get("publication_keys"), list)
            or not isinstance(state.get("created_at"), str)
        ):
            raise ProgramCandidateReadUnavailable(
                "Program candidate readback failed identity or digest validation."
            )
        return state

    def decide_program_candidate(
        self,
        candidate_id: str,
        expected_content_sha256: str,
        decision: Literal["approved", "rejected"],
        rationale: str,
        transaction_id: str,
        approval_reference: str,
        *,
        operator_audit_context: dict[str, str | None] | None = None,
    ) -> dict[str, object]:
        """Record and verify an immutable decision for one exact candidate digest."""
        normalized_rationale = rationale.strip() if isinstance(rationale, str) else ""
        normalized_approval_reference = (
            approval_reference.strip() if isinstance(approval_reference, str) else ""
        )
        try:
            normalized_transaction_id = str(uuid.UUID(transaction_id))
        except (AttributeError, TypeError, ValueError) as exc:
            raise InvalidProgramCandidateDecision(
                "Candidate review transaction ID is invalid."
            ) from exc
        if (
            not isinstance(candidate_id, str)
            or not _PROGRAM_CANDIDATE_ID.fullmatch(candidate_id)
            or not isinstance(expected_content_sha256, str)
            or not _SHA256.fullmatch(expected_content_sha256)
            or not isinstance(decision, str)
            or decision not in {"approved", "rejected"}
            or not normalized_rationale
            or len(normalized_rationale) > 2048
            or not normalized_approval_reference
            or len(normalized_approval_reference) > 512
            or any(
                ord(char) < 32 or ord(char) == 127
                for char in normalized_approval_reference
            )
        ):
            raise InvalidProgramCandidateDecision(
                "Candidate review identity or decision is invalid."
            )
        if self._write_client is None:
            raise ProgramCandidateDecisionUnavailable(
                "Program candidate review is disabled."
            )
        try:
            validated_audit = _validate_operator_audit_context(operator_audit_context)
        except ValueError as exc:
            raise InvalidProgramCandidateDecision(
                "Candidate review audit context is invalid."
            ) from exc
        base_path = f"/v1/registry/program-candidates/{quote(candidate_id, safe='')}"
        try:
            response = self._write_client.post(
                f"{base_path}/decision",
                json={
                    "expected_content_sha256": expected_content_sha256,
                    "decision": decision,
                    "rationale": normalized_rationale,
                    "transaction_id": normalized_transaction_id,
                    "approval_reference": normalized_approval_reference,
                    **(
                        {"operator_audit_context": validated_audit}
                        if validated_audit is not None
                        else {}
                    ),
                },
            )
            if response.status_code == httpx.codes.NOT_FOUND:
                raise ProgramCandidateDecisionNotFound(
                    "Program candidate was not found."
                )
            if response.status_code == httpx.codes.CONFLICT:
                raise ProgramCandidateDecisionConflict(
                    "Candidate digest or immutable decision conflicts."
                )
            if response.status_code == httpx.codes.BAD_REQUEST:
                raise InvalidProgramCandidateDecision(
                    "Program candidate review was rejected."
                )
            response.raise_for_status()
            submitted = response.json()
            readback_response = self._read_client.get(base_path)
            if readback_response.status_code == httpx.codes.NOT_FOUND:
                raise ProgramCandidateDecisionNotFound(
                    "Program candidate disappeared before readback."
                )
            readback_response.raise_for_status()
            readback = readback_response.json()
        except (
            ProgramCandidateDecisionNotFound,
            ProgramCandidateDecisionConflict,
            InvalidProgramCandidateDecision,
        ):
            raise
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
            raise ProgramCandidateDecisionUnavailable(
                "Program candidate review or readback is unavailable."
            ) from exc
        stored = readback.get("review_decision") if isinstance(readback, dict) else None
        if (
            not isinstance(submitted, dict)
            or not isinstance(readback, dict)
            or readback.get("candidate_id") != candidate_id
            or readback.get("content_sha256") != expected_content_sha256
            or readback.get("catalog_status") not in {"not_published", "published"}
            or not isinstance(stored, dict)
            or stored.get("content_sha256") != expected_content_sha256
            or stored.get("decision") != decision
            or stored.get("rationale") != normalized_rationale
            or stored.get("transaction_id") != normalized_transaction_id
            or stored.get("approval_reference") != normalized_approval_reference
            or submitted.get("decision_id") != stored.get("decision_id")
        ):
            raise ProgramCandidateDecisionUnavailable(
                "Program candidate decision readback did not match the exact digest."
            )
        try:
            stored_audit = _validate_operator_audit_context(
                stored.get("operator_audit_context")
            )
        except ValueError as exc:
            raise ProgramCandidateDecisionUnavailable(
                "Program candidate decision audit readback is invalid."
            ) from exc
        if not submitted.get("idempotent_replay") and stored_audit != validated_audit:
            raise ProgramCandidateDecisionUnavailable(
                "Program candidate decision audit readback did not match the request."
            )
        return {
            "status": (
                "idempotent_replay"
                if submitted.get("idempotent_replay")
                else "recorded"
            ),
            "candidate_id": candidate_id,
            "content_sha256": expected_content_sha256,
            "decision": decision,
            "review_decision": stored,
            "catalog_status": readback["catalog_status"],
        }

    def publish_program_candidate(
        self, candidate_id: str, expected_content_sha256: str, dataset_id: str
    ) -> dict[str, object]:
        """Publish one approved digest and verify candidate and pointer readback."""
        if (
            not isinstance(candidate_id, str)
            or not _PROGRAM_CANDIDATE_ID.fullmatch(candidate_id)
            or not isinstance(expected_content_sha256, str)
            or not _SHA256.fullmatch(expected_content_sha256)
            or not isinstance(dataset_id, str)
            or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", dataset_id)
        ):
            raise InvalidProgramCandidatePublication(
                "Candidate digest or target dataset is invalid."
            )
        if self._write_client is None:
            raise ProgramCandidatePublicationUnavailable(
                "Program candidate publication is disabled."
            )
        candidate_path = "/v1/registry/program-candidates/" + quote(
            candidate_id, safe=""
        )
        try:
            response = self._write_client.post(
                f"{candidate_path}/publish",
                json={
                    "expected_content_sha256": expected_content_sha256,
                    "dataset_id": dataset_id,
                },
            )
            if response.status_code == httpx.codes.NOT_FOUND:
                raise ProgramCandidatePublicationNotFound(
                    "Approved candidate or target dataset was not found."
                )
            if response.status_code == httpx.codes.CONFLICT:
                try:
                    conflict_body = response.json()
                except (ValueError, TypeError):
                    conflict_body = {}
                reason_code = (
                    conflict_body.get("reason_code")
                    if isinstance(conflict_body, dict)
                    else None
                )
                raise ProgramCandidatePublicationConflict(
                    reason_code=(
                        reason_code
                        if isinstance(reason_code, str)
                        else "publication_state_conflict"
                    )
                )
            if response.status_code == httpx.codes.BAD_REQUEST:
                raise InvalidProgramCandidatePublication(
                    "Candidate publication request was rejected."
                )
            response.raise_for_status()
            submitted = response.json()
            candidate_response = self._read_client.get(candidate_path)
            if candidate_response.status_code == httpx.codes.NOT_FOUND:
                raise ProgramCandidatePublicationNotFound(
                    "Published candidate is missing on readback."
                )
            candidate_response.raise_for_status()
            candidate_state = candidate_response.json()
            publication_key = (
                submitted.get("publication_key")
                if isinstance(submitted, dict)
                else None
            )
            if not isinstance(publication_key, str) or not _SHA256.fullmatch(
                publication_key
            ):
                raise ValueError("Publication key is missing from response.")
            publication_state = self.read_program_candidate_publication(publication_key)
        except (
            ProgramCandidatePublicationNotFound,
            ProgramCandidatePublicationConflict,
            InvalidProgramCandidatePublication,
            ProgramCandidatePublicationUnavailable,
        ):
            raise
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
            raise ProgramCandidatePublicationUnavailable(
                "Program candidate publication or readback is unavailable."
            ) from exc
        if (
            not isinstance(submitted, dict)
            or not isinstance(candidate_state, dict)
            or candidate_state.get("candidate_id") != candidate_id
            or candidate_state.get("content_sha256") != expected_content_sha256
            or candidate_state.get("review_status") != "approved"
            or candidate_state.get("catalog_status") != "published"
            or publication_state.get("publication_key") != publication_key
            or submitted.get("candidate_id") != candidate_id
            or submitted.get("content_sha256") != expected_content_sha256
            or submitted.get("dataset_id") != dataset_id
        ):
            raise ProgramCandidatePublicationUnavailable(
                "Publication readback does not match the approved candidate digest."
            )
        return {**submitted, "publication_state": publication_state}

    def read_program_candidate_publication(
        self, publication_key: str
    ) -> dict[str, object]:
        """Read the active pointer and immutable versions for one publication key."""
        if not isinstance(publication_key, str) or not _SHA256.fullmatch(
            publication_key
        ):
            raise InvalidProgramCandidatePublication("Publication key is invalid.")
        path = "/v1/registry/program-candidate-publications/" + quote(
            publication_key, safe=""
        )
        try:
            response = self._read_client.get(path)
            if response.status_code == httpx.codes.NOT_FOUND:
                raise ProgramCandidatePublicationNotFound(
                    "Program candidate publication was not found."
                )
            response.raise_for_status()
            state = response.json()
        except ProgramCandidatePublicationNotFound:
            raise
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
            raise ProgramCandidatePublicationUnavailable(
                "Program candidate publication read is unavailable."
            ) from exc
        if (
            not isinstance(state, dict)
            or state.get("publication_key") != publication_key
            or state.get("status") not in {"active", "inactive"}
            or not isinstance(state.get("versions"), list)
        ):
            raise ProgramCandidatePublicationUnavailable(
                "Program candidate publication readback is invalid."
            )
        return state

    def manage_program_candidate_publication(
        self,
        publication_key: str,
        request_id: str,
        action: Literal["activate", "deactivate", "rollback"],
        expected_current: dict[str, str] | None,
        target: dict[str, str] | None,
        rationale: str,
        *,
        operator_audit_context: dict[str, str | None] | None = None,
    ) -> dict[str, object]:
        """Append one compare-and-set lifecycle event and verify its active pointer."""
        if not isinstance(publication_key, str) or not _SHA256.fullmatch(
            publication_key
        ):
            raise InvalidProgramCandidatePublication("Publication key is invalid.")
        try:
            normalized_request_id = str(uuid.UUID(request_id))
        except (TypeError, ValueError, AttributeError) as exc:
            raise InvalidProgramCandidatePublication("Request ID is invalid.") from exc
        if (
            action not in {"activate", "deactivate", "rollback"}
            or not isinstance(rationale, str)
            or not rationale.strip()
            or len(rationale) > 2048
            or not _valid_publication_reference(expected_current)
            or not _valid_publication_reference(target)
            or (action == "deactivate" and target is not None)
            or (action in {"activate", "rollback"} and target is None)
        ):
            raise InvalidProgramCandidatePublication(
                "Publication lifecycle request is invalid."
            )
        if self._write_client is None:
            raise ProgramCandidatePublicationUnavailable(
                "Program candidate publication lifecycle is disabled."
            )
        path = (
            "/v1/registry/program-candidate-publications/"
            + quote(publication_key, safe="")
            + "/lifecycle"
        )
        payload = {
            "request_id": normalized_request_id,
            "action": action,
            "expected_current": expected_current,
            "target": target,
            "rationale": rationale.strip(),
        }
        try:
            validated_audit = _validate_operator_audit_context(operator_audit_context)
        except ValueError as exc:
            raise InvalidProgramCandidatePublication(
                "Publication lifecycle operator audit context is invalid."
            ) from exc
        if validated_audit is not None:
            payload["operator_audit_context"] = validated_audit
        try:
            response = self._write_client.post(path, json=payload)
            if response.status_code == httpx.codes.NOT_FOUND:
                raise ProgramCandidatePublicationNotFound(
                    "Publication or lifecycle target was not found."
                )
            if response.status_code == httpx.codes.CONFLICT:
                raise ProgramCandidatePublicationConflict(
                    "Publication lifecycle compare-and-set is stale or conflicts."
                )
            if response.status_code == httpx.codes.BAD_REQUEST:
                raise InvalidProgramCandidatePublication(
                    "Publication lifecycle request was rejected."
                )
            response.raise_for_status()
            submitted = response.json()
            readback = self.read_program_candidate_publication(publication_key)
        except (
            ProgramCandidatePublicationNotFound,
            ProgramCandidatePublicationConflict,
            InvalidProgramCandidatePublication,
            ProgramCandidatePublicationUnavailable,
        ):
            raise
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
            raise ProgramCandidatePublicationUnavailable(
                "Publication lifecycle or readback is unavailable."
            ) from exc
        event = submitted.get("event") if isinstance(submitted, dict) else None
        expected_active = target if action != "deactivate" else None
        if (
            not isinstance(submitted, dict)
            or submitted.get("status") not in {"recorded", "replayed"}
            or not isinstance(event, dict)
            or event.get("request_id") != normalized_request_id
            or event.get("publication_key") != publication_key
            or event.get("action") != action
            or event.get("expected_current") != expected_current
            or event.get("target") != target
            or submitted.get("active_state") != readback
            or (
                submitted.get("status") == "recorded"
                and (
                    readback.get("active_reference") != expected_active
                    or readback.get("last_event", {}).get("request_id")
                    != normalized_request_id
                )
            )
        ):
            raise ProgramCandidatePublicationUnavailable(
                "Publication lifecycle readback does not match the requested state."
            )
        return {"status": submitted["status"], "event": event, "active_state": readback}

    def read_active(self) -> dict[str, object]:
        """Return a compact active reference without exposing manifest contents."""
        try:
            response = self._read_client.get("/v1/registry/source-policy-active")
            response.raise_for_status()
            state = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourcePolicyLifecycleUnavailable(
                "Active source-policy state is unavailable."
            ) from exc
        if not isinstance(state, dict) or state.get("status") not in {
            "active",
            "inactive",
        }:
            raise SourcePolicyLifecycleUnavailable(
                "Active source-policy readback is invalid."
            )
        active_manifest = state.get("active_manifest")
        active_reference = None
        if state["status"] == "active":
            if not isinstance(active_manifest, dict):
                raise SourcePolicyLifecycleUnavailable(
                    "Active source-policy readback is invalid."
                )
            active_reference = _manifest_reference(active_manifest)
            review_decision = active_manifest.get("review_decision")
            if (
                active_reference is None
                or not isinstance(review_decision, dict)
                or review_decision.get("decision") != "approved"
                or review_decision.get("content_sha256")
                != active_reference["content_sha256"]
            ):
                raise SourcePolicyLifecycleUnavailable(
                    "Active source-policy digest or approval is invalid."
                )
        last_event = _safe_lifecycle_event(state.get("last_event"))
        return {
            "status": state["status"],
            "active_reference": active_reference,
            "last_event": last_event,
        }

    def apply_lifecycle(
        self,
        request_id: str,
        action: Literal["activate", "deactivate", "rollback"],
        expected_current: dict[str, str] | None,
        target: dict[str, str] | None,
        rationale: str,
        *,
        operator_audit_context: dict[str, str | None] | None = None,
    ) -> dict[str, object]:
        """Record a compare-and-set lifecycle event and verify active-state readback."""
        payload = _validate_lifecycle_request(
            request_id, action, expected_current, target, rationale
        )
        try:
            validated_audit = _validate_operator_audit_context(operator_audit_context)
        except ValueError as exc:
            raise InvalidSourcePolicyLifecycle(
                "Source-policy lifecycle operator audit context is invalid."
            ) from exc
        if validated_audit is not None:
            payload["operator_audit_context"] = validated_audit
        if self._write_client is None:
            raise SourcePolicyLifecycleUnavailable(
                "Source-policy lifecycle is disabled."
            )
        try:
            response = self._write_client.post(
                "/v1/registry/source-policy-activations", json=payload
            )
        except httpx.HTTPError as exc:
            raise SourcePolicyLifecycleUnavailable(
                "Source-policy lifecycle is unavailable."
            ) from exc
        if response.status_code == httpx.codes.NOT_FOUND:
            raise SourcePolicyLifecycleNotFound("Source-policy target was not found.")
        if response.status_code == httpx.codes.CONFLICT:
            raise SourcePolicyLifecycleConflict(
                "Source-policy lifecycle state conflicts with the request."
            )
        if response.status_code == httpx.codes.BAD_REQUEST:
            raise InvalidSourcePolicyLifecycle(
                "Source-policy lifecycle request was rejected."
            )
        try:
            response.raise_for_status()
            submitted = response.json()
            readback = self.read_active()
        except SourcePolicyLifecycleUnavailable:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise SourcePolicyLifecycleUnavailable(
                "Source-policy lifecycle readback is unavailable."
            ) from exc

        event = submitted.get("event") if isinstance(submitted, dict) else None
        active_state = (
            submitted.get("active_state") if isinstance(submitted, dict) else None
        )
        if (
            not isinstance(event, dict)
            or event.get("request_id") != payload["request_id"]
            or event.get("action") != action
            or event.get("expected_current") != expected_current
            or event.get("target") != target
            or not isinstance(active_state, dict)
            or submitted.get("status") not in {"recorded", "replayed"}
        ):
            raise SourcePolicyLifecycleUnavailable(
                "Source-policy lifecycle event readback did not match."
            )
        expected_active = target if action != "deactivate" else None
        last_event = readback.get("last_event")
        submitted_active_reference = _manifest_reference(
            active_state.get("active_manifest")
        )
        submitted_last_event = _safe_lifecycle_event(active_state.get("last_event"))
        if (
            active_state.get("status") != readback.get("status")
            or submitted_active_reference != readback.get("active_reference")
            or submitted_last_event != readback.get("last_event")
            or not isinstance(last_event, dict)
        ):
            raise SourcePolicyLifecycleUnavailable(
                "Source-policy lifecycle active-state readback did not match."
            )
        if submitted.get("status") == "recorded" and (
            submitted_active_reference != expected_active
            or last_event.get("request_id") != payload["request_id"]
        ):
            raise SourcePolicyLifecycleUnavailable(
                "New source-policy lifecycle event is not the active readback."
            )
        return {
            "status": submitted.get("status"),
            "event": _safe_lifecycle_event(event),
            "active_state": readback,
        }


class HttpPublicCatalogRegistrationRepository:
    """Call the typed Data API catalog editor with a dedicated writer token."""

    def __init__(
        self,
        api_url: str,
        write_token_file: Path,
        *,
        timeout_seconds: float = 5.0,
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
        self._client = HttpSourcePolicyReviewRepository._client(
            api_url, write_token_file, timeout_seconds, transport
        )

    def preview_registration(
        self, registration: dict[str, object]
    ) -> dict[str, object]:
        """Validate and preview source/dataset metadata without writing."""
        return self._post(
            "/v1/public-catalog/registrations/preview",
            registration,
            allow_stale=False,
        )

    def preview_record_jurisdiction(
        self, request: dict[str, object]
    ) -> dict[str, object]:
        """Preview one source-backed jurisdiction edit without writing."""
        return self._post(
            "/v1/public-catalog/record-jurisdiction/preview",
            request,
            allow_stale=False,
        )

    def apply_registration(
        self,
        registration: dict[str, object],
        expected_before_sha256: str,
        expected_proposed_sha256: str,
        transaction_id: str,
        approval_reference: str,
        operator_audit_context: dict[str, str | None],
    ) -> dict[str, object]:
        """Apply one approved compare-and-set request and return its receipt."""
        try:
            normalized_transaction_id = str(uuid.UUID(transaction_id))
        except (TypeError, ValueError, AttributeError) as exc:
            raise InvalidPublicCatalogRegistration(
                "Transaction ID is invalid."
            ) from exc
        try:
            validated_audit = _validate_operator_audit_context(operator_audit_context)
        except ValueError as exc:
            raise InvalidPublicCatalogRegistration(
                "Operator audit context is invalid."
            ) from exc
        if (
            not isinstance(expected_before_sha256, str)
            or _SHA256.fullmatch(expected_before_sha256) is None
            or not isinstance(expected_proposed_sha256, str)
            or _SHA256.fullmatch(expected_proposed_sha256) is None
            or normalized_transaction_id != transaction_id
            or not isinstance(approval_reference, str)
            or not 1 <= len(approval_reference.strip()) <= 512
        ):
            raise InvalidPublicCatalogRegistration("Approval envelope is invalid.")
        result = self._post(
            "/v1/public-catalog/registrations/governed",
            {
                "registration": registration,
                "expected_before_sha256": expected_before_sha256,
                "expected_proposed_sha256": expected_proposed_sha256,
                "transaction_id": normalized_transaction_id,
                "approval_reference": approval_reference.strip(),
                "operator_audit_context": validated_audit,
            },
            allow_stale=True,
        )
        receipt = self._read_registration_receipt(normalized_transaction_id)
        expected_outcome = result.get("status")
        if (
            receipt.get("status") != "recorded"
            or receipt.get("transaction_id") != normalized_transaction_id
            or receipt.get("source_id") != result.get("source_id")
            or receipt.get("dataset_ids") != result.get("dataset_ids")
            or receipt.get("expected_before_sha256") != expected_before_sha256
            or receipt.get("before_sha256") != result.get("before_sha256")
            or receipt.get("after_sha256") != result.get("after_sha256")
            or receipt.get("proposed_sha256") != expected_proposed_sha256
            or receipt.get("approval_reference") != approval_reference.strip()
            or receipt.get("outcome") != expected_outcome
        ):
            raise PublicCatalogRegistrationUnavailable(
                "Public catalog audit receipt did not match the applied result."
            )
        try:
            stored_audit = _validate_operator_audit_context(
                receipt.get("operator_audit_context")
            )
        except ValueError as exc:
            raise PublicCatalogRegistrationUnavailable(
                "Public catalog audit context readback was invalid."
            ) from exc
        if stored_audit is None or (
            not result.get("idempotent_replay") and stored_audit != validated_audit
        ):
            raise PublicCatalogRegistrationUnavailable(
                "Public catalog audit context did not match the submitted request."
            )
        return {
            **result,
            "audit_readback": receipt,
            "audit_readback_verified": True,
        }

    def apply_record_jurisdiction(
        self,
        request: dict[str, object],
        expected_before_sha256: str,
        expected_proposed_sha256: str,
        transaction_id: str,
        approval_reference: str,
        operator_audit_context: dict[str, str | None],
    ) -> dict[str, object]:
        """Apply one exact-digest jurisdiction edit and verify its readback."""
        try:
            normalized_transaction_id = str(uuid.UUID(transaction_id))
        except (TypeError, ValueError, AttributeError) as exc:
            raise InvalidPublicCatalogRegistration(
                "Transaction ID is invalid."
            ) from exc
        try:
            validated_audit = _validate_operator_audit_context(operator_audit_context)
        except ValueError as exc:
            raise InvalidPublicCatalogRegistration(
                "Operator audit context is invalid."
            ) from exc
        if (
            not isinstance(expected_before_sha256, str)
            or _SHA256.fullmatch(expected_before_sha256) is None
            or not isinstance(expected_proposed_sha256, str)
            or _SHA256.fullmatch(expected_proposed_sha256) is None
            or normalized_transaction_id != transaction_id
            or not isinstance(approval_reference, str)
            or not 1 <= len(approval_reference.strip()) <= 512
            or validated_audit is None
        ):
            raise InvalidPublicCatalogRegistration("Approval envelope is invalid.")
        result = self._post(
            "/v1/public-catalog/record-jurisdiction/governed",
            {
                **request,
                "expected_before_sha256": expected_before_sha256,
                "expected_proposed_sha256": expected_proposed_sha256,
                "transaction_id": normalized_transaction_id,
                "approval_reference": approval_reference.strip(),
                "operator_audit_context": validated_audit,
            },
            allow_stale=True,
        )
        receipt = self._read_registration_receipt(normalized_transaction_id)
        stored = receipt.get("receipt")
        if (
            receipt.get("status") != "recorded"
            or receipt.get("transaction_id") != normalized_transaction_id
            or receipt.get("source_id") != result.get("source_id")
            or receipt.get("dataset_ids") != [request.get("dataset_id")]
            or receipt.get("expected_before_sha256") != expected_before_sha256
            or receipt.get("before_sha256") != result.get("before_sha256")
            or receipt.get("after_sha256") != result.get("after_sha256")
            or receipt.get("proposed_sha256") != expected_proposed_sha256
            or receipt.get("approval_reference") != approval_reference.strip()
            or receipt.get("outcome") != result.get("status")
            or not isinstance(stored, dict)
            or stored.get("source_record_id") != request.get("source_record_id")
            or stored.get("readback") != result.get("readback")
        ):
            raise PublicCatalogRegistrationUnavailable(
                "Public catalog record receipt did not match the applied result."
            )
        try:
            stored_audit = _validate_operator_audit_context(
                receipt.get("operator_audit_context")
            )
        except ValueError as exc:
            raise PublicCatalogRegistrationUnavailable(
                "Public catalog audit context readback was invalid."
            ) from exc
        if stored_audit is None or (
            not result.get("idempotent_replay") and stored_audit != validated_audit
        ):
            raise PublicCatalogRegistrationUnavailable(
                "Public catalog audit context did not match the submitted request."
            )
        return {
            **result,
            "audit_readback": receipt,
            "audit_readback_verified": True,
        }

    def _read_registration_receipt(self, transaction_id: str) -> dict[str, object]:
        """Read the durable event and privacy-safe transport correlation fields."""
        try:
            response = self._client.get(
                f"/v1/public-catalog/registrations/receipts/"
                f"{quote(transaction_id, safe='')}"
            )
        except httpx.HTTPError as exc:
            raise PublicCatalogRegistrationUnavailable(
                "Public catalog audit receipt is unavailable."
            ) from exc
        if response.status_code == httpx.codes.NOT_FOUND:
            raise PublicCatalogRegistrationUnavailable(
                "Public catalog audit receipt was not found."
            )
        if response.status_code in {
            httpx.codes.BAD_REQUEST,
            httpx.codes.UNAUTHORIZED,
        }:
            raise PublicCatalogRegistrationUnavailable(
                "Public catalog audit receipt was rejected."
            )
        try:
            response.raise_for_status()
            receipt = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PublicCatalogRegistrationUnavailable(
                "Public catalog audit receipt response was invalid."
            ) from exc
        if not isinstance(receipt, dict):
            raise PublicCatalogRegistrationUnavailable(
                "Public catalog audit receipt response was invalid."
            )
        return receipt

    def _post(
        self,
        path: str,
        payload: dict[str, object],
        *,
        allow_stale: bool,
    ) -> dict[str, object]:
        try:
            response = self._client.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise PublicCatalogRegistrationUnavailable(
                "Public catalog metadata service is unavailable."
            ) from exc
        if response.status_code == httpx.codes.CONFLICT:
            try:
                body = response.json()
            except ValueError:
                body = {}
            if (
                allow_stale
                and isinstance(body, dict)
                and body.get("status") == "stale_before_state"
            ):
                return body
            raise PublicCatalogRegistrationConflict(
                "Public catalog metadata changed or the transaction conflicts."
            )
        if response.status_code == httpx.codes.REQUEST_ENTITY_TOO_LARGE:
            raise InvalidPublicCatalogRegistration(
                "Public catalog preview exceeds the reviewable response limit."
            )
        if response.status_code in {
            httpx.codes.BAD_REQUEST,
            httpx.codes.UNAUTHORIZED,
        }:
            raise InvalidPublicCatalogRegistration(
                "Public catalog metadata request was rejected."
            )
        try:
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PublicCatalogRegistrationUnavailable(
                "Public catalog metadata response was invalid."
            ) from exc
        if not isinstance(body, dict):
            raise PublicCatalogRegistrationUnavailable(
                "Public catalog metadata response was invalid."
            )
        return body


def _validate_request(
    manifest_id: str,
    manifest_version: str,
    digest: str,
    decision: str,
    rationale: str,
) -> None:
    if not _MANIFEST_ID.fullmatch(manifest_id) or not _SEMVER.fullmatch(
        manifest_version
    ):
        raise InvalidSourcePolicyReview("Source-policy candidate identity is invalid.")
    if not _SHA256.fullmatch(digest):
        raise InvalidSourcePolicyReview("Source-policy candidate digest is invalid.")
    if decision not in {"approved", "rejected"}:
        raise InvalidSourcePolicyReview("Review decision is invalid.")
    if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 2048:
        raise InvalidSourcePolicyReview("Review rationale is required and bounded.")


def _validate_lifecycle_request(
    request_id: str,
    action: str,
    expected_current: dict[str, str] | None,
    target: dict[str, str] | None,
    rationale: str,
) -> dict[str, object]:
    """Validate the flat MCP lifecycle inputs before sending any authenticated write."""
    try:
        normalized_request_id = str(uuid.UUID(request_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise InvalidSourcePolicyLifecycle("Lifecycle request ID is invalid.") from exc
    if action not in {"activate", "deactivate", "rollback"}:
        raise InvalidSourcePolicyLifecycle("Lifecycle action is invalid.")
    for reference, label in (
        (expected_current, "expected current"),
        (target, "target"),
    ):
        if reference is None:
            continue
        if (
            not isinstance(reference, dict)
            or set(reference) != {"manifest_id", "manifest_version", "content_sha256"}
            or not _MANIFEST_ID.fullmatch(str(reference.get("manifest_id", "")))
            or not _SEMVER.fullmatch(str(reference.get("manifest_version", "")))
            or not _SHA256.fullmatch(str(reference.get("content_sha256", "")))
        ):
            raise InvalidSourcePolicyLifecycle(
                f"Lifecycle {label} reference is invalid."
            )
    if action == "deactivate" and target is not None:
        raise InvalidSourcePolicyLifecycle("Deactivation cannot include a target.")
    if action != "deactivate" and target is None:
        raise InvalidSourcePolicyLifecycle("Activation requires a target reference.")
    if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 2048:
        raise InvalidSourcePolicyLifecycle("Lifecycle rationale is required.")
    return {
        "request_id": normalized_request_id,
        "action": action,
        "expected_current": expected_current,
        "target": target,
        "rationale": rationale,
    }


def _manifest_reference(manifest: object) -> dict[str, str] | None:
    """Extract and validate a manifest identity/digest from API readback."""
    if not isinstance(manifest, dict):
        return None
    reference = {
        "manifest_id": manifest.get("manifest_id"),
        "manifest_version": manifest.get("manifest_version"),
        "content_sha256": manifest.get("content_sha256"),
    }
    if (
        not _MANIFEST_ID.fullmatch(str(reference["manifest_id"] or ""))
        or not _SEMVER.fullmatch(str(reference["manifest_version"] or ""))
        or not _SHA256.fullmatch(str(reference["content_sha256"] or ""))
    ):
        return None
    return {key: str(value) for key, value in reference.items()}


def _safe_lifecycle_event(event: object) -> dict[str, object] | None:
    """Expose bounded audit metadata while omitting operator rationale."""
    if not isinstance(event, dict):
        return None
    return {
        key: event[key]
        for key in ("generation", "request_id", "action", "recorded_at")
        if key in event
    }
