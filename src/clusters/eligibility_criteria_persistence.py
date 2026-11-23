"""Persist validated source mappings and evaluate the active criteria ruleset."""

from __future__ import annotations

from datetime import UTC
from typing import Callable
from urllib.parse import parse_qsl, urlsplit

from fastmcp import FastMCP

from clusters.content_authorization import (
    ContentTarget,
    RuntimePrincipal,
    authorize_content_action,
)
from clusters.eligibility_candidate import (
    CandidateCitation,
    ProgramCriteriaPackageCompiler,
    ProgramCriteriaPackageRequest,
    RequirementCandidate,
)
from clusters.eligibility_candidate_evaluation import (
    ProgramCandidateEvaluationRequest,
    RequestScopedCandidateCriteriaPackage,
    evaluate_request_scoped_candidate,
)
from clusters.eligibility_contracts import Jurisdiction, NavigatorId, SourceReference
from clusters.eligibility_discovery import (
    CANONICAL_TO_PROFILE_ATTRIBUTE,
    PROFILE_TO_CANONICAL_ATTRIBUTE,
    ProfileSubmission,
)
from clusters.eligibility_persistence import (
    CriteriaRulesetConflict,
    CriteriaRulesetRejected,
    HttpCriteriaRulesetRepository,
    PersistenceApiUnavailable,
)
from clusters.eligibility_public_search import (
    canonicalize_public_https_url,
    match_approved_url,
)
from clusters.eligibility_source_reader import (
    _active_policy,
    _fetch_page,
    _source_verification_status,
)
from example_server.storage import StorageUnavailable

PROFILE_SCHEMA_REGISTRIES = (
    "core-schema-registry",
    "eligibility-profile-extension-schema",
)


