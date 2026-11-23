-- Preserve operator-approved source/dataset metadata edits as append-only receipts.
CREATE TABLE IF NOT EXISTS mcp_saas.public_catalog_registration_events (
    transaction_id UUID PRIMARY KEY,
    source_id TEXT NOT NULL,
    dataset_ids TEXT[] NOT NULL CHECK (cardinality(dataset_ids) BETWEEN 1 AND 100),
    expected_before_sha256 CHAR(64) NOT NULL
        CHECK (expected_before_sha256 ~ '^[0-9a-f]{64}$'),
    before_sha256 CHAR(64) NOT NULL CHECK (before_sha256 ~ '^[0-9a-f]{64}$'),
    after_sha256 CHAR(64) NOT NULL CHECK (after_sha256 ~ '^[0-9a-f]{64}$'),
    proposed_sha256 CHAR(64) NOT NULL CHECK (proposed_sha256 ~ '^[0-9a-f]{64}$'),
    approval_reference TEXT NOT NULL
        CHECK (length(btrim(approval_reference)) BETWEEN 1 AND 512
               AND approval_reference !~ '[[:cntrl:]]'),
    outcome TEXT NOT NULL CHECK (outcome IN ('applied', 'stale_before_state')),
    operator_audit_context JSONB NOT NULL CHECK (
        jsonb_typeof(operator_audit_context) = 'object'
        AND operator_audit_context->>'operator_credential_label' = 'core_operator_token'
        AND operator_audit_context->>'mcp_client_identity_status' = 'unavailable'
        AND (
            operator_audit_context->'mcp_session_sha256' = 'null'::jsonb
            OR operator_audit_context->>'mcp_session_sha256' ~ '^[0-9a-f]{64}$'
        )
        AND (
            operator_audit_context->'mcp_request_sha256' = 'null'::jsonb
            OR operator_audit_context->>'mcp_request_sha256' ~ '^[0-9a-f]{64}$'
        )
    ),
    receipt JSONB NOT NULL CHECK (jsonb_typeof(receipt) = 'object'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE mcp_saas.public_catalog_registration_events IS
    'Append-only audit receipts for governed source/dataset metadata compare-and-set operations; contains hashes and IDs only, never source bodies or profile values.';

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = 'mcp_saas_public_catalog_writer'
    ) THEN
        EXECUTE 'GRANT SELECT, INSERT '
            'ON mcp_saas.public_catalog_registration_events '
            'TO mcp_saas_public_catalog_writer';
    END IF;
END $$;
