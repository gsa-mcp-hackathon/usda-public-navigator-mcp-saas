-- Store privacy-safe MCP correlation hashes on append-only operator lifecycle events.
ALTER TABLE mcp_saas.source_policy_activation_events
    ADD COLUMN IF NOT EXISTS mcp_session_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS mcp_request_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS mcp_client_identity_status TEXT,
    ADD COLUMN IF NOT EXISTS operator_credential_label TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'mcp_saas.source_policy_activation_events'::regclass
          AND conname = 'source_policy_activation_session_hash_check'
    ) THEN
        ALTER TABLE mcp_saas.source_policy_activation_events
            ADD CONSTRAINT source_policy_activation_session_hash_check
            CHECK (mcp_session_sha256 IS NULL OR mcp_session_sha256 ~ '^[0-9a-f]{64}$');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'mcp_saas.source_policy_activation_events'::regclass
          AND conname = 'source_policy_activation_request_hash_check'
    ) THEN
        ALTER TABLE mcp_saas.source_policy_activation_events
            ADD CONSTRAINT source_policy_activation_request_hash_check
            CHECK (mcp_request_sha256 IS NULL OR mcp_request_sha256 ~ '^[0-9a-f]{64}$');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'mcp_saas.source_policy_activation_events'::regclass
          AND conname = 'source_policy_activation_client_identity_check'
    ) THEN
        ALTER TABLE mcp_saas.source_policy_activation_events
            ADD CONSTRAINT source_policy_activation_client_identity_check
            CHECK (mcp_client_identity_status IS NULL
                OR mcp_client_identity_status = 'unavailable');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'mcp_saas.source_policy_activation_events'::regclass
          AND conname = 'source_policy_activation_operator_label_check'
    ) THEN
        ALTER TABLE mcp_saas.source_policy_activation_events
            ADD CONSTRAINT source_policy_activation_operator_label_check
            CHECK (operator_credential_label IS NULL
                OR operator_credential_label = 'core_operator_token');
    END IF;
END $$;

COMMENT ON COLUMN mcp_saas.source_policy_activation_events.mcp_session_sha256 IS
    'Optional domain-separated SHA-256 of the MCP session identifier; not a human identity.';
COMMENT ON COLUMN mcp_saas.source_policy_activation_events.mcp_request_sha256 IS
    'Optional domain-separated SHA-256 of the MCP request identifier for log correlation.';
COMMENT ON COLUMN mcp_saas.source_policy_activation_events.mcp_client_identity_status IS
    'Trusted client identity is unavailable; caller-supplied client identifiers are not stored.';
COMMENT ON COLUMN mcp_saas.source_policy_activation_events.operator_credential_label IS
    'Fixed shared credential label; does not identify an individual operator.';

ALTER TABLE mcp_saas.program_candidate_publication_events
    ADD COLUMN IF NOT EXISTS mcp_session_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS mcp_request_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS mcp_client_identity_status TEXT,
    ADD COLUMN IF NOT EXISTS operator_credential_label TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'mcp_saas.program_candidate_publication_events'::regclass
          AND conname = 'program_publication_session_hash_check'
    ) THEN
        ALTER TABLE mcp_saas.program_candidate_publication_events
            ADD CONSTRAINT program_publication_session_hash_check
            CHECK (mcp_session_sha256 IS NULL OR mcp_session_sha256 ~ '^[0-9a-f]{64}$');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'mcp_saas.program_candidate_publication_events'::regclass
          AND conname = 'program_publication_request_hash_check'
    ) THEN
        ALTER TABLE mcp_saas.program_candidate_publication_events
            ADD CONSTRAINT program_publication_request_hash_check
            CHECK (mcp_request_sha256 IS NULL OR mcp_request_sha256 ~ '^[0-9a-f]{64}$');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'mcp_saas.program_candidate_publication_events'::regclass
          AND conname = 'program_publication_client_identity_check'
    ) THEN
        ALTER TABLE mcp_saas.program_candidate_publication_events
            ADD CONSTRAINT program_publication_client_identity_check
            CHECK (mcp_client_identity_status IS NULL
                OR mcp_client_identity_status = 'unavailable');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'mcp_saas.program_candidate_publication_events'::regclass
          AND conname = 'program_publication_operator_label_check'
    ) THEN
        ALTER TABLE mcp_saas.program_candidate_publication_events
            ADD CONSTRAINT program_publication_operator_label_check
            CHECK (operator_credential_label IS NULL
                OR operator_credential_label = 'core_operator_token');
    END IF;
END $$;

COMMENT ON COLUMN mcp_saas.program_candidate_publication_events.mcp_session_sha256 IS
    'Optional domain-separated SHA-256 of the MCP session identifier; not a human identity.';
COMMENT ON COLUMN mcp_saas.program_candidate_publication_events.mcp_request_sha256 IS
    'Optional domain-separated SHA-256 of the MCP request identifier for log correlation.';
COMMENT ON COLUMN mcp_saas.program_candidate_publication_events.mcp_client_identity_status IS
    'Trusted client identity is unavailable; caller-supplied client identifiers are not stored.';
COMMENT ON COLUMN mcp_saas.program_candidate_publication_events.operator_credential_label IS
    'Fixed shared credential label; does not identify an individual operator.';
