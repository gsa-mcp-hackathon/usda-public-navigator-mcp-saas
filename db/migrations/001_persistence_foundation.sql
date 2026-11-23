-- Transitional implementation: records carry schema_version while active definitions are packaged as JSON. The target is a database-backed Schema Registry.
-- This migration adds catalog/profile structures only; it does not yet persist active schema-registry definitions.
CREATE SCHEMA IF NOT EXISTS mcp_saas;

CREATE TABLE IF NOT EXISTS mcp_saas.program_catalog (
    tenant_id text NOT NULL,
    cluster_id text NOT NULL,
    program_id text NOT NULL,
    title text NOT NULL,
    source_id text NOT NULL,
    source_url text,
    jurisdiction text NOT NULL,
    catalog_version text NOT NULL,
    schema_version text NOT NULL DEFAULT 'core-v1',
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(attributes) = 'object'),
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(provenance) = 'object'),
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, program_id),
    CHECK (source_url IS NULL OR source_url ~ '^https?://')
);

CREATE INDEX IF NOT EXISTS ix_program_catalog_scope
    ON mcp_saas.program_catalog (cluster_id, jurisdiction, tenant_id);
CREATE INDEX IF NOT EXISTS ix_program_catalog_attributes
    ON mcp_saas.program_catalog USING gin (attributes jsonb_path_ops);

CREATE TABLE IF NOT EXISTS mcp_saas.synthetic_profiles (
    profile_id text PRIMARY KEY,
    profile_kind text NOT NULL,
    schema_version text NOT NULL,
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(provenance) = 'object'),
    created_at timestamptz NOT NULL
);

-- Values are keyed by canonical attribute IDs, not duplicated tenant field names.
-- Transitional implementation note: these values are checked against packaged schema JSON. Target runtime definitions are active records in the database-backed Schema Registry.
CREATE TABLE IF NOT EXISTS mcp_saas.profile_attribute_values (
    profile_id text NOT NULL REFERENCES mcp_saas.synthetic_profiles(profile_id)
        ON DELETE CASCADE,
    attribute_id text NOT NULL,
    dimension_id text NOT NULL,
    value_type text NOT NULL CHECK (
        value_type IN ('string', 'integer', 'number', 'boolean', 'object', 'array')
    ),
    value jsonb NOT NULL,
    source_id text NOT NULL,
    schema_version text NOT NULL,
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(provenance) = 'object'),
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

CREATE TABLE IF NOT EXISTS mcp_saas.profile_relationships (
    subject_profile_id text NOT NULL REFERENCES mcp_saas.synthetic_profiles(profile_id)
        ON DELETE CASCADE,
    relationship_id text NOT NULL,
    object_profile_id text NOT NULL REFERENCES mcp_saas.synthetic_profiles(profile_id)
        ON DELETE CASCADE,
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(provenance) = 'object'),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (subject_profile_id, relationship_id, object_profile_id),
    CHECK (subject_profile_id <> object_profile_id)
);

CREATE INDEX IF NOT EXISTS ix_profile_relationship_target
    ON mcp_saas.profile_relationships (object_profile_id, relationship_id);

COMMENT ON SCHEMA mcp_saas IS
    'MCP-SaaS runtime catalog and synthetic profile data; schema_version links records to the active Schema Registry definition.';
COMMENT ON TABLE mcp_saas.program_catalog IS
    'Transitional Eligibility-specific program catalog projection; not the shared platform Pattern Catalog contract. The Eligibility template defines the catalog name programs; other patterns define their own catalog names over the shared pattern-neutral persistence model.';
COMMENT ON TABLE mcp_saas.synthetic_profiles IS
    'Synthetic-only reusable profile entities; never store real people or contact details.';
COMMENT ON TABLE mcp_saas.profile_attribute_values IS
    'Typed values keyed to canonical Schema Registry IDs and reusable dimensions.';
COMMENT ON TABLE mcp_saas.profile_relationships IS
    'Explicit synthetic profile links such as person-to-household membership.';
