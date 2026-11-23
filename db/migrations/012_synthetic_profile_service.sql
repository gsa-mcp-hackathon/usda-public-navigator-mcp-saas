-- Revisioned, confirmed synthetic profile writes with field-level provenance.
ALTER TABLE mcp_saas.synthetic_profiles
    ADD COLUMN IF NOT EXISTS schema_registry_id text,
    ADD COLUMN IF NOT EXISTS revision integer NOT NULL DEFAULT 1
        CHECK (revision > 0),
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

CREATE TABLE IF NOT EXISTS mcp_saas.profile_attribute_history (
    profile_id text NOT NULL REFERENCES mcp_saas.synthetic_profiles(profile_id)
        ON DELETE CASCADE,
    attribute_id text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    dimension_id text NOT NULL,
    value_type text NOT NULL CHECK (
        value_type IN ('string', 'integer', 'number', 'boolean', 'object', 'array')
    ),
    value jsonb NOT NULL,
    source_id text NOT NULL,
    schema_version text NOT NULL,
    provenance jsonb NOT NULL CHECK (jsonb_typeof(provenance) = 'object'),
    observed_at timestamptz NOT NULL,
    PRIMARY KEY (profile_id, attribute_id, revision)
);

INSERT INTO mcp_saas.profile_attribute_history (
    profile_id, attribute_id, revision, dimension_id, value_type, value,
    source_id, schema_version, provenance, observed_at
)
SELECT attribute.profile_id, attribute.attribute_id, profile.revision,
       attribute.dimension_id, attribute.value_type, attribute.value,
       attribute.source_id, attribute.schema_version, attribute.provenance,
       attribute.observed_at
FROM mcp_saas.profile_attribute_values AS attribute
JOIN mcp_saas.synthetic_profiles AS profile USING (profile_id)
ON CONFLICT (profile_id, attribute_id, revision) DO NOTHING;

CREATE INDEX IF NOT EXISTS ix_profile_attribute_history_revision
    ON mcp_saas.profile_attribute_history (profile_id, revision, attribute_id);

COMMENT ON TABLE mcp_saas.profile_attribute_history IS
    'Append-only revisions of explicitly confirmed synthetic profile fields; never store conversation transcripts.';
