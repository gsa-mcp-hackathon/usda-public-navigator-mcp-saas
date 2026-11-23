-- Keep candidate registration attribution as an append-only event beside the
-- immutable candidate content. Existing rows intentionally receive no event.
CREATE UNIQUE INDEX IF NOT EXISTS uq_program_candidate_digest
    ON mcp_saas.program_candidate_records (candidate_id, content_sha256);

CREATE TABLE IF NOT EXISTS mcp_saas.program_candidate_registration_events (
    registration_event_id UUID PRIMARY KEY,
    candidate_id TEXT NOT NULL UNIQUE,
    content_sha256 CHAR(64) NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    action TEXT NOT NULL CHECK (action = 'register'),
    before_state_sha256 CHAR(64),
    after_state_sha256 CHAR(64) NOT NULL CHECK (after_state_sha256 ~ '^[0-9a-f]{64}$'),
    changed_fields JSONB NOT NULL CHECK (
        jsonb_typeof(changed_fields) = 'array'
        AND jsonb_array_length(changed_fields) > 0
    ),
    authorization_capability TEXT NOT NULL
        CHECK (authorization_capability = 'registry_writer'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    mcp_session_sha256 CHAR(64) CHECK (
        mcp_session_sha256 IS NULL OR mcp_session_sha256 ~ '^[0-9a-f]{64}$'
    ),
    mcp_request_sha256 CHAR(64) CHECK (
        mcp_request_sha256 IS NULL OR mcp_request_sha256 ~ '^[0-9a-f]{64}$'
    ),
    mcp_client_identity_status TEXT NOT NULL
        CHECK (mcp_client_identity_status = 'unavailable'),
    operator_credential_label TEXT NOT NULL
        CHECK (operator_credential_label = 'core_operator_token'),
    FOREIGN KEY (candidate_id, content_sha256)
        REFERENCES mcp_saas.program_candidate_records (candidate_id, content_sha256),
    CHECK (before_state_sha256 IS NULL OR before_state_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (after_state_sha256 = content_sha256)
);

COMMENT ON TABLE mcp_saas.program_candidate_registration_events IS
    'Append-only, privacy-safe operator receipt for candidate registration; legacy candidates have no fabricated event.';

CREATE OR REPLACE FUNCTION mcp_saas.reject_program_candidate_registration_event_mutation()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    RAISE EXCEPTION 'Program candidate registration events are immutable.'
        USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_immutable_program_candidate_registration_event
    ON mcp_saas.program_candidate_registration_events;
CREATE TRIGGER trg_immutable_program_candidate_registration_event
BEFORE UPDATE OR DELETE ON mcp_saas.program_candidate_registration_events
FOR EACH ROW EXECUTE FUNCTION mcp_saas.reject_program_candidate_registration_event_mutation();

DO $$
DECLARE
    api_role text;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['mcp_saas_core_api', 'mcp_saas_data_api'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format(
                'GRANT SELECT, INSERT ON mcp_saas.program_candidate_registration_events TO %I',
                api_role
            );
        END IF;
    END LOOP;
END $$;
