-- Explicit route context and bounded privacy-safe operational events.
ALTER TABLE mcp_saas.core_route_metadata
    ADD COLUMN IF NOT EXISTS template_id text,
    ADD COLUMN IF NOT EXISTS tenant_id text;

UPDATE mcp_saas.core_route_metadata AS route
SET template_id = package.template_id
FROM mcp_saas.pattern_template_packages AS package
WHERE route.template_id IS NULL
  AND route.tenant_id IS NULL
  AND route.route_id = package.template_id
  AND route.route_version = package.template_version;

UPDATE mcp_saas.core_route_metadata AS route
SET template_id = tenant.pattern_id,
    tenant_id = tenant.tenant_id
FROM mcp_saas.tenant_registry AS tenant
WHERE route.template_id IS NULL
  AND route.tenant_id IS NULL
  AND route.route_id = tenant.tenant_id || '.' || tenant.pattern_id
  AND route.route_version = tenant.pattern_version;

CREATE INDEX IF NOT EXISTS ix_core_route_metadata_tenant
    ON mcp_saas.core_route_metadata (tenant_id, template_id, route_version)
    WHERE tenant_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS mcp_saas.core_route_events (
    correlation_id uuid PRIMARY KEY,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    route_id text CHECK (route_id IS NULL OR length(route_id) BETWEEN 1 AND 129),
    route_version text CHECK (route_version IS NULL OR length(route_version) BETWEEN 1 AND 32),
    operation_id text CHECK (operation_id IS NULL OR length(operation_id) BETWEEN 1 AND 128),
    template_id text CHECK (template_id IS NULL OR template_id ~ '^[a-z][a-z0-9-]{0,63}$'),
    tenant_id text CHECK (tenant_id IS NULL OR tenant_id ~ '^[a-z][a-z0-9-]{0,63}$'),
    result_status text NOT NULL CHECK (
        result_status IN ('ok', 'unknown_route', 'incompatible', 'unavailable')
    ),
    latency_ms double precision NOT NULL CHECK (latency_ms >= 0 AND latency_ms <= 600000),
    error_class text CHECK (
        error_class IS NULL OR error_class IN (
            'registry_unavailable', 'tenant_unavailable', 'unknown_route',
            'incompatible_route', 'tenant_tool_error'
        )
    ),
    CHECK (
        (route_id IS NULL AND route_version IS NULL AND operation_id IS NULL)
        OR (route_id IS NOT NULL AND route_version IS NOT NULL AND operation_id IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS ix_core_route_events_window
    ON mcp_saas.core_route_events (occurred_at, template_id, tenant_id, operation_id);

COMMENT ON TABLE mcp_saas.core_route_events IS
    'Allowlisted route outcome and latency events; excludes arguments, prompts, profiles, credentials, endpoints, results, and source records.';
