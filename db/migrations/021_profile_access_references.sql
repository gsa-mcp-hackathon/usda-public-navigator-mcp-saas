-- Bind opaque profile references to one trusted MCP session and schema purpose.
CREATE TABLE IF NOT EXISTS mcp_saas.profile_access_references (
    reference_sha256 char(64) PRIMARY KEY CHECK (
        reference_sha256 ~ '^[0-9a-f]{64}$'
    ),
    profile_id text NOT NULL REFERENCES mcp_saas.synthetic_profiles(profile_id)
        ON DELETE CASCADE,
    scope_sha256 char(64) NOT NULL CHECK (scope_sha256 ~ '^[0-9a-f]{64}$'),
    purpose_id text NOT NULL CHECK (length(purpose_id) BETWEEN 1 AND 192),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL DEFAULT now() + interval '12 hours',
    revoked_at timestamptz
);

CREATE INDEX IF NOT EXISTS ix_profile_access_references_profile
    ON mcp_saas.profile_access_references (profile_id, scope_sha256);

COMMENT ON TABLE mcp_saas.profile_access_references IS
    'Hashed, short-lived opaque capabilities bound to one server-authenticated session scope and profile schema purpose.';
