-- One-time, digest-only bearer claims for resuming synthetic profiles across MCP sessions.
CREATE TABLE IF NOT EXISTS mcp_saas.profile_resume_claims (
    claim_sha256 char(64) PRIMARY KEY CHECK (
        claim_sha256 ~ '^[0-9a-f]{64}$'
    ),
    profile_id text NOT NULL REFERENCES mcp_saas.synthetic_profiles(profile_id)
        ON DELETE CASCADE,
    purpose_id text NOT NULL CHECK (length(purpose_id) BETWEEN 1 AND 192),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL DEFAULT now() + interval '30 days',
    consumed_at timestamptz,
    revoked_at timestamptz,
    CHECK (consumed_at IS NULL OR revoked_at IS NULL)
);

CREATE INDEX IF NOT EXISTS ix_profile_resume_claims_profile_purpose
    ON mcp_saas.profile_resume_claims (profile_id, purpose_id, expires_at);

COMMENT ON TABLE mcp_saas.profile_resume_claims IS
    'Digest-only, expiring, revocable, one-time bearer claims for one synthetic profile; possession is not verified identity.';
