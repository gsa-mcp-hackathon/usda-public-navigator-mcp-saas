-- A logical tenant may run distinct MCP apps for multiple pattern versions.
-- Preserve current rows while changing registry identity to that app binding.
ALTER TABLE mcp_saas.use_case_tenants
    DROP CONSTRAINT IF EXISTS use_case_tenants_tenant_id_fkey;
ALTER TABLE mcp_saas.use_case_tenants
    DROP CONSTRAINT IF EXISTS use_case_tenants_tenant_registration_fkey;

ALTER TABLE mcp_saas.tenant_registry
    DROP CONSTRAINT IF EXISTS tenant_registry_pkey;

ALTER TABLE mcp_saas.tenant_registry
    ADD CONSTRAINT tenant_registry_pkey
        PRIMARY KEY (tenant_id, pattern_id, pattern_version);

ALTER TABLE mcp_saas.use_case_tenants
    ADD COLUMN IF NOT EXISTS pattern_id text,
    ADD COLUMN IF NOT EXISTS pattern_version text;

UPDATE mcp_saas.use_case_tenants AS eligible
SET pattern_id = use_case.pattern_id,
    pattern_version = use_case.pattern_version
FROM mcp_saas.use_case_registry AS use_case
WHERE use_case.use_case_id = eligible.use_case_id
  AND use_case.use_case_version = eligible.use_case_version
  AND (eligible.pattern_id IS NULL OR eligible.pattern_version IS NULL);

ALTER TABLE mcp_saas.use_case_tenants
    ALTER COLUMN pattern_id SET NOT NULL,
    ALTER COLUMN pattern_version SET NOT NULL;

ALTER TABLE mcp_saas.use_case_tenants
    ADD CONSTRAINT use_case_tenants_tenant_registration_fkey
        FOREIGN KEY (tenant_id, pattern_id, pattern_version)
        REFERENCES mcp_saas.tenant_registry (tenant_id, pattern_id, pattern_version);

CREATE INDEX IF NOT EXISTS ix_tenant_registry_pattern
    ON mcp_saas.tenant_registry (pattern_id, pattern_version, lifecycle_state);

COMMENT ON TABLE mcp_saas.tenant_registry IS
    'Tenant MCP app registrations bound to a pattern version; URLs are environment-specific.';
