-- Persist immutable public program candidates without publishing them to a catalog.
CREATE TABLE IF NOT EXISTS mcp_saas.program_candidate_records (
    candidate_id TEXT PRIMARY KEY CHECK (
        candidate_id ~ '^candidate-[0-9a-f]{32}$'
    ),
    content_sha256 CHAR(64) NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    candidate JSONB NOT NULL CHECK (
        jsonb_typeof(candidate) = 'object'
        AND candidate->>'contract_version' = 'program-candidate-v1'
        AND candidate->>'candidate_id' = candidate_id
        AND candidate->>'status' = 'candidate_ready_for_review'
        AND candidate->'governance'->>'review_status' = 'review_required'
        AND candidate->'governance'->>'catalog_status' = 'not_published'
        AND candidate->'governance'->>'approval_status' = 'not_reviewed'
        AND candidate->'governance'->>'persistence_performed' = 'false'
        AND candidate->'governance'->'eligibility_outcome' = 'null'::jsonb
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE mcp_saas.program_candidate_records IS
    'Immutable, digest-addressed public program candidates awaiting review; these rows are not active catalog inventory.';
