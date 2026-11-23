from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import uuid
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[4]
API_DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIRECTORY))
SPEC = importlib.util.spec_from_file_location(
    "criteria_rulesets_postgres_test_module", API_DIRECTORY / "criteria_rulesets.py"
)
assert SPEC is not None and SPEC.loader is not None
criteria_rulesets = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = criteria_rulesets
SPEC.loader.exec_module(criteria_rulesets)


def test_cluster_scoped_rulesets_migrate_idempotently_and_read_independently(
    database_url: str,
) -> None:
    migration = (
        ROOT / "db/migrations/045_cluster_scoped_criteria_rulesets.sql"
    ).read_text(encoding="utf-8")
    program_id = f"test-scope-{uuid.uuid4().hex}"
    documents = {
        "cluster-one": {"program_id": program_id, "rules": ["one"]},
        "cluster-two": {"program_id": program_id, "rules": ["two"]},
    }

    with psycopg.connect(database_url) as connection:
        connection.execute(migration)
        connection.execute(migration)
        for cluster_id, ruleset in documents.items():
            digest = hashlib.sha256(
                json.dumps(ruleset, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            connection.execute(
                """INSERT INTO mcp_saas.eligibility_criteria_rulesets
                       (cluster_id, program_id, content_sha256, ruleset)
                   VALUES (%s, %s, %s, %s::jsonb)""",
                (cluster_id, program_id, digest, json.dumps(ruleset)),
            )
            connection.execute(
                """INSERT INTO mcp_saas.eligibility_criteria_ruleset_heads
                       (cluster_id, program_id, content_sha256, request_id)
                   VALUES (%s, %s, %s, %s)""",
                (cluster_id, program_id, digest, uuid.uuid4()),
            )

        first = criteria_rulesets.read_active_criteria_ruleset(
            connection, "cluster-one", program_id
        )
        second = criteria_rulesets.read_active_criteria_ruleset(
            connection, "cluster-two", program_id
        )

    assert first is not None and first["ruleset"] == documents["cluster-one"]
    assert second is not None and second["ruleset"] == documents["cluster-two"]
    assert first["content_sha256"] != second["content_sha256"]
