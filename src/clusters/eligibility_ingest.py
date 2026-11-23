"""Operator-triggered ingestion of approved public Eligibility program pages.

Fetch is restricted to exact HTTPS URLs in the checked-in source manifest. The
normalizer retains compact facts and provenance, never raw HTML or profile data.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from clusters.eligibility_contracts import (
    EligibilityCriterion,
    Jurisdiction,
    NavigatorId,
    ProgramRecord,
    SourceReference,
)

MANIFEST_RELATIVE_PATH = Path("configs/eligibility/public-sources.v1.json")


def _find_manifest_path(module_path: Path) -> Path:
    """Resolve the pattern source manifest from a source tree or installed image."""
    for parent in module_path.resolve().parents:
        candidate = parent / MANIFEST_RELATIVE_PATH
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "Eligibility public-source manifest is missing from the runtime package."
    )


MANIFEST_PATH = _find_manifest_path(Path(__file__))
TRANSFORM_VERSION = "eligibility-source-transform-v1"
CANONICAL_CRITERION_ATTRIBUTES = {
    "state_code": "core.geography.state_code",
    "years_farming": "core.farm_business.years_farming",
}
MAX_RESPONSE_BYTES = 1_000_000
MAX_RETRIES = 1
USER_AGENT = "MCP-SaaS-Hackathon/0.1 (bounded public-source import)"


class SourceUnavailable(RuntimeError):
    """An approved source could not be fetched and validated completely."""


class SourceInvalid(SourceUnavailable):
    """A source responded but no longer satisfies its reviewed content contract."""


class SourceBlocked(SourceUnavailable):
    """A source request was explicitly denied or redirected outside its allowlist."""


@dataclass(frozen=True)
class CriterionSpec:
    """One criterion a source must support: attribute, operator and value."""

    criterion_id: str
    attribute: str
    operator: Literal["eq", "lte"]
    value: str | int


@dataclass(frozen=True)
class SourceSpec:
    """Definition of one public source to ingest and the criterion it supports."""

    source_id: str
    dataset_id: str
    source_dataset_id: str
    url: str
    agency: str
    expected_title: str
    program_title: str
    navigator: NavigatorId
    program_id: str
    jurisdiction: dict[str, str]
    required_text: tuple[str, ...]
    criterion: CriterionSpec
    description: str
    source_limitations: tuple[str, ...]


@dataclass(frozen=True)
class NormalizedSource:
    """A source after fetching and normalizing: program record, version and hash."""

    spec: SourceSpec
    program: ProgramRecord
    source_version: str
    retrieved_at: datetime
    content_hash: str
    record: dict[str, object]


@dataclass(frozen=True)
class FetchedPage:
    """Bounded response evidence; page bytes are discarded after normalization."""

    text: str
    headers: httpx.Headers
    retrieved_at: datetime
    source_content_sha256: str


@dataclass(frozen=True)
class SourceOutcome:
    """The result of ingesting one source, with its status and record count."""

    source_id: str
    status: Literal["ready", "ingested", "unchanged", "unavailable", "invalid"]
    dataset_id: str
    record_count: int
    detail: str


class _PageSummaryParser(HTMLParser):
    """Extract only page title, headings, paragraphs, and list text."""

    excluded = {"aside", "footer", "header", "nav", "script", "style", "svg"}
    content_tags = {"h1", "h2", "h3", "p", "li"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._excluded_depth = 0
        self._capture_tag: str | None = None
        self._capture_parts: list[str] = []
        self.items: list[tuple[str, str]] = []
        self.title_parts: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.excluded:
            self._excluded_depth += 1
        if self._excluded_depth:
            return
        if tag == "title":
            self._in_title = True
        if tag in self.content_tags:
            self._capture_tag = tag
            self._capture_parts = []

    def handle_endtag(self, tag: str) -> None:
        if self._excluded_depth:
            if tag in self.excluded:
                self._excluded_depth -= 1
            return
        if tag == "title":
            self._in_title = False
        if tag == self._capture_tag:
            value = _normalize_text(" ".join(self._capture_parts))
            if value:
                self.items.append((tag, value))
            self._capture_tag = None
            self._capture_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._capture_tag and not self._excluded_depth:
            self._capture_parts.append(data)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def load_source_specs(path: Path = MANIFEST_PATH) -> list[SourceSpec]:
    """Load and validate the exact source allowlist used by the importer."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("manifest_version") != "eligibility-public-sources-v1":
        raise ValueError("Unsupported Eligibility source manifest version.")
    specs: list[SourceSpec] = []
    seen_sources: set[str] = set()
    seen_datasets: set[str] = set()
    for item in payload.get("sources", []):
        parsed_url = urlsplit(item["url"])
        if parsed_url.scheme != "https" or not parsed_url.hostname:
            raise ValueError("Allowlisted source URLs must use HTTPS.")
        if parsed_url.query or parsed_url.fragment or parsed_url.username:
            raise ValueError("Allowlisted source URLs must be canonical page URLs.")
        if item["source_id"] in seen_sources or item["dataset_id"] in seen_datasets:
            raise ValueError("Source and dataset identities must be unique.")
        seen_sources.add(item["source_id"])
        seen_datasets.add(item["dataset_id"])
        criterion = CriterionSpec(**item["criterion"])
        specs.append(
            SourceSpec(
                **{
                    key: value
                    for key, value in item.items()
                    if key not in {"criterion", "required_text", "source_limitations"}
                },
                required_text=tuple(item["required_text"]),
                criterion=criterion,
                source_limitations=tuple(item["source_limitations"]),
            )
        )
    if not specs:
        raise ValueError(
            "Eligibility source manifest must contain at least one source."
        )
    return specs


