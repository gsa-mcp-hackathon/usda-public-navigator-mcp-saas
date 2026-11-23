"""Project reviewed schemas into a Podman-hosted PostgreSQL registry only."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from pydantic import ValidationError

from example_server.registry import SchemaDefinition, SchemaProjectionManifest


def _sql_literal(value: str) -> str:
    """Quote one trusted string value for the generated psql transaction."""
    return "'" + value.replace("'", "''") + "'"


def build_schema_projection_sql(
    manifest: SchemaProjectionManifest,
    schemas: list[SchemaDefinition],
    git_revision: str,
) -> str:
    """Build an immutable, transactional write limited to schema_registry."""
    if re.fullmatch(r"[0-9a-f]{7,40}", git_revision) is None:
        raise ValueError("A reviewed Git revision is required for reconciliation.")
    expected = {
        (reference.registry_id, reference.schema_version): reference.lifecycle_state
        for reference in manifest.schemas
    }
    actual = {(schema.registry_id, schema.schema_version) for schema in schemas}
    if actual != set(expected):
        raise ValueError("Loaded schema definitions do not match the manifest.")

    statements = ["BEGIN;"]
    for schema in schemas:
        registry_id = _sql_literal(schema.registry_id)
        schema_version = _sql_literal(schema.schema_version)
        definition = _sql_literal(
            json.dumps(schema.model_dump(mode="json"), separators=(",", ":"))
        )
        lifecycle_state = _sql_literal(
            expected[(schema.registry_id, schema.schema_version)]
        )
        same_meaning = (
            "jsonb_set(definition, '{tags}', "
            "COALESCE(definition->'tags', '{}'::jsonb), true) IS DISTINCT FROM "
            f"jsonb_set({definition}::jsonb, '{{tags}}', "
            f"COALESCE(({definition}::jsonb)->'tags', '{{}}'::jsonb), true)"
        )
        statements.extend(
            (
                "DO $$ BEGIN IF EXISTS (SELECT 1 FROM mcp_saas.schema_registry "
                f"WHERE registry_id = {registry_id} "
                f"AND schema_version = {schema_version} "
                f"AND {same_meaning}) THEN "
                "RAISE EXCEPTION 'immutable schema definition conflict'; "
                "END IF; END $$;",
                "INSERT INTO mcp_saas.schema_registry "
                "(manifest_id, registry_id, schema_version, lifecycle_state, "
                "definition, git_revision) "
                f"VALUES ({_sql_literal(manifest.manifest_id)}, {registry_id}, "
                f"{schema_version}, {lifecycle_state}, {definition}::jsonb, "
                f"{_sql_literal(git_revision)}) "
                "ON CONFLICT (registry_id, schema_version) DO UPDATE SET "
                "manifest_id = EXCLUDED.manifest_id, "
                "lifecycle_state = EXCLUDED.lifecycle_state, "
                "git_revision = EXCLUDED.git_revision, updated_at = now() "
                "WHERE jsonb_set(mcp_saas.schema_registry.definition, '{tags}', "
                "COALESCE(mcp_saas.schema_registry.definition->'tags', "
                "'{}'::jsonb), true) "
                "= jsonb_set(EXCLUDED.definition, '{tags}', "
                "COALESCE(EXCLUDED.definition->'tags', '{}'::jsonb), true) "
                "RETURNING registry_id, schema_version, lifecycle_state, git_revision;",
            )
        )

    identities = ", ".join(
        "("
        + _sql_literal(schema.registry_id)
        + ", "
        + _sql_literal(schema.schema_version)
        + ")"
        for schema in schemas
    )
    statements.append(
        "UPDATE mcp_saas.schema_registry SET lifecycle_state = 'inactive', "
        f"git_revision = {_sql_literal(git_revision)}, updated_at = now() "
        f"WHERE manifest_id = {_sql_literal(manifest.manifest_id)} "
        f"AND (registry_id, schema_version) NOT IN ({identities});"
    )
    statements.append("COMMIT;")
    return "\n".join(statements) + "\n"


def main() -> None:
    """Apply the schema-only projection through the selected Podman DB container."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("configs/registry/vs8-schema-projection.v4.json"),
    )
    parser.add_argument("--container", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--git-revision", required=True)
    args = parser.parse_args()

    try:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", args.container) is None:
            raise ValueError("Invalid Podman container name.")
        manifest = SchemaProjectionManifest.model_validate_json(
            args.manifest.read_text(encoding="utf-8")
        )
        repository_root = Path(__file__).resolve().parents[1]
        schemas = manifest.load_schemas(repository_root)
        sql = build_schema_projection_sql(manifest, schemas, args.git_revision)
        result = subprocess.run(
            [
                "podman",
                "exec",
                "-i",
                args.container,
                "psql",
                "-X",
                "-q",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "postgres",
                "-d",
                args.database,
                "-f",
                "-",
            ],
            input=sql,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("Podman schema projection failed.")
    except (OSError, ValueError, ValidationError, RuntimeError) as exc:
        print(
            f"Schema-only Podman reconciliation failed ({type(exc).__name__}); "
            "review the manifest, target container, and database.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    if result.stdout.strip():
        print(result.stdout.strip())
    print(
        f"Schema-only Podman reconciliation succeeded: {len(schemas)} schemas; "
        f"revision {args.git_revision}."
    )


if __name__ == "__main__":
    main()
