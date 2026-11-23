-- Store privacy-safe MCP correlation hashes on immutable operator review decisions.
ALTER TABLE mcp_saas.source_policy_review_decisions
    ADD COLUMN IF NOT EXISTS mcp_session_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS mcp_request_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS mcp_client_identity_status TEXT,
    ADD COLUMN IF NOT EXISTS operator_credential_label TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'mcp_saas.source_policy_review_decisions'::regclass
          AND conname = 'source_policy_review_session_hash_check'
    ) THEN
        ALTER TABLE mcp_saas.source_policy_review_decisions
            ADD CONSTRAINT source_policy_review_session_hash_check
            CHECK (mcp_session_sha256 IS NULL OR mcp_session_sha256 ~ '^[0-9a-f]{64}$');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'mcp_saas.source_policy_review_decisions'::regclass
          AND conname = 'source_policy_review_request_hash_check'
    ) THEN
        ALTER TABLE mcp_saas.source_policy_review_decisions
            ADD CONSTRAINT source_policy_review_request_hash_check
            CHECK (mcp_request_sha256 IS NULL OR mcp_request_sha256 ~ '^[0-9a-f]{64}$');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'mcp_saas.source_policy_review_decisions'::regclass
          AND conname = 'source_policy_review_client_identity_check'
    ) THEN
        ALTER TABLE mcp_saas.source_policy_review_decisions
            ADD CONSTRAINT source_policy_review_client_identity_check
            CHECK (mcp_client_identity_status IS NULL
                OR mcp_client_identity_status = 'unavailable');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'mcp_saas.source_policy_review_decisions'::regclass
          AND conname = 'source_policy_review_operator_label_check'
    ) THEN
        ALTER TABLE mcp_saas.source_policy_review_decisions
            ADD CONSTRAINT source_policy_review_operator_label_check
            CHECK (operator_credential_label IS NULL
                OR operator_credential_label = 'core_operator_token');
    END IF;
END $$;

ALTER TABLE mcp_saas.program_candidate_review_decisions
    ADD COLUMN IF NOT EXISTS mcp_session_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS mcp_request_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS mcp_client_identity_status TEXT,
    ADD COLUMN IF NOT EXISTS operator_credential_label TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'mcp_saas.program_candidate_review_decisions'::regclass
          AND conname = 'program_candidate_review_session_hash_check'
    ) THEN
        ALTER TABLE mcp_saas.program_candidate_review_decisions
            ADD CONSTRAINT program_candidate_review_session_hash_check
            CHECK (mcp_session_sha256 IS NULL OR mcp_session_sha256 ~ '^[0-9a-f]{64}$');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'mcp_saas.program_candidate_review_decisions'::regclass
          AND conname = 'program_candidate_review_request_hash_check'
    ) THEN
        ALTER TABLE mcp_saas.program_candidate_review_decisions
            ADD CONSTRAINT program_candidate_review_request_hash_check
            CHECK (mcp_request_sha256 IS NULL OR mcp_request_sha256 ~ '^[0-9a-f]{64}$');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'mcp_saas.program_candidate_review_decisions'::regclass
          AND conname = 'program_candidate_review_client_identity_check'
    ) THEN
        ALTER TABLE mcp_saas.program_candidate_review_decisions
            ADD CONSTRAINT program_candidate_review_client_identity_check
            CHECK (mcp_client_identity_status IS NULL
                OR mcp_client_identity_status = 'unavailable');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'mcp_saas.program_candidate_review_decisions'::regclass
          AND conname = 'program_candidate_review_operator_label_check'
    ) THEN
        ALTER TABLE mcp_saas.program_candidate_review_decisions
            ADD CONSTRAINT program_candidate_review_operator_label_check
            CHECK (operator_credential_label IS NULL
                OR operator_credential_label = 'core_operator_token');
    END IF;
END $$;

COMMENT ON COLUMN mcp_saas.source_policy_review_decisions.mcp_session_sha256 IS
    'Optional domain-separated SHA-256 of the MCP session identifier; not a human identity.';
COMMENT ON COLUMN mcp_saas.source_policy_review_decisions.mcp_request_sha256 IS
    'Optional domain-separated SHA-256 of the MCP request identifier for log correlation.';
COMMENT ON COLUMN mcp_saas.source_policy_review_decisions.mcp_client_identity_status IS
    'Trusted client identity is unavailable; caller-supplied client identifiers are not stored.';
COMMENT ON COLUMN mcp_saas.source_policy_review_decisions.operator_credential_label IS
    'Fixed shared credential label; does not identify an individual operator.';

COMMENT ON COLUMN mcp_saas.program_candidate_review_decisions.mcp_session_sha256 IS
    'Optional domain-separated SHA-256 of the MCP session identifier; not a human identity.';
COMMENT ON COLUMN mcp_saas.program_candidate_review_decisions.mcp_request_sha256 IS
    'Optional domain-separated SHA-256 of the MCP request identifier for log correlation.';
COMMENT ON COLUMN mcp_saas.program_candidate_review_decisions.mcp_client_identity_status IS
    'Trusted client identity is unavailable; caller-supplied client identifiers are not stored.';
COMMENT ON COLUMN mcp_saas.program_candidate_review_decisions.operator_credential_label IS
    'Fixed shared credential label; does not identify an individual operator.';