def _fetch_html(spec: SourceSpec) -> FetchedPage:
    """Fetch one exact approved URL with bounded timeout, bytes, and retries."""
    expected_host = urlsplit(spec.url).hostname
    for attempt in range(MAX_RETRIES + 1):
        try:
            with httpx.Client(
                timeout=httpx.Timeout(8.0),
                follow_redirects=False,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
            ) as client:
                with client.stream("GET", spec.url) as response:
                    if response.url.host != expected_host:
                        raise SourceBlocked("Source redirected outside its allowlist.")
                    if response.status_code != 200:
                        error = (
                            SourceBlocked
                            if response.status_code in {401, 403}
                            else SourceUnavailable
                        )
                        raise error(f"Source returned HTTP {response.status_code}.")
                    if (
                        not response.headers.get("content-type", "")
                        .lower()
                        .startswith("text/html")
                    ):
                        raise SourceInvalid("Source did not return HTML.")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise SourceInvalid(
                                "Source exceeded the response size cap."
                            )
                    headers = response.headers
            return FetchedPage(
                text=body.decode("utf-8", errors="replace"),
                headers=headers,
                retrieved_at=datetime.now(UTC),
                source_content_sha256=hashlib.sha256(body).hexdigest(),
            )
        except SourceUnavailable:
            raise
        except httpx.RequestError as exc:
            if attempt >= MAX_RETRIES:
                raise SourceUnavailable("Source request failed or timed out.") from exc
            time.sleep(0.25 * (attempt + 1))
    raise SourceUnavailable("Source request did not complete.")


def _source_version(headers: httpx.Headers, text: str) -> str:
    """Use publisher validators, falling back to a hash of extracted text."""
    return (
        headers.get("etag")
        or headers.get("last-modified")
        or hashlib.sha256(text.encode("utf-8")).hexdigest()
    )


