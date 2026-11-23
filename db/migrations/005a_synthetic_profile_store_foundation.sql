-- Current normalized profile-store foundation; replaces use of transitional 001.
CREATE SCHEMA IF NOT EXISTS mcp_saas;

CREATE TABLE IF NOT EXISTS mcp_saas.synthetic_profiles (
    profile_id text PRIMARY KEY CHECK (
        profile_id ~ '^(demo|synthetic)-[a-z0-9][a-z0-9-]{0,62}$'
    ),
    profile_kind text NOT NULL CHECK (
        profile_kind ~ '^[a-z][a-z0-9_-]{0,63}$'
    ),
    schema_version text NOT NULL CHECK (
        schema_version ~ '^[a-z0-9][a-z0-9._-]{0,63}$'
    ),
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (
        jsonb_typeof(provenance) = 'object'
        AND provenance->>'record_type' = 'synthetic'
    ),
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Profile attributes retain canonical namespaced identities and dimensions.
CREATE TABLE IF NOT EXISTS mcp_saas.profile_attribute_values (
    profile_id text NOT NULL REFERENCES mcp_saas.synthetic_profiles(profile_id)
        ON DELETE CASCADE,
    attribute_id text NOT NULL CHECK (
        attribute_id ~ '^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+$'
    ),
    dimension_id text NOT NULL CHECK (
        dimension_id ~ '^[a-z][a-z0-9-]{0,63}$'
    ),
    value_type text NOT NULL CHECK (
        value_type IN ('string', 'integer', 'number', 'boolean', 'object', 'array')
    ),
    value jsonb NOT NULL,
    source_id text NOT NULL CHECK (length(source_id) BETWEEN 1 AND 128),
    schema_version text NOT NULL CHECK (
        schema_version ~ '^[a-z0-9][a-z0-9._-]{0,63}$'
    ),
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (
        jsonb_typeof(provenance) = 'object'
    ),
    observed_at timestamptz NOT NULL,
    PRIMARY KEY (profile_id, attribute_id),
    CHECK (
        (value_type IN ('object', 'array') AND jsonb_typeof(value) = value_type)
        OR (value_type = 'string' AND jsonb_typeof(value) = 'string')
        OR (value_type = 'boolean' AND jsonb_typeof(value) = 'boolean')
        OR (value_type = 'integer' AND jsonb_typeof(value) = 'number'
            AND value::text !~ '[.eE]')
        OR (value_type = 'number' AND jsonb_typeof(value) = 'number')
    )
);

CREATE INDEX IF NOT EXISTS ix_profile_attributes_dimension
    ON mcp_saas.profile_attribute_values (dimension_id, attribute_id, profile_id);

COMMENT ON TABLE mcp_saas.synthetic_profiles IS
    'Synthetic-only profile identity and active schema version; no transcripts or real-person records.';
COMMENT ON TABLE mcp_saas.profile_attribute_values IS
    'Versioned typed synthetic profile values keyed by canonical namespaced attributes and dimensions.';
