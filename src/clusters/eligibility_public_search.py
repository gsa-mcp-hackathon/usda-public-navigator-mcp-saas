"""Bounded, read-only search over explicitly approved public-source paths."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

import httpx
from pydantic import Field, ValidationError, model_validator

from clusters.eligibility_contracts import StrictModel
from example_server.storage import StorageUnavailable

POLICY_RELATIVE_PATH = Path("configs/eligibility/public-search-policy.v1.json")
BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
MAX_QUERY_LENGTH = 240
MAX_PAGE_SIZE = 10
# Per-request fan-out bound: entries approved for ONE jurisdiction in a search.
MAX_POLICY_ENTRIES = 12
# Whole-manifest bound: total entries across all jurisdictions in one policy
# version. Large enough for one entry set per state; it is not a fan-out limit.
MAX_POLICY_MANIFEST_ENTRIES = 500
MAX_PROVIDER_OFFSET = 9
EXHAUSTED_PROVIDER_OFFSET = MAX_PROVIDER_OFFSET + 1
CAPPED_PROVIDER_OFFSET = MAX_PROVIDER_OFFSET + 2
MAX_RESPONSE_BYTES = 1_000_000
MAX_REDIRECT_HOPS = 5
DEFAULT_TIMEOUT_SECONDS = 5.0
logger = logging.getLogger(__name__)


class PublicSearchPolicyEntry(StrictModel):
    """A versioned discovery scope; matching a domain class does not verify a source."""

    entry_id: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9.-]+$")
    source_id: str = Field(min_length=2, max_length=120)
    # Two-character top-level labels such as.us are valid discovery scopes.
    host: str = Field(min_length=2, max_length=253)
    path_prefixes: list[str] = Field(min_length=1, max_length=10)
    scope_kind: Literal["exact_host_path", "domain_suffix", "related_domain"] = (
        "exact_host_path"
    )
    authority_class: Literal[
        "federal",
        "state",
        "local",
        "tribal",
        "institutional",
        "approved_operator",
        "unverified_domain_class",
        "unverified_related_domain",
    ]
    owner: str = Field(min_length=2, max_length=200)
    authority_jurisdiction: str = Field(pattern=r"^(?:US(?:-[A-Z]{2})?|unknown)$")
    permitted_uses: list[
        Literal["discovery", "criteria", "application_guidance", "context"]
    ] = Field(min_length=1, max_length=4)
    verification_evidence: str = Field(min_length=10, max_length=1200)
    review_status: Literal["approved_for_bounded_discovery"]
    reviewed_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    request_timeout_seconds: float | None = Field(
        default=None,
        ge=3.0,
        le=240.0,
        description=(
            "Optional per-source HTTPS request timeout. When absent, the source "
            "reader uses its bounded 180-second default."
        ),
    )

    @model_validator(mode="after")
    def validate_exact_scope(self) -> PublicSearchPolicyEntry:
        """Reject malformed scopes and require broad matches to stay unverified."""
        if (
            self.host != self.host.lower()
            or self.host.endswith(".")
            or "*" in self.host
            or "/" in self.host
            or "@" in self.host
        ):
            raise ValueError(
                "Policy host/suffix must be normalized and contain no wildcard."
            )
        if any(
            not path.startswith("/")
            or "?" in path
            or "#" in path
            or "\\" in path
            or any(part in {".", ".."} for part in path.split("/"))
            for path in self.path_prefixes
        ):
            raise ValueError("Policy paths must be exact absolute path prefixes.")
        if self.scope_kind == "domain_suffix":
            if (
                self.host not in {"gov", "org", "us", "edu"}
                or self.path_prefixes != ["/"]
                or self.authority_class != "unverified_domain_class"
                or self.authority_jurisdiction != "unknown"
                or self.permitted_uses != ["discovery"]
            ):
                raise ValueError(
                    "Domain-class scopes must be unverified discovery-only entries."
                )
        elif self.scope_kind == "related_domain":
            if (
                not self.host.endswith(".com")
                or self.path_prefixes != ["/"]
                or self.authority_class != "unverified_related_domain"
                or "https://" not in self.verification_evidence
            ):
                raise ValueError(
                    "Related-domain scopes require an evidenced .com lead domain."
                )
        return self


class PublicSearchPolicy(StrictModel):
    """Versioned public discovery policy snapshot."""

    policy_id: str = Field(min_length=2, max_length=100)
    policy_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    reviewed_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    entries: list[PublicSearchPolicyEntry] = Field(
        max_length=MAX_POLICY_MANIFEST_ENTRIES
    )

    @model_validator(mode="after")
    def validate_unique_entries(self) -> PublicSearchPolicy:
        """Require unique policy entry ids."""
        ids = [entry.entry_id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("Policy entry IDs must be unique.")
        return self

    @classmethod
    def load(cls, path: Path | None = None) -> PublicSearchPolicy:
        """Load the checked-in policy from source or installed package roots."""
        target = path or _find_policy_path(Path(__file__))
        return cls.model_validate_json(target.read_text(encoding="utf-8"))

    def approved_entries(self, jurisdiction_code: str) -> list[PublicSearchPolicyEntry]:
        """Return discovery scopes applicable to this US jurisdiction."""
        requested_state = jurisdiction_code.partition("-")[2]
        return [
            entry
            for entry in self.entries
            if "discovery" in entry.permitted_uses
            and (
                entry.scope_kind == "domain_suffix"
                or entry.authority_jurisdiction == "US"
                or (
                    requested_state
                    and entry.authority_jurisdiction == f"US-{requested_state}"
                )
            )
        ]


class ProviderResult(StrictModel):
    """Minimal untrusted search provider result, before policy filtering."""

    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=8, max_length=2048)
    description: str = Field(default="", max_length=1200)


class ProviderResponse(StrictModel):
    """Search provider's bounded, normalized response contract."""

    provider_id: str = Field(min_length=2, max_length=80)
    results: list[ProviderResult] = Field(max_length=MAX_PAGE_SIZE)


