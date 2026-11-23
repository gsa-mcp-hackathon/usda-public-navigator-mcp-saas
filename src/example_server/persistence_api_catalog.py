"""HTTP adapter for tenant and shared pattern catalogs via Persistence Access API."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx
from pydantic import TypeAdapter, ValidationError

from example_server.models import TenantProgram
from example_server.request_context import MCP_CORRELATION_ID
from example_server.storage import CatalogRepository, StorageUnavailable

PUBLIC_DATASET_PAGE_SIZE = 1000
MAX_PUBLIC_DATASET_RECORDS = 10_000
MAX_PUBLIC_CATALOG_PAGES = 100


class PersistenceApiCatalogRepository(CatalogRepository):
    """Read tenant semantics and the active shared public program inventory."""

    def __init__(
        self,
        base_url: str,
        token_file: Path,
        *,
        tenant_id: str,
        cluster_id: str,
        pattern_version: str,
        requirements_package_token_file: Path | None = None,
        timeout_seconds: float = 3.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token_file = token_file
        self._tenant_id = tenant_id
        self._cluster_id = cluster_id
        self._pattern_version = pattern_version
        self._requirements_package_token_file = requirements_package_token_file
        self._timeout_seconds = timeout_seconds

    def list_programs(self, tenant_id: str, cluster_id: str) -> list[TenantProgram]:
        """Return this tenant's validated catalog rows; fail closed on API errors."""
        if tenant_id != self._tenant_id or cluster_id != self._cluster_id:
            raise StorageUnavailable(
                "Catalog scope does not match this tenant runtime."
            )
        try:
            token = self._token_file.read_text(encoding="utf-8").strip()
            if not token:
                raise ValueError("Persistence API catalog token is empty.")
            path = (
                f"/v1/tenants/{quote(self._tenant_id, safe='')}/patterns/"
                f"{quote(self._cluster_id, safe='')}/"
                f"{quote(self._pattern_version, safe='')}/programs"
            )
            headers = {"Authorization": f"Bearer {token}"}
            correlation_id = MCP_CORRELATION_ID.get()
            if correlation_id is not None:
                headers["X-MCP-Correlation-Id"] = correlation_id
            response = httpx.get(
                self._base_url + path,
                headers=headers,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            if (
                payload.get("tenant_id") != self._tenant_id
                or payload.get("cluster_id") != self._cluster_id
                or payload.get("pattern_version") != self._pattern_version
            ):
                raise ValueError("Persistence API returned a different catalog scope.")
            return TypeAdapter(list[TenantProgram]).validate_python(payload["programs"])
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            ValidationError,
            httpx.HTTPError,
        ) as exc:
            raise StorageUnavailable("Persistence API catalog read failed.") from exc

    def read_public_dataset(self, dataset_id: str) -> dict[str, object]:
        """Read a listed shared dataset; tenant bindings are not an allowlist."""
        return self.read_cluster_public_dataset(dataset_id)

    def list_pattern_cluster_bindings(self) -> dict[str, object]:
        """Read active public catalog bindings shared by this pattern version."""
        try:
            token = self._token_file.read_text(encoding="utf-8").strip()
            if not token:
                raise ValueError("Persistence API catalog token is empty.")
            path = (
                f"/v1/patterns/{quote(self._cluster_id, safe='')}/"
                "catalog-bindings/programs"
            )
            response = httpx.get(
                self._base_url + path,
                headers={"Authorization": f"Bearer {token}"},
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            if (
                not isinstance(payload, dict)
                or payload.get("status") != "ok"
                or payload.get("cluster_id") != self._cluster_id
                or payload.get("catalog_namespace") != "programs"
                or not isinstance(payload.get("bindings"), list)
                or not isinstance(payload.get("truncated"), bool)
                or not isinstance(payload.get("inventory_datasets", []), list)
                or not isinstance(payload.get("inventory_truncated", False), bool)
            ):
                raise ValueError(
                    "Persistence API returned an unexpected pattern catalog."
                )
            bindings = payload["bindings"]
            inventory_datasets = payload.get("inventory_datasets", [])
            for binding in bindings:
                if (
                    not isinstance(binding, dict)
                    or not isinstance(binding.get("tenant_id"), str)
                    or not isinstance(binding.get("dataset_id"), str)
                    or not isinstance(binding.get("pattern_version"), str)
                    or not isinstance(binding.get("source_id"), str)
                    or not isinstance(binding.get("binding_context"), dict)
                    or binding["binding_context"].get("catalog_namespace") != "programs"
                    or binding["binding_context"].get("source_id")
                    != binding["source_id"]
                ):
                    raise ValueError(
                        "Persistence API returned an invalid catalog binding."
                    )
            for dataset in inventory_datasets:
                if (
                    not isinstance(dataset, dict)
                    or not isinstance(dataset.get("dataset_id"), str)
                    or not isinstance(dataset.get("source_id"), str)
                    or not isinstance(dataset.get("title"), str)
                    or not isinstance(dataset.get("schema_version"), str)
                ):
                    raise ValueError(
                        "Persistence API returned an invalid inventory dataset."
                    )
            return {
                "bindings": bindings,
                "truncated": payload["truncated"],
                "inventory_datasets": inventory_datasets,
                "inventory_truncated": payload.get("inventory_truncated", False),
            }
        except (
            OSError,
            ValueError,
            TypeError,
            AttributeError,
            httpx.HTTPError,
        ) as exc:
            raise StorageUnavailable(
                "Persistence API pattern catalog read failed."
            ) from exc

    def list_public_catalog_inventory(
        self, *, limit: int = 25, cursor: str | None = None
    ) -> dict[str, object]:
        """Read one agent-visible page of shared source and dataset status."""
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise ValueError(
                "Public catalog inventory limit must be between 1 and 100."
            )
        if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 2048):
            raise ValueError("Public catalog inventory cursor is invalid.")
        try:
            token = self._token_file.read_text(encoding="utf-8").strip()
            if not token:
                raise ValueError("Persistence API catalog token is empty.")
            query = {"limit": limit}
            if cursor is not None:
                query["cursor"] = cursor
            response = httpx.get(
                self._base_url + "/v1/public-catalog/inventory?" + urlencode(query),
                headers={"Authorization": f"Bearer {token}"},
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            items = payload.get("items") if isinstance(payload, dict) else None
            if (
                not isinstance(payload, dict)
                or payload.get("status") != "ok"
                or not isinstance(items, list)
                or len(items) > limit
                or not isinstance(payload.get("truncated"), bool)
                or (
                    payload.get("next_cursor") is not None
                    and not isinstance(payload.get("next_cursor"), str)
                )
            ):
                raise ValueError("Persistence API returned an invalid inventory page.")
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError(
                        "Persistence API returned an invalid inventory item."
                    )
                source = item.get("source")
                dataset = item.get("dataset")
                if (
                    not isinstance(source, dict)
                    or not isinstance(source.get("source_id"), str)
                    or not isinstance(dataset, dict)
                    or not isinstance(dataset.get("dataset_id"), str)
                    or not isinstance(dataset.get("metadata"), dict)
                    or item.get("registration_status") != "registered"
                    or item.get("data_status")
                    not in {
                        "loaded",
                        "partial",
                        "stale",
                        "unavailable",
                        "registered_no_records",
                    }
                    or item.get("coverage_status")
                    not in {
                        "unknown",
                        "partial",
                        "fresh_as_of_last_check",
                        "stale",
                        "unavailable",
                        "refreshing",
                    }
                    or not isinstance(item.get("record_count"), int)
                    or isinstance(item.get("record_count"), bool)
                    or item.get("record_count", -1) < 0
                    or not isinstance(item.get("searchable"), bool)
                    or item.get("criteria_coverage")
                    not in {"mapped", "partial", "not_mapped", "not_assessed"}
                    or not isinstance(item.get("freshness"), dict)
                ):
                    raise ValueError(
                        "Persistence API returned an invalid inventory item."
                    )
            return {
                "items": items,
                "truncated": payload["truncated"],
                "next_cursor": payload.get("next_cursor"),
            }
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            httpx.HTTPError,
        ) as exc:
            raise StorageUnavailable(
                "Persistence API public inventory read failed."
            ) from exc

    def read_cluster_public_dataset_page(
        self,
        dataset_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
        source_record_id: str | None = None,
    ) -> dict[str, object]:
        """Read one bounded normalized record page from a listed shared dataset."""
        self._require_cluster_inventory_dataset(dataset_id)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 1000
            or isinstance(offset, bool)
            or not isinstance(offset, int)
            or not 0 <= offset <= 10_000_000
            or (
                source_record_id is not None
                and (
                    not isinstance(source_record_id, str)
                    or not source_record_id
                    or len(source_record_id) > 160
                )
            )
        ):
            raise ValueError("Public dataset page is outside its configured bounds.")
        try:
            token = self._token_file.read_text(encoding="utf-8").strip()
            if not token:
                raise ValueError("Persistence API catalog token is empty.")
            path = f"/v1/public-datasets/{quote(dataset_id, safe='')}/records"
            headers = {"Authorization": f"Bearer {token}"}
            correlation_id = MCP_CORRELATION_ID.get()
            if correlation_id is not None:
                headers["X-MCP-Correlation-Id"] = correlation_id
            response = httpx.get(
                self._base_url + path,
                params={
                    "limit": limit,
                    "offset": offset,
                    **(
                        {"source_record_id": source_record_id}
                        if source_record_id is not None
                        else {}
                    ),
                },
                headers=headers,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            if (
                not isinstance(payload, dict)
                or payload.get("status") != "ok"
                or not isinstance(payload.get("dataset"), dict)
                or payload["dataset"].get("dataset_id") != dataset_id
                or not isinstance(payload.get("records"), list)
                or not isinstance(payload.get("freshness"), dict)
                or not isinstance(payload.get("pagination"), dict)
                or payload["pagination"].get("limit") != limit
                or payload["pagination"].get("offset") != offset
                or not isinstance(payload["pagination"].get("total_count"), int)
                or (
                    source_record_id is not None
                    and (
                        payload["pagination"].get("total_count") > 1
                        or any(
                            not isinstance(record, dict)
                            or record.get("source_record_id") != source_record_id
                            for record in payload["records"]
                        )
                    )
                )
            ):
                raise ValueError("Persistence API returned an invalid dataset page.")
            return payload
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            httpx.HTTPError,
        ) as exc:
            raise StorageUnavailable(
                "Persistence API dataset page read failed."
            ) from exc

    def read_cluster_public_dataset(self, dataset_id: str) -> dict[str, object]:
        """Read a dataset only after it appears in the active pattern catalog."""
        self._require_cluster_inventory_dataset(dataset_id, allow_pattern_binding=True)
        return self._read_cluster_public_dataset_records(dataset_id)

    def read_cluster_public_dataset_from_catalog_snapshot(
        self,
        dataset_id: str,
        *,
        catalog_snapshot: Mapping[str, object],
    ) -> dict[str, object]:
        """Read a dataset already verified in this request's fresh catalog snapshot.

        Search loads the active bindings and inventory once before reading datasets.
        Reusing that request-scoped snapshot avoids an identical inventory request
        for every dataset while retaining the same visibility check.
        """
        if not isinstance(dataset_id, str) or not dataset_id.strip():
            raise StorageUnavailable("Public dataset identifier is invalid.")
        inventory_items = catalog_snapshot.get("inventory_items")
        if isinstance(inventory_items, list):
            visible_dataset_ids: set[str] = set()
            for item in inventory_items:
                dataset = item.get("dataset") if isinstance(item, Mapping) else None
                metadata = (
                    dataset.get("metadata") if isinstance(dataset, Mapping) else None
                )
                inventory_dataset_id = (
                    dataset.get("dataset_id") if isinstance(dataset, Mapping) else None
                )
                if (
                    isinstance(item, Mapping)
                    and item.get("registration_status") == "registered"
                    and item.get("searchable") is True
                    and item.get("data_status") in {"loaded", "partial", "stale"}
                    and isinstance(metadata, Mapping)
                    and metadata.get("catalog_namespace") == "programs"
                    and isinstance(inventory_dataset_id, str)
                ):
                    visible_dataset_ids.add(inventory_dataset_id)
        else:
            inventory_datasets = catalog_snapshot.get("inventory_datasets")
            bindings = catalog_snapshot.get("bindings")
            if not isinstance(inventory_datasets, list) or not isinstance(
                bindings, list
            ):
                raise StorageUnavailable("Public catalog snapshot is invalid.")
            visible_dataset_ids = {
                item.get("dataset_id")
                for item in inventory_datasets
                if isinstance(item, Mapping) and isinstance(item.get("dataset_id"), str)
            }
            visible_dataset_ids.update(
                item.get("dataset_id")
                for item in bindings
                if isinstance(item, Mapping) and isinstance(item.get("dataset_id"), str)
            )
        if dataset_id not in visible_dataset_ids:
            raise StorageUnavailable(
                "Public dataset is not present in the current catalog snapshot."
            )
        return self._read_cluster_public_dataset_records(dataset_id)

    def _read_cluster_public_dataset_records(
        self, dataset_id: str
    ) -> dict[str, object]:
        """Read all active pages after a caller has verified dataset visibility."""
        try:
            token = self._token_file.read_text(encoding="utf-8").strip()
            if not token:
                raise ValueError("Persistence API catalog token is empty.")
            path = f"/v1/public-datasets/{quote(dataset_id, safe='')}/records"
            offset = 0
            first_page: dict[str, object] | None = None
            records: list[dict[str, object]] = []
            while True:
                headers = {"Authorization": f"Bearer {token}"}
                correlation_id = MCP_CORRELATION_ID.get()
                if correlation_id is not None:
                    headers["X-MCP-Correlation-Id"] = correlation_id
                response = httpx.get(
                    self._base_url + path,
                    params={"limit": PUBLIC_DATASET_PAGE_SIZE, "offset": offset},
                    headers=headers,
                    timeout=self._timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                dataset = payload.get("dataset") if isinstance(payload, dict) else None
                page_records = (
                    payload.get("records") if isinstance(payload, dict) else None
                )
                pagination = (
                    payload.get("pagination") if isinstance(payload, dict) else None
                )
                if (
                    not isinstance(payload, dict)
                    or payload.get("status") != "ok"
                    or not isinstance(dataset, dict)
                    or dataset.get("dataset_id") != dataset_id
                    or not isinstance(page_records, list)
                    or not isinstance(payload.get("freshness"), dict)
                    or not isinstance(pagination, dict)
                    or pagination.get("limit") != PUBLIC_DATASET_PAGE_SIZE
                    or pagination.get("offset") != offset
                    or not isinstance(pagination.get("total_count"), int)
                ):
                    raise ValueError(
                        "Persistence API returned an unexpected shared dataset page."
                    )
                total_count = pagination["total_count"]
                if not 0 <= total_count <= MAX_PUBLIC_DATASET_RECORDS:
                    raise ValueError(
                        "Shared dataset exceeds the configured record-read bound."
                    )
                if first_page is None:
                    first_page = payload
                elif dataset != first_page.get("dataset") or total_count != first_page[
                    "pagination"
                ].get("total_count"):
                    raise ValueError(
                        "Shared dataset changed during paginated readback."
                    )
                if any(not isinstance(item, dict) for item in page_records):
                    raise ValueError("Shared dataset page contains an invalid record.")
                records.extend(page_records)
                if len(records) > total_count:
                    raise ValueError("Shared dataset pagination exceeded its total.")
                next_offset = pagination.get("next_offset")
                if next_offset is None:
                    if len(records) != total_count:
                        raise ValueError("Shared dataset pagination ended early.")
                    break
                if next_offset != len(records) or next_offset <= offset:
                    raise ValueError("Shared dataset pagination did not advance.")
                offset = next_offset
            if first_page is None:
                raise ValueError("Persistence API returned no shared dataset page.")
            first_page["records"] = records
            first_page["pagination"] = {
                "limit": PUBLIC_DATASET_PAGE_SIZE,
                "offset": 0,
                "total_count": len(records),
                "next_offset": None,
            }
            return first_page
        except (
            OSError,
            ValueError,
            TypeError,
            AttributeError,
            httpx.HTTPError,
        ) as exc:
            raise StorageUnavailable(
                "Persistence API shared dataset read failed."
            ) from exc

    def _require_cluster_inventory_dataset(
        self, dataset_id: str, *, allow_pattern_binding: bool = False
    ) -> None:
        """Check shared inventory on each request, independent of process state."""
        if not isinstance(dataset_id, str) or not dataset_id.strip():
            raise StorageUnavailable("Public dataset identifier is invalid.")
        cursor: str | None = None
        seen_cursors: set[str] = set()
        inventory_complete = False
        for _ in range(MAX_PUBLIC_CATALOG_PAGES):
            page = self.list_public_catalog_inventory(limit=100, cursor=cursor)
            items = page["items"]
            if any(
                isinstance(item, dict)
                and isinstance(item.get("dataset"), dict)
                and item["dataset"].get("dataset_id") == dataset_id
                for item in items
            ):
                return
            if not page["truncated"]:
                inventory_complete = True
                break
            next_cursor = page["next_cursor"]
            if (
                not isinstance(next_cursor, str)
                or not next_cursor
                or next_cursor in seen_cursors
            ):
                raise StorageUnavailable(
                    "Public catalog inventory pagination is invalid."
                )
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        if not inventory_complete:
            raise StorageUnavailable(
                "Public dataset visibility could not be confirmed within "
                "the page limit."
            )
        if allow_pattern_binding:
            bindings = self.list_pattern_cluster_bindings()
            if any(
                binding.get("dataset_id") == dataset_id
                for binding in bindings["bindings"]
            ):
                return
            if bindings["truncated"]:
                raise StorageUnavailable(
                    "Public dataset visibility could not be confirmed within "
                    "the binding page."
                )
        raise StorageUnavailable(
            "Public dataset is not present in the current shared inventory "
            "or pattern bindings."
        )

    def write_requirements_package(
        self, package: dict[str, object]
    ) -> dict[str, object]:
        """Persist one immutable packet through its dedicated capability."""
        if self._requirements_package_token_file is None:
            raise StorageUnavailable("Requirements package writer is not configured.")
        try:
            token = self._requirements_package_token_file.read_text(
                encoding="utf-8"
            ).strip()
            if not token:
                raise ValueError("Package writer capability is empty.")
            response = httpx.post(
                self._base_url + "/v1/eligibility/requirements-packages",
                json=package,
                headers={"Authorization": f"Bearer {token}"},
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            result = response.json()
            if (
                not isinstance(result, dict)
                or result.get("status") != "stored"
                or result.get("package_id") != package.get("package_id")
                or not isinstance(result.get("created"), bool)
            ):
                raise ValueError("Persistence API returned an invalid package receipt.")
            return result
        except (
            OSError,
            ValueError,
            TypeError,
            AttributeError,
            httpx.HTTPError,
        ) as exc:
            raise StorageUnavailable("Requirements package write failed.") from exc

    def enrich_requirements_package(
        self,
        package_id: str,
        *,
        tenant_id: str,
        profile_id: str,
        expected_revision: int,
        details: list[dict[str, object]],
    ) -> dict[str, object]:
        """Append a source-cited detail revision using compare-and-set."""
        if self._requirements_package_token_file is None:
            raise StorageUnavailable("Requirements package writer is not configured.")
        try:
            token = self._requirements_package_token_file.read_text(
                encoding="utf-8"
            ).strip()
            if not token:
                raise ValueError("Package writer capability is empty.")
            response = httpx.post(
                self._base_url
                + "/v1/eligibility/requirements-packages/"
                + quote(package_id, safe="")
                + "/enrich",
                json={
                    "tenant_id": tenant_id,
                    "profile_id": profile_id,
                    "expected_revision": expected_revision,
                    "details": details,
                },
                headers={"Authorization": f"Bearer {token}"},
                timeout=self._timeout_seconds,
            )
            result = response.json()
            if response.status_code == 409:
                if (
                    not isinstance(result, dict)
                    or result.get("status") != "revision_conflict"
                    or isinstance(result.get("current_revision"), bool)
                    or not isinstance(result.get("current_revision"), int)
                ):
                    raise ValueError(
                        "Persistence API returned an invalid revision conflict."
                    )
                return result
            response.raise_for_status()
            if (
                not isinstance(result, dict)
                or result.get("status") not in {"enriched", "unchanged"}
                or result.get("package_id") != package_id
                or isinstance(result.get("revision"), bool)
                or not isinstance(result.get("revision"), int)
                or not isinstance(result.get("package"), dict)
            ):
                raise ValueError(
                    "Persistence API returned an invalid enrichment receipt."
                )
            return result
        except (
            OSError,
            ValueError,
            TypeError,
            AttributeError,
            httpx.HTTPError,
        ) as exc:
            raise StorageUnavailable("Requirements package enrichment failed.") from exc

    def read_requirements_package(self, package_id: str) -> dict[str, object]:
        """Read back one requirements packet through its dedicated capability."""
        return self._read_requirements_package(package_id)

    def read_scoped_requirements_package(
        self, package_id: str, tenant_id: str, profile_id: str
    ) -> dict[str, object]:
        """Read one packet only when its tenant and synthetic profile match."""
        return self._read_requirements_package(
            package_id,
            params={"tenant_id": tenant_id, "profile_id": profile_id},
        )

    def read_scoped_requirements_package_state(
        self, package_id: str, tenant_id: str, profile_id: str
    ) -> tuple[dict[str, object], int]:
        """Read a scoped package and its compare-and-set revision together."""
        result = self._read_requirements_package(
            package_id,
            params={"tenant_id": tenant_id, "profile_id": profile_id},
            include_revision=True,
        )
        assert isinstance(result, tuple)
        return result

    def _read_requirements_package(
        self,
        package_id: str,
        *,
        params: dict[str, str] | None = None,
        include_revision: bool = False,
    ) -> dict[str, object] | tuple[dict[str, object], int]:
        """Read a packet through the dedicated persistence capability."""
        if self._requirements_package_token_file is None:
            raise StorageUnavailable("Requirements package writer is not configured.")
        try:
            token = self._requirements_package_token_file.read_text(
                encoding="utf-8"
            ).strip()
            if not token:
                raise ValueError("Package writer capability is empty.")
            response = httpx.get(
                self._base_url
                + "/v1/eligibility/requirements-packages/"
                + quote(package_id, safe=""),
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            result = response.json()
            package = result.get("package") if isinstance(result, dict) else None
            if (
                not isinstance(result, dict)
                or result.get("status") != "ok"
                or not isinstance(package, dict)
                or package.get("package_id") != package_id
            ):
                raise ValueError(
                    "Persistence API returned an invalid package readback."
                )
            if include_revision:
                revision = result.get("revision")
                if (
                    isinstance(revision, bool)
                    or not isinstance(revision, int)
                    or revision < 1
                ):
                    raise ValueError(
                        "Persistence API returned an invalid package revision."
                    )
                return package, revision
            return package
        except (
            OSError,
            ValueError,
            TypeError,
            AttributeError,
            httpx.HTTPError,
        ) as exc:
            raise StorageUnavailable("Requirements package readback failed.") from exc

    def list_requirements_packages(
        self,
        tenant_id: str,
        profile_id: str,
        *,
        limit: int = 25,
        cursor: str | None = None,
    ) -> dict[str, object]:
        """List bounded package summaries for one synthetic profile."""
        if self._requirements_package_token_file is None:
            raise StorageUnavailable("Requirements package reader is not configured.")
        if not 1 <= limit <= 100:
            raise ValueError("Package page size must be between 1 and 100.")
        params: dict[str, str | int] = {
            "tenant_id": tenant_id,
            "profile_id": profile_id,
            "limit": limit,
        }
        if cursor is not None:
            params["cursor"] = cursor
        try:
            token = self._requirements_package_token_file.read_text(
                encoding="utf-8"
            ).strip()
            if not token:
                raise ValueError("Package writer capability is empty.")
            response = httpx.get(
                self._base_url + "/v1/eligibility/requirements-packages",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            result = response.json()
            packages = result.get("packages") if isinstance(result, dict) else None
            if (
                not isinstance(result, dict)
                or result.get("status") != "available"
                or not isinstance(packages, list)
                or not isinstance(result.get("truncated"), bool)
                or (
                    result.get("next_cursor") is not None
                    and not isinstance(result.get("next_cursor"), str)
                )
                or any(
                    not isinstance(package, dict)
                    or not all(
                        isinstance(package.get(field), str)
                        for field in (
                            "package_id",
                            "program_id",
                            "program_title",
                            "created_at",
                        )
                    )
                    for package in packages
                )
            ):
                raise ValueError("Persistence API returned an invalid package list.")
            return result
        except (
            OSError,
            ValueError,
            TypeError,
            AttributeError,
            httpx.HTTPError,
        ) as exc:
            raise StorageUnavailable("Requirements package listing failed.") from exc

    def read_active_public_search_policy(self) -> dict[str, object]:
        """Read the public approved-policy projection with the catalog capability."""
        try:
            token = self._token_file.read_text(encoding="utf-8").strip()
            if not token:
                raise ValueError("Persistence API catalog token is empty.")
            response = httpx.get(
                self._base_url + "/v1/public-search-policy/active",
                headers={"Authorization": f"Bearer {token}"},
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("status") not in {
                "active",
                "inactive",
                "unsupported_active_manifest",
            }:
                raise ValueError("Persistence API returned an invalid search policy.")
            if payload["status"] == "active":
                reference = payload.get("active_reference")
                bundle = payload.get("policy_bundle")
                if (
                    not isinstance(reference, dict)
                    or not isinstance(reference.get("manifest_id"), str)
                    or not isinstance(reference.get("manifest_version"), str)
                    or not isinstance(reference.get("content_sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", reference["content_sha256"])
                    or not isinstance(bundle, dict)
                    or not isinstance(bundle.get("entries"), list)
                ):
                    raise ValueError(
                        "Persistence API returned an invalid active policy."
                    )
            return payload
        except (
            OSError,
            ValueError,
            TypeError,
            AttributeError,
            httpx.HTTPError,
        ) as exc:
            raise StorageUnavailable(
                "Active public-search policy read failed."
            ) from exc