def normalize_source(
    spec: SourceSpec,
    html: str,
    headers: httpx.Headers,
    retrieved_at: datetime | None = None,
    *,
    source_content_sha256: str | None = None,
) -> NormalizedSource:
    """Validate source markers and retain hashes, never the fetched page body."""
    if source_content_sha256 is not None and not re.fullmatch(
        r"[a-fA-F0-9]{64}", source_content_sha256
    ):
        raise SourceInvalid("Fetched source content hash is invalid.")
    parser = _PageSummaryParser()
    parser.feed(html)
    title = next((value for tag, value in parser.items if tag == "h1"), "")
    visible_text = _normalize_text(" ".join(value for _, value in parser.items)).lower()
    if title.lower() != spec.expected_title.lower():
        raise SourceInvalid("Published page title changed; source review is required.")
    if any(marker.lower() not in visible_text for marker in spec.required_text):
        raise SourceInvalid("Expected source content was missing or changed.")

    retrieved_at = retrieved_at or datetime.now(UTC)
    source_version = _source_version(headers, visible_text)
    source = SourceReference(
        source_id=spec.source_id,
        source_type="public_source",
        approval_status="approved",
        agency=spec.agency,
        title=spec.expected_title,
        url=spec.url,
        jurisdiction=Jurisdiction.model_validate(spec.jurisdiction),
        retrieved_at=retrieved_at,
        effective_date=None,
        publisher_version=headers.get("last-modified") or source_version,
    )
    criterion = EligibilityCriterion(
        criterion_id=spec.criterion.criterion_id,
        attribute=spec.criterion.attribute,
        operator=spec.criterion.operator,
        value=spec.criterion.value,
        source_id=spec.source_id,
    )
    try:
        program = ProgramRecord(
            catalog_version="eligibility-programs-v1",
            program_id=spec.program_id,
            navigator=spec.navigator,
            title=spec.program_title,
            description=spec.description,
            jurisdiction=Jurisdiction.model_validate(spec.jurisdiction),
            data_origin="public_source",
            sources=[source],
            criteria=[criterion],
        )
    except ValidationError as exc:
        raise SourceInvalid(
            "Fetched source did not meet the Eligibility contract."
        ) from exc

    source_metadata = {
        "source_id": spec.source_id,
        "source_version": source_version,
        "retrieved_at": retrieved_at.isoformat(),
        "effective_date": None,
        "effective_date_status": "not_published_on_reviewed_page",
        "transform_version": TRANSFORM_VERSION,
        "limitations": list(spec.source_limitations),
        "raw_payload_stored": False,
    }
    if source_content_sha256 is not None:
        source_metadata["source_content_sha256"] = source_content_sha256.lower()
    program_payload = program.model_dump(mode="json")
    # Source attribution is stored once in provenance; catalog facts use the
    # template-owned Eligibility dimension and shared Core geography/farm IDs.
    criteria = [
        {
            **item,
            "attribute": CANONICAL_CRITERION_ATTRIBUTES[item["attribute"]],
        }
        for item in program_payload["criteria"]
    ]
    canonical_attributes: dict[str, object] = {
        "eligibility.program.program_id": program_payload["program_id"],
        "eligibility.program.catalog_version": program_payload["catalog_version"],
        "eligibility.program.navigator": program_payload["navigator"],
        "eligibility.program.data_origin": program_payload["data_origin"],
        "eligibility.program.title": program_payload["title"],
        "eligibility.program.description": program_payload["description"],
        "eligibility.program.criteria": criteria,
        "core.geography.country_code": spec.jurisdiction["country_code"],
        "core.geography.jurisdiction_level": spec.jurisdiction["level"],
    }
    if state_code := spec.jurisdiction.get("state_code"):
        canonical_attributes["core.geography.state_code"] = state_code
    source_facts = {
        key: value
        for key, value in canonical_attributes.items()
        if key
        not in {
            "eligibility.program.catalog_version",
            "eligibility.program.navigator",
        }
    }
    # Retrieval time belongs to run provenance, not semantic record identity.
    # Hash the normalized source-backed criteria and publisher/transform
    # versions so a changed page updates one stable source record.
    content_hash = hashlib.sha256(
        json.dumps(
            {
                "source_facts": source_facts,
                "source_version": source_version,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    record = {
        "source_record_id": spec.program_id,
        "record_type": "eligibility_program",
        "canonical_attributes": canonical_attributes,
        "semantic_context": {
            "catalog_namespace": "programs",
            "coverage": "partial_source_backed_screening",
            "agency_decision": "not_provided",
        },
        "source_metadata": {**source_metadata, "content_hash": content_hash},
        "source_url": spec.url,
        "content_hash": content_hash,
        "schema_version": "eligibility-program-v1",
        "observed_at": retrieved_at.isoformat(),
    }
    return NormalizedSource(
        spec=spec,
        program=program,
        source_version=source_version,
        retrieved_at=retrieved_at,
        content_hash=content_hash,
        record=record,
    )


def fetch_source(spec: SourceSpec) -> NormalizedSource:
    """Fetch and normalize one source independently; never return partial data."""
    page = _fetch_html(spec)
    return normalize_source(
        spec,
        page.text,
        page.headers,
        page.retrieved_at,
        source_content_sha256=page.source_content_sha256,
    )