class PublicSearchSourceIdentity(StrictModel):
    """Policy evidence for the authority and allowed use of a returned lead."""

    source_id: str
    entry_id: str
    scope_kind: Literal[
        "exact_host_path", "domain_suffix", "related_domain", "off_manifest"
    ]
    authority_class: Literal[
        "federal",
        "state",
        "local",
        "tribal",
        "institutional",
        "approved_operator",
        "unverified_domain_class",
        "unverified_related_domain",
        "unverified_off_manifest",
    ]
    owner: str
    authority_jurisdiction: str
    permitted_uses: list[
        Literal["discovery", "criteria", "application_guidance", "context"]
    ]
    verification_evidence: str
    review_status: Literal["approved_for_bounded_discovery", "not_in_manifest"]
    source_verification_status: Literal["unverified"] = "unverified"
    reviewed_at: str
    policy_id: str
    policy_version: str
    criteria_status: Literal["not_assessed"]
    catalog_status: Literal["not_checked"]
    retrieved_at_utc: str


class PublicSearchSourceHealth(StrictModel):
    """Per-policy-entry status from the current search request."""

    entry_id: str
    status: Literal[
        "available",
        "timeout",
        "upstream_unavailable",
        "invalid_upstream_response",
        "unavailable",
    ]


class PublicSearchProvider(Protocol):
    """Provider boundary; implementations may use any approved search API."""

    provider_id: str

    async def search(
        self,
        *,
        query: str,
        source: PublicSearchPolicyEntry,
        count: int,
        offset: int,
    ) -> ProviderResponse:
        """Run a public web search for a source and return its results."""


class PublicSearchError(RuntimeError):
    """Sanitized search adapter failure with a stable result classification."""

    def __init__(
        self,
        status: Literal[
            "timeout",
            "upstream_unavailable",
            "invalid_upstream_response",
            "unavailable",
        ],
    ):
        self.status = status
        super().__init__(status)


