-- Dedicated synthetic connectivity probe table; no application catalog contract changes.
CREATE TABLE IF NOT EXISTS mcp_saas.vm_data_api_probe_records (
    probe_id UUID PRIMARY KEY,
    payload JSONB NOT NULL CHECK (
        payload->>'kind' = 'synthetic-connectivity-probe'
        AND payload ? 'label'
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Immutable Layer 5 output records, including synthetic v2 report inputs.
CREATE TABLE IF NOT EXISTS mcp_saas.eligibility_requirements_packages (
    package_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL CHECK (tenant_id <> ''),
    definition JSONB NOT NULL CHECK (
        definition->>'contract_version' IS NOT NULL
        AND
        definition->>'contract_version' IN (
            'eligibility-requirements-package-v1',
            'eligibility-requirements-package-v2'
        )
        AND definition->>'package_id' = package_id::text
        AND definition->>'tenant_id' = tenant_id
        AND definition->>'official_eligibility_determination' = 'false'
        AND definition->>'official_application_submitted' = 'false'
        AND NOT (definition ? 'profile_id')
        AND NOT (definition ? 'profile_payload')
        AND (
            definition->>'contract_version' <> 'eligibility-requirements-package-v2'
            OR COALESCE((
                jsonb_typeof(definition->'program_title') = 'string'
                AND length(btrim(definition->>'program_title')) > 0
                AND jsonb_typeof(definition->'profile_snapshot') = 'object'
                AND jsonb_typeof(definition->'evaluation') = 'object'
            ), FALSE)
        )
    ),
    content_sha256 CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE mcp_saas.eligibility_requirements_packages IS
    'Immutable source-grounded sandbox reports. V2 includes the synthetic profile snapshot and evaluation used to render a user-ready report; it is not an official determination or application submission.';

-- Append-only package revisions keep the original report and each enrichment
-- intact while exposing a compare-and-set current version to clients.
CREATE TABLE IF NOT EXISTS mcp_saas.eligibility_requirements_package_revisions (
    package_id UUID NOT NULL REFERENCES mcp_saas.eligibility_requirements_packages(package_id)
        ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK (revision >= 2),
    definition JSONB NOT NULL CHECK (
        definition->>'package_id' = package_id::text
        AND definition->>'official_eligibility_determination' = 'false'
        AND definition->>'official_application_submitted' = 'false'
    ),
    details_sha256 CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (package_id, revision)
);

-- Immutable, digest-pinned source and criteria candidates awaiting review.
CREATE TABLE IF NOT EXISTS mcp_saas.source_policy_manifests (
    manifest_id TEXT NOT NULL CHECK (manifest_id ~ '^[a-z][a-z0-9.-]{0,127}$'),
    manifest_version TEXT NOT NULL CHECK (
        manifest_version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'
    ),
    candidate_id TEXT NOT NULL CHECK (candidate_id ~ '^[a-z][a-z0-9.-]{0,127}$'),
    content_sha256 CHAR(64) NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    manifest JSONB NOT NULL CHECK (
        jsonb_typeof(manifest) = 'object'
        AND manifest->>'manifest_id' = manifest_id
        AND manifest->>'manifest_version' = manifest_version
        AND manifest->>'candidate_id' = candidate_id
        AND manifest->>'review_status' = 'pending_review'
        AND manifest->'approval' = 'null'::jsonb
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (manifest_id, manifest_version)
);

COMMENT ON TABLE mcp_saas.source_policy_manifests IS
    'Append-only, digest-pinned source/criteria candidate manifests. Review decisions and activation are separate lifecycle records.';

CREATE TABLE IF NOT EXISTS mcp_saas.program_candidate_records (
    candidate_id TEXT PRIMARY KEY CHECK (
        candidate_id ~ '^candidate-[0-9a-f]{32}$'
    ),
    content_sha256 CHAR(64) NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    candidate JSONB NOT NULL CONSTRAINT program_candidate_records_candidate_check CHECK (
        jsonb_typeof(candidate) = 'object'
        AND candidate->>'contract_version' IN (
            'program-candidate-v1', 'program-candidate-v2', 'program-candidate-v3'
        )
        AND candidate->>'candidate_id' = candidate_id
        AND candidate->>'status' = 'candidate_ready_for_review'
        AND candidate->'governance'->>'review_status' = 'review_required'
        AND candidate->'governance'->>'catalog_status' = 'not_published'
        AND candidate->'governance'->>'approval_status' = 'not_reviewed'
        AND candidate->'governance'->>'persistence_performed' = 'false'
        AND candidate->'governance'->'eligibility_outcome' = 'null'::jsonb
        AND (
            candidate->>'contract_version' <> 'program-candidate-v3'
            OR (
                jsonb_typeof(candidate->'program_id') = 'string'
                AND candidate->>'program_id' ~ '^[a-z0-9][a-z0-9._-]{1,99}$'
                AND jsonb_typeof(candidate->'program_identity_basis') = 'string'
                AND candidate->>'program_identity_basis' IN (
                    'selected_catalog_program_id', 'publisher_program_number',
                    'canonical_url', 'source_record_id'
                )
            )
        )
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE mcp_saas.program_candidate_records IS
    'Immutable, digest-addressed public program candidates awaiting review; these rows are not active catalog inventory.';

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

CREATE TABLE IF NOT EXISTS mcp_saas.program_candidate_review_decisions (
    decision_id UUID PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    content_sha256 CHAR(64) NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
    rationale TEXT NOT NULL CHECK (length(btrim(rationale)) BETWEEN 1 AND 2048),
    authorization_capability TEXT NOT NULL
        CHECK (authorization_capability = 'registry_writer'),
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    mcp_session_sha256 CHAR(64),
    mcp_request_sha256 CHAR(64),
    mcp_client_identity_status TEXT,
    operator_credential_label TEXT,
    transaction_id UUID NOT NULL UNIQUE,
    approval_reference TEXT,
    CONSTRAINT program_candidate_review_session_hash_check
        CHECK (mcp_session_sha256 IS NULL OR mcp_session_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT program_candidate_review_request_hash_check
        CHECK (mcp_request_sha256 IS NULL OR mcp_request_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT program_candidate_review_client_identity_check
        CHECK (mcp_client_identity_status IS NULL
            OR mcp_client_identity_status = 'unavailable'),
    CONSTRAINT program_candidate_review_operator_label_check
        CHECK (operator_credential_label IS NULL
            OR operator_credential_label = 'core_operator_token'),
    CONSTRAINT program_candidate_approval_reference_check
        CHECK (approval_reference IS NULL OR
            (length(btrim(approval_reference)) BETWEEN 1 AND 512
             AND approval_reference !~ '[[:cntrl:]]')),
    UNIQUE (candidate_id),
    FOREIGN KEY (candidate_id, content_sha256)
        REFERENCES mcp_saas.program_candidate_records (candidate_id, content_sha256)
);
COMMENT ON TABLE mcp_saas.program_candidate_review_decisions IS
    'One immutable exact-digest approve/reject decision per pending public-program candidate.';

CREATE UNIQUE INDEX IF NOT EXISTS uq_source_policy_manifest_digest
    ON mcp_saas.source_policy_manifests
    (manifest_id, manifest_version, content_sha256);

CREATE TABLE IF NOT EXISTS mcp_saas.source_policy_review_decisions (
    decision_id UUID PRIMARY KEY,
    manifest_id TEXT NOT NULL,
    manifest_version TEXT NOT NULL,
    content_sha256 CHAR(64) NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
    rationale TEXT NOT NULL CHECK (length(btrim(rationale)) BETWEEN 1 AND 2048),
    authorization_capability TEXT NOT NULL
        CHECK (authorization_capability = 'registry_writer'),
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    mcp_session_sha256 CHAR(64),
    mcp_request_sha256 CHAR(64),
    mcp_client_identity_status TEXT,
    operator_credential_label TEXT,
    CONSTRAINT source_policy_review_session_hash_check
        CHECK (mcp_session_sha256 IS NULL OR mcp_session_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT source_policy_review_request_hash_check
        CHECK (mcp_request_sha256 IS NULL OR mcp_request_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT source_policy_review_client_identity_check
        CHECK (mcp_client_identity_status IS NULL
            OR mcp_client_identity_status = 'unavailable'),
    CONSTRAINT source_policy_review_operator_label_check
        CHECK (operator_credential_label IS NULL
            OR operator_credential_label = 'core_operator_token'),
    UNIQUE (manifest_id, manifest_version),
    FOREIGN KEY (manifest_id, manifest_version, content_sha256)
        REFERENCES mcp_saas.source_policy_manifests
            (manifest_id, manifest_version, content_sha256)
);

COMMENT ON TABLE mcp_saas.source_policy_review_decisions IS
    'One immutable, exact-digest approval or rejection per candidate version. Records the writer capability, not a person identity; caller-supplied actor identity is rejected.';

-- Keep initial schema provisioning aligned with migration 027.
CREATE UNIQUE INDEX IF NOT EXISTS uq_public_dataset_record_exact_content
    ON mcp_saas.public_dataset_records
        (dataset_id, source_record_id, content_hash);

CREATE TABLE IF NOT EXISTS mcp_saas.program_candidate_publications (
    publication_key CHAR(64) NOT NULL CHECK (publication_key ~ '^[0-9a-f]{64}$'),
    candidate_id TEXT NOT NULL,
    candidate_content_sha256 CHAR(64) NOT NULL
        CHECK (candidate_content_sha256 ~ '^[0-9a-f]{64}$'),
    dataset_id TEXT NOT NULL REFERENCES mcp_saas.public_datasets(dataset_id),
    source_record_id TEXT NOT NULL,
    record_content_sha256 TEXT NOT NULL
        CHECK (record_content_sha256 ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (dataset_id, source_record_id),
    UNIQUE (publication_key, candidate_id, candidate_content_sha256),
    UNIQUE (candidate_id, candidate_content_sha256, dataset_id),
    FOREIGN KEY (candidate_id, candidate_content_sha256)
        REFERENCES mcp_saas.program_candidate_records(candidate_id, content_sha256),
    FOREIGN KEY (dataset_id, source_record_id, record_content_sha256)
        REFERENCES mcp_saas.public_dataset_records
            (dataset_id, source_record_id, content_hash)
);

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
    'Append-only audit receipts for governed source/dataset/record-jurisdiction metadata compare-and-set operations; contains hashes, IDs, and bounded jurisdiction readback only, never source bodies or profile values.';

CREATE OR REPLACE FUNCTION mcp_saas.reject_published_candidate_record_mutation()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM mcp_saas.program_candidate_publications AS publication
        WHERE publication.dataset_id = OLD.dataset_id
          AND publication.source_record_id = OLD.source_record_id
    ) THEN
        RAISE EXCEPTION 'Published program candidate records are immutable.'
            USING ERRCODE = '55000';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_immutable_published_candidate_record
    ON mcp_saas.public_dataset_records;
CREATE TRIGGER trg_immutable_published_candidate_record
BEFORE UPDATE OR DELETE ON mcp_saas.public_dataset_records
FOR EACH ROW EXECUTE FUNCTION mcp_saas.reject_published_candidate_record_mutation();

CREATE TABLE IF NOT EXISTS mcp_saas.program_candidate_publication_events (
    generation BIGINT PRIMARY KEY CHECK (generation > 0),
    request_id UUID NOT NULL UNIQUE,
    publication_key CHAR(64) NOT NULL CHECK (publication_key ~ '^[0-9a-f]{64}$'),
    action TEXT NOT NULL CHECK (action IN ('activate', 'deactivate', 'rollback')),
    expected_candidate_id TEXT,
    expected_candidate_content_sha256 CHAR(64),
    target_candidate_id TEXT,
    target_candidate_content_sha256 CHAR(64),
    rationale TEXT NOT NULL CHECK (length(btrim(rationale)) BETWEEN 1 AND 2048),
    authorization_capability TEXT NOT NULL
        CHECK (authorization_capability = 'registry_writer'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    mcp_session_sha256 CHAR(64),
    mcp_request_sha256 CHAR(64),
    mcp_client_identity_status TEXT,
    operator_credential_label TEXT,
    CONSTRAINT program_publication_session_hash_check
        CHECK (mcp_session_sha256 IS NULL OR mcp_session_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT program_publication_request_hash_check
        CHECK (mcp_request_sha256 IS NULL OR mcp_request_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT program_publication_client_identity_check
        CHECK (mcp_client_identity_status IS NULL
            OR mcp_client_identity_status = 'unavailable'),
    CONSTRAINT program_publication_operator_label_check
        CHECK (operator_credential_label IS NULL
            OR operator_credential_label = 'core_operator_token'),
    CHECK (
        (expected_candidate_id IS NULL AND expected_candidate_content_sha256 IS NULL)
        OR (expected_candidate_id IS NOT NULL
            AND expected_candidate_content_sha256 ~ '^[0-9a-f]{64}$')
    ),
    CHECK (
        (action = 'deactivate' AND target_candidate_id IS NULL
            AND target_candidate_content_sha256 IS NULL)
        OR (action IN ('activate', 'rollback') AND target_candidate_id IS NOT NULL
            AND target_candidate_content_sha256 ~ '^[0-9a-f]{64}$')
    ),
    FOREIGN KEY (publication_key, expected_candidate_id,
                 expected_candidate_content_sha256)
        REFERENCES mcp_saas.program_candidate_publications
            (publication_key, candidate_id, candidate_content_sha256),
    FOREIGN KEY (publication_key, target_candidate_id,
                 target_candidate_content_sha256)
        REFERENCES mcp_saas.program_candidate_publications
            (publication_key, candidate_id, candidate_content_sha256)
);

CREATE OR REPLACE VIEW mcp_saas.active_public_dataset_records AS
SELECT record.*
FROM mcp_saas.public_dataset_records AS record
LEFT JOIN mcp_saas.program_candidate_publications AS publication
  ON publication.dataset_id = record.dataset_id
 AND publication.source_record_id = record.source_record_id
LEFT JOIN LATERAL (
    SELECT event.action, event.target_candidate_id,
           event.target_candidate_content_sha256
    FROM mcp_saas.program_candidate_publication_events AS event
    WHERE event.publication_key = publication.publication_key
    ORDER BY event.generation DESC
    LIMIT 1
) AS active ON publication.publication_key IS NOT NULL
WHERE (
    record.source_metadata ? 'program_candidate_id'
    AND publication.publication_key IS NOT NULL
    AND active.action IN ('activate', 'rollback')
    AND active.target_candidate_id = publication.candidate_id
    AND active.target_candidate_content_sha256 =
        publication.candidate_content_sha256
    AND record.content_hash = publication.record_content_sha256
    AND record.source_metadata->>'program_candidate_id' = publication.candidate_id
    AND record.source_metadata->>'program_candidate_sha256' =
        publication.candidate_content_sha256
    AND record.source_metadata->>'program_candidate_publication_key' =
        publication.publication_key
)
OR NOT (record.source_metadata ? 'program_candidate_id');
