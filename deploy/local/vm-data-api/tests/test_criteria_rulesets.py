from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

API_DIRECTORY = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "criteria_rulesets_test_module", API_DIRECTORY / "criteria_rulesets.py"
)
assert SPEC is not None and SPEC.loader is not None
rulesets = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rulesets
SPEC.loader.exec_module(rulesets)


def valid_ruleset() -> dict[str, object]:
    return {
        "contract_version": "eligibility-criteria-ruleset-v1",
        "program_id": "usda-csfp",
        "criteria_package_id": "criteria-" + "a" * 32,
        "navigator": "senior_caregiver",
        "title": "CSFP partial minimum-age mapping",
        "criteria_status": "partial",
        "profile_schema_refs": [
            {"registry_id": "core-schema-registry", "schema_version": "core-v4"}
        ],
        "source_references": [
            {
                "source_id": "usda.fna.senior-food",
                "source_type": "public_source",
                "approval_status": "approved",
                "url": "https://www.fns.usda.gov/csfp/factsheet",
                "retrieved_at": "2026-11-05T12:00:00+00:00",
                "source_content_sha256": "b" * 64,
                "policy_entry_id": "fns-csfp",
                "source_verification_status": "verified",
                "verified_at_utc": "2026-11-05T12:00:00+00:00",
            }
        ],
        "requirements": [
            {
                "requirement_id": "csfp-minimum-age-60",
                "requirement_type": "eligibility",
                "mapping_status": "mapped",
            }
        ],
        "compiled_rules": [
            {
                "requirement_id": "csfp-minimum-age-60",
                "criterion": {
                    "criterion_id": "csfp-minimum-age-60",
                    "attribute": "core.person.age_band",
                    "operator": "one_of",
                    "value": ["60_64", "65_74", "75_plus"],
                    "source_id": "usda.fna.senior-food",
                },
                "citation": {
                    "source_id": "usda.fna.senior-food",
                    "source_version": "sha256:" + "b" * 64,
                    "source_content_sha256": "b" * 64,
                    "locator": "Age eligibility paragraph",
                    "excerpt": "People age 60 and older may participate.",
                },
            }
        ],
        "program_record": {
            "contract_version": "eligibility-program-v1",
            "program_id": "usda-csfp",
            "criteria": [
                {
                    "criterion_id": "csfp-minimum-age-60",
                    "attribute": "core.person.age_band",
                    "operator": "one_of",
                    "value": ["60_64", "65_74", "75_plus"],
                    "source_id": "usda.fna.senior-food",
                }
            ],
        },
    }


def registration(
    ruleset: dict[str, object],
    *,
    cluster_id: str = "eligibility",
    expected_current: str | None = None,
) -> dict[str, object]:
    return {
        "request_id": str(uuid.uuid4()),
        "cluster_id": cluster_id,
        "expected_current_content_sha256": expected_current,
        "expected_content_sha256": rulesets.canonical_sha256(ruleset),
        "ruleset": ruleset,
    }


