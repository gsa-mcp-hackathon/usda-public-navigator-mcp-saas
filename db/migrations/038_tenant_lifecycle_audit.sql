-- Govern tenant route activation/deactivation with exact before-state receipts.
CREATE TABLE IF NOT EXISTS mcp_saas.tenant_lifecycle_events (
    transaction_id uuid PRIMARY KEY,
    tenant_id text NOT NULL,
    template_id text NOT NULL,
    template_version text NOT NULL,
    action text NOT NULL CHECK (action IN ('activate', 'deactivate')),
    expected_before_sha256 char(64) NOT NULL
        CHECK (expected_before_sha256 ~ '^[0-9a-f]{64}$'),
    approval_reference text NOT NULL
        CHECK (length(btrim(approval_reference)) BETWEEN 1 AND 512
            AND approval_reference !~ '[[:cntrl:]]'),
    before_state text NOT NULL CHECK (before_state IN ('pending', 'inactive', 'active')),
    after_state text NOT NULL CHECK (after_state IN ('active', 'inactive')),
    outcome text NOT NULL CHECK (outcome = 'applied'),
    tenant_spec_sha256 char(64) NOT NULL
        CHECK (tenant_spec_sha256 ~ '^[0-9a-f]{64}$'),
    before_state_sha256 char(64) NOT NULL
        CHECK (before_state_sha256 ~ '^[0-9a-f]{64}$'),
    after_state_sha256 char(64) NOT NULL
        CHECK (after_state_sha256 ~ '^[0-9a-f]{64}$'),
    authorization_capability text NOT NULL
        CHECK (authorization_capability = 'tenant_registry_writer'),
    mcp_session_sha256 char(64)
        CHECK (mcp_session_sha256 IS NULL OR mcp_session_sha256 ~ '^[0-9a-f]{64}$'),
    mcp_request_sha256 char(64)
        CHECK (mcp_request_sha256 IS NULL OR mcp_request_sha256 ~ '^[0-9a-f]{64}$'),
    mcp_client_identity_status text NOT NULL
        CHECK (mcp_client_identity_status = 'unavailable'),
    operator_credential_label text NOT NULL
        CHECK (operator_credential_label = 'core_operator_token'),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
        (action = 'activate' AND before_state IN ('pending', 'inactive')
            AND after_state = 'active')
        OR (action = 'deactivate' AND before_state = 'active'
            AND after_state = 'inactive')
    ),
    FOREIGN KEY (tenant_id, template_id, template_version)
        REFERENCES mcp_saas.tenant_registry (tenant_id, pattern_id, pattern_version)
);

CREATE INDEX IF NOT EXISTS ix_tenant_lifecycle_events_target
    ON mcp_saas.tenant_lifecycle_events
       (tenant_id, template_id, template_version, recorded_at DESC);

COMMENT ON TABLE mcp_saas.tenant_lifecycle_events IS
    'Append-only receipts for operator-approved tenant activation/deactivation, including compare-and-set state digests and privacy-safe MCP correlation hashes.';

DO $$
DECLARE
    api_role text;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['mcp_saas_core_api', 'mcp_saas_data_api'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format(
                'GRANT UPDATE (lifecycle_state, updated_at) '
                'ON mcp_saas.tenant_registry TO %I', api_role
            );
            EXECUTE format(
                'GRANT UPDATE (lifecycle_state, updated_at) '
                'ON mcp_saas.core_route_metadata TO %I', api_role
            );
            EXECUTE format(
                'GRANT SELECT, INSERT ON mcp_saas.tenant_lifecycle_events TO %I',
                api_role
            );
        END IF;
    END LOOP;
END $$;