@dataclass(repr=False)
class BraveSearchProvider:
    """Brave Search adapter; broad suffixes are enforced after provider search."""

    token_file: Path
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)
    provider_id: str = field(default="brave_web_search_api", init=False)

    async def search(
        self,
        *,
        query: str,
        source: PublicSearchPolicyEntry,
        count: int,
        offset: int,
    ) -> ProviderResponse:
        """Run a Brave web search for a source and return its results."""
        try:
            token = _read_api_token(self.token_file)
        except (OSError, ValueError) as exc:
            raise PublicSearchError("unavailable") from exc
        # Brave's site operator targets a specific website/domain, not a TLD
        # class such as.gov. Search broadly for suffix entries, then enforce
        # the approved suffix locally before any lead is returned.
        bounded_query = (
            query
            if source.scope_kind == "domain_suffix"
            else f"{query} site:{source.host}"
        )
        params = {
            "q": bounded_query,
            "count": count,
            "offset": offset,
            "country": "US",
            "safesearch": "moderate",
            "spellcheck": "false",
            "operators": "true",
            "result_filter": "web",
        }
        try:
            provider_error_code = "unknown"
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                async with client.stream(
                    "GET",
                    BRAVE_SEARCH_URL,
                    params=params,
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": token,
                    },
                ) as response:
                    if response.is_error:
                        try:
                            error_body = await _read_limited(response, 8_192)
                            provider_error_code = _provider_error_code(error_body)
                        except (OSError, ValueError):
                            # Diagnostics must never change the failure classification.
                            pass
                    response.raise_for_status()
                    raw_body = await _read_limited(response, MAX_RESPONSE_BYTES)
        except httpx.TimeoutException as exc:
            logger.warning(
                "public_search_provider_failure provider=%s source=%s failure=timeout",
                self.provider_id,
                source.entry_id,
            )
            raise PublicSearchError("timeout") from exc
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "public_search_provider_failure provider=%s source=%s "
                "failure=http_status status_code=%d error_code=%s",
                self.provider_id,
                source.entry_id,
                exc.response.status_code,
                provider_error_code,
            )
            raise PublicSearchError("upstream_unavailable") from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "public_search_provider_failure provider=%s source=%s "
                "failure=transport error_type=%s",
                self.provider_id,
                source.entry_id,
                type(exc).__name__,
            )
            raise PublicSearchError("upstream_unavailable") from exc
        except (OSError, ValueError) as exc:
            logger.warning(
                "public_search_provider_failure provider=%s source=%s "
                "failure=local_io error_type=%s",
                self.provider_id,
                source.entry_id,
                type(exc).__name__,
            )
            raise PublicSearchError("upstream_unavailable") from exc

        try:
            payload = json.loads(raw_body)
            web = payload.get("web", {}) if isinstance(payload, Mapping) else None
            raw_results = web.get("results", []) if isinstance(web, Mapping) else None
            if not isinstance(raw_results, list):
                raise ValueError("Search response has no web results list.")
            results = []
            for item in raw_results[:count]:
                if not isinstance(item, Mapping):
                    continue
                results.append(
                    ProviderResult(
                        title=item.get("title"),
                        url=item.get("url"),
                        description=item.get("description", ""),
                    )
                )
            return ProviderResponse(provider_id=self.provider_id, results=results)
        except (TypeError, ValueError, ValidationError, json.JSONDecodeError) as exc:
            raise PublicSearchError("invalid_upstream_response") from exc


class ExternalProgramLead(StrictModel):
    """Public search lead; source authority and program facts remain unverified."""

    lead_id: str
    title: str
    summary: str
    canonical_source_url: str
    source_identity: PublicSearchSourceIdentity
    authority_jurisdiction: str
    program_jurisdiction: Literal["unknown"] = "unknown"
    catalog_status: Literal["not_checked"] = "not_checked"
    criteria_status: Literal["not_assessed"] = "not_assessed"
    review_status: Literal["unreviewed"] = "unreviewed"


class ExternalSearchRecoveryAction(StrictModel):
    """Honest client-side follow-up when search returns no usable leads."""

    kind: Literal["ask_user_for_program_or_agency_clue"]
    reason: Literal[
        "no_match",
        "partial_coverage",
        "timeout",
        "upstream_unavailable",
        "invalid_upstream_response",
        "provider_unavailable",
    ]
    follow_up_prompt: str = Field(min_length=1, max_length=300)
    retry_later: bool


class ExternalSearchResult(StrictModel):
    """Bounded external discovery with explicit coverage and no-write semantics."""

    contract_version: Literal["public-program-search-v1"] = "public-program-search-v1"
    status: Literal[
        "ok",
        "no_match",
        "partial",
        "policy_not_configured",
        "policy_invalid",
        "policy_unavailable",
        "unsupported_jurisdiction",
        "invalid_request",
        "timeout",
        "upstream_unavailable",
        "invalid_upstream_response",
        "unavailable",
    ]
    query: str
    jurisdiction_code: str
    policy_id: str
    policy_version: str
    provider_id: str | None
    retrieved_at_utc: str
    leads: list[ExternalProgramLead]
    source_health: list[PublicSearchSourceHealth]
    coverage: Literal[
        "federal_and_state",
        "federal_only",
        "broad_domain_classes",
        "policy_limited",
        "unavailable",
    ]
    truncated: bool
    next_cursor: str | None = None
    recovery_action: ExternalSearchRecoveryAction | None = None
    persistence_performed: Literal[False] = False
    profile_accessed: Literal[False] = False
    eligibility_evaluation_performed: Literal[False] = False
    explanation: str


