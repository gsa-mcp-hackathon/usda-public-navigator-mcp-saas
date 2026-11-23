-- Preserve the exact before/after context and spec digests when an active
-- tenant's jurisdiction scope is expanded through the approved registry flow.
CREATE TABLE IF NOT EXISTS mcp_saas.tenant_registry_jurisdiction_revisions (
    tenant_id text NOT NULL,
    pattern_id text NOT NULL,
    pattern_version text NOT NULL,
    previous_spec_sha256 text NOT NULL
        CHECK (previous_spec_sha256 ~ '^[0-9a-f]{64}$'),
    revised_spec_sha256 text NOT NULL
        CHECK (revised_spec_sha256 ~ '^[0-9a-f]{64}$'),
    previous_context jsonb NOT NULL
        CHECK (jsonb_typeof(previous_context) = 'object'),
    revised_context jsonb NOT NULL
        CHECK (jsonb_typeof(revised_context) = 'object'),
    changed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, pattern_id, pattern_version, revised_spec_sha256),
    FOREIGN KEY (tenant_id, pattern_id, pattern_version)
        REFERENCES mcp_saas.tenant_registry (tenant_id, pattern_id, pattern_version),
    CHECK (previous_spec_sha256 <> revised_spec_sha256)
);

COMMENT ON TABLE mcp_saas.tenant_registry_jurisdiction_revisions IS
    'Immutable before/after audit for exact-digest-approved, jurisdiction-only tenant scope expansions.';

