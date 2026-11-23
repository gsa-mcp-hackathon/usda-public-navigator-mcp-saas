-- Allow the unpublished candidate store to retain stable program identities.
ALTER TABLE mcp_saas.program_candidate_records
    DROP CONSTRAINT IF EXISTS program_candidate_records_check;

ALTER TABLE mcp_saas.program_candidate_records
    DROP CONSTRAINT IF EXISTS program_candidate_records_candidate_check;

ALTER TABLE mcp_saas.program_candidate_records
    ADD CONSTRAINT program_candidate_records_candidate_check CHECK (
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
    );
