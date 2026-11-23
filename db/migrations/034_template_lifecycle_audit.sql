-- Append-only receipts for exact, operator-approved cluster-template activation.
CREATE TABLE IF NOT EXISTS mcp_saas.pattern_template_lifecycle_events (
    transaction_id uuid PRIMARY KEY,
    template_id text NOT NULL,
    template_version text NOT NULL,
    action text NOT NULL CHECK (action = 'activate'),
    expected_before_sha256 char(64) NOT NULL
        CHECK (expected_before_sha256 ~ '^[0-9a-f]{64}$'),
    approval_reference text NOT NULL
        CHECK (length(btrim(approval_reference)) BETWEEN 1 AND 512
            AND approval_reference !~ '[[:cntrl:]]'),
    before_state text NOT NULL CHECK (before_state = 'pending'),
    after_state text NOT NULL CHECK (after_state = 'active'),
    descriptor_sha256 char(64) NOT NULL
        CHECK (descriptor_sha256 ~ '^[0-9a-f]{64}$'),
    before_state_sha256 char(64) NOT NULL
        CHECK (before_state_sha256 ~ '^[0-9a-f]{64}$'),
    after_state_sha256 char(64) NOT NULL
        CHECK (after_state_sha256 ~ '^[0-9a-f]{64}$'),
    authorization_capability text NOT NULL
        CHECK (authorization_capability = 'template_registry_writer'),
    mcp_session_sha256 char(64)
        CHECK (mcp_session_sha256 IS NULL OR mcp_session_sha256 ~ '^[0-9a-f]{64}$'),
    mcp_request_sha256 char(64)
        CHECK (mcp_request_sha256 IS NULL OR mcp_request_sha256 ~ '^[0-9a-f]{64}$'),
    mcp_client_identity_status text NOT NULL
        CHECK (mcp_client_identity_status = 'unavailable'),
    operator_credential_label text NOT NULL
        CHECK (operator_credential_label = 'core_operator_token'),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (template_id, template_version)
        REFERENCES mcp_saas.pattern_template_packages (template_id, template_version)
);

CREATE INDEX IF NOT EXISTS ix_pattern_template_lifecycle_events_target
    ON mcp_saas.pattern_template_lifecycle_events
       (template_id, template_version, recorded_at DESC);

COMMENT ON TABLE mcp_saas.pattern_template_lifecycle_events IS
    'Append-only receipts for operator-approved template activation, including compare-and-set state digests and privacy-safe MCP correlation hashes.';