def read_active_criteria_readiness(
    program_id: str,
    linked_source_references: list[dict[str, object]],
    repository: HttpCriteriaRulesetRepository,
) -> dict[str, object]:
    """Read active-rule availability without evaluating a profile or writing data."""
    try:
        active = repository.read_active(program_id)
    except PersistenceApiUnavailable:
        return {
            "active_ruleset_status": "unavailable",
            "evaluation_available": False,
        }
    if active is None:
        return {
            "active_ruleset_status": "not_found",
            "evaluation_available": False,
        }

    ruleset = active.get("ruleset")
    record = ruleset.get("program_record") if isinstance(ruleset, dict) else None
    if (
        not isinstance(ruleset, dict)
        or not isinstance(record, dict)
        or not isinstance(record.get("sources"), list)
        or not isinstance(ruleset.get("requirements"), list)
    ):
        return {
            "active_ruleset_status": "invalid",
            "evaluation_available": False,
        }

    active_sources = {
        (source.get("source_id"), source.get("url"))
        for source in record["sources"]
        if isinstance(source, dict)
        and isinstance(source.get("source_id"), str)
        and isinstance(source.get("url"), str)
    }
    linked_sources = {
        (source.get("source_id"), source.get("url"))
        for source in linked_source_references
        if isinstance(source.get("source_id"), str)
        and isinstance(source.get("url"), str)
    }
    if not active_sources.intersection(linked_sources):
        return {
            "active_ruleset_status": "source_mismatch",
            "evaluation_available": False,
        }

    request_sources = list(record["sources"])
    persisted_citation_source_ids: set[str] = set()
    persisted_source_references = ruleset.get("source_references")
    if isinstance(persisted_source_references, list):
        for source_payload in persisted_source_references:
            if not isinstance(source_payload, dict):
                continue
            try:
                source = SourceReference.model_validate(source_payload)
            except (TypeError, ValueError):
                continue
            cited_exactly = False
            for requirement in ruleset["requirements"]:
                if not isinstance(requirement, dict):
                    continue
                citation_payload = requirement.get("citation")
                if not isinstance(citation_payload, dict):
                    continue
                try:
                    citation = CandidateCitation.model_validate(citation_payload)
                except (TypeError, ValueError):
                    continue
                if (
                    citation.source_id == source.source_id
                    and citation.source_url == source.url
                    and citation.retrieved_at == source.retrieved_at
                    and (
                        citation.source_version is None
                        or citation.source_version == source.publisher_version
                    )
                ):
                    cited_exactly = True
                    break
            if not cited_exactly:
                continue
            request_sources = [
                item
                for item in request_sources
                if not (
                    isinstance(item, dict) and item.get("source_id") == source.source_id
                )
            ]
            request_sources.append(source.model_dump(mode="json"))
            persisted_citation_source_ids.add(source.source_id)

    for linked_source in linked_source_references:
        source_id = linked_source.get("source_id")
        if not isinstance(source_id, str):
            continue
        source_payload = dict(linked_source)
        source_payload.setdefault("url", source_payload.get("source_url"))
        try:
            source = SourceReference.model_validate(source_payload)
        except (TypeError, ValueError):
            continue
        if source.source_id in persisted_citation_source_ids:
            continue
        exact_citation_exists = False
        for requirement in ruleset["requirements"]:
            if not isinstance(requirement, dict):
                continue
            citation_payload = requirement.get("citation")
            if not isinstance(citation_payload, dict):
                continue
            try:
                citation = CandidateCitation.model_validate(citation_payload)
            except (TypeError, ValueError):
                continue
            if (
                citation.source_id == source.source_id
                and citation.source_url == source.url
                and citation.retrieved_at == source.retrieved_at
                and (
                    citation.source_version is None
                    or citation.source_version == source.publisher_version
                )
            ):
                exact_citation_exists = True
                break
        if not exact_citation_exists:
            continue

        matching_sources = [
            item
            for item in request_sources
            if isinstance(item, dict) and item.get("source_id") == source.source_id
        ]
        exact_source_exists = False
        for item in matching_sources:
            try:
                existing_source = SourceReference.model_validate(item)
            except (TypeError, ValueError):
                continue
            if existing_source == source:
                exact_source_exists = True
                break
        if exact_source_exists:
            continue
        request_sources = [
            item
            for item in request_sources
            if not (
                isinstance(item, dict) and item.get("source_id") == source.source_id
            )
        ]
        request_sources.append(source.model_dump(mode="json"))

    try:
        request = ProgramCriteriaPackageRequest(
            program_id=program_id,
            catalog_version=str(record["catalog_version"]),
            navigator=str(record["navigator"]),
            title=str(record["title"]),
            description=str(record["description"]),
            jurisdiction=record["jurisdiction"],
            sources=request_sources,
            requirements=ruleset["requirements"],
            source_limitations=record.get("source_limitations", []),
        )
    except (KeyError, TypeError, ValueError):
        return {
            "active_ruleset_status": "invalid",
            "evaluation_available": False,
        }

    profile_facts: list[dict[str, object]] = []
    for requirement in request.requirements:
        if (
            requirement.mapping_status != "mapped"
            or requirement.requirement_type != "eligibility"
        ):
            continue
        source = next(
            (
                item
                for item in request.sources
                if item.source_id == requirement.citation.source_id
            ),
            None,
        )
        if source is None:
            continue
        criterion_id = requirement.criterion_id or requirement.requirement_id
        attributes = {requirement.profile_attribute}
        if requirement.context_attribute is not None:
            attributes.add(requirement.context_attribute)
        for canonical_attribute in attributes:
            if canonical_attribute is None:
                continue
            profile_facts.append(
                {
                    "attribute": CANONICAL_TO_PROFILE_ATTRIBUTE.get(
                        canonical_attribute, canonical_attribute
                    ),
                    "criterion_id": criterion_id,
                    "program_id": request.program_id,
                    "program_title": request.title,
                    "source_reference": source.model_dump(mode="json"),
                }
            )

    return {
        "active_ruleset_status": "active",
        "evaluation_available": any(
            requirement.mapping_status == "mapped"
            and requirement.requirement_type == "eligibility"
            for requirement in request.requirements
        ),
        "active_ruleset_package_id": ruleset.get("criteria_package_id"),
        "active_ruleset_content_sha256": active.get("content_sha256"),
        "profile_facts": profile_facts,
    }


