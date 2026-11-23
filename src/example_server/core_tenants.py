"""Core's pattern-neutral tenant registry and onboarding boundary."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from example_server.persistence_api_client import persistence_api_headers


class TenantRegistryUnavailable(RuntimeError):
    """The Persistence Access API could not complete the tenant operation."""


class InvalidTenantSpecification(ValueError):
    """The tenant specification was rejected by its authoritative registry."""

    def __init__(self, detail: str = "Tenant specification was rejected.") -> None:
        self.detail = detail[:256]
        super().__init__(self.detail)


class TenantIdentityConflict(RuntimeError):
    """An immutable tenant identity already exists with different content."""


class TenantLifecycleConflict(RuntimeError):
    """A tenant lifecycle transition is stale or conflicts with its receipt."""

    def __init__(self, message: str, *, reason: str = "lifecycle_conflict") -> None:
        super().__init__(message)
        self.reason = reason


def tenant_lifecycle_state_digest(record: dict[str, object]) -> str:
    """Hash the exact tenant identity, lifecycle state, and immutable spec digest."""
    tenant_id = record.get("tenant_id")
    template_id = record.get("template_id")
    version = record.get("template_version")
    state = record.get("lifecycle_state")
    spec_digest = record.get("tenant_spec_sha256")
    if (
        not isinstance(tenant_id, str)
        or not isinstance(template_id, str)
        or not isinstance(version, str)
        or state not in {"pending", "active", "inactive", "deprecated"}
        or not isinstance(spec_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", spec_digest) is None
    ):
        raise ValueError("Tenant lifecycle record is incomplete.")
    canonical = json.dumps(
        {
            "tenant_id": tenant_id,
            "template_id": template_id,
            "template_version": version,
            "lifecycle_state": state,
            "tenant_spec_sha256": spec_digest,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TenantInventory(BaseModel):
    """Bounded endpoint-free tenant registry inventory."""

    model_config = ConfigDict(extra="forbid")

    tenants: list[dict[str, object]]
    truncated: bool = False
    next_cursor: str | None = None


class TenantValidation(BaseModel):
    """Validated tenant-spec digest and safe registry summary."""

    model_config = ConfigDict(extra="forbid")

    status: str
    tenant_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tenant: dict[str, object]


class TenantRepository(Protocol):
    """Read and govern tenant registrations using scoped API credentials."""

    def list_tenants(
        self, page_size: int = 25, cursor: str | None = None
    ) -> TenantInventory:
        """List registered tenants one page at a time."""

    def read_source_metadata(
        self,
        tenant_id: str,
        template_id: str,
        template_version: str,
        dataset_id: str,
        catalog_namespace: str,
    ) -> dict[str, object] | None:
        """Read the source metadata a tenant exposes."""

    def validate_tenant(self, specification: object) -> TenantValidation:
        """Validate a tenant specification without registering it."""

    def register_tenant(self, specification: dict[str, object]) -> dict[str, object]:
        """Register a tenant."""

    def read_tenant(
        self, tenant_id: str, template_id: str, template_version: str
    ) -> dict[str, object] | None:
        """Read one tenant by id and template version, or None if absent."""

    def activate_tenant(
        self,
        tenant_id: str,
        template_id: str,
        template_version: str,
        digest: str,
        *,
        expected_before_sha256: str,
        transaction_id: str,
        approval_reference: str,
        operator_audit_context: dict[str, str | None],
    ) -> dict[str, object] | None:
        """Activate a tenant under operator governance."""

    def deactivate_tenant(
        self,
        tenant_id: str,
        template_id: str,
        template_version: str,
        digest: str,
        *,
        expected_before_sha256: str,
        transaction_id: str,
        approval_reference: str,
        operator_audit_context: dict[str, str | None],
    ) -> dict[str, object] | None:
        """Deactivate a tenant under operator governance."""


class HttpTenantRepository:
    """Call the fixed Persistence Access API; never expose the database."""

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
        self._read_client = httpx.Client(
            base_url=api_url.rstrip("/"),
            headers=persistence_api_headers(self._read_token(read_token_file)),
            timeout=timeout_seconds,
            transport=transport,
        )
        self._write_client: httpx.Client | None = None
        if write_token_file is not None:
            self._write_client = httpx.Client(
                base_url=api_url.rstrip("/"),
                headers=persistence_api_headers(self._read_token(write_token_file)),
                timeout=timeout_seconds,
                transport=transport,
            )

    @staticmethod
    def _read_token(path: Path) -> str:
        try:
            token = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError("Persistence API credential is unavailable.") from exc
        if not token:
            raise ValueError("Persistence API credential is empty.")
        return token

    def list_tenants(
        self, page_size: int = 25, cursor: str | None = None
    ) -> TenantInventory:
        """List registered tenants one page at a time."""
        try:
            response = self._read_client.get(
                "/v1/tenants",
                params={"limit": page_size, "cursor": cursor}
                if cursor
                else {"limit": page_size},
            )
            response.raise_for_status()
            return TenantInventory.model_validate(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise TenantRegistryUnavailable("Tenant inventory is unavailable.") from exc

    def read_source_metadata(
        self,
        tenant_id: str,
        template_id: str,
        template_version: str,
        dataset_id: str,
        catalog_namespace: str,
    ) -> dict[str, object] | None:
        """Read public metadata only for a dataset explicitly bound to this tenant."""
        _validate_tenant_identity(tenant_id, template_id, template_version)
        if re.fullmatch(r"[a-z][a-z0-9.-]{0,127}", dataset_id) is None:
            raise InvalidTenantSpecification("Dataset identity is invalid.")
        if (
            re.fullmatch(
                r"[a-z][a-z0-9_-]{0,63}(?:\.[a-z][a-z0-9_-]{0,63})*",
                catalog_namespace,
            )
            is None
        ):
            raise InvalidTenantSpecification("Catalog namespace is invalid.")
        path = (
            "/v1/tenants/"
            f"{quote(tenant_id, safe='')}/patterns/{quote(template_id, safe='')}/"
            f"{quote(template_version, safe='')}/sources/{quote(dataset_id, safe='')}"
            f"/namespaces/{quote(catalog_namespace, safe='')}"
        )
        try:
            response = self._read_client.get(path)
            if response.status_code == httpx.codes.NOT_FOUND:
                return None
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else None
        except (httpx.HTTPError, ValueError) as exc:
            raise TenantRegistryUnavailable(
                "Bound source metadata is unavailable."
            ) from exc

    def validate_tenant(self, specification: object) -> TenantValidation:
        """Validate a tenant specification without registering it."""
        try:
            response = self._read_client.post(
                "/v1/registry/tenants/validate", json=specification
            )
        except httpx.HTTPError as exc:
            raise TenantRegistryUnavailable(
                "Tenant validation is unavailable."
            ) from exc
        if response.status_code in {httpx.codes.BAD_REQUEST, httpx.codes.CONFLICT}:
            raise InvalidTenantSpecification(_rejection_detail(response))
        try:
            response.raise_for_status()
            return TenantValidation.model_validate(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise TenantRegistryUnavailable(
                "Tenant validation is unavailable."
            ) from exc

    def register_tenant(self, specification: dict[str, object]) -> dict[str, object]:
        """Register a tenant with the registry-writer credential."""
        response = self._write_request(
            "POST", "/v1/registry/tenants", json=specification
        )
        if response.status_code == httpx.codes.BAD_REQUEST:
            raise InvalidTenantSpecification(_rejection_detail(response))
        if response.status_code == httpx.codes.CONFLICT:
            if _error_code(response) == "tenant_identity_conflict":
                raise TenantIdentityConflict("Tenant identity conflicts with registry.")
            raise InvalidTenantSpecification(_rejection_detail(response))
        try:
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except (httpx.HTTPError, ValueError) as exc:
            raise TenantRegistryUnavailable("Tenant registration failed.") from exc

    def read_tenant(
        self, tenant_id: str, template_id: str, template_version: str
    ) -> dict[str, object] | None:
        """Read one tenant by id and template version, or None if absent."""
        _validate_tenant_identity(tenant_id, template_id, template_version)
        path = (
            "/v1/tenants/"
            f"{quote(tenant_id, safe='')}/patterns/{quote(template_id, safe='')}/"
            f"{quote(template_version, safe='')}"
        )
        try:
            response = self._read_client.get(path)
            if response.status_code == httpx.codes.NOT_FOUND:
                return None
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else None
        except (httpx.HTTPError, ValueError) as exc:
            raise TenantRegistryUnavailable("Tenant readback is unavailable.") from exc

    def activate_tenant(
        self,
        tenant_id: str,
        template_id: str,
        template_version: str,
        digest: str,
        *,
        expected_before_sha256: str,
        transaction_id: str,
        approval_reference: str,
        operator_audit_context: dict[str, str | None],
    ) -> dict[str, object] | None:
        """Activate a tenant under operator governance."""
        _validate_tenant_identity(tenant_id, template_id, template_version)
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise InvalidTenantSpecification("Tenant digest is invalid.")
        path = (
            "/v1/registry/tenants/"
            f"{quote(tenant_id, safe='')}/patterns/{quote(template_id, safe='')}/"
            f"{quote(template_version, safe='')}/activate"
        )
        response = self._write_request(
            "POST",
            path,
            json={
                "approved_tenant_spec_sha256": digest,
                "expected_before_sha256": expected_before_sha256,
                "transaction_id": transaction_id,
                "approval_reference": approval_reference,
                "operator_audit_context": operator_audit_context,
            },
        )
        if response.status_code == httpx.codes.CONFLICT:
            try:
                detail = response.json()
            except ValueError:
                detail = None
            reason = (
                detail.get("error")
                if isinstance(detail, dict)
                and detail.get("error")
                in {
                    "stale_before_state",
                    "approval_digest_mismatch",
                    "invalid_lifecycle_state",
                    "template_not_active",
                    "transaction_conflict",
                    "readback_mismatch",
                }
                else "lifecycle_conflict"
            )
            raise TenantLifecycleConflict(
                "Tenant activation is stale or conflicts with its approval.",
                reason=reason,
            )
        if response.status_code == httpx.codes.BAD_REQUEST:
            raise InvalidTenantSpecification(
                "Tenant activation was rejected; revalidate the pending tenant "
                "and exact digest."
            )
        try:
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else None
        except (httpx.HTTPError, ValueError) as exc:
            raise TenantRegistryUnavailable("Tenant activation failed.") from exc

    def deactivate_tenant(
        self,
        tenant_id: str,
        template_id: str,
        template_version: str,
        digest: str,
        *,
        expected_before_sha256: str,
        transaction_id: str,
        approval_reference: str,
        operator_audit_context: dict[str, str | None],
    ) -> dict[str, object] | None:
        """Deactivate a tenant under operator governance."""
        _validate_tenant_identity(tenant_id, template_id, template_version)
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise InvalidTenantSpecification("Tenant digest is invalid.")
        path = (
            "/v1/registry/tenants/"
            f"{quote(tenant_id, safe='')}/patterns/{quote(template_id, safe='')}/"
            f"{quote(template_version, safe='')}/deactivate"
        )
        response = self._write_request(
            "POST",
            path,
            json={
                "approved_tenant_spec_sha256": digest,
                "expected_before_sha256": expected_before_sha256,
                "transaction_id": transaction_id,
                "approval_reference": approval_reference,
                "operator_audit_context": operator_audit_context,
            },
        )
        if response.status_code == httpx.codes.CONFLICT:
            try:
                detail = response.json()
            except ValueError:
                detail = None
            reason = (
                detail.get("error")
                if isinstance(detail, dict)
                and detail.get("error")
                in {
                    "stale_before_state",
                    "approval_digest_mismatch",
                    "invalid_lifecycle_state",
                    "transaction_conflict",
                    "readback_mismatch",
                }
                else "lifecycle_conflict"
            )
            raise TenantLifecycleConflict(
                "Tenant deactivation is stale or conflicts with its approval.",
                reason=reason,
            )
        if response.status_code == httpx.codes.BAD_REQUEST:
            raise InvalidTenantSpecification(
                "Tenant deactivation was rejected; verify the current tenant state "
                "and exact approved digest."
            )
        try:
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else None
        except (httpx.HTTPError, ValueError) as exc:
            raise TenantRegistryUnavailable("Tenant deactivation failed.") from exc

    def _write_request(
        self, method: str, path: str, **kwargs: object
    ) -> httpx.Response:
        if self._write_client is None:
            raise TenantRegistryUnavailable("Tenant registry writes are disabled.")
        try:
            return self._write_client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise TenantRegistryUnavailable("Tenant registry is unavailable.") from exc


def _validate_tenant_identity(
    tenant_id: str, template_id: str, template_version: str
) -> None:
    if (
        re.fullmatch(r"[a-z][a-z0-9-]{0,63}", tenant_id) is None
        or re.fullmatch(r"[a-z][a-z0-9-]{0,63}", template_id) is None
        or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", template_version) is None
    ):
        raise InvalidTenantSpecification("Tenant identity is invalid.")


def _error_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    return error if isinstance(error, str) else None


def _rejection_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, str) and detail.strip():
        return detail[:256]
    return (
        "Tenant specification was rejected; check its identity, declared namespaces "
        "and operations, private endpoint, and available public datasets."
    )