def register_external_search_tool(
    app,
    *,
    provider: PublicSearchProvider | None,
    policy: PublicSearchPolicy | None = None,
    policy_reader: Callable[[], dict[str, object]] | None = None,
) -> None:
    """Register a read-only public-search tool with no catalog/profile writer."""

    @app.tool(name="search_external_program_leads")
    async def search_external_program_leads(
        need_query: str,
        jurisdiction_code: str = "US",
        page_size: int = MAX_PAGE_SIZE,
        cursor: str | None = None,
    ) -> dict[str, object]:
        """Search public sources after first-party inventory review.

        Use this only when shared inventory is thin/unavailable or the user asks
        for broader discovery. Safe results outside the active source manifest
        are returned as off-manifest, unverified leads. This never returns
        catalog records, criteria, eligibility outcomes, profile data, or writes.
        """
        active_policy = policy
        if policy_reader is not None:
            try:
                state = await asyncio.to_thread(policy_reader)
            except StorageUnavailable:
                return _policy_failure_result(
                    need_query,
                    jurisdiction_code,
                    status="policy_unavailable",
                    explanation=(
                        "The active search policy could not be read; external "
                        "search was not performed."
                    ),
                ).model_dump(mode="json")
            try:
                if not isinstance(state, dict):
                    raise ValueError("Active search policy response is invalid.")
                reference = state.get("active_reference")
                bundle = state.get("policy_bundle")
                if state.get("status") == "inactive":
                    return _policy_not_configured_result(
                        need_query, jurisdiction_code
                    ).model_dump(mode="json")
                if (
                    state.get("status") != "active"
                    or not isinstance(reference, dict)
                    or not isinstance(bundle, dict)
                ):
                    return _policy_failure_result(
                        need_query,
                        jurisdiction_code,
                        status="policy_invalid",
                        explanation=(
                            "The active search policy is malformed or unsupported; "
                            "external search was not performed."
                        ),
                    ).model_dump(mode="json")
                active_policy = PublicSearchPolicy.model_validate(
                    {
                        "policy_id": reference.get("manifest_id"),
                        "policy_version": reference.get("manifest_version"),
                        "reviewed_at": bundle.get("reviewed_at"),
                        "entries": bundle.get("entries"),
                    }
                )
            except (ValidationError, ValueError, TypeError, KeyError):
                return _policy_failure_result(
                    need_query,
                    jurisdiction_code,
                    status="policy_invalid",
                    explanation=(
                        "The active search policy failed validation; external "
                        "search was not performed."
                    ),
                ).model_dump(mode="json")
        if active_policy is None:
            return _policy_not_configured_result(
                need_query, jurisdiction_code
            ).model_dump(mode="json")
        result = await search_external_programs(
            need_query,
            jurisdiction_code,
            provider=provider,
            policy=active_policy,
            page_size=page_size,
            cursor=cursor,
        )
        return result.model_dump(mode="json")


def _policy_not_configured_result(
    query: str, jurisdiction_code: str
) -> ExternalSearchResult:
    """Return a distinct empty result when no governed policy is active."""
    return _policy_failure_result(
        query,
        jurisdiction_code,
        status="policy_not_configured",
        explanation=(
            "No active digest-approved public-search policy is available; "
            "external search was not performed."
        ),
    )


def _policy_failure_result(
    query: str,
    jurisdiction_code: str,
    *,
    status: Literal["policy_not_configured", "policy_invalid", "policy_unavailable"],
    explanation: str,
) -> ExternalSearchResult:
    """Build a no-search result for policy setup, validation, or read failures."""
    return ExternalSearchResult(
        status=status,
        query=query[:MAX_QUERY_LENGTH],
        jurisdiction_code=jurisdiction_code[:32],
        policy_id="unconfigured",
        policy_version="0.0.0",
        provider_id=None,
        retrieved_at_utc=datetime.now(UTC).isoformat(),
        leads=[],
        source_health=[],
        coverage="unavailable",
        truncated=False,
        explanation=explanation,
    )


