-- Active, versioned semantic definitions are runtime data; source JSON stays in Git.
CREATE TABLE IF NOT EXISTS mcp_saas.schema_registry (
    manifest_id text NOT NULL,
    registry_id text NOT NULL,
    schema_version text NOT NULL,
    lifecycle_state text NOT NULL
        CHECK (lifecycle_state IN ('active', 'inactive', 'deprecated')),
    definition jsonb NOT NULL CHECK (jsonb_typeof(definition) = 'object'),
    git_revision text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (registry_id, schema_version)
);

COMMENT ON TABLE mcp_saas.schema_registry IS
    'Active projection of complete, versioned schema definitions reviewed and versioned in Git.';
