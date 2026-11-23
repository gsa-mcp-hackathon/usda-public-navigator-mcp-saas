"""Typed, idempotent persistence for validated Eligibility criteria metadata."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime
from typing import Any

PROGRAM_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,99}$")
CLUSTER_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
RULESET_FIELDS = {
    "contract_version",
    "program_id",
    "criteria_package_id",
    "navigator",
    "title",
    "criteria_status",
    "profile_schema_refs",
    "source_references",
    "requirements",
    "compiled_rules",
    "program_record",
}


class InvalidCriteriaRuleset(ValueError):
    """The proposed ruleset does not satisfy the persistence contract."""


class CriteriaRulesetConflict(RuntimeError):
    """An idempotency key was reused for different ruleset content."""


def canonical_sha256(value: object) -> str:
    """Hash canonical JSON without whitespace or key-order dependence."""
    serialized = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _timestamp_with_timezone(value: object) -> bool:
    """Accept ISO timestamps only when they identify an explicit timezone."""
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.utcoffset() is not None


def validate_criteria_ruleset_registration(
    payload: object,
) -> tuple[str, str, str, str, str | None, dict[str, object]]:
    """Validate digest, source lineage, profile schemas, and compiled rules."""
    if not isinstance(payload, dict) or set(payload) != {
        "request_id",
        "cluster_id",
        "expected_current_content_sha256",
        "expected_content_sha256",
        "ruleset",
    }:
        raise InvalidCriteriaRuleset("Ruleset registration fields are invalid.")
    try:
        request_id = str(uuid.UUID(payload["request_id"]))
    except (TypeError, ValueError, AttributeError) as exc:
        raise InvalidCriteriaRuleset("Ruleset request ID is invalid.") from exc
    ruleset = payload["ruleset"]
    cluster_id = payload["cluster_id"]
    if not isinstance(ruleset, dict) or set(ruleset) != RULESET_FIELDS:
        raise InvalidCriteriaRuleset("Ruleset fields do not match the contract.")
    program_id = ruleset["program_id"]
    digest = payload["expected_content_sha256"]
    expected_current = payload["expected_current_content_sha256"]
    if (
        ruleset["contract_version"] != "eligibility-criteria-ruleset-v1"
        or not isinstance(cluster_id, str)
        or CLUSTER_ID.fullmatch(cluster_id) is None
        or not isinstance(program_id, str)
        or PROGRAM_ID.fullmatch(program_id) is None
        or not isinstance(ruleset["criteria_package_id"], str)
        or not ruleset["criteria_package_id"].startswith("criteria-")
        or not isinstance(ruleset["title"], str)
        or not ruleset["title"].strip()
        or ruleset["criteria_status"] not in {"mapped", "partial"}
        or not isinstance(ruleset["navigator"], str)
        or not isinstance(ruleset["profile_schema_refs"], list)
        or not ruleset["profile_schema_refs"]
        or not isinstance(ruleset["source_references"], list)
        or not ruleset["source_references"]
        or not isinstance(ruleset["requirements"], list)
        or not ruleset["requirements"]
        or not isinstance(ruleset["compiled_rules"], list)
        or not ruleset["compiled_rules"]
        or not isinstance(ruleset["program_record"], dict)
        or not isinstance(digest, str)
        or SHA256.fullmatch(digest) is None
        or (
            expected_current is not None
            and (
                not isinstance(expected_current, str)
                or SHA256.fullmatch(expected_current) is None
            )
        )
        or canonical_sha256(ruleset) != digest
    ):
        raise InvalidCriteriaRuleset("Ruleset identity, digest, or content is invalid.")

    source_ids: set[str] = set()
    for source in ruleset["source_references"]:
        verification_status = (
            source.get("source_verification_status")
            if isinstance(source, dict)
            else None
        )
        verified_lineage = (
            isinstance(source, dict)
            and isinstance(source.get("policy_entry_id"), str)
            and _timestamp_with_timezone(source.get("verified_at_utc"))
        )
        observed_lineage = (
            isinstance(source, dict)
            and _timestamp_with_timezone(source.get("observed_at_utc"))
            and not source.get("policy_entry_id")
            and not source.get("verified_at_utc")
        )
        if (
            not isinstance(source, dict)
            or not isinstance(source.get("source_id"), str)
            or source.get("source_type") != "public_source"
            or source.get("approval_status")
            != ("approved" if verification_status == "verified" else "unverified")
            or not isinstance(source.get("url"), str)
            or not source["url"].startswith("https://")
            or not isinstance(source.get("retrieved_at"), str)
            or not isinstance(source.get("source_content_sha256"), str)
            or SHA256.fullmatch(source["source_content_sha256"]) is None
            or verification_status not in {"verified", "unverified"}
            or (verification_status == "verified" and not verified_lineage)
            or (verification_status == "unverified" and not observed_lineage)
        ):
            raise InvalidCriteriaRuleset(
                "Ruleset source lineage or verification status is invalid."
            )
        source_ids.add(source["source_id"])
    if len(source_ids) != len(ruleset["source_references"]):
        raise InvalidCriteriaRuleset("Ruleset source IDs must be unique.")

    schema_refs = ruleset["profile_schema_refs"]
    if any(
        not isinstance(reference, dict)
        or set(reference) != {"registry_id", "schema_version"}
        or not isinstance(reference["registry_id"], str)
        or not reference["registry_id"]
        or not isinstance(reference["schema_version"], str)
        or not reference["schema_version"]
        for reference in schema_refs
    ):
        raise InvalidCriteriaRuleset("Profile schema references are invalid.")
    if len(
        {(item["registry_id"], item["schema_version"]) for item in schema_refs}
    ) != len(schema_refs):
        raise InvalidCriteriaRuleset("Profile schema references must be unique.")

    record = ruleset["program_record"]
    if (
        record.get("program_id") != program_id
        or record.get("contract_version") != "eligibility-program-v1"
        or not isinstance(record.get("criteria"), list)
        or len(record["criteria"]) != len(ruleset["compiled_rules"])
        or record["criteria"]
        != [item.get("criterion") for item in ruleset["compiled_rules"]]
    ):
        raise InvalidCriteriaRuleset("Compiled program rules do not match identity.")
    criterion_ids: set[str] = set()
    for rule in ruleset["compiled_rules"]:
        citation = rule.get("citation") if isinstance(rule, dict) else None
        requirement_id = rule.get("requirement_id") if isinstance(rule, dict) else None
        if (
            not isinstance(requirement_id, str)
            or requirement_id in criterion_ids
            or not isinstance(citation, dict)
            or citation.get("source_id") not in source_ids
            or not all(
                isinstance(citation.get(field), str) and citation[field]
                for field in (
                    "source_version",
                    "source_content_sha256",
                    "locator",
                    "excerpt",
                )
            )
            or SHA256.fullmatch(citation["source_content_sha256"]) is None
        ):
            raise InvalidCriteriaRuleset("Compiled rule citation is incomplete.")
        criterion_ids.add(requirement_id)
    return cluster_id, request_id, program_id, digest, expected_current, ruleset


def register_criteria_ruleset(conn: Any, payload: object) -> dict[str, object]:
    """Append an immutable package and atomically select it as the active ruleset."""
    (
        cluster_id,
        request_id,
        program_id,
        digest,
        expected_current,
        ruleset,
    ) = validate_criteria_ruleset_registration(payload)
    with conn.transaction():
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{cluster_id}:{program_id}",),
            )
            cursor.execute(
                """SELECT cluster_id, program_id, content_sha256, outcome,
                          previous_content_sha256
                   FROM mcp_saas.eligibility_criteria_ruleset_events
                   WHERE request_id = %s""",
                (request_id,),
            )
            replay = cursor.fetchone()
            if replay is not None:
                if (replay[0], replay[1], replay[2], replay[4]) != (
                    cluster_id,
                    program_id,
                    digest,
                    expected_current,
                ):
                    raise CriteriaRulesetConflict(
                        "Ruleset request ID was already used for different content."
                    )
                return {
                    "status": "replayed",
                    "cluster_id": cluster_id,
                    "program_id": program_id,
                    "content_sha256": digest,
                    "outcome": replay[3],
                    "idempotent_replay": True,
                }

            cursor.execute(
                """SELECT content_sha256
                   FROM mcp_saas.eligibility_criteria_ruleset_heads
                   WHERE cluster_id = %s AND program_id = %s FOR UPDATE""",
                (cluster_id, program_id),
            )
            previous = cursor.fetchone()
            previous_digest = previous[0].strip() if previous else None
            if previous_digest != expected_current:
                raise CriteriaRulesetConflict(
                    "Active criteria ruleset changed during mapping."
                )
            if previous_digest != digest:
                _require_active_profile_schemas(cursor, ruleset)
            cursor.execute(
                """INSERT INTO mcp_saas.eligibility_criteria_rulesets
                       (cluster_id, program_id, content_sha256, ruleset)
                   VALUES (%s, %s, %s, %s::jsonb)
                   ON CONFLICT (cluster_id, program_id, content_sha256) DO NOTHING""",
                (cluster_id, program_id, digest, json.dumps(ruleset, sort_keys=True)),
            )
            cursor.execute(
                """SELECT ruleset FROM mcp_saas.eligibility_criteria_rulesets
                   WHERE cluster_id = %s AND program_id = %s
                     AND content_sha256 = %s""",
                (cluster_id, program_id, digest),
            )
            stored = cursor.fetchone()
            if stored is None or stored[0] != ruleset:
                raise CriteriaRulesetConflict(
                    "Stored ruleset content did not match its digest."
                )
            outcome = "duplicate" if previous_digest == digest else "activated"
            if outcome == "activated":
                cursor.execute(
                    """INSERT INTO mcp_saas.eligibility_criteria_ruleset_heads
                           (cluster_id, program_id, content_sha256, request_id)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (cluster_id, program_id) DO UPDATE SET
                           content_sha256 = EXCLUDED.content_sha256,
                           request_id = EXCLUDED.request_id,
                           updated_at = now()""",
                    (cluster_id, program_id, digest, request_id),
                )
            cursor.execute(
                """INSERT INTO mcp_saas.eligibility_criteria_ruleset_events
                       (request_id, cluster_id, program_id, content_sha256,
                        previous_content_sha256, outcome,
                        authorization_capability)
                   VALUES (%s, %s, %s, %s, %s, %s,
                           'validated_sandbox_mapping')""",
                (request_id, cluster_id, program_id, digest, previous_digest, outcome),
            )
    return {
        "status": "stored" if outcome == "activated" else "duplicate",
        "cluster_id": cluster_id,
        "program_id": program_id,
        "content_sha256": digest,
        "outcome": outcome,
        "idempotent_replay": False,
    }


def _require_active_profile_schemas(cursor: Any, ruleset: dict[str, object]) -> None:
    """Reject rulesets whose cited profile schemas are no longer active."""
    active_attributes: set[str] = set()
    for reference in ruleset["profile_schema_refs"]:
        cursor.execute(
            """SELECT definition FROM mcp_saas.schema_registry
               WHERE registry_id = %s AND schema_version = %s
                 AND lifecycle_state = 'active'""",
            (reference["registry_id"], reference["schema_version"]),
        )
        row = cursor.fetchone()
        if row is None:
            raise InvalidCriteriaRuleset(
                "Ruleset must reference active profile schema versions."
            )
        definition = row[0]
        attributes = (
            definition.get("attributes") if isinstance(definition, dict) else None
        )
        if not isinstance(attributes, dict):
            raise InvalidCriteriaRuleset("Active profile schema is malformed.")
        active_attributes.update(attributes)
    for criterion in ruleset["program_record"]["criteria"]:
        if not isinstance(criterion, dict):
            raise InvalidCriteriaRuleset("Mapped criterion is malformed.")
        required_attributes = {
            value
            for value in (
                criterion.get("attribute"),
                criterion.get("context_attribute"),
            )
            if isinstance(value, str)
        }
        if not required_attributes <= active_attributes:
            raise InvalidCriteriaRuleset(
                "Mapped criterion fields must exist in cited active profile schemas."
            )


def read_active_criteria_ruleset(
    conn: Any, cluster_id: str, program_id: str
) -> dict[str, object] | None:
    """Read the exact active immutable ruleset for one program identity."""
    if (
        not isinstance(cluster_id, str)
        or CLUSTER_ID.fullmatch(cluster_id) is None
        or not isinstance(program_id, str)
        or PROGRAM_ID.fullmatch(program_id) is None
    ):
        raise InvalidCriteriaRuleset("Program identity is invalid.")
    with conn.cursor() as cursor:
        cursor.execute(
            """SELECT ruleset.program_id, ruleset.content_sha256,
                      ruleset.ruleset, head.updated_at
               FROM mcp_saas.eligibility_criteria_ruleset_heads AS head
               JOIN mcp_saas.eligibility_criteria_rulesets AS ruleset
               USING (cluster_id, program_id, content_sha256)
               WHERE head.cluster_id = %s AND head.program_id = %s""",
            (cluster_id, program_id),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return {
        "status": "ok",
        "cluster_id": cluster_id,
        "program_id": row[0],
        "content_sha256": row[1].strip(),
        "ruleset": row[2],
        "updated_at": row[3].isoformat(),
    }