async def search_external_programs(
    need_query: str,
    jurisdiction_code: str,
    *,
    provider: PublicSearchProvider | None,
    policy: PublicSearchPolicy,
    page_size: int = MAX_PAGE_SIZE,
    cursor: str | None = None,
) -> ExternalSearchResult:
    """Search policy-derived domain scopes; off-manifest hits stay unverified leads."""
    query = _validated_query(need_query)
    requested_jurisdiction = _validated_jurisdiction(jurisdiction_code)
    retrieved_at = datetime.now(UTC).isoformat()
    invalid_status = (
        "invalid_request" if query is None or requested_jurisdiction is None else None
    )
    normalized_jurisdiction = requested_jurisdiction or "US"
    if (
        invalid_status
        or isinstance(page_size, bool)
        or not isinstance(page_size, int)
        or not 1 <= page_size <= MAX_PAGE_SIZE
    ):
        return _result(
            status="invalid_request",
            query=query or "",
            jurisdiction=normalized_jurisdiction,
            policy=policy,
            provider_id=getattr(provider, "provider_id", None),
            retrieved_at=retrieved_at,
            explanation=(
                "Provide a short search phrase, a US jurisdiction, and a page size "
                "from 1 to 10."
            ),
        )
    if not normalized_jurisdiction.startswith("US"):
        return _result(
            status="unsupported_jurisdiction",
            query=query,
            jurisdiction=normalized_jurisdiction,
            policy=policy,
            provider_id=getattr(provider, "provider_id", None),
            retrieved_at=retrieved_at,
            explanation=(
                "External program discovery currently supports US jurisdictions only."
            ),
        )
    if provider is None:
        return _result(
            status="unavailable",
            query=query,
            jurisdiction=normalized_jurisdiction,
            policy=policy,
            provider_id=None,
            retrieved_at=retrieved_at,
            explanation="No public-search provider is configured for this runtime.",
        )

    entries = policy.approved_entries(normalized_jurisdiction)
    if not entries:
        return _result(
            status="unsupported_jurisdiction",
            query=query,
            jurisdiction=normalized_jurisdiction,
            policy=policy,
            provider_id=provider.provider_id,
            retrieved_at=retrieved_at,
            explanation="No approved public source paths cover this jurisdiction.",
        )
    if len(entries) > MAX_POLICY_ENTRIES:
        return _result(
            status="unavailable",
            query=query,
            jurisdiction=normalized_jurisdiction,
            policy=policy,
            provider_id=provider.provider_id,
            retrieved_at=retrieved_at,
            explanation="The approved source policy exceeds the search request cap.",
        )

    offsets = _decode_cursor(cursor, query, normalized_jurisdiction, policy, entries)
    if offsets is None:
        return _result(
            status="invalid_request",
            query=query,
            jurisdiction=normalized_jurisdiction,
            policy=policy,
            provider_id=provider.provider_id,
            retrieved_at=retrieved_at,
            explanation=(
                "The continuation cursor is invalid or belongs to another search."
            ),
        )

    semaphore = asyncio.Semaphore(3)
    active_entries = [
        entry for entry in entries if offsets[entry.entry_id] <= MAX_PROVIDER_OFFSET
    ]
    if not active_entries:
        capped = any(value == CAPPED_PROVIDER_OFFSET for value in offsets.values())
        missing_state_source = _missing_state_source(normalized_jurisdiction, entries)
        return _result(
            status="partial" if capped or missing_state_source else "no_match",
            query=query,
            jurisdiction=normalized_jurisdiction,
            policy=policy,
            provider_id=provider.provider_id,
            retrieved_at=retrieved_at,
            explanation=(
                "The provider page limit was reached; additional results may exist."
                if capped
                else (
                    "No matching lead was returned from the approved paths; "
                    "this does not show that no program exists."
                )
            ),
            coverage=_coverage(normalized_jurisdiction, entries),
            truncated=capped,
        )
    per_source_counts = _allocate_counts(page_size, len(active_entries))

    async def search_one(entry: PublicSearchPolicyEntry, count: int):
        async with semaphore:
            return await provider.search(
                query=query,
                source=entry,
                count=count,
                offset=offsets[entry.entry_id],
            )

    results = await asyncio.gather(
        *(
            search_one(entry, count)
            for entry, count in zip(active_entries, per_source_counts, strict=True)
        ),
        return_exceptions=True,
    )
    source_health: list[PublicSearchSourceHealth] = []
    leads: list[ExternalProgramLead] = []
    next_offsets: dict[str, int] = {}
    saw_timeout = False
    saw_unavailable = False
    saw_invalid = False
    saw_unconfigured = False
    filtered_count = 0
    reached_provider_cap = False
    off_manifest_urls: set[str] = set()

    next_offsets = {
        entry.entry_id: offsets[entry.entry_id]
        for entry in entries
        if offsets[entry.entry_id] > MAX_PROVIDER_OFFSET
    }
    for entry, count, item in zip(
        active_entries, per_source_counts, results, strict=True
    ):
        if isinstance(item, BaseException):
            status = (
                item.status
                if isinstance(item, PublicSearchError)
                else "upstream_unavailable"
            )
            source_health.append(
                PublicSearchSourceHealth(entry_id=entry.entry_id, status=status)
            )
            saw_timeout |= status == "timeout"
            saw_unavailable |= status == "upstream_unavailable"
            saw_invalid |= status == "invalid_upstream_response"
            saw_unconfigured |= status == "unavailable"
            next_offsets[entry.entry_id] = offsets[entry.entry_id]
            continue
        source_health.append(
            PublicSearchSourceHealth(entry_id=entry.entry_id, status="available")
        )
        if len(item.results) >= count:
            if offsets[entry.entry_id] < MAX_PROVIDER_OFFSET:
                next_offsets[entry.entry_id] = offsets[entry.entry_id] + 1
            else:
                next_offsets[entry.entry_id] = CAPPED_PROVIDER_OFFSET
                reached_provider_cap = True
        else:
            next_offsets[entry.entry_id] = EXHAUSTED_PROVIDER_OFFSET
        for raw in item.results:
            # Search results outside the manifest remain useful as leads, but
            # they must never inherit the authority of the query's policy entry.
            canonical_url = canonicalize_public_https_url(raw.url)
            match = match_approved_url(raw.url, entries)
            if match is None and canonical_url is None:
                filtered_count += 1
                continue
            if match is None:
                off_manifest_urls.add(canonical_url)
            leads.append(_lead(raw, match, retrieved_at, policy))

    deduplicated = {lead.canonical_source_url: lead for lead in leads}
    # Keep policy-entry and provider ordering stable; do not sort away relevance.
    ordered = list(deduplicated.values())[:page_size]
    failed_entries = {
        item.entry_id for item in source_health if item.status != "available"
    }
    resumable_offsets = {
        entry_id: offset
        for entry_id, offset in next_offsets.items()
        if offset <= MAX_PROVIDER_OFFSET
    }
    next_cursor = (
        _encode_cursor(query, normalized_jurisdiction, policy, next_offsets)
        if resumable_offsets and (ordered or not failed_entries)
        else None
    )
    missing_state_source = _missing_state_source(normalized_jurisdiction, entries)
    coverage = _coverage(normalized_jurisdiction, entries)
    capped = reached_provider_cap or any(
        offset == CAPPED_PROVIDER_OFFSET for offset in next_offsets.values()
    )
    truncated = bool(resumable_offsets) or capped or filtered_count > 0
    partial = (
        saw_timeout
        or saw_unavailable
        or saw_invalid
        or truncated
        or missing_state_source
        or filtered_count > 0
        or bool(off_manifest_urls)
    )
    if ordered:
        status: str = "partial" if partial else "ok"
        explanation = (
            "Results are unreviewed source leads. Program applicability and criteria "
            "were not assessed; the approved search policy is not a complete "
            "public-program inventory."
        )
        if missing_state_source:
            explanation += (
                " No approved state source is configured for this state; "
                "federal sources only were searched."
            )
        if off_manifest_urls:
            explanation += (
                f" {len(off_manifest_urls)} returned lead(s) are outside the source "
                "manifest and remain explicitly unverified."
            )
    elif saw_invalid:
        status = "invalid_upstream_response"
        explanation = "The configured search provider returned an invalid response."
    elif saw_unconfigured:
        status = "unavailable"
        explanation = "The configured search provider credential is unavailable."
    elif saw_timeout and not saw_unavailable:
        status = "timeout"
        explanation = "The configured public-search provider timed out."
    elif saw_unavailable:
        status = "upstream_unavailable"
        explanation = "One or more configured public-search requests were unavailable."
    elif partial:
        status = "partial"
        explanation = (
            "Search coverage is limited by the approved source policy or provider "
            "result cap."
        )
        if missing_state_source:
            explanation += (
                " No approved state source is configured for this state; "
                "federal sources only were searched."
            )
        if filtered_count:
            explanation += (
                " Some provider results were excluded by the exact host/path policy."
            )
    else:
        status = "no_match"
        explanation = (
            "No matching lead was returned from the approved paths; "
            "this does not show that no program exists."
        )
    return _result(
        status=status,
        query=query,
        jurisdiction=normalized_jurisdiction,
        policy=policy,
        provider_id=provider.provider_id,
        retrieved_at=retrieved_at,
        leads=ordered,
        source_health=source_health,
        coverage=coverage,
        truncated=truncated,
        next_cursor=next_cursor,
        explanation=explanation,
    )


