"""HTTP adapters for the platform's authenticated Persistence Access API."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import httpx

from example_server.persistence_api_client import persistence_api_headers
from example_server.registry import RegistryLookupUnavailable, ResolvedUseCase


class PersistenceApiRegistryRepository:
    """Resolve active route metadata without giving the core a database secret."""

    def __init__(
        self,
        base_url: str,
        token_file: Path,
        *,
        timeout_seconds: float = 3.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token_file = token_file
        self._timeout_seconds = timeout_seconds

    def resolve_use_case(
        self, use_case_id: str, use_case_version: str
    ) -> ResolvedUseCase | None:
        """Return persisted compatible routes, ``None`` if the use case is unknown."""
        try:
            token = self._token_file.read_text(encoding="utf-8").strip()
            if not token:
                raise ValueError("Persistence API token is empty.")
            path = (
                f"/v1/registry/use-cases/{quote(use_case_id, safe='')}/"
                f"{quote(use_case_version, safe='')}"
            )
            response = httpx.get(
                self._base_url + path,
                headers=persistence_api_headers(token),
                timeout=self._timeout_seconds,
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return ResolvedUseCase.model_validate(response.json())
        except (OSError, ValueError, httpx.HTTPError) as exc:
            raise RegistryLookupUnavailable(
                "Persistence API registry lookup failed."
            ) from exc
