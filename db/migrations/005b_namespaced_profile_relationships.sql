-- Preserve explicit profile links with the same namespaced semantics as fields.
CREATE TABLE IF NOT EXISTS mcp_saas.profile_relationships (
    subject_profile_id text NOT NULL
        REFERENCES mcp_saas.synthetic_profiles(profile_id) ON DELETE CASCADE,
    relationship_id text NOT NULL CHECK (
        relationship_id ~ '^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+$'
    ),
    object_profile_id text NOT NULL
        REFERENCES mcp_saas.synthetic_profiles(profile_id) ON DELETE CASCADE,
    context jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (
        jsonb_typeof(context) = 'object'
    ),
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (
        jsonb_typeof(provenance) = 'object'
    ),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (subject_profile_id, relationship_id, object_profile_id),
    CHECK (subject_profile_id <> object_profile_id)
);

-- Upgrade the transitional table from migration 001 without requiring it on
-- the current VM path. NOT VALID preserves legacy rows while enforcing new writes.
ALTER TABLE mcp_saas.profile_relationships
    ADD COLUMN IF NOT EXISTS context jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE mcp_saas.profile_relationships
    DROP CONSTRAINT IF EXISTS profile_relationships_relationship_id_check;
ALTER TABLE mcp_saas.profile_relationships
    ADD CONSTRAINT profile_relationships_relationship_id_check
        CHECK (relationship_id ~ '^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+$')
        NOT VALID;

ALTER TABLE mcp_saas.profile_relationships
    DROP CONSTRAINT IF EXISTS profile_relationships_context_check;
ALTER TABLE mcp_saas.profile_relationships
    ADD CONSTRAINT profile_relationships_context_check
        CHECK (jsonb_typeof(context) = 'object') NOT VALID;

CREATE INDEX IF NOT EXISTS ix_profile_relationship_target
    ON mcp_saas.profile_relationships
       (object_profile_id, relationship_id, subject_profile_id);

COMMENT ON TABLE mcp_saas.profile_relationships IS
    'Explicit synthetic profile links with namespaced relationship identity, context, and provenance.';
