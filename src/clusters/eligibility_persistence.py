"""Eligibility catalog handoff through the Persistence Access API only."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import NAMESPACE_URL, uuid5

import httpx

from clusters.eligibility_ingest import NormalizedSource


class PersistenceApiUnavailable(RuntimeError):
    """The Persistence Access API did not complete the requested operation."""


class CriteriaRulesetConflict(RuntimeError):
    """The same program's active criteria digest changed during writeback."""


class CriteriaRulesetRejected(RuntimeError):
    """The Persistence API rejected a structurally invalid ruleset."""


class HttpCriteriaRulesetRepository:
    """Read and persist immutable, source-cited Eligibility criteria rulesets."""

    def __init__(
        self,
        base_url: str,
        reader_token_file: Path,
        writer_token_file: Path | None = None,
        *,
        cluster_id: str = "eligibility",
        timeout_seconds: float = 8.0,
    ) -> None:
        self._base_url = _api_origin(base_url)
        self._reader_token_file = reader_token_file
        self._writer_token_file = writer_token_file
        self._cluster_id = cluster_id
        self._timeout_seconds = timeout_seconds

    @property
    def writable(self) -> bool:
        """Whether this runtime has the criteria metadata write capability."""
        return self._writer_token_file is not None

    def read_active(self, program_id: str) -> dict[str, object] | None:
        """Read one active package by stable program identity."""
        path = (
            f"/v1/eligibility/clusters/{quote(self._cluster_id, safe='')}"
            f"/criteria-rulesets/{quote(program_id, safe='')}"
        )
        try:
            response = httpx.get(
                self._base_url + path,
                headers={"Authorization": _bearer(self._reader_token_file)},
                timeout=self._timeout_seconds,
            )
            if response.status_code == httpx.codes.NOT_FOUND:
                return None
            response.raise_for_status()
            result = response.json()
            ruleset = result.get("ruleset") if isinstance(result, dict) else None
            if (
                not isinstance(result, dict)
                or result.get("status") != "ok"
                or result.get("cluster_id") != self._cluster_id
                or result.get("program_id") != program_id
                or not isinstance(result.get("content_sha256"), str)
                or len(result["content_sha256"]) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in result["content_sha256"]
                )
                or not isinstance(ruleset, dict)
                or ruleset.get("program_id") != program_id
            ):
                raise ValueError(
                    "Persistence API returned an invalid ruleset readback."
                )
            return result
        except (OSError, ValueError, TypeError, httpx.HTTPError) as exc:
            raise PersistenceApiUnavailable(
                "Persistence API criteria-ruleset read failed."
            ) from exc

    def read_active_schemas(self, registry_id: str) -> list[dict[str, object]]:
        """Read active schema definitions through the catalog-reader capability."""
        path = f"/v1/profile-schemas/{quote(registry_id, safe='')}/active"
        try:
            response = httpx.get(
                self._base_url + path,
                headers={"Authorization": _bearer(self._reader_token_file)},
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            result = response.json()
            schemas = result.get("schemas") if isinstance(result, dict) else None
            if (
                not isinstance(result, dict)
                or result.get("status") != "ok"
                or result.get("registry_id") != registry_id
                or not isinstance(schemas, list)
                or any(
                    not isinstance(schema, dict)
                    or schema.get("registry_id") != registry_id
                    or not isinstance(schema.get("schema_version"), str)
                    or not isinstance(schema.get("attributes"), dict)
                    or not isinstance(schema.get("dimensions"), dict)
                    for schema in schemas
                )
            ):
                raise ValueError("Persistence API returned invalid active schemas.")
            return schemas
        except (OSError, ValueError, TypeError, httpx.HTTPError) as exc:
            raise PersistenceApiUnavailable(
                "Persistence API active profile schemas are unavailable."
            ) from exc

    def register(self, ruleset: dict[str, object]) -> dict[str, object]:
        """Write by content digest and verify the exact active readback."""
        if self._writer_token_file is None:
            raise PersistenceApiUnavailable(
                "Persistence API criteria-ruleset writer is not configured."
            )
        encoded = json.dumps(
            ruleset, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        program_id = ruleset.get("program_id")
        if not isinstance(program_id, str):
            raise ValueError("Criteria ruleset program identity is missing.")
        active = self.read_active(program_id)
        expected_current = (
            active.get("content_sha256") if isinstance(active, dict) else None
        )
        request_id = str(
            uuid5(
                NAMESPACE_URL,
                f"criteria-ruleset:{self._cluster_id}:{program_id}:"
                f"{expected_current or 'none'}:{digest}",
            )
        )
        payload = {
            "request_id": request_id,
            "cluster_id": self._cluster_id,
            "expected_current_content_sha256": expected_current,
            "expected_content_sha256": digest,
            "ruleset": ruleset,
        }
        path = (
            f"/v1/eligibility/clusters/{quote(self._cluster_id, safe='')}"
            "/criteria-rulesets"
        )
        try:
            response = httpx.post(
                self._base_url + path,
                json=payload,
                headers={"Authorization": _bearer(self._writer_token_file)},
                timeout=self._timeout_seconds,
            )
            if response.status_code == httpx.codes.CONFLICT:
                raise CriteriaRulesetConflict(
                    "Criteria ruleset identity conflicts with stored content."
                )
            if response.status_code == httpx.codes.BAD_REQUEST:
                raise CriteriaRulesetRejected(
                    "Persistence API rejected the criteria ruleset contract."
                )
            response.raise_for_status()
            write_result = response.json()
            if (
                not isinstance(write_result, dict)
                or write_result.get("cluster_id") != self._cluster_id
                or write_result.get("program_id") != program_id
                or write_result.get("content_sha256") != digest
                or write_result.get("status") not in {"stored", "duplicate", "replayed"}
            ):
                raise ValueError("Persistence API returned an invalid ruleset write.")
            readback = self.read_active(program_id)
            if (
                not isinstance(readback, dict)
                or readback.get("content_sha256") != digest
                or readback.get("ruleset") != ruleset
            ):
                raise CriteriaRulesetConflict(
                    "Active criteria ruleset readback did not match the submitted "
                    "digest."
                )
            return {
                **write_result,
                "readback_verified": True,
                "active_ruleset": readback["ruleset"],
            }
        except (
            CriteriaRulesetConflict,
            CriteriaRulesetRejected,
            PersistenceApiUnavailable,
        ):
            raise
        except (OSError, ValueError, TypeError, httpx.HTTPError) as exc:
            raise PersistenceApiUnavailable(
                "Persistence API criteria-ruleset write failed."
            ) from exc


def _api_origin(base_url: str) -> str:
    parsed = urlsplit(base_url)
    allowed_http_hosts = {"127.0.0.1", "localhost", "mcp-saas-vm-data-api"}
    if not parsed.hostname or parsed.path not in {"", "/"}:
        raise ValueError("Persistence API URL must be an origin without a path.")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in allowed_http_hosts
    ):
        raise ValueError("Persistence API must use HTTPS or a trusted local origin.")
    return base_url.rstrip("/")


def _bearer(token_file: Path) -> str:
    token = token_file.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError("Persistence API capability file is empty.")
    return f"Bearer {token}"


def post_source_outcome(
    base_url: str,
    token_file: Path,
    dataset_id: str,
    *,
    outcome: str,
    validation_outcome: str,
    detail: str,
    source: NormalizedSource | None = None,
) -> dict[str, object]:
    """Post a complete record snapshot or a record-free failure audit outcome."""
    if source is None:
        payload = {
            "outcome": outcome,
            "source_version": None,
            "retrieved_at": None,
            "transform_version": "eligibility-source-transform-v1",
            "validation_outcome": validation_outcome,
            "detail": detail[:240],
            "records": [],
        }
    else:
        payload = {
            "outcome": "succeeded",
            "source_version": source.source_version,
            "retrieved_at": source.retrieved_at.isoformat(),
            "transform_version": "eligibility-source-transform-v1",
            "validation_outcome": "valid",
            "detail": "Complete source page validated and normalized.",
            "records": [source.record],
        }
    path = f"/v1/public-datasets/{quote(dataset_id, safe='')}/ingestion-runs"
    api_origin = _api_origin(base_url)
    try:
        response = httpx.post(
            api_origin + path,
            json=payload,
            headers={"Authorization": _bearer(token_file)},
            timeout=8.0,
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or result.get("status") != payload["outcome"]:
            raise ValueError("Persistence API returned an unexpected ingestion status.")
        return result
    except (OSError, ValueError, TypeError, httpx.HTTPError) as exc:
        raise PersistenceApiUnavailable(
            "Persistence API public-catalog write failed."
        ) from exc


def read_source_dataset(
    base_url: str,
    token_file: Path,
    dataset_id: str,
    *,
    source_record_id: str | None = None,
) -> dict[str, object]:
    """Read source records and latest outcome by dataset identity, without a tenant."""
    path = f"/v1/public-datasets/{quote(dataset_id, safe='')}/records"
    params = (
        {"source_record_id": source_record_id, "limit": "1", "offset": "0"}
        if source_record_id is not None
        else None
    )
    try:
        response = httpx.get(
            _api_origin(base_url) + path,
            headers={"Authorization": _bearer(token_file)},
            params=params,
            timeout=8.0,
        )
        response.raise_for_status()
        result = response.json()
        dataset = result["dataset"]
        records = result["records"]
        if (
            result.get("status") != "ok"
            or dataset.get("dataset_id") != dataset_id
            or not isinstance(records, list)
        ):
            raise ValueError("Persistence API returned an unexpected dataset scope.")
        return result
    except (OSError, ValueError, KeyError, TypeError, httpx.HTTPError) as exc:
        raise PersistenceApiUnavailable(
            "Persistence API public-catalog read failed."
        ) from exc