def register_criteria_ruleset_persistence(
    app: FastMCP,
    *,
    repository: HttpCriteriaRulesetRepository,
    policy_reader: Callable[[], dict[str, object]] | None,
    runtime_principal: RuntimePrincipal | None = None,
) -> None:
    """Register validated ruleset persistence and evaluation by stable program ID."""

    @app.tool(
        name="persist_program_criteria_package",
        annotations={
            "title": "Map and save program criteria",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    def persist_program_criteria_package(
        program_id: str | None = None,
        catalog_version: str | None = None,
        navigator: NavigatorId | None = None,
        title: str | None = None,
        description: str | None = None,
        jurisdiction: Jurisdiction | None = None,
        sources: list[SourceReference] | None = None,
        requirements: list[RequirementCandidate] | None = None,
        source_limitations: list[str] | None = None,
        request: ProgramCriteriaPackageRequest | None = None,
    ) -> dict[str, object]:
        """Validate selected source evidence and save reusable mapped rule metadata.

        The active sandbox source policy and active profile schemas are checked
        before the exact criteria package is stored. No page body or profile fact
        is written. Unsupported clauses remain unresolved and cited.
        """
        flat_arguments = (
            program_id,
            catalog_version,
            navigator,
            title,
            description,
            jurisdiction,
            sources,
            requirements,
            source_limitations,
        )
        if request is None:
            flat_payload = {
                key: value
                for key, value in {
                    "program_id": program_id,
                    "catalog_version": catalog_version,
                    "navigator": navigator,
                    "title": title,
                    "description": description,
                    "jurisdiction": jurisdiction,
                    "sources": sources,
                    "requirements": requirements,
                    "source_limitations": source_limitations,
                }.items()
                if value is not None
            }
            try:
                request = ProgramCriteriaPackageRequest.model_validate(flat_payload)
            except (TypeError, ValueError) as exc:
                return {
                    "status": "invalid_mapping",
                    "reason_code": "criteria_package_contract_invalid",
                    "mapping_persisted": False,
                    "errors": _validation_errors(exc),
                    "next_action": "correct_the_selected_source_or_mapping_fields",
                }
        elif any(value is not None for value in flat_arguments):
            return {
                "status": "invalid_mapping",
                "reason_code": "criteria_package_contract_invalid",
                "mapping_persisted": False,
                "next_action": "supply_flat_criteria_fields_or_the_request_envelope",
            }
        # A selected public source keeps its unverified status; it is persisted as
        # request-scoped criteria, not as a shared-catalog record ( B1).
        if (
            any(source.approval_status != "approved" for source in request.sources)
            and request.catalog_version != "request-scoped"
        ):
            request = request.model_copy(update={"catalog_version": "request-scoped"})
        decision = authorize_content_action(
            "criteria.ruleset.persist",
            ContentTarget(
                scope_id=(runtime_principal.scope_id if runtime_principal else ""),
                resource_id=request.program_id,
            ),
            runtime_principal,
        )
        if not decision.allowed:
            return {
                "status": "authorization_required",
                "reason_code": decision.reason,
                "mapping_persisted": False,
                "next_action": "route_content_write_to_its_bound_runtime",
            }
        try:
            result = _persist_ruleset(request, repository, policy_reader)
            if result.get("status") in {"stored", "duplicate", "replayed"}:
                result["authorization"] = {
                    "decided_by": decision.decided_by,
                    "scope_id": runtime_principal.scope_id,
                    "action": "criteria.ruleset.persist",
                }
            return result
        except (TypeError, ValueError) as exc:
            return {
                "status": "invalid_mapping",
                "reason_code": "criteria_package_contract_invalid",
                "mapping_persisted": False,
                "errors": _validation_errors(exc),
                "next_action": "correct_the_selected_source_or_mapping_fields",
            }

    @app.tool(
        name="evaluate_persisted_program_criteria",
        annotations={
            "title": "Score against saved program criteria",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    def evaluate_persisted_program_criteria(
        program_id: str, profile_submission: ProfileSubmission
    ) -> dict[str, object]:
        """Evaluate one synthetic profile against the active saved ruleset.

        Reads only reusable public-source criteria metadata. Missing mapped
        profile facts and unresolved source clauses remain unknown; no profile
        values are persisted and no official eligibility decision is made.
        """
        return evaluate_active_criteria_ruleset(
            program_id, profile_submission, repository
        )


def evaluate_active_criteria_ruleset(
    program_id: str,
    profile_submission: ProfileSubmission,
    repository: HttpCriteriaRulesetRepository,
) -> dict[str, object]:
    """Read and evaluate one active persisted ruleset without writing profile data."""
    try:
        active = repository.read_active(program_id)
    except PersistenceApiUnavailable:
        return _criteria_read_unavailable(program_id)
    if active is None:
        return {
            "status": "mapping_required",
            "reason_code": "criteria_mapping_required",
            "program_id": program_id,
            "evaluation_performed": False,
            "next_action": (
                "call_map_save_and_score_program_candidate_with_the_selected_source"
            ),
        }
    ruleset = active.get("ruleset")
    if not isinstance(ruleset, dict):
        return _criteria_read_unavailable(program_id)
    try:
        record = ruleset["program_record"]
        request = ProgramCriteriaPackageRequest(
            program_id=program_id,
            catalog_version=str(record["catalog_version"]),
            navigator=str(record["navigator"]),
            title=str(record["title"]),
            description=str(record["description"]),
            jurisdiction=record["jurisdiction"],
            sources=record["sources"],
            requirements=ruleset["requirements"],
            source_limitations=record.get("source_limitations", []),
        )
        source_references = ruleset.get("source_references")
        if not isinstance(source_references, list):
            source_references = [
                source.model_dump(mode="json", exclude_none=True)
                for source in request.sources
            ]
        active_identity = {
            "criteria_package_id": ruleset.get("criteria_package_id"),
            "criteria_content_sha256": active.get("content_sha256"),
            "source_references": source_references,
            "ruleset_readback_verified": True,
        }
        candidate_payload = request.model_dump(mode="python")
        candidate_payload["catalog_version"] = "request-scoped"
        candidate = RequestScopedCandidateCriteriaPackage.model_validate(
            candidate_payload
        )
        result = evaluate_request_scoped_candidate(
            ProgramCandidateEvaluationRequest(
                criteria_package=candidate,
                profile_submission=profile_submission,
            )
        )
    except (KeyError, TypeError, ValueError):
        return _criteria_read_unavailable(program_id)
    return {
        **result,
        "evaluation_scope": "persisted_program_ruleset",
        **active_identity,
    }


def _persist_ruleset(
    request: ProgramCriteriaPackageRequest,
    repository: HttpCriteriaRulesetRepository,
    policy_reader: Callable[[], dict[str, object]] | None,
) -> dict[str, object]:
    """Compile, validate current source/schema evidence, then write/read back."""
    policy = None
    policy_reference: dict[str, str] = {}
    if policy_reader is not None:
        try:
            policy_state = policy_reader()
            policy, policy_reference, _ = _active_policy(policy_state)
        except (StorageUnavailable, OSError, ValueError):
            # The policy classifies known publishers; it does not gate this
            # sandbox's source-backed mapping path.
            pass

    persisted_sources: list[dict[str, object]] = []
    matched_source_hashes: dict[str, str] = {}
    for source in request.sources:
        if source.url is None or source.source_type != "public_source":
            return _mapping_blocked(
                request.program_id,
                "source_reference_not_public_and_cited",
                "select_a_cited_public_source",
            )
        canonical_url = canonicalize_public_https_url(str(source.url))
        if canonical_url is None:
            return _mapping_blocked(
                request.program_id,
                "source_reference_not_safe_public_https",
                "select_a_public_https_source_without_credentials_or_redirect_parameters",
            )
        query_keys = {
            key.lower() for key, _ in parse_qsl(urlsplit(canonical_url).query)
        }
        if query_keys & {
            "api_key",
            "apikey",
            "access_token",
            "token",
            "password",
            "secret",
        }:
            return _mapping_blocked(
                request.program_id,
                "credential_like_query_parameter_rejected",
                "remove_credential_like_query_parameters_from_the_source_url",
            )
        matched = match_approved_url(canonical_url, policy.entries) if policy else None
        entry = matched[0] if matched else None
        if matched:
            canonical_url = matched[1]
        publisher_verified = bool(
            entry
            and entry.source_id == source.source_id
            and _source_verification_status(entry) == "verified"
        )
        fetched = _fetch_page(canonical_url)
        if fetched.get("status") != "ok":
            return _mapping_blocked(
                request.program_id,
                str(fetched.get("reason_code", "source_fetch_failed")),
                "retry_the_source_read_and_extract_current_citations",
            )
        content_hash = fetched.get("source_content_sha256")
        visible_text_hash = fetched.get("source_visible_text_sha256")
        source_citations = [
            requirement.citation
            for requirement in request.requirements
            if requirement.citation.source_id == source.source_id
        ]
        cited_content_hashes = {
            citation.source_content_sha256 for citation in source_citations
        }
        cited_visible_text_hashes = {
            citation.source_visible_text_sha256 for citation in source_citations
        }
        has_consistent_raw_hash = (
            len(cited_content_hashes) == 1 and None not in cited_content_hashes
        )
        if cited_visible_text_hashes == {None}:
            snapshot_matches = isinstance(content_hash, str) and all(
                citation.source_content_sha256 == content_hash
                for citation in source_citations
            )
            verified_visible_text_hash = None
        else:
            snapshot_matches = (
                len(cited_visible_text_hashes) == 1
                and None not in cited_visible_text_hashes
                and isinstance(visible_text_hash, str)
                and cited_visible_text_hashes == {visible_text_hash}
            )
            verified_visible_text_hash = (
                visible_text_hash if isinstance(visible_text_hash, str) else None
            )
        if (
            not isinstance(content_hash, str)
            or not source_citations
            or not has_consistent_raw_hash
            or not snapshot_matches
        ):
            return _mapping_blocked(
                request.program_id,
                "source_snapshot_changed_or_citation_missing",
                "read_the_current_source_and_refresh_citations",
            )
        # Keep the exact response fingerprint from the reviewed citation. The
        # visible-text fingerprint separately confirms the current criteria text.
        cited_content_hash = next(iter(cited_content_hashes))
        matched_source_hashes[source.source_id] = cited_content_hash
        source_reference = {
            **source.model_dump(mode="json", exclude_none=True),
            "url": canonical_url,
            "approval_status": "approved" if publisher_verified else "unverified",
            "source_content_sha256": cited_content_hash,
            **(
                {"source_visible_text_sha256": verified_visible_text_hash}
                if verified_visible_text_hash is not None
                else {}
            ),
            "authority_class": (
                entry.authority_class if publisher_verified else "unverified"
            ),
            "source_verification_status": (
                "verified" if publisher_verified else "unverified"
            ),
        }
        if publisher_verified and entry is not None:
            source_reference["policy_entry_id"] = entry.entry_id
            source_reference["policy_reference"] = policy_reference
            # Keep verified content-addressed rulesets stable across retries.
            # The Persistence API separately records the write event timestamp.
            source_reference["verified_at_utc"] = source.retrieved_at.astimezone(
                UTC
            ).isoformat()
        else:
            source_reference["observed_at_utc"] = source.retrieved_at.astimezone(
                UTC
            ).isoformat()
        persisted_sources.append(source_reference)

    compiled = ProgramCriteriaPackageCompiler().compile(request)
    record = compiled.get("program_record_preview")
    executable = compiled.get("compiled_rules")
    if (
        not isinstance(record, dict)
        or not isinstance(executable, list)
        or not executable
    ):
        return {
            "status": "mapping_required",
            "reason_code": "no_executable_criteria_rules",
            "program_id": request.program_id,
            "mapping_persisted": False,
            "compiled_rules": [],
            "unresolved_requirements": compiled.get("unresolved_requirements", []),
            "next_action": (
                "map_supported_clauses_and_preserve_unsupported_clauses_as_unknown"
            ),
        }

    canonical_rules = [
        _canonicalize_persisted_rule(rule, matched_source_hashes)
        for rule in executable
        if isinstance(rule, dict)
    ]
    if len(canonical_rules) != len(executable):
        return _mapping_blocked(
            request.program_id,
            "criteria_ruleset_compilation_invalid",
            "correct_the_mapped_criteria_and_retry",
        )
    canonical_record = dict(record)
    canonical_record["criteria"] = [rule["criterion"] for rule in canonical_rules]

    schema_refs, schema_error = _active_schema_refs(repository, canonical_record)
    if schema_error is not None:
        return _mapping_blocked(
            request.program_id, schema_error, "refresh_active_profile_schemas"
        )

    ruleset = {
        "contract_version": "eligibility-criteria-ruleset-v1",
        "program_id": request.program_id,
        "criteria_package_id": compiled["criteria_package_id"],
        "navigator": request.navigator,
        "title": request.title,
        "criteria_status": "partial"
        if compiled.get("unresolved_requirements")
        else "mapped",
        "profile_schema_refs": schema_refs,
        "source_references": persisted_sources,
        "requirements": [
            item.model_dump(mode="json", exclude_none=True)
            for item in request.requirements
        ],
        "compiled_rules": canonical_rules,
        "program_record": canonical_record,
    }
    try:
        result = repository.register(ruleset)
    except CriteriaRulesetConflict:
        return _mapping_blocked(
            request.program_id,
            "active_ruleset_changed_during_mapping",
            "reread_the_active_ruleset_and_retry_with_the_current_mapping",
        )
    except CriteriaRulesetRejected:
        return _mapping_blocked(
            request.program_id,
            "criteria_ruleset_contract_rejected",
            "correct_the_ruleset_schema_and_retry",
        )
    except PersistenceApiUnavailable:
        return _mapping_blocked(
            request.program_id,
            "criteria_ruleset_store_unavailable",
            "retry_when_the_persistence_api_is_healthy",
        )
    return {
        "status": "stored",
        "reason_code": "criteria_ruleset_persisted",
        "program_id": request.program_id,
        "criteria_package_id": compiled["criteria_package_id"],
        "criteria_status": ruleset["criteria_status"],
        "content_sha256": result["content_sha256"],
        "mapping_persisted": True,
        "readback_verified": result["readback_verified"],
        "source_verification_status": (
            "verified"
            if all(
                item["source_verification_status"] == "verified"
                for item in persisted_sources
            )
            else "unverified"
        ),
        "unresolved_requirements": compiled.get("unresolved_requirements", []),
        "next_action": "evaluate_persisted_program_criteria_with_a_synthetic_profile",
    }


def _active_schema_refs(
    repository: HttpCriteriaRulesetRepository, program_record: dict[str, object]
) -> tuple[list[dict[str, str]], str | None]:
    """Resolve every executable field against an active registered profile schema."""
    criteria = program_record.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        return [], "criteria_ruleset_has_no_executable_fields"
    wanted: set[str] = set()
    for criterion in criteria:
        if not isinstance(criterion, dict):
            return [], "criteria_ruleset_field_invalid"
        for key in ("attribute", "context_attribute"):
            attribute = criterion.get(key)
            if isinstance(attribute, str):
                wanted.add(PROFILE_TO_CANONICAL_ATTRIBUTE.get(attribute, attribute))
    found: set[str] = set()
    refs: list[dict[str, str]] = []
    for registry_id in PROFILE_SCHEMA_REGISTRIES:
        try:
            schemas = repository.read_active_schemas(registry_id)
        except PersistenceApiUnavailable:
            return [], "active_profile_schema_registry_unavailable"
        matching_schemas: list[dict[str, object]] = []
        for schema in schemas:
            attributes = schema.get("attributes")
            version = schema.get("schema_version")
            if not isinstance(attributes, dict) or not isinstance(version, str):
                continue
            matched = wanted.intersection(attributes)
            if matched:
                found.update(matched)
                matching_schemas.append(
                    {"registry_id": registry_id, "schema_version": version}
                )
        if matching_schemas:
            refs.extend(matching_schemas)
    missing = sorted(wanted - found)
    if missing:
        return [], "mapped_fields_not_in_an_active_profile_schema:" + ",".join(missing)
    return refs, None


def _canonicalize_persisted_rule(
    rule: dict[str, object], matched_source_hashes: dict[str, str]
) -> dict[str, object]:
    """Persist canonical schema keys while runtime evaluation uses profile aliases."""
    criterion = rule.get("criterion")
    citation = rule.get("citation")
    if not isinstance(criterion, dict) or not isinstance(citation, dict):
        raise ValueError("Compiled criteria rule is malformed.")
    canonical_criterion = dict(criterion)
    for field in ("attribute", "context_attribute"):
        attribute = canonical_criterion.get(field)
        if isinstance(attribute, str):
            canonical_criterion[field] = PROFILE_TO_CANONICAL_ATTRIBUTE.get(
                attribute, attribute
            )
    source_id = citation.get("source_id")
    content_hash = matched_source_hashes.get(str(source_id))
    if content_hash is None:
        raise ValueError("Compiled criteria rule has no verified source hash.")
    return {
        **rule,
        "criterion": canonical_criterion,
        "citation": {**citation, "source_content_sha256": content_hash},
    }


def _mapping_blocked(
    program_id: str, reason_code: str, next_action: str
) -> dict[str, object]:
    """Return a specific remediation path without disguising it as source absence."""
    return {
        "status": "mapping_required",
        "reason_code": reason_code,
        "program_id": program_id,
        "mapping_persisted": False,
        "next_action": next_action,
    }


def _criteria_read_unavailable(program_id: str) -> dict[str, object]:
    """Report a ruleset-store failure separately from missing source criteria."""
    return {
        "status": "unavailable",
        "reason_code": "criteria_ruleset_store_unavailable",
        "program_id": program_id,
        "evaluation_performed": False,
        "next_action": "retry_when_the_persistence_api_is_healthy",
    }


def _validation_errors(exc: Exception) -> list[dict[str, str]]:
    """Name the failing fields without echoing submitted values ( B1)."""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            return [
                {
                    "field": ".".join(str(part) for part in item.get("loc", ())),
                    "message": str(item.get("msg", ""))[:300],
                }
                for item in errors(include_input=False)[:10]
            ]
        except TypeError:
            pass
    return [{"field": "", "message": str(exc).splitlines()[0][:300]}]
