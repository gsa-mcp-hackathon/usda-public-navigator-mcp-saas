-- Persist validated Eligibility rule packages separately from public listings.
-- Each content digest is immutable; the head selects the currently reusable ruleset.
CREATE TABLE IF NOT EXISTS mcp_saas.eligibility_criteria_rulesets (
    program_id TEXT NOT NULL
        CHECK (program_id ~ '^[a-z0-9][a-z0-9._-]{1,99}$'),
    content_sha256 CHAR(64) NOT NULL
        CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    ruleset JSONB NOT NULL CHECK (jsonb_typeof(ruleset) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (program_id, content_sha256)
);

CREATE TABLE IF NOT EXISTS mcp_saas.eligibility_criteria_ruleset_heads (
    program_id TEXT PRIMARY KEY
        CHECK (program_id ~ '^[a-z0-9][a-z0-9._-]{1,99}$'),
    content_sha256 CHAR(64) NOT NULL,
    request_id UUID NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (program_id, content_sha256)
        REFERENCES mcp_saas.eligibility_criteria_rulesets(program_id, content_sha256)
);

CREATE TABLE IF NOT EXISTS mcp_saas.eligibility_criteria_ruleset_events (
    request_id UUID PRIMARY KEY,
    program_id TEXT NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    previous_content_sha256 CHAR(64),
    outcome TEXT NOT NULL CHECK (outcome IN ('activated', 'duplicate')),
    authorization_capability TEXT NOT NULL
        CHECK (authorization_capability = 'validated_sandbox_mapping'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (program_id, content_sha256)
        REFERENCES mcp_saas.eligibility_criteria_rulesets(program_id, content_sha256),
    CHECK (previous_content_sha256 IS NULL OR previous_content_sha256 ~ '^[0-9a-f]{64}$')
);

COMMENT ON TABLE mcp_saas.eligibility_criteria_rulesets IS
    'Immutable source-cited, profile-schema-validated rule metadata, separate from public program listings and source documents.';
COMMENT ON TABLE mcp_saas.eligibility_criteria_ruleset_heads IS
    'One active exact-digest pointer per program; changed rulesets remain in immutable history.';
COMMENT ON TABLE mcp_saas.eligibility_criteria_ruleset_events IS
    'Idempotent audit events for validated sandbox ruleset activation; no profile facts or source page bodies.';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mcp_public_catalog_ingest') THEN
        EXECUTE 'GRANT SELECT, INSERT ON mcp_saas.eligibility_criteria_rulesets TO mcp_public_catalog_ingest';
        EXECUTE 'GRANT SELECT, INSERT, UPDATE (content_sha256, request_id, updated_at) ON mcp_saas.eligibility_criteria_ruleset_heads TO mcp_public_catalog_ingest';
        EXECUTE 'GRANT SELECT, INSERT ON mcp_saas.eligibility_criteria_ruleset_events TO mcp_public_catalog_ingest';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mcp_catalog_reader') THEN
        EXECUTE 'GRANT SELECT ON mcp_saas.eligibility_criteria_rulesets, mcp_saas.eligibility_criteria_ruleset_heads TO mcp_catalog_reader';
    END IF;
END $$;
