"""Provision scoped Data API roles and protected Podman credentials."""

import os
import re
import secrets
import subprocess
import tempfile
from pathlib import Path

from config_loader import load_config

ROOT = Path(__file__).resolve().parent
CONFIG = load_config(ROOT / "config.json")
ROLE = str(CONFIG.get("api_database_role", "mcp_saas_data_api"))
PUBLIC_CATALOG_ROLE = str(
    CONFIG.get("public_catalog_database_role", "mcp_saas_public_catalog_writer")
)
DB = str(CONFIG.get("database_name", "hackathon_demo"))
CONTAINER_RUNTIME = os.environ.get("MCP_CONTAINER_RUNTIME", "podman")
DB_CONTAINER = str(CONFIG.get("database_container", "mcp-dbtest-postgres"))
DB_HOST = str(CONFIG.get("database_host", DB_CONTAINER))
API_DSN_SECRET = str(CONFIG.get("api_dsn_secret", "mcp-saas-data-api-dsn"))
API_TOKEN_SECRET = str(CONFIG.get("api_token_secret", "mcp-saas-data-api-token"))
BASE_SECRETS = {API_DSN_SECRET, API_TOKEN_SECRET}
REGISTRY_TOKENS = {
    str(CONFIG.get("registry_read_token_secret", "mcp-saas-registry-read-token")): str(
        CONFIG.get("registry_read_token_local_file", "registry-read-token")
    ),
    str(
        CONFIG.get("registry_write_token_secret", "mcp-saas-registry-write-token")
    ): str(CONFIG.get("registry_write_token_local_file", "registry-write-token")),
    str(
        CONFIG.get(
            "template_registry_write_token_secret",
            "mcp-saas-template-registry-write-token",
        )
    ): str(
        CONFIG.get(
            "template_registry_write_token_local_file", "template-registry-write-token"
        )
    ),
    str(
        CONFIG.get(
            "tenant_registry_write_token_secret",
            "mcp-saas-tenant-registry-write-token",
        )
    ): str(
        CONFIG.get(
            "tenant_registry_write_token_local_file", "tenant-registry-write-token"
        )
    ),
    str(
        CONFIG.get(
            "route_telemetry_write_token_secret",
            "mcp-saas-route-telemetry-write-token",
        )
    ): str(
        CONFIG.get(
            "route_telemetry_write_token_local_file", "route-telemetry-write-token"
        )
    ),
    str(CONFIG.get("profile_read_token_secret", "mcp-saas-profile-read-token")): str(
        CONFIG.get("profile_read_token_local_file", "profile-read-token")
    ),
    str(
        CONFIG.get(
            "profile_fixture_read_token_secret", "mcp-saas-profile-fixture-read-token"
        )
    ): str(
        CONFIG.get(
            "profile_fixture_read_token_local_file", "profile-fixture-read-token"
        )
    ),
    str(CONFIG.get("profile_write_token_secret", "mcp-saas-profile-write-token")): str(
        CONFIG.get("profile_write_token_local_file", "profile-write-token")
    ),
}
CATALOG_READ_SECRET = str(
    CONFIG.get("catalog_read_token_secret", "mcp-saas-catalog-read-token")
)
PUBLIC_CATALOG_INGEST_DSN_SECRET = str(
    CONFIG.get("public_catalog_ingest_dsn_secret", "mcp-saas-public-catalog-ingest-dsn")
)
PUBLIC_CATALOG_WRITE_SECRET = str(
    CONFIG.get(
        "public_catalog_write_token_secret", "mcp-saas-public-catalog-write-token"
    )
)
REQUIREMENTS_PACKAGE_WRITE_SECRET = str(
    CONFIG.get(
        "requirements_package_write_token_secret",
        "mcp-saas-requirements-package-write-token",
    )
)
CRITERIA_RULESET_WRITE_SECRET = str(
    CONFIG.get(
        "criteria_ruleset_write_token_secret",
        "mcp-saas-criteria-ruleset-write-token",
    )
)