def match_approved_url(
    raw_url: str, entries: Sequence[PublicSearchPolicyEntry]
) -> tuple[PublicSearchPolicyEntry, str] | None:
    """Return the most-specific approved scope match and safe canonical URL."""
    try:
        canonical = canonicalize_public_https_url(raw_url)
        if canonical is None:
            return None
        parsed = urlsplit(canonical)
        host = parsed.hostname or ""
        decoded_path = unquote(parsed.path or "/")
        candidates = []
        for entry in entries:
            policy_host = entry.host.lower().rstrip(".")
            if entry.scope_kind == "exact_host_path":
                host_matches = host == policy_host
            elif entry.scope_kind == "domain_suffix":
                host_matches = host.endswith("." + policy_host)
            else:
                host_matches = host == policy_host or host.endswith("." + policy_host)
            if host_matches:
                candidates.extend(
                    (entry, prefix)
                    for prefix in entry.path_prefixes
                    if _path_matches(decoded_path, prefix)
                )
        if not candidates:
            return None
        entry, _ = max(candidates, key=lambda item: len(item[1]))
        return entry, canonical
    except ValueError:
        return None


def canonicalize_public_https_url(raw_url: str) -> str | None:
    """Canonicalize a public HTTPS URL without accepting unsafe URL forms."""
    try:
        parsed = urlsplit(raw_url.strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme.lower() != "https"
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or "\\" in parsed.path
        ):
            return None
        decoded_path = unquote(parsed.path or "/")
        if (
            "\\" in decoded_path
            or any(part in {".", ".."} for part in decoded_path.split("/"))
            or re.search(r"%(?:2f|5c)", parsed.path, re.IGNORECASE)
            or _has_redirect_query(parsed.query)
        ):
            return None
        return urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))
    except (AttributeError, ValueError):
        return None


