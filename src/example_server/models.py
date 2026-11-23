"""Pydantic models and enums for tool parameters and responses.

Typed parameters give MCP clients a precise schema, which helps the LLM call
your tools correctly. Prefer enums for closed sets of values and Pydantic
models for structured inputs/outputs.

These are illustrative — replace them with models for your own service.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class ResponseFormat(str, Enum):
    """How a tool should shape its return value.

    - JSON → machine-readable dict/list (best for chaining tool calls)
    - MARKDOWN → human-readable text (best for direct display)
    """

    JSON = "json"
    MARKDOWN = "markdown"


class PaginationParams(BaseModel):
    """Reusable pagination inputs for list-style tools.

    Federal APIs almost always paginate. Expose these to the caller (or handle
    paging internally) and return `has_more` / `next_offset` in your response.
    """

    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of items to return (1–100).",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Number of items to skip from the start of the result set.",
    )


class TenantProgram(BaseModel):
    """Public or synthetic program record exposed by one tenant.

    Extra fields are retained so tenant catalogs can add reviewed dimensions
    without changing the shared transport contract.
    """

    model_config = ConfigDict(extra="allow")

    program_id: str = Field(
        min_length=1, description="Stable tenant-scoped program ID."
    )
    title: str = Field(min_length=1, description="Public program name.")
    source_id: str = Field(min_length=1, description="Catalog source reference.")
    jurisdiction: str = Field(
        min_length=1, description="Federal, state, or local scope."
    )
    catalog_version: str = Field(
        default="", description="Version of the tenant catalog record set."
    )
    schema_version: str = Field(
        default="core-v1", description="Version of canonical metadata definitions."
    )
    source_url: HttpUrl | None = Field(
        default=None, description="Public source URL when the record has one."
    )
    attributes: dict[str, object] = Field(
        default_factory=dict,
        description="Namespaced, non-sensitive program dimensions.",
    )
    provenance: dict[str, object] = Field(
        default_factory=dict, description="Source and catalog provenance metadata."
    )


class SyntheticProfile(BaseModel):
    """Synthetic profile with canonical attributes and explicit profile links."""

    profile_id: str
    profile_kind: str
    schema_version: str
    attributes: dict[str, object]
    relationships: list[dict[str, str]] = Field(default_factory=list)
    provenance: dict[str, object] = Field(default_factory=dict)


class PatternRouteResult(BaseModel):
    """Pattern-neutral outcome and provenance for one tenant operation."""

    status: Literal["ok", "unavailable", "incompatible"]
    tenant_id: str | None = None
    cluster_id: str | None = None
    pattern_id: str
    pattern_version: str
    operation_id: str
    source: str
    result: object | None = None
    error: str | None = None


class UseCaseRouteResult(BaseModel):
    """Shared routing response that treats cluster payloads as opaque data."""

    status: Literal["ok", "unknown_use_case", "unavailable", "incompatible"]
    use_case_id: str
    use_case_version: str
    pattern_id: str = ""
    pattern_version: str = ""
    operation_id: str = ""
    correlation_id: str
    results: list[PatternRouteResult] = Field(default_factory=list)


class TenantPublicListing(BaseModel):
    """Shared public listing surfaced without implying mapped eligibility."""

    listing_id: str = Field(min_length=1)
    record_kind: Literal[
        "funding_opportunity", "assistance_program", "referral_resource"
    ]
    title: str = Field(min_length=1)
    agency: str = Field(min_length=1)
    jurisdiction: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_url: HttpUrl | None = None
    criteria_status: Literal["not_assessed", "unavailable"]
    evaluation_available: Literal[False] = False


class TenantCatalogResult(BaseModel):
    """Validated catalog response received from a tenant MCP server."""

    tenant_id: str
    cluster_id: str
    programs: list[TenantProgram]
    public_listings: list[TenantPublicListing] = Field(default_factory=list)
    status: Literal["ok", "partial", "unavailable"] = "ok"
    error: str | None = None
    unavailable_sources: list[str] = Field(default_factory=list)
    catalog_read_failures: list[dict[str, str]] | None = None
