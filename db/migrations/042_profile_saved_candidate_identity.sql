-- Keep the selected program reference separate from its immutable source snapshot.
-- Existing selected-interest rows remain readable with NULL candidate metadata.
ALTER TABLE mcp_saas.profile_saved_interests
    ADD COLUMN IF NOT EXISTS candidate_ref text
        CHECK (candidate_ref IS NULL OR candidate_ref ~ '^candidate-ref-[0-9a-f]{32}$'),
    ADD COLUMN IF NOT EXISTS candidate_id text
        CHECK (candidate_id IS NULL OR candidate_id ~ '^candidate-[0-9a-f]{32}$'),
    ADD COLUMN IF NOT EXISTS candidate_identity_basis text
        CHECK (candidate_identity_basis IS NULL OR candidate_identity_basis IN (
            'publisher_program_number', 'canonical_url', 'source_record_id'
        )),
    ADD COLUMN IF NOT EXISTS source_record_id text
        CHECK (source_record_id IS NULL OR length(source_record_id) BETWEEN 1 AND 200),
    ADD COLUMN IF NOT EXISTS source_version text
        CHECK (source_version IS NULL OR length(source_version) BETWEEN 1 AND 120),
    ADD COLUMN IF NOT EXISTS source_content_sha256 char(64)
        CHECK (source_content_sha256 IS NULL OR source_content_sha256 ~ '^[a-f0-9]{64}$');

ALTER TABLE mcp_saas.profile_saved_interest_history
    ADD COLUMN IF NOT EXISTS candidate_ref text
        CHECK (candidate_ref IS NULL OR candidate_ref ~ '^candidate-ref-[0-9a-f]{32}$'),
    ADD COLUMN IF NOT EXISTS candidate_id text
        CHECK (candidate_id IS NULL OR candidate_id ~ '^candidate-[0-9a-f]{32}$'),
    ADD COLUMN IF NOT EXISTS candidate_identity_basis text
        CHECK (candidate_identity_basis IS NULL OR candidate_identity_basis IN (
            'publisher_program_number', 'canonical_url', 'source_record_id'
        )),
    ADD COLUMN IF NOT EXISTS source_record_id text
        CHECK (source_record_id IS NULL OR length(source_record_id) BETWEEN 1 AND 200),
    ADD COLUMN IF NOT EXISTS source_version text
        CHECK (source_version IS NULL OR length(source_version) BETWEEN 1 AND 120),
    ADD COLUMN IF NOT EXISTS source_content_sha256 char(64)
        CHECK (source_content_sha256 IS NULL OR source_content_sha256 ~ '^[a-f0-9]{64}$');

COMMENT ON COLUMN mcp_saas.profile_saved_interests.candidate_ref IS
    'Stable source-scoped candidate identity; not an authorization token or snapshot digest.';
COMMENT ON COLUMN mcp_saas.profile_saved_interests.candidate_id IS
    'Immutable normalized source snapshot identity, distinct from candidate_ref.';
COMMENT ON COLUMN mcp_saas.profile_saved_interest_history.candidate_ref IS
    'Stable source-scoped candidate identity captured at each selected-interest revision.';
COMMENT ON COLUMN mcp_saas.profile_saved_interest_history.candidate_id IS
    'Immutable normalized source snapshot identity captured at each selected-interest revision.';