def _path_matches(path: str, prefix: str) -> bool:
    normalized = prefix.rstrip("/") or "/"
    return path == normalized or path.startswith(normalized + "/") or normalized == "/"


def _has_redirect_query(query: str) -> bool:
    """Exclude common open-redirect parameters without fetching result pages."""
    redirect_keys = {
        "continue",
        "destination",
        "dest",
        "next",
        "redirect",
        "redirect_uri",
        "return",
        "returnto",
        "target",
        "url",
    }
    return any(key.lower() in redirect_keys for key, _ in parse_qsl(query))


def _missing_state_source(
    jurisdiction: str, entries: Sequence[PublicSearchPolicyEntry]
) -> bool:
    return jurisdiction != "US" and not any(
        entry.authority_jurisdiction == jurisdiction
        or entry.scope_kind == "domain_suffix"
        for entry in entries
    )


def _coverage(jurisdiction: str, entries: Sequence[PublicSearchPolicyEntry]) -> str:
    if any(entry.scope_kind == "domain_suffix" for entry in entries):
        return "broad_domain_classes"
    if jurisdiction == "US":
        return "federal_only"
    return (
        "federal_and_state"
        if not _missing_state_source(jurisdiction, entries)
        else "policy_limited"
    )


def _lead(
    result: ProviderResult,
    matched: tuple[PublicSearchPolicyEntry, str] | None,
    retrieved_at: str,
    policy: PublicSearchPolicy,
) -> ExternalProgramLead:
    if matched is None:
        canonical = canonicalize_public_https_url(result.url)
        if canonical is None:  # Guard the invariant even if called independently.
            raise ValueError("Off-manifest result URL is not a safe HTTPS URL.")
        entry_id = "off-manifest"
        identity = {
            "source_id": "off-manifest",
            "entry_id": entry_id,
            "scope_kind": "off_manifest",
            "authority_class": "unverified_off_manifest",
            "owner": "Publisher not verified",
            "authority_jurisdiction": "unknown",
            "permitted_uses": ["discovery"],
            "verification_evidence": (
                "No entry in the active source manifest matched this returned URL."
            ),
            "review_status": "not_in_manifest",
            "source_verification_status": "unverified",
            "reviewed_at": policy.reviewed_at,
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "criteria_status": "not_assessed",
            "catalog_status": "not_checked",
            "retrieved_at_utc": retrieved_at,
        }
        authority_jurisdiction = "unknown"
    else:
        entry, canonical = matched
        entry_id = entry.entry_id
        identity = {
            "source_id": entry.source_id,
            "entry_id": entry.entry_id,
            "scope_kind": entry.scope_kind,
            "authority_class": entry.authority_class,
            "owner": entry.owner,
            "authority_jurisdiction": entry.authority_jurisdiction,
            "permitted_uses": entry.permitted_uses,
            "verification_evidence": entry.verification_evidence,
            "review_status": entry.review_status,
            "source_verification_status": "unverified",
            "reviewed_at": entry.reviewed_at,
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "criteria_status": "not_assessed",
            "catalog_status": "not_checked",
            "retrieved_at_utc": retrieved_at,
        }
        authority_jurisdiction = entry.authority_jurisdiction
    lead_id = hashlib.sha256(f"{entry_id}\n{canonical}".encode()).hexdigest()[:24]
    return ExternalProgramLead(
        lead_id=lead_id,
        title=result.title,
        summary=result.description,
        canonical_source_url=canonical,
        source_identity=identity,
        authority_jurisdiction=authority_jurisdiction,
    )


def _result(
    *,
    status: str,
    query: str,
    jurisdiction: str,
    policy: PublicSearchPolicy,
    provider_id: str | None,
    retrieved_at: str,
    explanation: str,
    leads: list[ExternalProgramLead] | None = None,
    source_health: list[PublicSearchSourceHealth] | None = None,
    coverage: str = "unavailable",
    truncated: bool = False,
    next_cursor: str | None = None,
) -> ExternalSearchResult:
    result_leads = leads or []
    return ExternalSearchResult(
        status=status,
        query=query,
        jurisdiction_code=jurisdiction,
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        provider_id=provider_id,
        retrieved_at_utc=retrieved_at,
        leads=result_leads,
        source_health=source_health or [],
        coverage=coverage,
        truncated=truncated,
        next_cursor=next_cursor,
        recovery_action=_recovery_action(status, has_leads=bool(result_leads)),
        explanation=explanation,
    )


