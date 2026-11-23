-- Immutable, provider-neutral pattern package descriptors and their lifecycle.
CREATE TABLE IF NOT EXISTS mcp_saas.pattern_template_packages (
    template_id text NOT NULL CHECK (template_id ~ '^[a-z][a-z0-9-]{0,63}$'),
    template_version text NOT NULL CHECK (
        template_version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'
    ),
    lifecycle_state text NOT NULL DEFAULT 'pending'
        CHECK (lifecycle_state IN ('pending', 'active', 'inactive', 'deprecated')),
    definition jsonb NOT NULL CHECK (
        jsonb_typeof(definition) = 'object'
        AND definition->>'template_id' = template_id
        AND definition->>'template_version' = template_version
        AND definition->>'artifact_sha256' ~ '^[0-9a-f]{64}$'
        AND definition->'compatible_core_contracts' @> '["core-route.v1"]'::jsonb
        AND jsonb_typeof(definition->'operations') = 'array'
        AND jsonb_array_length(definition->'operations') > 0
    ),
    registered_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (template_id, template_version)
);

CREATE INDEX IF NOT EXISTS ix_pattern_template_packages_active
    ON mcp_saas.pattern_template_packages (template_id, template_version)
    WHERE lifecycle_state = 'active';

COMMENT ON TABLE mcp_saas.pattern_template_packages IS
    'Immutable organization-defined pattern package descriptors, separate from Core releases and tenant registrations.';
