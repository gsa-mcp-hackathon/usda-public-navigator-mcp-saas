-- Save only an explicitly selected public lead reference in a scoped synthetic profile.
CREATE TABLE IF NOT EXISTS mcp_saas.profile_saved_interests (
    profile_id text NOT NULL REFERENCES mcp_saas.synthetic_profiles(profile_id)
        ON DELETE CASCADE,
    lead_id char(24) NOT NULL CHECK (lead_id ~ '^[0-9a-f]{24}$'),
    source_id text NOT NULL CHECK (source_id ~ '^[a-z][a-z0-9._-]{0,127}$'),
    source_entry_id text NOT NULL
        CHECK (source_entry_id ~ '^[a-z][a-z0-9._-]{0,127}$'),
    canonical_source_url text NOT NULL CHECK (
        length(canonical_source_url) BETWEEN 9 AND 2048
        AND canonical_source_url LIKE 'https://%'
    ),
    policy_manifest_id text NOT NULL,
    policy_manifest_version text NOT NULL CHECK (
        policy_manifest_version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'
    ),
    policy_content_sha256 char(64) NOT NULL
        CHECK (policy_content_sha256 ~ '^[0-9a-f]{64}$'),
    retrieved_at timestamptz NOT NULL,
    selected boolean NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (profile_id, lead_id)
);

CREATE INDEX IF NOT EXISTS ix_profile_saved_interests_profile_updated
    ON mcp_saas.profile_saved_interests (profile_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS mcp_saas.profile_saved_interest_history (
    profile_id text NOT NULL REFERENCES mcp_saas.synthetic_profiles(profile_id)
        ON DELETE CASCADE,
    lead_id char(24) NOT NULL CHECK (lead_id ~ '^[0-9a-f]{24}$'),
    profile_revision integer NOT NULL CHECK (profile_revision > 1),
    source_id text NOT NULL,
    source_entry_id text NOT NULL,
    canonical_source_url text NOT NULL,
    policy_manifest_id text NOT NULL,
    policy_manifest_version text NOT NULL,
    policy_content_sha256 char(64) NOT NULL
        CHECK (policy_content_sha256 ~ '^[0-9a-f]{64}$'),
    retrieved_at timestamptz NOT NULL,
    selected boolean NOT NULL,
    authorization_scope_sha256 char(64) NOT NULL
        CHECK (authorization_scope_sha256 ~ '^[0-9a-f]{64}$'),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (profile_id, lead_id, profile_revision)
);

COMMENT ON TABLE mcp_saas.profile_saved_interests IS
    'Current user-selected public lead references attached to one authorized synthetic profile; stores no page content or eligibility outcome.';
COMMENT ON TABLE mcp_saas.profile_saved_interest_history IS
    'Append-only selected-interest state changes with profile revision and one-way session scope digest; never stores a profile ID in client-visible output.';
