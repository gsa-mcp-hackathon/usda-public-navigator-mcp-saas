-- Generic, version-pinned metadata assignments across shared entity types.
CREATE TABLE IF NOT EXISTS mcp_saas.schema_tag_assignments (
    entity_type text NOT NULL CHECK (
        entity_type IN (
            'profile', 'public_dataset', 'pattern_template',
            'tenant', 'source', 'route'
        )
    ),
    entity_id text NOT NULL CHECK (length(entity_id) BETWEEN 1 AND 192),
    registry_id text NOT NULL,
    schema_version text NOT NULL,
    tag_id text NOT NULL CHECK (
        tag_id ~ '^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+$'
    ),
    context_id text NOT NULL CHECK (
        context_id ~ '^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+$'
    ),
    tag_value jsonb NOT NULL,
    context jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(context) = 'object'),
    provenance jsonb NOT NULL CHECK (jsonb_typeof(provenance) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (
        entity_type, entity_id, registry_id, schema_version, tag_id, context_id
    ),
    FOREIGN KEY (registry_id, schema_version)
        REFERENCES mcp_saas.schema_registry (registry_id, schema_version)
);

CREATE INDEX IF NOT EXISTS ix_schema_tag_assignments_entity
    ON mcp_saas.schema_tag_assignments
       (entity_type, entity_id, registry_id, schema_version);

COMMENT ON TABLE mcp_saas.schema_tag_assignments IS
    'Immutable, version-pinned metadata tags for typed global entities; tags describe context and never grant authorization.';

-- Runtime allowlist is separate from tags: membership, not metadata, grants
-- fixture discovery/read. A fixture can be associated with one case per set.
CREATE TABLE IF NOT EXISTS mcp_saas.evaluation_fixture_members (
    fixture_set_id text NOT NULL CHECK (fixture_set_id ~ '^[a-z][a-z0-9-]{0,63}$'),
    fixture_set_version text NOT NULL CHECK (fixture_set_version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'),
    fixture_id text NOT NULL CHECK (fixture_id ~ '^fixture-[a-z0-9]{4,32}$'),
    use_case_id text NOT NULL CHECK (use_case_id ~ '^[a-z][a-z0-9.-]{0,127}$'),
    profile_id text NOT NULL
        REFERENCES mcp_saas.synthetic_profiles(profile_id),
    schema_registry_id text NOT NULL,
    schema_version text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (fixture_set_id, fixture_set_version, fixture_id),
    UNIQUE (fixture_set_id, fixture_set_version, profile_id),
    FOREIGN KEY (schema_registry_id, schema_version)
        REFERENCES mcp_saas.schema_registry (registry_id, schema_version)
);

CREATE INDEX IF NOT EXISTS ix_evaluation_fixture_members_set_case
    ON mcp_saas.evaluation_fixture_members
       (fixture_set_id, fixture_set_version, use_case_id, fixture_id);

COMMENT ON TABLE mcp_saas.evaluation_fixture_members IS
    'Explicitly authorized synthetic evaluation fixtures. Membership is the only fixture discovery/read allowlist; schema tags alone do not grant access.';
