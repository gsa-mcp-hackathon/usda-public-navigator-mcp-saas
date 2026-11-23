-- Active runtime projection of reviewed, pattern-neutral Git definitions.
CREATE TABLE IF NOT EXISTS mcp_saas.pattern_templates (
    manifest_id text NOT NULL,
    pattern_id text NOT NULL,
    pattern_version text NOT NULL,
    lifecycle_state text NOT NULL
        CHECK (lifecycle_state IN ('active', 'inactive', 'deprecated')),
    definition jsonb NOT NULL CHECK (jsonb_typeof(definition) = 'object'),
    git_revision text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (pattern_id, pattern_version)
);

CREATE TABLE IF NOT EXISTS mcp_saas.use_case_registry (
    manifest_id text NOT NULL,
    use_case_id text NOT NULL,
    use_case_version text NOT NULL,
    pattern_id text NOT NULL,
    pattern_version text NOT NULL,
    operation_id text NOT NULL,
    lifecycle_state text NOT NULL
        CHECK (lifecycle_state IN ('active', 'inactive', 'deprecated')),
    git_revision text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (use_case_id, use_case_version),
    FOREIGN KEY (pattern_id, pattern_version)
        REFERENCES mcp_saas.pattern_templates (pattern_id, pattern_version)
);

CREATE TABLE IF NOT EXISTS mcp_saas.tenant_registry (
    manifest_id text NOT NULL,
    tenant_id text PRIMARY KEY,
    pattern_id text NOT NULL,
    pattern_version text NOT NULL,
    mcp_endpoint text NOT NULL CHECK (mcp_endpoint ~ '^https?://'),
    lifecycle_state text NOT NULL
        CHECK (lifecycle_state IN ('active', 'inactive', 'deprecated')),
    git_revision text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pattern_id, pattern_version)
        REFERENCES mcp_saas.pattern_templates (pattern_id, pattern_version)
);

CREATE TABLE IF NOT EXISTS mcp_saas.use_case_tenants (
    use_case_id text NOT NULL,
    use_case_version text NOT NULL,
    tenant_id text NOT NULL REFERENCES mcp_saas.tenant_registry (tenant_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (use_case_id, use_case_version, tenant_id),
    FOREIGN KEY (use_case_id, use_case_version)
        REFERENCES mcp_saas.use_case_registry (use_case_id, use_case_version)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_use_case_registry_pattern
    ON mcp_saas.use_case_registry (pattern_id, pattern_version, lifecycle_state);
CREATE INDEX IF NOT EXISTS ix_tenant_registry_pattern
    ON mcp_saas.tenant_registry (pattern_id, pattern_version, lifecycle_state);
CREATE INDEX IF NOT EXISTS ix_use_case_tenants_tenant
    ON mcp_saas.use_case_tenants (tenant_id);

COMMENT ON TABLE mcp_saas.pattern_templates IS
    'Active projection of versioned, pattern-neutral definitions reviewed in Git.';
COMMENT ON TABLE mcp_saas.use_case_registry IS
    'Stable use-case versions mapped to registered pattern operations.';
COMMENT ON TABLE mcp_saas.tenant_registry IS
    'Environment-specific MCP endpoints bound to a registered pattern version.';
COMMENT ON TABLE mcp_saas.use_case_tenants IS
    'Explicit eligible tenant set for each registered use-case version.';
