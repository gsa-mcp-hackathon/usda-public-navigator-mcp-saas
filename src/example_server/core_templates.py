"""Core's bounded, pattern-neutral cluster-template management boundary."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from example_server.persistence_api_client import persistence_api_headers


class TemplateRegistryUnavailable(RuntimeError):
    """The template registry could not complete a requested operation."""


class InvalidTemplateDefinition(ValueError):
    """The Persistence Access API rejected the submitted descriptor."""


class TemplateRegistryConflict(RuntimeError):
    """The template registry rejected a conflicting lifecycle or identity change."""

    def __init__(self, message: str, *, reason: str = "lifecycle_conflict") -> None:
        super().__init__(message)
        self.reason = reason


class TemplateRecord(BaseModel):
    """Validated registry record with a generic descriptor boundary."""

    model_config = ConfigDict(extra="forbid")

    template_id: str = Field(min_length=1)
    template_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    lifecycle_state: Literal["pending", "active", "inactive", "deprecated"]
    definition: dict[str, object]


class TemplateInventory(BaseModel):
    """Endpoint-free, bounded cluster-template inventory."""

    model_config = ConfigDict(extra="forbid")

    templates: list[TemplateRecord]
    truncated: bool = False
    next_cursor: str | None = None


class TemplateValidation(BaseModel):
    """Normalized descriptor and digest returned by authoritative validation."""

    model_config = ConfigDict(extra="forbid")

    status: str
    descriptor_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    definition: dict[str, object]


class ClusterTemplateRepository(Protocol):
    """Read and govern generic templates through the Persistence Access API."""

    def list_templates(
        self, page_size: int = 25, cursor: str | None = None
    ) -> TemplateInventory:
        """List pattern templates one page at a time."""

    def validate_template(self, descriptor: object) -> TemplateValidation:
        """Validate a template descriptor without registering it."""

    def register_template(self, descriptor: dict[str, object]) -> dict[str, object]:
        """Register a pattern template."""

    def read_template(self, template_id: str, version: str) -> dict[str, object] | None:
        """Read one template by id and version, or None if it is not registered."""

    def activate_template_governed(
        self,
        template_id: str,
        version: str,
        *,
        expected_before_sha256: str,
        transaction_id: str,
        approval_reference: str,
        operator_audit_context: dict[str, str | None],
    ) -> dict[str, object] | None:
        """Activate a template under operator governance."""

    def deactivate_template_governed(
        self,
        template_id: str,
        version: str,
        *,
        expected_before_sha256: str,
        transaction_id: str,
        approval_reference: str,
        operator_audit_context: dict[str, str | None],
    ) -> dict[str, object] | None:
        """Deactivate a template under operator governance."""

    def bind_template_operation(
        self, template_id: str, version: str, operation_id: str, endpoint: str
    ) -> dict[str, object]:
        """Bind an operation of a template to its endpoint."""


def descriptor_digest(descriptor: dict[str, object]) -> str:
    """Hash a JSON descriptor using the registry's canonical representation."""
    canonical = json.dumps(descriptor, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def template_lifecycle_state_digest(record: dict[str, object]) -> str:
    """Hash the exact template lifecycle state and immutable descriptor digest."""
    definition = record.get("definition")
    template_id = record.get("template_id")
    version = record.get("template_version")
    state = record.get("lifecycle_state")
    if (
        not isinstance(definition, dict)
        or not isinstance(template_id, str)
        or not isinstance(version, str)
        or state not in {"pending", "active", "inactive", "deprecated"}
    ):
        raise ValueError("Template lifecycle record is incomplete.")
    return descriptor_digest(
        {
            "template_id": template_id,
            "template_version": version,
            "lifecycle_state": state,
            "descriptor_sha256": descriptor_digest(definition),
        }
    )


def binding_digest(
    template_id: str, template_version: str, operation_id: str, endpoint: str
) -> str:
    """Hash the exact reviewed template-operation endpoint binding."""
    return descriptor_digest(
        {
            "template_id": template_id,
            "template_version": template_version,
            "operation_id": operation_id,
            "endpoint": endpoint,
        }
    )


class HttpClusterTemplateRepository:
    """Call template registry APIs with separately scoped read and write secrets."""

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
        read_token = self._read_token(read_token_file)
        self._read_client = httpx.Client(
            base_url=api_url.rstrip("/"),
            headers=persistence_api_headers(read_token),
            timeout=timeout_seconds,
            transport=transport,
        )
        self._write_client: httpx.Client | None = None
        if write_token_file is not None:
            write_token = self._read_token(write_token_file)
            self._write_client = httpx.Client(
                base_url=api_url.rstrip("/"),
                headers=persistence_api_headers(write_token),
                timeout=timeout_seconds,
                transport=transport,
            )

    @staticmethod
    def _read_token(token_file: Path) -> str:
        try:
            token = token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError("Persistence API credential is unavailable.") from exc
        if not token:
            raise ValueError("Persistence API credential is empty.")
        return token

    def list_templates(
        self, page_size: int = 25, cursor: str | None = None
    ) -> TemplateInventory:
        """List pattern templates one page at a time."""
        try:
            response = self._read_client.get(
                "/v1/pattern-templates",
                params={"limit": page_size, "cursor": cursor}
                if cursor
                else {"limit": page_size},
            )
            response.raise_for_status()
            return TemplateInventory.model_validate(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise TemplateRegistryUnavailable(
                "Template inventory is unavailable."
            ) from exc

    def validate_template(self, descriptor: object) -> TemplateValidation:
        """Validate a template descriptor without registering it."""
        try:
            response = self._read_client.post(
                "/v1/registry/pattern-templates/validate", json=descriptor
            )
        except httpx.HTTPError as exc:
            raise TemplateRegistryUnavailable(
                "Template validation is unavailable."
            ) from exc
        if response.status_code in {
            httpx.codes.BAD_REQUEST,
            httpx.codes.CONFLICT,
        }:
            raise InvalidTemplateDefinition("Template descriptor was rejected.")
        try:
            response.raise_for_status()
            return TemplateValidation.model_validate(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise TemplateRegistryUnavailable(
                "Template validation is unavailable."
            ) from exc

    def register_template(self, descriptor: dict[str, object]) -> dict[str, object]:
        """Register a pattern template with the registry-writer credential."""
        response = self._write_request(
            "POST", "/v1/registry/pattern-templates", json=descriptor
        )
        if response.status_code == httpx.codes.CONFLICT:
            raise TemplateRegistryConflict("Template identity conflicts with registry.")
        try:
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except (httpx.HTTPError, ValueError) as exc:
            raise TemplateRegistryUnavailable("Template registration failed.") from exc

    def read_template(self, template_id: str, version: str) -> dict[str, object] | None:
        """Read one template by id and version, or None if it is not registered."""
        _validate_template_identity(template_id, version)
        try:
            response = self._read_client.get(
                "/v1/pattern-templates/"
                f"{quote(template_id, safe='')}/{quote(version, safe='')}"
            )
            if response.status_code == httpx.codes.NOT_FOUND:
                return None
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else None
        except (httpx.HTTPError, ValueError) as exc:
            raise TemplateRegistryUnavailable(
                "Template readback is unavailable."
            ) from exc

    def activate_template_governed(
        self,
        template_id: str,
        version: str,
        *,
        expected_before_sha256: str,
        transaction_id: str,
        approval_reference: str,
        operator_audit_context: dict[str, str | None],
    ) -> dict[str, object] | None:
        """Apply one audited, exact-before-state activation transaction."""
        _validate_template_identity(template_id, version)
        if re.fullmatch(r"[0-9a-f]{64}", expected_before_sha256) is None:
            raise ValueError("Template before-state digest is invalid.")
        action = "activate"
        response = self._write_request(
            "POST",
            "/v1/registry/pattern-templates/"
            f"{quote(template_id, safe='')}/{quote(version, safe='')}/{action}",
            json={
                "expected_before_sha256": expected_before_sha256,
                "transaction_id": transaction_id,
                "approval_reference": approval_reference,
                "operator_audit_context": operator_audit_context,
            },
        )
        if response.status_code == httpx.codes.CONFLICT:
            raise TemplateRegistryConflict(
                "Template lifecycle state is stale or conflicts."
            )
        if response.status_code == httpx.codes.BAD_REQUEST:
            raise InvalidTemplateDefinition("Template lifecycle request was rejected.")
        try:
            if response.status_code == httpx.codes.NOT_FOUND:
                return None
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else None
        except (httpx.HTTPError, ValueError) as exc:
            raise TemplateRegistryUnavailable("Template state change failed.") from exc

    def deactivate_template_governed(
        self,
        template_id: str,
        version: str,
        *,
        expected_before_sha256: str,
        transaction_id: str,
        approval_reference: str,
        operator_audit_context: dict[str, str | None],
    ) -> dict[str, object] | None:
        """Deactivate one package through the audited compare-and-set endpoint."""
        _validate_template_identity(template_id, version)
        if re.fullmatch(r"[0-9a-f]{64}", expected_before_sha256) is None:
            raise ValueError("Template before-state digest is invalid.")
        response = self._write_request(
            "POST",
            "/v1/registry/pattern-templates/"
            f"{quote(template_id, safe='')}/{quote(version, safe='')}/deactivate",
            json={
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
                    "template_not_active",
                    "active_tenant_dependency",
                    "transaction_conflict",
                }
                else "lifecycle_conflict"
            )
            raise TemplateRegistryConflict(
                "Template lifecycle state is stale or conflicts.", reason=reason
            )
        if response.status_code == httpx.codes.BAD_REQUEST:
            raise InvalidTemplateDefinition("Template lifecycle request was rejected.")
        try:
            if response.status_code == httpx.codes.NOT_FOUND:
                return None
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else None
        except (httpx.HTTPError, ValueError) as exc:
            raise TemplateRegistryUnavailable("Template state change failed.") from exc

    def bind_template_operation(
        self, template_id: str, version: str, operation_id: str, endpoint: str
    ) -> dict[str, object]:
        """Bind one active, declared operation to a registry-validated handler."""
        _validate_template_identity(template_id, version)
        if re.fullmatch(r"[a-z][a-z0-9.-]{0,127}", operation_id) is None:
            raise InvalidTemplateDefinition("Template operation identity is invalid.")
        path = (
            "/v1/registry/pattern-templates/"
            f"{quote(template_id, safe='')}/{quote(version, safe='')}/operations/"
            f"{quote(operation_id, safe='')}/route"
        )
        response = self._write_request("POST", path, json={"endpoint": endpoint})
        if response.status_code == httpx.codes.CONFLICT:
            raise TemplateRegistryConflict("Template route binding conflicts.")
        if response.status_code in {
            httpx.codes.BAD_REQUEST,
            httpx.codes.NOT_FOUND,
        }:
            raise InvalidTemplateDefinition("Template route binding was rejected.")
        try:
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except (httpx.HTTPError, ValueError) as exc:
            raise TemplateRegistryUnavailable("Template route binding failed.") from exc

    def _write_request(
        self, method: str, path: str, **kwargs: object
    ) -> httpx.Response:
        if self._write_client is None:
            raise TemplateRegistryUnavailable("Template registry writes are disabled.")
        try:
            return self._write_client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise TemplateRegistryUnavailable(
                "Template registry is unavailable."
            ) from exc


def _validate_template_identity(template_id: str, version: str) -> None:
    """Reject caller-controlled path components before attaching API credentials."""
    if (
        re.fullmatch(r"[a-z][a-z0-9-]{0,63}", template_id) is None
        or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None
    ):
        raise InvalidTemplateDefinition("Template identity is invalid.")