class _FakeCursor:
    def __init__(self) -> None:
        self.result: tuple[Any, ...] | None = None
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self.rulesets: dict[tuple[str, str, str], dict[str, object]] = {}
        self.events: dict[str, tuple[Any, ...]] = {}
        self.heads: dict[tuple[str, str], str] = {}

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[Any, ...] = ()) -> None:
        self.statements.append((statement, parameters))
        if "FROM mcp_saas.eligibility_criteria_ruleset_events" in statement:
            self.result = self.events.get(str(parameters[0]))
        elif "FROM mcp_saas.schema_registry" in statement:
            self.result = (
                {"attributes": {"core.person.age_band": {"dimension_id": "person"}}},
            )
        elif "FROM mcp_saas.eligibility_criteria_ruleset_heads" in statement:
            key = (str(parameters[0]), str(parameters[1]))
            self.result = (self.heads[key],) if key in self.heads else None
        elif statement.startswith("INSERT INTO mcp_saas.eligibility_criteria_rulesets"):
            self.rulesets[
                (str(parameters[0]), str(parameters[1]), str(parameters[2]))
            ] = json.loads(parameters[3])
        elif statement.startswith(
            "INSERT INTO mcp_saas.eligibility_criteria_ruleset_heads"
        ):
            self.heads[(str(parameters[0]), str(parameters[1]))] = str(parameters[2])
        elif statement.startswith(
            "INSERT INTO mcp_saas.eligibility_criteria_ruleset_events"
        ):
            request_id, cluster_id, program_id, digest, previous, outcome = parameters
            self.events[str(request_id)] = (
                cluster_id,
                program_id,
                digest,
                outcome,
                previous,
            )
        elif "FROM mcp_saas.eligibility_criteria_rulesets" in statement:
            self.result = (
                self.rulesets.get(
                    (str(parameters[0]), str(parameters[1]), str(parameters[2]))
                ),
            )
        if "FROM mcp_saas.eligibility_criteria_ruleset_heads AS head" in statement:
            cluster_id, program_id = str(parameters[0]), str(parameters[1])
            digest = self.heads.get((cluster_id, program_id))
            ruleset = (
                self.rulesets.get((cluster_id, program_id, digest))
                if digest is not None
                else None
            )
            self.result = (
                (program_id, digest, ruleset, datetime.now(UTC))
                if ruleset is not None
                else None
            )

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.result


class _FakeConnection:
    def __init__(self) -> None:
        self.current_cursor = _FakeCursor()

    def transaction(self) -> _FakeConnection:
        return self

    def cursor(self) -> _FakeCursor:
        return self.current_cursor

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_ruleset_registration_accepts_cited_active_schema_package() -> None:
    ruleset = valid_ruleset()

    cluster_id, request_id, program_id, digest, expected_current, stored = (
        rulesets.validate_criteria_ruleset_registration(registration(ruleset))
    )

    assert request_id
    assert cluster_id == "eligibility"
    assert program_id == "usda-csfp"
    assert digest == rulesets.canonical_sha256(ruleset)
    assert expected_current is None
    assert stored == ruleset


def test_ruleset_registration_accepts_current_unverified_public_source() -> None:
    ruleset = valid_ruleset()
    source = ruleset["source_references"][0]
    source.pop("policy_entry_id")
    source.pop("verified_at_utc")
    source["source_verification_status"] = "unverified"
    source["approval_status"] = "unverified"
    source["observed_at_utc"] = "2026-11-07T12:00:00+00:00"

    _, _, _, _, _, stored = rulesets.validate_criteria_ruleset_registration(
        registration(ruleset)
    )

    assert stored["source_references"][0]["source_verification_status"] == "unverified"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda source: source.pop("observed_at_utc"),
        lambda source: source.update(policy_entry_id="unexpected"),
        lambda source: source.update(source_verification_status="unknown"),
        lambda source: source.update(approval_status="approved"),
        lambda source: source.update(observed_at_utc="2026-11-07T12:00:00"),
    ],
)
def test_unverified_source_requires_consistent_observation_lineage(mutate: Any) -> None:
    ruleset = valid_ruleset()
    source = ruleset["source_references"][0]
    source.pop("policy_entry_id")
    source.pop("verified_at_utc")
    source["source_verification_status"] = "unverified"
    source["approval_status"] = "unverified"
    source["observed_at_utc"] = "2026-11-07T12:00:00+00:00"
    mutate(source)

    with pytest.raises(rulesets.InvalidCriteriaRuleset, match="source lineage"):
        rulesets.validate_criteria_ruleset_registration(registration(ruleset))


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda r: r.update(program_id="../other"), "identity"),
        (
            lambda r: r["source_references"][0].update(approval_status="not_reviewed"),
            "source",
        ),
        (lambda r: r["profile_schema_refs"].clear(), "identity"),
        (
            lambda r: r["compiled_rules"][0]["citation"].update(
                source_content_sha256="x"
            ),
            "citation",
        ),
        (
            lambda r: r["program_record"].update(program_id="another-program"),
            "identity",
        ),
    ],
)
def test_ruleset_registration_rejects_invalid_source_or_mapping(
    mutate: Any, message: str
) -> None:
    ruleset = valid_ruleset()
    mutate(ruleset)

    with pytest.raises(rulesets.InvalidCriteriaRuleset, match=message):
        rulesets.validate_criteria_ruleset_registration(registration(ruleset))


