"""Environment-backed settings with typed validation at the configuration boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, TypeAdapter
from pydantic_settings import BaseSettings, SettingsConfigDict

from clusters.eligibility_public_search import BraveSearchProvider, PublicSearchProvider
from example_server.models import TenantProgram


class Settings(BaseSettings):
    """MCP server settings. Override any field via environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    # stdio is for local clients; streamable-http is for remote deployments.
    mcp_transport: Literal["stdio", "streamable-http"] = Field(
        default="stdio",
        description="MCP transport for a local client or remote service.",
    )
    mcp_host: str = Field(
        default="0.0.0.0",
        description="HTTP bind address when streamable-http is selected.",
    )
    mcp_port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        description="HTTP port when streamable-http is selected.",
    )
    mcp_role: Literal["example", "core", "cluster", "tenant"] = Field(
        default="example", description="Runtime role served by this process."
    )
    catalog_backend: Literal["config", "postgres", "persistence_api"] = Field(
        default="config",
        description=(
            "Catalog source for tenant or cluster runtimes; PostgreSQL is tenant-only."
        ),
    )
    registry_backend: Literal["postgres", "persistence_api"] = Field(
        default="postgres",
        description=(
            "Core route registry source; Cloud consumers use the Persistence API."
        ),
    )
    persistence_api_url: str | None = Field(
        default=None,
        description="Base URL for the authenticated Persistence Access API.",
    )
    persistence_api_token_file: Path | None = Field(
        default=None,
        description="Mounted reader capability used by this runtime role.",
    )
    persistence_api_catalog_token_file: Path | None = Field(
        default=None,
        description="Mounted catalog-reader capability used by tenant runtimes.",
    )
    persistence_api_requirements_package_token_file: Path | None = Field(
        default=None,
        description="Mounted write capability limited to Eligibility package records.",
    )
    persistence_api_criteria_ruleset_token_file: Path | None = Field(
        default=None,
        description="Mounted write capability limited to validated criteria rulesets.",
    )
    persistence_api_criteria_ruleset_read_token_file: Path | None = Field(
        default=None,
        description=(
            "Mounted read capability bound to this cluster's criteria rulesets."
        ),
    )
    persistence_api_profile_fixture_token_file: Path | None = Field(
        default=None,
        description=(
            "Dedicated read capability limited to allowlisted synthetic fixtures."
        ),
    )
    database_url: SecretStr | None = Field(
        default=None,
        repr=False,
        description="Optional database connection secret supplied by the runtime.",
    )
    database_url_file: Path | None = Field(
        default=None,
        description="Read-only mounted file containing the database connection secret.",
    )
    tenant_id: str = Field(default="", description="Stable ID for this tenant runtime.")
    cluster_id: str = Field(
        default="", description="Pattern cluster ID used by this tenant."
    )
    pattern_version: str = Field(
        default="", description="Exact pattern version used by this tenant."
    )
    cluster_config_json: str = Field(
        default="{}",
        description="Non-secret JSON configuration passed to the selected cluster.",
    )
    tenant_programs_json: str = Field(
        default="[]",
        description="JSON array of public or synthetic tenant program records.",
    )
    cluster_mcp_endpoints_json: str = Field(
        default="{}",
        description="JSON map of active pattern IDs to private cluster MCP endpoints.",
    )
    public_search_provider: Literal["brave"] | None = Field(
        default=None,
        description=(
            "Optional server-side public-search provider for current cluster versions."
        ),
    )
    public_search_api_token_file: Path | None = Field(
        default=None,
        description="Mounted secret file for the configured public-search provider.",
    )
    public_search_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=10,
        description="Per-request timeout for the bounded external search adapter.",
    )

    @property
    def tenant_programs(self) -> list[TenantProgram]:
        """Parse and validate configured tenant catalog records."""
        return TypeAdapter(list[TenantProgram]).validate_json(self.tenant_programs_json)

    @property
    def cluster_config(self) -> dict[str, object]:
        """Parse cluster-specific, non-secret configuration."""
        return TypeAdapter(dict[str, object]).validate_json(self.cluster_config_json)

    @property
    def cluster_mcp_endpoints(self) -> dict[str, str]:
        """Parse configured private cluster MCP endpoints by semantic pattern ID."""
        endpoints = TypeAdapter(dict[str, str]).validate_json(
            self.cluster_mcp_endpoints_json
        )
        for pattern_id, endpoint in endpoints.items():
            parsed = urlsplit(endpoint)
            if (
                not pattern_id
                or parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("Cluster MCP endpoint configuration is invalid.")
        return endpoints

    def resolved_public_search_provider(self) -> PublicSearchProvider | None:
        """Build an optional external search adapter without exposing its token."""
        if (
            self.public_search_provider is None
            or self.public_search_api_token_file is None
        ):
            return None
        return BraveSearchProvider(
            token_file=self.public_search_api_token_file,
            timeout_seconds=self.public_search_timeout_seconds,
        )

    def resolved_database_url(self) -> SecretStr:
        """Load the database secret from exactly one configured secure source."""
        if self.database_url is not None and self.database_url_file is not None:
            raise ValueError("Configure only one database URL source.")
        if self.database_url_file is not None:
            try:
                value = self.database_url_file.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ValueError(
                    "The configured database secret file is unavailable."
                ) from exc
            if not value:
                raise ValueError("The configured database secret file is empty.")
            return SecretStr(value)
        if (
            self.database_url is None
            or not self.database_url.get_secret_value().strip()
        ):
            raise ValueError("PostgreSQL catalog mode requires a database secret.")
        return self.database_url

    def resolved_persistence_api(self) -> tuple[str, Path]:
        """Return the configured API URL and mounted token path, failing closed."""
        if self.persistence_api_token_file is None:
            raise ValueError(
                "Persistence API mode requires its URL and mounted reader token."
            )
        return self._validated_persistence_api_origin(), self.persistence_api_token_file

    def resolved_persistence_api_catalog(self) -> tuple[str, Path]:
        """Return catalog API settings, failing closed if its capability is absent."""
        if self.persistence_api_catalog_token_file is None:
            raise ValueError(
                "Persistence API catalog mode requires its URL and mounted "
                "reader token."
            )
        return (
            self._validated_persistence_api_origin(),
            self.persistence_api_catalog_token_file,
        )

    def _validated_persistence_api_origin(self) -> str:
        """Accept HTTPS remotely and one private VM-only Podman service origin."""
        value = (self.persistence_api_url or "").strip().rstrip("/")
        parsed = urlsplit(value)
        remote_origin = parsed.scheme == "https" and parsed.port in {None, 443}
        vm_private_origin = (
            parsed.scheme == "http"
            and parsed.hostname == "mcp-saas-vm-data-api"
            and parsed.port == 8080
        )
        if (
            not (remote_origin or vm_private_origin)
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Persistence API URL must be an approved API origin.")
        return value


settings = Settings()