def validate_config() -> None:
    """Reject SQL identifiers and container/host values unsafe for interpolation."""
    for value, label in (
        (ROLE, "API database role"),
        (PUBLIC_CATALOG_ROLE, "public catalog database role"),
        (DB, "database name"),
    ):
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) is None:
            raise ValueError(f"Invalid {label} in Data API configuration.")
    for value, label in (
        (DB_CONTAINER, "database container"),
        (DB_HOST, "database host"),
    ):
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", value) is None:
            raise ValueError(f"Invalid {label} in Data API configuration.")


def container(*args, **kwargs):
    """Run the container runtime with the given arguments and capture its output."""
    return subprocess.run(
        [CONTAINER_RUNTIME, *args], capture_output=True, text=True, **kwargs
    )


def secret_directory() -> Path:
    """Return the external host directory used for Docker Compose file secrets."""
    directory = Path(
        os.environ.get(
            "MCP_SAAS_DATA_API_SECRET_DIR",
            Path.home() / ".config/mcp-saas/secrets",
        )
    )
    directory.mkdir(mode=0o750, parents=True, exist_ok=True)
    if os.geteuid() == 0:
        os.chown(directory, 0, 10001)
        directory.chmod(0o750)
    return directory


def create_secret(name, value):
    """Create a container secret, refusing to overwrite a different value."""
    if CONTAINER_RUNTIME == "docker":
        directory = secret_directory()
        path = directory / name
        if path.exists():
            if path.read_text(encoding="utf-8").strip() != value:
                raise RuntimeError(
                    "a Docker file secret conflicts with the stored capability"
                )
            return
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(path, flags, 0o440 if os.geteuid() == 0 else 0o600)
        try:
            if os.geteuid() == 0:
                os.fchown(fd, 0, 10001)
            with os.fdopen(fd, "w", encoding="utf-8") as secret_file:
                secret_file.write(value)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return
    fd, path = tempfile.mkstemp(prefix="mcp-saas-secret-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(value)
        result = container("secret", "create", name, path)
        if result.returncode:
            raise RuntimeError("secret write failed")
    finally:
        Path(path).unlink(missing_ok=True)


def grant_api_scoped_permissions() -> None:
    """Grant scoped registry, catalog, and privacy-safe telemetry operations."""
    result = subprocess.run(
        [
            CONTAINER_RUNTIME,
            "exec",
            "-i",
            DB_CONTAINER,
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-d",
            DB,
        ],
        input=(
            "CREATE TABLE IF NOT EXISTS mcp_saas.eligibility_requirements_packages ("
            "package_id UUID PRIMARY KEY, "
            "tenant_id TEXT NOT NULL CHECK (tenant_id <> ''), "
            "definition JSONB NOT NULL CHECK ("
            "definition->>'contract_version' = 'eligibility-requirements-package-v1' "
            "AND definition->>'package_id' = package_id::text "
            "AND definition->>'tenant_id' = tenant_id "
            "AND definition->>'official_eligibility_determination' = 'false' "
            "AND definition->>'official_application_submitted' = 'false' "
            "AND NOT (definition ? 'profile_id') "
            "AND NOT (definition ? 'profile_payload')), "
            "content_sha256 CHAR(64) NOT NULL, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now());\n"
            "CREATE TABLE IF NOT EXISTS "
            "mcp_saas.eligibility_requirements_package_revisions ("
            "package_id UUID NOT NULL REFERENCES "
            "mcp_saas.eligibility_requirements_packages(package_id) "
            "ON DELETE CASCADE, "
            "revision INTEGER NOT NULL CHECK (revision >= 2), "
            "definition JSONB NOT NULL CHECK ("
            "definition->>'package_id' = package_id::text "
            "AND definition->>'official_eligibility_determination' = 'false' "
            "AND definition->>'official_application_submitted' = 'false'), "
            "details_sha256 CHAR(64) NOT NULL, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
            "PRIMARY KEY (package_id, revision));\n"
            f"REVOKE ALL ON mcp_saas.pattern_catalog_bindings, "
            f"mcp_saas.pattern_templates, mcp_saas.use_case_registry, "
            f"mcp_saas.tenant_registry, mcp_saas.use_case_tenants, "
            f"mcp_saas.core_route_metadata, mcp_saas.core_route_events, "
            f"mcp_saas.pattern_template_packages, mcp_saas.synthetic_profiles, "
            f"mcp_saas.pattern_template_lifecycle_events, "
            f"mcp_saas.tenant_lifecycle_events, "
            f"mcp_saas.eligibility_requirements_packages, "
            f"mcp_saas.eligibility_requirements_package_revisions, "
            f"mcp_saas.source_policy_manifests, "
            f"mcp_saas.program_candidate_records, "
            f"mcp_saas.program_candidate_review_decisions, "
            f"mcp_saas.program_candidate_publications, "
            f"mcp_saas.program_candidate_publication_events, "
            f"mcp_saas.source_policy_review_decisions, "
            f"mcp_saas.source_policy_activation_events, "
            f"mcp_saas.tenant_registry_jurisdiction_revisions, "
            f"mcp_saas.profile_attribute_values, mcp_saas.profile_relationships, "
            f"mcp_saas.profile_attribute_history, "
            f"mcp_saas.profile_access_references, "
            f"mcp_saas.profile_resume_claims, "
            f"mcp_saas.profile_saved_interests, "
            f"mcp_saas.profile_saved_interest_history, "
            f"mcp_saas.schema_tag_assignments, "
            f"mcp_saas.evaluation_fixture_members FROM {ROLE};\n"
            f"GRANT SELECT ON mcp_saas.pattern_catalog_bindings, "
            f"mcp_saas.pattern_templates, mcp_saas.use_case_registry, "
            f"mcp_saas.tenant_registry, mcp_saas.use_case_tenants, "
            f"mcp_saas.core_route_metadata, mcp_saas.pattern_template_packages, "
            f"mcp_saas.core_route_events "
            f"TO {ROLE};\n"
            f"GRANT SELECT, INSERT ON "
            f"mcp_saas.pattern_template_lifecycle_events TO {ROLE};\n"
            f"GRANT SELECT, INSERT ON "
            f"mcp_saas.tenant_lifecycle_events TO {ROLE};\n"
            f"GRANT INSERT ON mcp_saas.tenant_registry_jurisdiction_revisions "
            f"TO {ROLE};\n"
            f"GRANT SELECT (tenant_id, pattern_id, pattern_version, "
            f"revised_spec_sha256) "
            f"ON mcp_saas.tenant_registry_jurisdiction_revisions TO {ROLE};\n"
            f"GRANT SELECT ON mcp_saas.synthetic_profiles, "
            f"mcp_saas.profile_attribute_values, mcp_saas.profile_relationships, "
            f"mcp_saas.profile_attribute_history, mcp_saas.schema_registry, "
            f"mcp_saas.profile_access_references "
            f"TO {ROLE};\n"
            f"GRANT INSERT, UPDATE ON mcp_saas.schema_registry TO {ROLE};\n"
            f"GRANT SELECT, INSERT ON mcp_saas.profile_resume_claims "
            f"TO {ROLE};\n"
            f"GRANT UPDATE (consumed_at, revoked_at) "
            f"ON mcp_saas.profile_resume_claims TO {ROLE};\n"
            f"GRANT SELECT, INSERT, UPDATE ON "
            f"mcp_saas.profile_saved_interests TO {ROLE};\n"
            f"GRANT SELECT, INSERT ON "
            f"mcp_saas.profile_saved_interest_history TO {ROLE};\n"
            f"GRANT SELECT ON mcp_saas.evaluation_fixture_members "
            f"TO {ROLE};\n"
            f"GRANT SELECT ON mcp_saas.schema_tag_assignments "
            f"TO {ROLE};\n"
            f"GRANT SELECT ON mcp_saas.public_data_sources, mcp_saas.public_datasets, "
            f"mcp_saas.public_dataset_records, "
            f"mcp_saas.active_public_dataset_records, "
            f"mcp_saas.public_dataset_ingestion_runs "
            f"TO {ROLE};\n"
            f"GRANT SELECT, INSERT ON "
            f"mcp_saas.eligibility_requirements_packages TO {ROLE};\n"
            f"GRANT SELECT, INSERT ON "
            f"mcp_saas.eligibility_requirements_package_revisions TO {ROLE};\n"
            f"GRANT SELECT, INSERT ON "
            f"mcp_saas.source_policy_manifests TO {ROLE};\n"
            f"GRANT SELECT, INSERT ON "
            f"mcp_saas.program_candidate_records TO {ROLE};\n"
            f"GRANT SELECT, INSERT ON "
            f"mcp_saas.program_candidate_registration_events TO {ROLE};\n"
            f"GRANT SELECT, INSERT ON "
            f"mcp_saas.program_candidate_review_decisions TO {ROLE};\n"
            f"GRANT SELECT, INSERT ON mcp_saas.program_candidate_publications, "
            f"mcp_saas.program_candidate_publication_events TO {ROLE};\n"
            f"GRANT SELECT, INSERT ON "
            f"mcp_saas.source_policy_review_decisions TO {ROLE};\n"
            f"GRANT SELECT, INSERT ON "
            f"mcp_saas.source_policy_activation_events TO {ROLE};\n"
            f"GRANT INSERT ON mcp_saas.pattern_template_packages, "
            f"mcp_saas.core_route_metadata TO {ROLE};\n"
            f"GRANT INSERT ON mcp_saas.core_route_events TO {ROLE};\n"
            f"GRANT INSERT ON mcp_saas.synthetic_profiles, "
            f"mcp_saas.profile_attribute_values, "
            f"mcp_saas.profile_attribute_history TO {ROLE};\n"
            f"GRANT INSERT ON mcp_saas.profile_access_references TO {ROLE};\n"
            f"GRANT INSERT ON mcp_saas.schema_tag_assignments, "
            f"mcp_saas.evaluation_fixture_members TO {ROLE};\n"
            f"GRANT UPDATE (schema_registry_id, revision, updated_at) "
            f"ON mcp_saas.synthetic_profiles TO {ROLE};\n"
            f"GRANT UPDATE (dimension_id, value_type, value, source_id, "
            f"schema_version, provenance, observed_at) "
            f"ON mcp_saas.profile_attribute_values TO {ROLE};\n"
            f"GRANT INSERT (manifest_id, tenant_id, pattern_id, pattern_version, "
            f"mcp_endpoint, lifecycle_state, git_revision, tenant_spec_sha256, "
            f"tenant_context) ON mcp_saas.tenant_registry TO {ROLE};\n"
            f"GRANT INSERT ON mcp_saas.pattern_catalog_bindings TO {ROLE};\n"
            f"GRANT UPDATE (binding_context) "
            f"ON mcp_saas.pattern_catalog_bindings TO {ROLE};\n"
            f"GRANT UPDATE (lifecycle_state, updated_at) "
            f"ON mcp_saas.pattern_template_packages TO {ROLE};\n"
            f"GRANT UPDATE (lifecycle_state, updated_at) "
            f"ON mcp_saas.core_route_metadata TO {ROLE};\n"
            f"GRANT UPDATE (mcp_endpoint, updated_at) "
            f"ON mcp_saas.tenant_registry TO {ROLE};\n"
            f"GRANT UPDATE (tenant_spec_sha256, tenant_context, "
            f"lifecycle_state, updated_at) "
            f"ON mcp_saas.tenant_registry TO {ROLE};\n"
        ),
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise SystemExit("Data API scope permission could not be applied")


def ensure_public_catalog_ingest_database_secret(found: set[str]) -> bool:
    """Mount a database identity limited to source catalog writes and run audit."""
    directory = Path(
        os.environ.get(
            "MCP_SAAS_DATA_API_TOKEN_DIR",
            Path.home() / ".config/mcp-saas",
        )
    )
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    dsn_file = directory / str(
        CONFIG.get("public_catalog_ingest_dsn_local_file", "public-catalog-ingest-dsn")
    )
    if PUBLIC_CATALOG_INGEST_DSN_SECRET in found:
        return False
    if dsn_file.is_file():
        dsn_file.chmod(0o600)
        create_secret(
            PUBLIC_CATALOG_INGEST_DSN_SECRET,
            dsn_file.read_text(encoding="utf-8").strip(),
        )
        return True

    role = PUBLIC_CATALOG_ROLE
    password = secrets.token_hex(32)
    dsn = f"postgresql://{role}:{password}@{DB_HOST}:5432/{DB}"
    sql = f"""
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN
    CREATE ROLE {role} LOGIN;
  END IF;
END $$;
ALTER ROLE {role} WITH LOGIN PASSWORD '{password}' NOSUPERUSER
  NOCREATEDB NOCREATEROLE NOINHERIT CONNECTION LIMIT 2;
GRANT CONNECT ON DATABASE {DB} TO {role};
GRANT USAGE ON SCHEMA mcp_saas TO {role};
GRANT SELECT ON mcp_saas.public_data_sources, mcp_saas.public_datasets,
  mcp_saas.public_dataset_records, mcp_saas.public_dataset_ingestion_runs,
  mcp_saas.schema_registry TO {role};
DO $$
BEGIN
  IF to_regclass('mcp_saas.public_catalog_registration_events') IS NOT NULL THEN
    EXECUTE 'GRANT SELECT, INSERT ON mcp_saas.public_catalog_registration_events
      TO {role}';
  END IF;
  IF to_regclass('mcp_saas.program_candidate_publications') IS NOT NULL THEN
    EXECUTE 'GRANT SELECT ON mcp_saas.program_candidate_publications TO {role}';
  END IF;
  IF to_regclass('mcp_saas.eligibility_criteria_rulesets') IS NOT NULL THEN
    EXECUTE format(
      'GRANT SELECT, INSERT ON mcp_saas.eligibility_criteria_rulesets TO %I',
      '{role}'
    );
    EXECUTE format(
      'GRANT SELECT, INSERT, UPDATE (content_sha256, request_id, updated_at) ON
       mcp_saas.eligibility_criteria_ruleset_heads TO %I',
      '{role}'
    );
    EXECUTE format(
      'GRANT SELECT, INSERT ON mcp_saas.eligibility_criteria_ruleset_events TO %I',
      '{role}'
    );
  END IF;
END $$;
GRANT INSERT ON mcp_saas.public_data_sources, mcp_saas.public_datasets,
  mcp_saas.public_dataset_records,
  mcp_saas.public_dataset_ingestion_runs TO {role};
GRANT UPDATE (source_name, source_url, access_status, source_context, updated_at)
  ON mcp_saas.public_data_sources TO {role};
GRANT UPDATE (title, landing_page, schema_version, namespace_ids, metadata, updated_at)
  ON mcp_saas.public_datasets TO {role};
GRANT UPDATE (record_type, canonical_attributes, semantic_context, source_metadata,
  source_url, content_hash, schema_version, observed_at)
  ON mcp_saas.public_dataset_records TO {role};
GRANT UPDATE (inserted_count, updated_count, duplicate_count, status, details,
  completed_at)
  ON mcp_saas.public_dataset_ingestion_runs TO {role};
"""
    result = subprocess.run(
        [
            CONTAINER_RUNTIME,
            "exec",
            "-i",
            DB_CONTAINER,
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-d",
            DB,
        ],
        input=sql,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise SystemExit("Public catalog writer database role setup failed")
    fd = os.open(dsn_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as dsn_stream:
        dsn_stream.write(dsn)
    try:
        create_secret(PUBLIC_CATALOG_INGEST_DSN_SECRET, dsn)
    except Exception:
        dsn_file.unlink(missing_ok=True)
        raise SystemExit("Public catalog writer secret creation failed")
    return True


def grant_public_catalog_ingest_permissions() -> None:
    """Reassert the writer's source, dataset, and record allowlisted operations."""
    role = PUBLIC_CATALOG_ROLE
    sql = f"""
GRANT CONNECT ON DATABASE {DB} TO {role};
GRANT USAGE ON SCHEMA mcp_saas TO {role};
GRANT SELECT ON mcp_saas.public_data_sources, mcp_saas.public_datasets,
  mcp_saas.public_dataset_records, mcp_saas.public_dataset_ingestion_runs,
  mcp_saas.schema_registry TO {role};
DO $$
BEGIN
  IF to_regclass('mcp_saas.public_catalog_registration_events') IS NOT NULL THEN
    EXECUTE 'GRANT SELECT, INSERT ON mcp_saas.public_catalog_registration_events
      TO {role}';
  END IF;
  IF to_regclass('mcp_saas.program_candidate_publications') IS NOT NULL THEN
    EXECUTE 'GRANT SELECT ON mcp_saas.program_candidate_publications TO {role}';
  END IF;
  IF to_regclass('mcp_saas.eligibility_criteria_rulesets') IS NOT NULL THEN
    EXECUTE format(
      'GRANT SELECT, INSERT ON mcp_saas.eligibility_criteria_rulesets TO %I',
      '{role}'
    );
    EXECUTE format(
      'GRANT SELECT, INSERT, UPDATE (content_sha256, request_id, updated_at) ON
       mcp_saas.eligibility_criteria_ruleset_heads TO %I',
      '{role}'
    );
    EXECUTE format(
      'GRANT SELECT, INSERT ON mcp_saas.eligibility_criteria_ruleset_events TO %I',
      '{role}'
    );
  END IF;
END $$;
GRANT INSERT ON mcp_saas.public_data_sources, mcp_saas.public_datasets,
  mcp_saas.public_dataset_records, mcp_saas.public_dataset_ingestion_runs TO {role};
GRANT UPDATE (source_name, source_url, access_status, source_context, updated_at)
  ON mcp_saas.public_data_sources TO {role};
GRANT UPDATE (title, landing_page, schema_version, namespace_ids, metadata, updated_at)
  ON mcp_saas.public_datasets TO {role};
GRANT UPDATE (record_type, canonical_attributes, semantic_context, source_metadata,
  source_url, content_hash, schema_version, observed_at)
  ON mcp_saas.public_dataset_records TO {role};
GRANT UPDATE (inserted_count, updated_count, duplicate_count, status, details,
  completed_at)
  ON mcp_saas.public_dataset_ingestion_runs TO {role};
"""
    result = subprocess.run(
        [
            CONTAINER_RUNTIME,
            "exec",
            "-i",
            DB_CONTAINER,
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-d",
            DB,
        ],
        input=sql,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise SystemExit("Public catalog writer permissions could not be applied")


def write_pending_token(name: str, token: str) -> None:
    """Store a pending token file next to the current one."""
    write_protected_token(f"{name}.pending", token, preserve=True)


def write_protected_token(name: str, token: str, *, preserve: bool = False) -> Path:
    """Keep one bearer value in an owner-only file without printing or replacing it."""
    directory = Path(
        os.environ.get(
            "MCP_SAAS_DATA_API_TOKEN_DIR",
            Path.home() / ".config/mcp-saas",
        )
    )
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    path = directory / name
    if path.exists():
        if path.read_text(encoding="utf-8").strip() != token:
            raise SystemExit(
                "A protected token file conflicts with the stored capability."
            )
        path.chmod(0o600)
        return path
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as token_file:
        token_file.write(token)
    if preserve:
        path.chmod(0o600)
    return path


def ensure_capability_token(
    *, secret_name: str, local_name: str, handoff_name: str, found: set[str]
) -> bool:
    """Create a missing API capability and retain a protected VM runtime copy."""
    directory = Path(
        os.environ.get(
            "MCP_SAAS_DATA_API_TOKEN_DIR",
            Path.home() / ".config/mcp-saas",
        )
    )
    local_path = directory / local_name
    if secret_name in found:
        if not local_path.is_file():
            raise SystemExit(
                f"{secret_name} exists but its protected VM token file is missing; "
                "restore the existing token without rotating it."
            )
        return False
    token = (
        local_path.read_text(encoding="utf-8").strip()
        if local_path.is_file()
        else secrets.token_urlsafe(48)
    )
    if not token:
        raise SystemExit("A protected VM token file is empty.")
    write_protected_token(local_name, token)
    create_secret(secret_name, token)
    write_pending_token(handoff_name, token)
    return True


def main():
    """Parse command-line arguments and run this tool."""
    validate_config()
    if CONTAINER_RUNTIME == "docker":
        found = {path.name for path in secret_directory().iterdir() if path.is_file()}
    elif CONTAINER_RUNTIME == "podman":
        found = set(
            container("secret", "ls", "--format", "{{.Name}}").stdout.splitlines()
        )
    else:
        raise SystemExit("MCP_CONTAINER_RUNTIME must be podman or docker")
    base_names = set(BASE_SECRETS)
    if found & base_names and not base_names <= found:
        raise SystemExit("partial base API secret set exists; inspect before retrying")

    if not base_names <= found:
        password = secrets.token_hex(32)
        api_token = secrets.token_urlsafe(48)
        dsn = f"postgresql://{ROLE}:{password}@{DB_HOST}:5432/{DB}"
        migration = (ROOT / "schema.sql").read_text(encoding="utf-8")
        sql = f"""
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{ROLE}') THEN
    CREATE ROLE {ROLE} LOGIN;
  END IF;
END $$;
ALTER ROLE {ROLE} WITH LOGIN PASSWORD '{password}' NOSUPERUSER
  NOCREATEDB NOCREATEROLE NOINHERIT CONNECTION LIMIT 4;
{migration}
GRANT CONNECT ON DATABASE {DB} TO {ROLE};
GRANT USAGE ON SCHEMA mcp_saas TO {ROLE};
REVOKE ALL ON mcp_saas.public_dataset_records FROM {ROLE};
REVOKE ALL ON mcp_saas.vm_data_api_probe_records FROM {ROLE};
GRANT SELECT ON mcp_saas.public_dataset_records TO {ROLE};
GRANT SELECT, INSERT ON mcp_saas.vm_data_api_probe_records TO {ROLE};
"""
        result = subprocess.run(
            [
                CONTAINER_RUNTIME,
                "exec",
                "-i",
                DB_CONTAINER,
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "postgres",
                "-d",
                DB,
            ],
            input=sql,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise SystemExit(
                "DB role/table setup failed; inspect logs without revealing "
                "secret values"
            )
        try:
            create_secret(API_DSN_SECRET, dsn)
            create_secret(API_TOKEN_SECRET, api_token)
        except Exception:
            subprocess.run(
                [CONTAINER_RUNTIME, "secret", "rm", API_DSN_SECRET],
                capture_output=True,
            )
            raise SystemExit(
                "Podman secret creation failed; generated credentials were not printed"
            )
        write_pending_token(API_TOKEN_SECRET, api_token)

    grant_api_scoped_permissions()
    created_registry_tokens = sum(
        ensure_capability_token(
            secret_name=secret_name,
            local_name=local_name,
            handoff_name=secret_name,
            found=found,
        )
        for secret_name, local_name in REGISTRY_TOKENS.items()
    )
    created_registry_tokens += ensure_capability_token(
        secret_name=CATALOG_READ_SECRET,
        local_name=str(
            CONFIG.get("catalog_read_token_local_file", "catalog-read-token")
        ),
        handoff_name=CATALOG_READ_SECRET,
        found=found,
    )
    created_registry_tokens += ensure_capability_token(
        secret_name=REQUIREMENTS_PACKAGE_WRITE_SECRET,
        local_name=str(
            CONFIG.get(
                "requirements_package_write_token_local_file",
                "requirements-package-write-token",
            )
        ),
        handoff_name=REQUIREMENTS_PACKAGE_WRITE_SECRET,
        found=found,
    )
    created_registry_tokens += ensure_capability_token(
        secret_name=PUBLIC_CATALOG_WRITE_SECRET,
        local_name=str(
            CONFIG.get(
                "public_catalog_write_token_local_file", "public-catalog-write-token"
            )
        ),
        handoff_name=PUBLIC_CATALOG_WRITE_SECRET,
        found=found,
    )
    created_registry_tokens += ensure_capability_token(
        secret_name=CRITERIA_RULESET_WRITE_SECRET,
        local_name=str(
            CONFIG.get(
                "criteria_ruleset_write_token_local_file",
                "criteria-ruleset-write-token",
            )
        ),
        handoff_name=CRITERIA_RULESET_WRITE_SECRET,
        found=found,
    )
    created_ingest_dsn = ensure_public_catalog_ingest_database_secret(found)
    grant_public_catalog_ingest_permissions()
    print(
        "Data API registry permissions are applied; existing secrets were preserved. "
        f"Created {created_registry_tokens} scoped capability token(s) and "
        f"{'a' if created_ingest_dsn else 'no'} dedicated public catalog writer "
        "DB secret."
    )


if __name__ == "__main__":
    main()