def _recovery_action(
    status: str, *, has_leads: bool
) -> ExternalSearchRecoveryAction | None:
    """Ask for a useful clue when discovery cannot supply a source-backed lead."""
    if has_leads:
        return None
    if status == "no_match":
        return ExternalSearchRecoveryAction(
            kind="ask_user_for_program_or_agency_clue",
            reason="no_match",
            follow_up_prompt=(
                "I did not find a source-backed lead in this search, which does not "
                "mean no program exists. Do you know an agency or program name?"
            ),
            retry_later=False,
        )
    if status == "partial":
        return ExternalSearchRecoveryAction(
            kind="ask_user_for_program_or_agency_clue",
            reason="partial_coverage",
            follow_up_prompt=(
                "Search coverage was incomplete and returned no usable lead. Do you "
                "know an agency or program name to help narrow the search?"
            ),
            retry_later=True,
        )
    if status in {
        "timeout",
        "upstream_unavailable",
        "invalid_upstream_response",
        "unavailable",
    }:
        reason = "provider_unavailable" if status == "unavailable" else status
        return ExternalSearchRecoveryAction(
            kind="ask_user_for_program_or_agency_clue",
            reason=reason,
            follow_up_prompt=(
                "Public search could not return usable results from its configured "
                "sources. Do you know an agency or program name, or would you like "
                "to try again later?"
            ),
            retry_later=True,
        )
    return None


def _validated_query(value: str) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    if (
        not 3 <= len(normalized) <= MAX_QUERY_LENGTH
        or not re.search(r"[A-Za-z0-9]", normalized)
        or any(ord(char) < 32 for char in normalized)
    ):
        return None
    return normalized


def _validated_jurisdiction(value: str) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    if re.fullmatch(r"[A-Z]{2}(?:-[A-Z]{2})?", normalized):
        return normalized
    return None


def _allocate_counts(page_size: int, source_count: int) -> list[int]:
    quotient, remainder = divmod(page_size, source_count)
    return [quotient + (1 if index < remainder else 0) for index in range(source_count)]


def _cursor_fingerprint(
    query: str,
    jurisdiction: str,
    policy: PublicSearchPolicy,
    entries: Sequence[PublicSearchPolicyEntry],
) -> str:
    material = json.dumps(
        {
            "query": query,
            "jurisdiction": jurisdiction,
            "policy": policy.policy_version,
            "entries": sorted(entry.entry_id for entry in entries),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _encode_cursor(
    query: str,
    jurisdiction: str,
    policy: PublicSearchPolicy,
    offsets: Mapping[str, int],
) -> str:
    entries = policy.approved_entries(jurisdiction)
    payload = {
        "fingerprint": _cursor_fingerprint(query, jurisdiction, policy, entries),
        "offsets": {
            entry.entry_id: offsets.get(entry.entry_id, 0)
            for entry in sorted(entries, key=lambda item: item.entry_id)
        },
    }
    return (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


def _decode_cursor(
    cursor: str | None,
    query: str,
    jurisdiction: str,
    policy: PublicSearchPolicy,
    entries: Sequence[PublicSearchPolicyEntry],
) -> dict[str, int] | None:
    if cursor is None:
        return {entry.entry_id: 0 for entry in entries}
    if not isinstance(cursor, str) or len(cursor) > 2048:
        return None
    try:
        decoded = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(decoded)
        if payload.get("fingerprint") != _cursor_fingerprint(
            query, jurisdiction, policy, entries
        ):
            return None
        offsets = payload.get("offsets")
        if not isinstance(offsets, dict) or set(offsets) - {
            entry.entry_id for entry in entries
        }:
            return None
        result = {entry.entry_id: offsets.get(entry.entry_id, 0) for entry in entries}
        if any(
            not isinstance(value, int) or not 0 <= value <= CAPPED_PROVIDER_OFFSET
            for value in result.values()
        ):
            return None
        return result
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _read_api_token(token_file: Path) -> str:
    token = token_file.read_text(encoding="utf-8").strip()
    if not token or len(token) > 4096 or any(char.isspace() for char in token):
        raise ValueError("Search token file is empty or malformed.")
    return token


def _provider_error_code(raw_body: bytes) -> str:
    """Extract only an opaque provider error code for sanitized diagnostics."""
    try:
        payload = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "unknown"
    error = payload.get("error") if isinstance(payload, Mapping) else None
    code = error.get("code") if isinstance(error, Mapping) else None
    if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code):
        return code
    return "unknown"


async def _read_limited(response: httpx.Response, max_bytes: int) -> bytes:
    content_length = response.headers.get("content-length")
    if content_length and int(content_length) > max_bytes:
        raise ValueError("Search response exceeded the configured size cap.")
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise ValueError("Search response exceeded the configured size cap.")
        chunks.append(chunk)
    return b"".join(chunks)


def _find_policy_path(module_path: Path) -> Path:
    for parent in module_path.resolve().parents:
        candidate = parent / POLICY_RELATIVE_PATH
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "Public search domain policy is missing from the runtime package."
    )
