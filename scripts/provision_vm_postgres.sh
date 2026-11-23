#!/usr/bin/env bash
set -euo pipefail

# Apply additive demo migrations and create scoped runtime and operator roles.
# Generated connection details stay in a mode-0600 file outside the repository.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_CONTAINER="${MCP_POSTGRES_CONTAINER:-mcp-dbtest-postgres}"
DB_NAME="${MCP_POSTGRES_DATABASE:-hackathon_demo}"
DB_NETWORK="${MCP_DATABASE_NETWORK:-mcp-dbtest-net}"
MCP_RUNTIME_UID="${MCP_RUNTIME_UID:-10001}"
SECRET_DIR="${HOME}/.config/mcp-saas"
DATABASE_URL_FILE="${SECRET_DIR}/database-url"
INGEST_URL_FILE="${SECRET_DIR}/public-ingest-url"
REGISTRY_URL_FILE="${SECRET_DIR}/registry-reconciler-url"

[[ "${DB_NAME}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || {
  echo "Database name must be a simple SQL identifier." >&2
  exit 1
}

podman container exists "${DB_CONTAINER}" || {
  echo "Required PostgreSQL container is not running: ${DB_CONTAINER}" >&2
  exit 1
}
podman network exists "${DB_NETWORK}" || {
  echo "Required private database network is not available: ${DB_NETWORK}" >&2
  exit 1
}
podman exec "${DB_CONTAINER}" pg_isready -U postgres -d "${DB_NAME}" >/dev/null

podman exec -i "${DB_CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d "${DB_NAME}" \
  < "${ROOT}/db/migrations/001_persistence_foundation.sql"
podman exec -i "${DB_CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d "${DB_NAME}" \
  < "${ROOT}/db/seeds/demo.sql"
podman exec -i "${DB_CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d "${DB_NAME}" \
  < "${ROOT}/db/migrations/002_shared_public_datasets.sql"
podman exec -i "${DB_CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d "${DB_NAME}" \
  < "${ROOT}/db/seeds/public-datasets.sql"
podman exec -i "${DB_CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d "${DB_NAME}" \
  < "${ROOT}/db/migrations/003_platform_registry.sql"
podman exec -i "${DB_CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d "${DB_NAME}" \
  < "${ROOT}/db/migrations/004_cluster_scoped_tenant_registrations.sql"
podman exec -i "${DB_CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d "${DB_NAME}" \
  < "${ROOT}/db/migrations/005_schema_registry.sql"
podman exec -i "${DB_CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d "${DB_NAME}" \
  < "${ROOT}/db/migrations/006_versioned_pattern_catalog_bindings.sql"
podman exec -i "${DB_CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d "${DB_NAME}" \
  < "${ROOT}/db/migrations/007_core_route_metadata.sql"
podman exec -i "${DB_CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d "${DB_NAME}" \
  < "${ROOT}/db/migrations/008_pattern_template_packages.sql"
podman exec -i "${DB_CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d "${DB_NAME}" \
  < "${ROOT}/db/migrations/009_eligibility_public_sources.sql"

install -d -m 700 "${SECRET_DIR}"
if [[ ! -s "${DATABASE_URL_FILE}" ]]; then
  password="$(openssl rand -hex 32)"
  role_sql="$(mktemp "${SECRET_DIR}/role.XXXXXX.sql")"
  trap 'rm -f "${role_sql}"' EXIT
  chmod 600 "${role_sql}"
  cat > "${role_sql}" <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mcp_catalog_reader') THEN
    CREATE ROLE mcp_catalog_reader LOGIN;
  END IF;
END
\$\$;
ALTER ROLE mcp_catalog_reader WITH LOGIN PASSWORD '${password}'
  NOSUPERUSER NOCREATEDB NOCREATEROLE;
GRANT CONNECT ON DATABASE ${DB_NAME} TO mcp_catalog_reader;
GRANT USAGE ON SCHEMA mcp_saas TO mcp_catalog_reader;
GRANT SELECT ON mcp_saas.program_catalog, mcp_saas.synthetic_profiles,
  mcp_saas.profile_attribute_values, mcp_saas.profile_relationships,
  mcp_saas.public_data_sources, mcp_saas.public_datasets,
  mcp_saas.public_dataset_records, mcp_saas.pattern_catalog_bindings,
  mcp_saas.public_dataset_ingestion_runs, mcp_saas.schema_registry
  TO mcp_catalog_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA mcp_saas
  GRANT SELECT ON TABLES TO mcp_catalog_reader;
SQL
  podman exec -i "${DB_CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d "${DB_NAME}" \
    < "${role_sql}"
  rm -f "${role_sql}"
  trap - EXIT
  printf 'postgresql://mcp_catalog_reader:%s@postgres:5432/%s?connect_timeout=3\n' \
    "${password}" "${DB_NAME}" > "${DATABASE_URL_FILE}"
  chmod 600 "${DATABASE_URL_FILE}"
  unset password
fi
podman unshare chown "${MCP_RUNTIME_UID}:${MCP_RUNTIME_UID}" "${DATABASE_URL_FILE}"
podman unshare chmod 600 "${DATABASE_URL_FILE}"

if [[ ! -s "${REGISTRY_URL_FILE}" ]]; then
  registry_password="$(openssl rand -hex 32)"
  registry_sql="$(mktemp "${SECRET_DIR}/registry-role.XXXXXX.sql")"
  trap 'rm -f "${registry_sql}"' EXIT
  chmod 600 "${registry_sql}"
  cat > "${registry_sql}" <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mcp_registry_reconciler') THEN
    CREATE ROLE mcp_registry_reconciler LOGIN;
  END IF;
END
\$\$;
ALTER ROLE mcp_registry_reconciler WITH LOGIN PASSWORD '${registry_password}'
  NOSUPERUSER NOCREATEDB NOCREATEROLE;
GRANT CONNECT ON DATABASE ${DB_NAME} TO mcp_registry_reconciler;
GRANT USAGE ON SCHEMA mcp_saas TO mcp_registry_reconciler;
GRANT SELECT, INSERT, UPDATE ON mcp_saas.pattern_templates,
  mcp_saas.use_case_registry, mcp_saas.tenant_registry,
  mcp_saas.use_case_tenants, mcp_saas.schema_registry TO mcp_registry_reconciler;
GRANT DELETE ON mcp_saas.use_case_tenants TO mcp_registry_reconciler;
SQL
  podman exec -i "${DB_CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d "${DB_NAME}" < "${registry_sql}"
  rm -f "${registry_sql}"
  trap - EXIT
  printf 'postgresql://mcp_registry_reconciler:%s@postgres:5432/%s?connect_timeout=3' "${registry_password}" "${DB_NAME}" > "${REGISTRY_URL_FILE}"
  chmod 600 "${REGISTRY_URL_FILE}"
  unset registry_password
fi
podman unshare chown "${MCP_RUNTIME_UID}:${MCP_RUNTIME_UID}" "${REGISTRY_URL_FILE}"
podman unshare chmod 600 "${REGISTRY_URL_FILE}"

if [[ ! -s "${INGEST_URL_FILE}" ]]; then
  ingest_password="$(openssl rand -hex 32)"
  ingest_sql="$(mktemp "${SECRET_DIR}/ingest-role.XXXXXX.sql")"
  trap 'rm -f "${ingest_sql}"' EXIT
  chmod 600 "${ingest_sql}"
  cat > "${ingest_sql}" <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mcp_public_catalog_ingest') THEN
    CREATE ROLE mcp_public_catalog_ingest LOGIN;
  END IF;
END
\$\$;
ALTER ROLE mcp_public_catalog_ingest WITH LOGIN PASSWORD '${ingest_password}'
  NOSUPERUSER NOCREATEDB NOCREATEROLE;
GRANT CONNECT ON DATABASE ${DB_NAME} TO mcp_public_catalog_ingest;
GRANT USAGE ON SCHEMA mcp_saas TO mcp_public_catalog_ingest;
GRANT SELECT, INSERT ON mcp_saas.public_data_sources, mcp_saas.public_datasets,
  mcp_saas.public_dataset_records, mcp_saas.pattern_catalog_bindings,
  mcp_saas.public_dataset_ingestion_runs TO mcp_public_catalog_ingest;
GRANT UPDATE (source_name, source_url, access_status, updated_at)
  ON mcp_saas.public_data_sources TO mcp_public_catalog_ingest;
GRANT UPDATE (title, landing_page, schema_version, namespace_ids, metadata, updated_at)
  ON mcp_saas.public_datasets TO mcp_public_catalog_ingest;
GRANT UPDATE (record_type, canonical_attributes, semantic_context, source_metadata,
  source_url, content_hash, schema_version, observed_at)
  ON mcp_saas.public_dataset_records TO mcp_public_catalog_ingest;
GRANT UPDATE (binding_context) ON mcp_saas.pattern_catalog_bindings TO mcp_public_catalog_ingest;
GRANT UPDATE (fetched_count, inserted_count, updated_count, duplicate_count, status, details, completed_at)
  ON mcp_saas.public_dataset_ingestion_runs TO mcp_public_catalog_ingest;
SQL
  podman exec -i "${DB_CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d "${DB_NAME}" \
    < "${ingest_sql}"
  rm -f "${ingest_sql}"
  trap - EXIT
  printf 'postgresql://mcp_public_catalog_ingest:%s@postgres:5432/%s?connect_timeout=3\n' \
    "${ingest_password}" "${DB_NAME}" > "${INGEST_URL_FILE}"
  chmod 600 "${INGEST_URL_FILE}"
  unset ingest_password
fi
podman unshare chown "${MCP_RUNTIME_UID}:${MCP_RUNTIME_UID}" "${INGEST_URL_FILE}"
podman unshare chmod 600 "${INGEST_URL_FILE}"

# Reassert additive table grants when role/secret files already existed.
podman exec "${DB_CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d "${DB_NAME}" -c \
  "GRANT SELECT ON mcp_saas.public_data_sources, mcp_saas.public_datasets, mcp_saas.public_dataset_records, mcp_saas.pattern_catalog_bindings, mcp_saas.public_dataset_ingestion_runs TO mcp_catalog_reader"
podman exec "${DB_CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d "${DB_NAME}" -c "GRANT SELECT ON mcp_saas.pattern_templates, mcp_saas.use_case_registry, mcp_saas.tenant_registry, mcp_saas.use_case_tenants TO mcp_catalog_reader"
podman exec "${DB_CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d "${DB_NAME}" -c "GRANT SELECT ON mcp_saas.schema_registry TO mcp_catalog_reader"
podman exec "${DB_CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d "${DB_NAME}" -c "GRANT SELECT, INSERT, UPDATE ON mcp_saas.pattern_templates, mcp_saas.use_case_registry, mcp_saas.tenant_registry, mcp_saas.use_case_tenants, mcp_saas.schema_registry TO mcp_registry_reconciler; GRANT DELETE ON mcp_saas.use_case_tenants TO mcp_registry_reconciler"
podman exec "${DB_CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d "${DB_NAME}" -c \
  "GRANT SELECT, INSERT ON mcp_saas.public_data_sources, mcp_saas.public_datasets, mcp_saas.public_dataset_records, mcp_saas.pattern_catalog_bindings, mcp_saas.public_dataset_ingestion_runs TO mcp_public_catalog_ingest"
podman exec "${DB_CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d "${DB_NAME}" -c \
  "GRANT UPDATE (source_name, source_url, access_status, updated_at) ON mcp_saas.public_data_sources TO mcp_public_catalog_ingest; GRANT UPDATE (title, landing_page, schema_version, namespace_ids, metadata, updated_at) ON mcp_saas.public_datasets TO mcp_public_catalog_ingest; GRANT UPDATE (record_type, canonical_attributes, semantic_context, source_metadata, source_url, content_hash, schema_version, observed_at) ON mcp_saas.public_dataset_records TO mcp_public_catalog_ingest; GRANT UPDATE (binding_context) ON mcp_saas.pattern_catalog_bindings TO mcp_public_catalog_ingest; GRANT UPDATE (inserted_count, updated_count, duplicate_count, status, completed_at) ON mcp_saas.public_dataset_ingestion_runs TO mcp_public_catalog_ingest"

echo "Database migrations and synthetic seed applied."

echo "Protected tenant connection file: ${DATABASE_URL_FILE}"
echo "Secret file is readable only by the application UID ${MCP_RUNTIME_UID} inside rootless Podman."
echo "Database network: ${DB_NETWORK} (not published to the host)."
echo "Protected registry reconciler connection file: ${REGISTRY_URL_FILE} (registry tables only)."
echo "One-shot public catalog writer secret: ${INGEST_URL_FILE} (separate from tenant read-only credential)."
echo "Run the documented Compose overlay to start database-backed tenant MCP apps."
