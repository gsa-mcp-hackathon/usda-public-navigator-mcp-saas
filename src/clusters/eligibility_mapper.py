"""Optional criteria-mapping adapter (reference: IBM watsonx Orchestrate).

Enabled with MCP_LLM_MAPPER_ENABLED=true. Not required for the platform to run;
any provider that returns the same criteria schema can fill this role.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, Callable
from urllib.parse import urlsplit

import httpx
from fastmcp import FastMCP
from pydantic import Field

from clusters.eligibility_candidate import (
    CandidateCitation,
    ProgramCriteriaPackageRequest,
    RequirementCandidate,
)
from clusters.eligibility_candidate_evaluation import (
    ProgramCandidateEvaluationRequest,
    RequestScopedCandidateCriteriaPackage,
    RequestScopedProgramDetail,
    as_request_scoped_candidate_package,
    evaluate_request_scoped_candidate,
)
from clusters.eligibility_contracts import Jurisdiction, NavigatorId, SourceReference
from clusters.eligibility_discovery import ProfileSubmission
from clusters.eligibility_public_search import match_approved_url
from clusters.eligibility_source_reader import (
    REQUEST_TIMEOUT_SECONDS,
    _active_policy,
    _fetch_page,
)
from example_server.storage import StorageUnavailable

DEFAULT_AGENT_ID = ""  # set WXO_MAPPER_AGENT_ID for your own mapper agent
MAPPER_TIMEOUT_SECONDS = 240
SUPPORTED_OPERATORS = {
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "lte_by",
    "one_of",
    "exists",
}
PROFILE_MODELS = {
    "farmer_producer": "FarmerProducerProfile",
    "senior_caregiver": "SeniorCaregiverProfile",
    "low_income_assistance": "LowIncomeAssistanceProfile",
    "general": "GeneralProfile",
}


class InvalidMapperCitationError(ValueError):
    """A mapper retry still returned citations outside the selected source."""

    def __init__(self, mapper_run_id: str, invalid_indices: list[int]) -> None:
        self.mapper_run_id = mapper_run_id
        self.invalid_indices = invalid_indices
        joined = ", ".join(str(index) for index in invalid_indices)
        super().__init__(
            "Mapper retry still returned non-verbatim citations for "
            f"requirement index/indices: {joined}."
        )


class SourceFetchFailure(ValueError):
    """A public source read failed with safe elapsed-time context."""

    def __init__(self, reason_code: str, fetch_metrics: dict[str, object]) -> None:
        self.fetch_metrics = fetch_metrics
        super().__init__(reason_code)


def register_runtime_criteria_mapper(
    app: FastMCP,
    repository: object,
    *,
    policy_reader: Callable[[], object] | None = None,
) -> None:
    """Map a selected source with IBM and immediately persist usable rules."""

    @app.tool(
        name="map_and_save_program_criteria",
        annotations={
            "title": "Map and save program criteria",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    def map_and_save_program_criteria_tool(
        program_id: Annotated[
            str,
            Field(
                min_length=2,
                max_length=100,
                pattern=r"^[a-z0-9][a-z0-9._-]{1,99}$",
            ),
        ],
        catalog_version: Annotated[str, Field(min_length=1, max_length=120)],
        navigator: NavigatorId,
        title: Annotated[str, Field(min_length=1, max_length=500)],
        description: Annotated[str, Field(min_length=1, max_length=2000)],
        jurisdiction: Jurisdiction,
        sources: Annotated[list[SourceReference], Field(min_length=1, max_length=20)],
        requirements: Annotated[
            list[RequirementCandidate], Field(min_length=1, max_length=100)
        ],
        source_limitations: Annotated[list[str], Field(max_length=100)] = Field(
            default_factory=list
        ),
    ) -> dict[str, object]:
        """Use the criteria mapper for the selected program and persist its rules.

        Any source-backed selected program can use this operation. Supported
        mappings are saved and read back immediately; unsupported clauses remain
        visible as unknown. Benchmarking is separate from this user operation.
        """
        try:
            request = ProgramCriteriaPackageRequest(
                program_id=program_id,
                catalog_version=catalog_version,
                navigator=navigator,
                title=title,
                description=description,
                jurisdiction=jurisdiction,
                sources=sources,
                requirements=requirements,
                source_limitations=source_limitations,
            )
            mapped, details, run_id, source_fetch = _map_request(request, policy_reader)
            from clusters.eligibility_criteria_persistence import _persist_ruleset

            result = _persist_ruleset(mapped, repository, None)
            return {
                **result,
                "criteria_package": mapped.model_dump(mode="json"),
                "details": [item.model_dump(mode="json") for item in details],
                "mapper": "ibm_watsonx_orchestrate",
                "mapper_run_id": run_id,
                "source_fetch": source_fetch,
                "mapped_requirement_count": sum(
                    item.mapping_status == "mapped" for item in mapped.requirements
                ),
                "unresolved_requirement_count": sum(
                    item.mapping_status != "mapped" for item in mapped.requirements
                ),
            }
        except (OSError, ValueError, TypeError, KeyError, httpx.HTTPError) as exc:
            return {
                "status": "mapping_failed",
                "reason_code": "runtime_criteria_mapping_failed",
                "mapping_persisted": False,
                "reason": str(exc),
                "next_action": "fix_source_or_mapper_connection_and_retry",
                **_mapper_failure_context(exc),
            }

    @app.tool(
        name="map_save_and_score_program_candidate",
        annotations={
            "title": "Map, save, and score selected program",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    def map_save_and_score_program_candidate_tool(
        program: ProgramCriteriaPackageRequest,
        profile_submission: ProfileSubmission,
    ) -> dict[str, object]:
        """Map a selected source, persist the reusable rules, then score it."""
        saved: dict[str, object] | None = None
        candidate: RequestScopedCandidateCriteriaPackage | None = None
        details: list[RequestScopedProgramDetail] = []
        run_id: str | None = None
        source_fetch: dict[str, object] = {}
        try:
            mapped, details, run_id, source_fetch = _map_request(program, policy_reader)
            from clusters.eligibility_criteria_persistence import (
                _persist_ruleset,
            )

            saved = _persist_ruleset(mapped, repository, None)
            if saved.get("mapping_persisted") is not True:
                return {
                    **saved,
                    "mapper": "ibm_watsonx_orchestrate",
                    "mapper_run_id": run_id,
                    "source_fetch": source_fetch,
                    "evaluation_performed": False,
                }
            candidate = as_request_scoped_candidate_package(mapped)
            evaluation = evaluate_request_scoped_candidate(
                ProgramCandidateEvaluationRequest(
                    criteria_package=candidate,
                    profile_submission=profile_submission,
                )
            )
            if evaluation.get("evaluation_performed") is not True:
                return {
                    "status": "scoring_failed_after_mapping",
                    "reason_code": "request_scoped_scoring_failed_after_mapping",
                    "mapping": saved,
                    "evaluation": evaluation,
                    "criteria_package": candidate.model_dump(mode="json"),
                    "details": [item.model_dump(mode="json") for item in details],
                    "mapper_run_id": run_id,
                    "source_fetch": source_fetch,
                    "mapping_persisted": True,
                    "ruleset_readback_verified": saved.get("readback_verified") is True,
                    "evaluation_performed": False,
                    "next_action": "retry_scoring_without_remapping",
                }
            return {
                "status": "mapped_saved_and_scored",
                "mapping": saved,
                "criteria_package": candidate.model_dump(mode="json"),
                "details": [item.model_dump(mode="json") for item in details],
                "evaluation": evaluation,
                "mapper": "ibm_watsonx_orchestrate",
                "mapper_run_id": run_id,
                "source_fetch": source_fetch,
                "mapping_persisted": True,
                "ruleset_readback_verified": saved.get("readback_verified") is True,
                "evaluation_performed": evaluation.get("evaluation_performed") is True,
                "official_eligibility_determination": False,
            }
        except (OSError, ValueError, TypeError, KeyError, httpx.HTTPError) as exc:
            mapping_persisted = (
                isinstance(saved, dict) and saved.get("mapping_persisted") is True
            )
            return {
                "status": (
                    "scoring_failed_after_mapping"
                    if mapping_persisted
                    else "mapping_failed"
                ),
                "reason_code": (
                    "request_scoped_scoring_failed_after_mapping"
                    if mapping_persisted
                    else "runtime_criteria_mapping_failed"
                ),
                "mapping": saved,
                **(
                    {
                        "criteria_package": candidate.model_dump(mode="json"),
                        "details": [item.model_dump(mode="json") for item in details],
                        "mapper_run_id": run_id,
                    }
                    if candidate is not None
                    else {}
                ),
                "mapping_persisted": mapping_persisted,
                "ruleset_readback_verified": (
                    saved.get("readback_verified") is True
                    if isinstance(saved, dict)
                    else False
                ),
                "evaluation_performed": False,
                "reason": str(exc),
                "next_action": (
                    "retry_scoring_without_remapping"
                    if mapping_persisted
                    else "fix_source_or_mapper_connection_and_retry"
                ),
                **_mapper_failure_context(exc),
            }


def _map_request(
    request: ProgramCriteriaPackageRequest,
    policy_reader: Callable[[], object] | None = None,
) -> tuple[
    ProgramCriteriaPackageRequest,
    list[RequestScopedProgramDetail],
    str,
    dict[str, object],
]:
    """Fetch current source text, call IBM, and attach trusted source lineage."""
    source = request.sources[0]
    if source.url is None:
        raise ValueError("Selected program source has no URL.")
    timeout_override = _source_request_timeout_seconds(str(source.url), policy_reader)
    timeout_seconds = timeout_override or REQUEST_TIMEOUT_SECONDS
    page = (
        _fetch_page(str(source.url), timeout_seconds=timeout_override)
        if timeout_override is not None
        else _fetch_page(str(source.url))
    )
    fetch_metrics = {
        "source_fetch_elapsed_ms": page.get("source_fetch_elapsed_ms"),
        "source_request_timeout_seconds": timeout_seconds,
        **{
            key: page[key]
            for key in ("reason_code", "failure_stage", "failure_class")
            if isinstance(page.get(key), str)
        },
    }
    if page.get("status") != "ok":
        raise SourceFetchFailure(
            str(page.get("reason_code", "selected_source_unavailable")),
            fetch_metrics,
        )
    source_text = page.get("source_text")
    if not isinstance(source_text, str) or not source_text.strip():
        raise ValueError("Selected source returned no readable page text.")
    instance_url = os.environ.get("WXO_INSTANCE_URL", "").strip().rstrip("/")
    key_path = Path(os.environ.get("WXO_API_KEY_FILE", ""))
    if not instance_url or not key_path.is_file():
        raise ValueError("IBM mapper runtime URL or server key is not configured.")
    api_key = key_path.read_text(encoding="utf-8").strip()
    if not api_key:
        raise ValueError("IBM mapper server key is empty.")
    agent_id = os.environ.get("WXO_MAPPER_AGENT_ID", DEFAULT_AGENT_ID)
    if not agent_id.strip():
        raise ValueError("IBM mapper agent ID is not configured.")
    input_payload = {
        "task": "map_program_criteria",
        "contract_version": "criteria-mapper-input-v1",
        "sample_id": request.program_id,
        "program": {
            "program_id": request.program_id,
            "title": request.title,
            "jurisdiction": request.jurisdiction.model_dump(mode="json"),
        },
        "navigator": request.navigator,
        "profile_fields": _profile_fields(request.navigator),
        "operators": [
            "eq",
            "ne",
            "gt",
            "gte",
            "lt",
            "lte",
            "lte_by",
            "one_of",
            "exists",
        ],
        "supported_units": {
            "annual_household_income_usd": "USD/year",
            "monthly_gross_income_usd": "USD/month",
            "years_farming": "years",
            "acreage": "acres",
        },
        "detail_kinds": ["application_step", "form", "required_document", "deadline"],
        "source": {
            "source_url": str(source.url),
            "source_title": source.title,
            "source_text": source_text,
        },
    }
    reply, run_id = _call_orchestrate_with_json_retry(
        instance_url, api_key, agent_id, input_payload
    )
    rules = reply.get("requirements")
    if not isinstance(rules, list) or not rules:
        raise ValueError("Mapper returned no criteria requirements.")
    invalid_citations = _invalid_mapper_citations(rules, source_text)
    if invalid_citations:
        invalid_requirements = [
            {
                "index": index,
                "requirement_id": rule.get("requirement_id"),
                "statement": str(rule.get("statement", ""))[:500],
                "rejected_excerpt": str(rule.get("excerpt", ""))[:500],
            }
            for index, rule in enumerate(rules, start=1)
            if index in invalid_citations and isinstance(rule, dict)
        ]
        retry_payload = {
            **input_payload,
            "citation_correction": {
                "invalid_requirement_indices": invalid_citations,
                "invalid_requirements": invalid_requirements,
                "instruction": (
                    "Repair only the listed requirements and return the complete "
                    "mapping. Their previous excerpts are included for diagnosis "
                    "but are not valid citations. Copy each replacement excerpt "
                    "character-for-character from source.source_text as one exact, "
                    "contiguous substring. Preserve all other rule fields. Never "
                    "paraphrase, normalize, or invent citation text. If an exact "
                    "source excerpt cannot be found, do not claim a corrected "
                    "citation; leave that requirement unmapped and explain why."
                ),
            },
        }
        reply, run_id = _call_orchestrate(
            instance_url, api_key, agent_id, retry_payload
        )
        rules = reply.get("requirements")
        if not isinstance(rules, list) or not rules:
            raise ValueError("Mapper retry returned no criteria requirements.")
        invalid_citations = _invalid_mapper_citations(rules, source_text)
        if invalid_citations:
            raise InvalidMapperCitationError(run_id, invalid_citations)
    source_digest = page.get("source_content_sha256")
    visible_digest = page.get("source_visible_text_sha256")
    if not isinstance(source_digest, str) or not isinstance(visible_digest, str):
        raise ValueError("Source reader did not provide current content fingerprints.")
    source_id = source.source_id
    retrieved_at_value = page.get("retrieved_at")
    if not isinstance(retrieved_at_value, str):
        raise ValueError("Source reader did not return an observation time.")
    now = datetime.fromisoformat(retrieved_at_value)
    final_url = page.get("source_url")
    if not isinstance(final_url, str) or not final_url:
        final_url = str(source.url)
    final_host = urlsplit(final_url).hostname
    original_host = urlsplit(str(source.url)).hostname
    if not final_host:
        raise ValueError("Source reader did not return a valid final source URL.")
    if final_host != original_host:
        # A cross-host redirect changes publisher provenance. Until policy
        # verification is recomputed for that host, retain it as unverified.
        source_id = "public." + hashlib.sha256(final_host.encode()).hexdigest()[:16]
        approval_status = "unverified"
    else:
        source_id = source.source_id
        approval_status = source.approval_status
    source_record_id = (
        "source-page-" + hashlib.sha256(final_url.encode("utf-8")).hexdigest()[:20]
    )
    source_version = f"sha256:{source_digest}"
    normalized: list[RequirementCandidate] = []
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            continue
        excerpt = rule.get("excerpt")
        if not isinstance(excerpt, str) or not excerpt or excerpt not in source_text:
            raise ValueError(f"Mapper citation for rule {index + 1} is not verbatim.")
        citation = CandidateCitation(
            source_url=final_url,
            source_record_id=source_record_id,
            retrieved_at=now,
            source_id=source_id,
            source_version=source_version,
            source_content_sha256=source_digest,
            source_visible_text_sha256=visible_digest,
            locator=f"runtime-mapper:{index + 1}",
            excerpt=excerpt,
        )
        payload = {
            key: value
            for key, value in rule.items()
            if key in RequirementCandidate.model_fields
        }
        if payload.get("mapping_status") == "mapped" and (
            payload.get("requirement_type") != "eligibility"
        ):
            payload = _mark_partial(payload, "non_eligibility_clause_is_not_executable")
        operator = payload.get("operator")
        unsupported_operator = (
            not isinstance(operator, str) or operator not in SUPPORTED_OPERATORS
        )
        if payload.get("mapping_status") == "mapped" and unsupported_operator:
            payload.update(
                _mark_partial(
                    payload,
                    "unsupported_operator:" + str(operator),
                )
            )
        payload["citation"] = citation.model_dump(mode="python")
        try:
            normalized.append(RequirementCandidate.model_validate(payload))
        except (TypeError, ValueError):
            if payload.get("mapping_status") != "mapped":
                raise
            normalized.append(
                RequirementCandidate.model_validate(
                    _mark_partial(
                        payload, "mapper_rule_outside_active_profile_contract"
                    )
                )
            )
    if not normalized:
        raise ValueError("Mapper returned no structurally usable requirement rows.")
    source_data = source.model_dump(mode="python")
    source_data.update(
        {
            "source_id": source_id,
            "approval_status": approval_status,
            "url": final_url,
            "retrieved_at": now,
            "publisher_version": source_version,
        }
    )
    updated_source = SourceReference.model_validate(source_data)
    raw_details = reply.get("details", [])
    if not isinstance(raw_details, list) or len(raw_details) > 100:
        raise ValueError("Mapper returned an invalid requirements-detail list.")
    normalized_details: list[RequestScopedProgramDetail] = []
    detail_limitations: list[str] = []
    for index, item in enumerate(raw_details):
        if not isinstance(item, dict):
            detail_limitations.append(
                f"Requirements detail {index + 1} was omitted because it was malformed."
            )
            continue
        excerpt = item.get("excerpt")
        if not isinstance(excerpt, str) or not excerpt or excerpt not in source_text:
            detail_limitations.append(
                f"Requirements detail {index + 1} was omitted because its source quote "
                "did not match the retrieved page."
            )
            continue
        if len(excerpt) > 500:
            detail_limitations.append(
                f"Requirements detail {index + 1} was omitted because its citation "
                "exceeded the 500-character limit."
            )
            continue
        citation = CandidateCitation(
            source_url=final_url,
            source_record_id=source_record_id,
            retrieved_at=now,
            source_id=source_id,
            source_version=source_version,
            source_content_sha256=source_digest,
            source_visible_text_sha256=visible_digest,
            locator=f"runtime-mapper-detail:{index + 1}",
            excerpt=excerpt,
        )
        try:
            normalized_details.append(
                RequestScopedProgramDetail(
                    kind=item.get("kind", item.get("detail_type")),
                    text=item.get("text"),
                    citation=citation,
                )
            )
        except (TypeError, ValueError):
            detail_limitations.append(
                f"Requirements detail {index + 1} was omitted because it did not "
                "match the detail contract."
            )
    return (
        request.model_copy(
            update={
                "sources": [updated_source, *request.sources[1:]],
                "requirements": normalized,
                "source_limitations": [
                    *request.source_limitations,
                    *detail_limitations,
                ],
            }
        ),
        normalized_details,
        run_id,
        fetch_metrics,
    )


def _source_request_timeout_seconds(
    source_url: str,
    policy_reader: Callable[[], object] | None,
) -> float | None:
    """Read an optional per-source timeout from the active public-source policy."""
    if policy_reader is None:
        return None
    try:
        state = policy_reader()
    except (StorageUnavailable, OSError, ValueError):
        return None
    policy, _, _ = _active_policy(state)
    if policy is None:
        return None
    matched = match_approved_url(source_url, policy.entries)
    return matched[0].request_timeout_seconds if matched else None


def _invalid_mapper_citations(rules: list[object], source_text: str) -> list[int]:
    """Return 1-based rule indices whose citation is not an exact source substring."""
    return [
        index
        for index, rule in enumerate(rules, start=1)
        if not isinstance(rule, dict)
        or not isinstance(rule.get("excerpt"), str)
        or not rule["excerpt"]
        or rule["excerpt"] not in source_text
    ]


def _mapper_failure_context(exc: Exception) -> dict[str, object]:
    """Expose safe trace pointers without returning source text or credentials."""
    if isinstance(exc, SourceFetchFailure):
        return {"source_fetch": exc.fetch_metrics}
    if not isinstance(exc, InvalidMapperCitationError):
        return {}
    return {
        "mapper_run_id": exc.mapper_run_id,
        "invalid_citation_requirement_indices": exc.invalid_indices,
        "next_action": "inspect_mapper_trace_for_exact_source_excerpt",
    }


def _mark_partial(payload: dict[str, object], reason: str) -> dict[str, object]:
    """Keep a source clause visible when its proposed rule is unsupported."""
    return {
        **payload,
        "mapping_status": "partial",
        "unresolved_reason": reason[:500],
        "concept_id": None,
        "profile_attribute": None,
        "operator": None,
        "value": None,
        "context_attribute": None,
        "logic_group_id": None,
        "logic_group_operator": None,
    }


def _profile_fields(navigator: str) -> list[dict[str, object]]:
    """Describe supported profile fields to the mapper without profile values."""
    from clusters import eligibility_contracts as contracts

    class_name = PROFILE_MODELS.get(navigator)
    if class_name is None:
        raise ValueError(f"No profile schema is registered for {navigator}.")
    model = getattr(contracts, class_name)
    fields: list[dict[str, object]] = []
    for name, field in model.model_fields.items():
        if name in {"profile_id", "navigator", "schema_version", "attributes"}:
            continue
        annotation = str(field.annotation).lower()
        value_type = (
            "boolean"
            if "bool" in annotation
            else "integer"
            if "int" in annotation
            else "number"
            if "float" in annotation
            else "list"
            if "list" in annotation
            else "string"
        )
        fields.append({"name": name, "type": value_type})
    return fields


def _call_orchestrate(
    instance_url: str, api_key: str, agent_id: str, payload: dict[str, object]
) -> tuple[dict[str, object], str]:
    """Exchange IAM key and wait for one stateless Orchestrate mapper run."""
    timeout = httpx.Timeout(60.0, connect=15.0)
    with httpx.Client(timeout=timeout) as client:
        token_response = client.post(
            "https://iam.cloud.ibm.com/identity/token",
            data={
                "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                "apikey": api_key,
            },
            headers={"Accept": "application/json"},
        )
        token_response.raise_for_status()
        token = token_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        base = instance_url + "/v1/orchestrate"
        started = client.post(
            base + "/runs",
            headers=headers,
            json={
                "message": {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
                "capture_logs": False,
                "agent_id": agent_id,
            },
        )
        started.raise_for_status()
        run = started.json()
        run_id, thread_id = run.get("run_id"), run.get("thread_id")
        if not isinstance(run_id, str) or not isinstance(thread_id, str):
            raise ValueError("IBM Orchestrate did not return a run and thread ID.")
        deadline = time.monotonic() + MAPPER_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            status_response = client.get(f"{base}/runs/{run_id}", headers=headers)
            status_response.raise_for_status()
            state = str(status_response.json().get("status", "")).lower()
            if state in {"failed", "error", "cancelled", "canceled"}:
                raise ValueError(f"IBM mapper run ended with status {state}.")
            if state in {"completed", "complete", "success", "succeeded"}:
                break
            time.sleep(1.5)
        else:
            raise ValueError("IBM mapper run exceeded its 240-second limit.")
        messages_response = client.get(
            f"{base}/threads/{thread_id}/messages", headers=headers
        )
        messages_response.raise_for_status()
        messages = messages_response.json()
        messages = (
            messages.get("messages", messages)
            if isinstance(messages, dict)
            else messages
        )
        text = ""
        for message in reversed(messages or []):
            if message.get("role") == "assistant":
                content = message.get("content", "")
                text = (
                    " ".join(
                        part.get("text", "")
                        for part in content
                        if isinstance(part, dict)
                    )
                    if isinstance(content, list)
                    else str(content)
                )
                break
        if not text:
            raise ValueError("IBM mapper returned an empty response.")
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
        result = json.loads(text)
        if not isinstance(result, dict):
            raise ValueError("IBM mapper response is not a JSON object.")
        return result, run_id


def _call_orchestrate_with_json_retry(
    instance_url: str, api_key: str, agent_id: str, payload: dict[str, object]
) -> tuple[dict[str, object], str]:
    """Retry malformed Orchestrate JSON once without relaxing validation."""
    try:
        return _call_orchestrate(instance_url, api_key, agent_id, payload)
    except json.JSONDecodeError as exc:
        correction_payload = {
            **payload,
            "response_format_correction": {
                "parse_error": {
                    "message": exc.msg,
                    "line": exc.lineno,
                    "column": exc.colno,
                    "position": exc.pos,
                },
                "instruction": (
                    "Your previous response failed JSON parsing at the supplied "
                    "line and column. Correct the syntax error and return exactly "
                    "one valid JSON object matching the requested schema. Do not "
                    "include markdown fences, comments, or prose. Preserve all "
                    "criteria and source citations verbatim; do not invent, omit, "
                    "or alter requirements."
                ),
            },
        }
        return _call_orchestrate(instance_url, api_key, agent_id, correction_payload)