def test_ruleset_registration_requires_digest_to_match_canonical_content() -> None:
    request = registration(valid_ruleset())
    request["expected_content_sha256"] = "0" * 64

    with pytest.raises(rulesets.InvalidCriteriaRuleset, match="digest"):
        rulesets.validate_criteria_ruleset_registration(request)


def test_ruleset_write_appends_and_selects_one_active_program_digest() -> None:
    connection = _FakeConnection()
    request = registration(valid_ruleset())

    result = rulesets.register_criteria_ruleset(connection, request)

    assert result["status"] == "stored"
    assert result["outcome"] == "activated"
    statements = "\n".join(item[0] for item in connection.current_cursor.statements)
    assert "INSERT INTO mcp_saas.eligibility_criteria_rulesets" in statements
    assert "INSERT INTO mcp_saas.eligibility_criteria_ruleset_heads" in statements
    assert "INSERT INTO mcp_saas.eligibility_criteria_ruleset_events" in statements
    assert "FROM mcp_saas.schema_registry" in statements

    replay = rulesets.register_criteria_ruleset(connection, request)
    assert replay["status"] == "replayed"
    assert replay["idempotent_replay"] is True


def test_ruleset_write_uses_compare_and_set_and_can_reactivate_history() -> None:
    connection = _FakeConnection()
    first_ruleset = valid_ruleset()
    first_request = registration(first_ruleset)
    first = rulesets.register_criteria_ruleset(connection, first_request)
    first_digest = str(first["content_sha256"])

    second_ruleset = valid_ruleset()
    second_ruleset["title"] = "Updated CSFP mapping"
    second = rulesets.register_criteria_ruleset(
        connection,
        registration(second_ruleset, expected_current=first_digest),
    )
    assert second["outcome"] == "activated"

    previous_digest = str(second["content_sha256"])
    restored = rulesets.register_criteria_ruleset(
        connection,
        registration(first_ruleset, expected_current=previous_digest),
    )
    assert restored["outcome"] == "activated"
    assert connection.current_cursor.heads[("eligibility", "usda-csfp")] == first_digest


def test_ruleset_write_rejects_a_stale_active_digest() -> None:
    connection = _FakeConnection()
    rulesets.register_criteria_ruleset(connection, registration(valid_ruleset()))

    with pytest.raises(rulesets.CriteriaRulesetConflict, match="changed"):
        rulesets.register_criteria_ruleset(
            connection,
            registration(
                {**valid_ruleset(), "title": "Stale mapping"},
                expected_current=None,
            ),
        )


def test_same_program_rulesets_are_isolated_by_cluster_on_write_and_read() -> None:
    connection = _FakeConnection()
    first_ruleset = valid_ruleset()
    second_ruleset = {**valid_ruleset(), "title": "Independent cluster mapping"}

    first = rulesets.register_criteria_ruleset(
        connection, registration(first_ruleset, cluster_id="eligibility")
    )
    second = rulesets.register_criteria_ruleset(
        connection, registration(second_ruleset, cluster_id="eligibility-alt")
    )

    first_read = rulesets.read_active_criteria_ruleset(
        connection, "eligibility", "usda-csfp"
    )
    second_read = rulesets.read_active_criteria_ruleset(
        connection, "eligibility-alt", "usda-csfp"
    )
    assert first["cluster_id"] == "eligibility"
    assert second["cluster_id"] == "eligibility-alt"
    assert first_read is not None and first_read["ruleset"] == first_ruleset
    assert second_read is not None and second_read["ruleset"] == second_ruleset
    assert first_read["content_sha256"] != second_read["content_sha256"]

    with pytest.raises(rulesets.CriteriaRulesetConflict, match="changed"):
        rulesets.register_criteria_ruleset(
            connection,
            registration(
                {**valid_ruleset(), "title": "Cross-scope stale write"},
                cluster_id="eligibility-alt",
                expected_current=str(first["content_sha256"]),
            ),
        )
