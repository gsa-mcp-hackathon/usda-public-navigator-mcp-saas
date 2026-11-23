-- Persist a stable caller transaction ID and explicit approval reference for
-- new operator candidate decisions. Existing rows retain a server-generated
-- decision ID as their legacy transaction ID; approval references stay NULL.
ALTER TABLE mcp_saas.program_candidate_review_decisions
    ADD COLUMN IF NOT EXISTS transaction_id UUID,
    ADD COLUMN IF NOT EXISTS approval_reference TEXT;

UPDATE mcp_saas.program_candidate_review_decisions
SET transaction_id = decision_id
WHERE transaction_id IS NULL;

ALTER TABLE mcp_saas.program_candidate_review_decisions
    ALTER COLUMN transaction_id SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_program_candidate_decision_transaction
    ON mcp_saas.program_candidate_review_decisions (transaction_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'mcp_saas.program_candidate_review_decisions'::regclass
          AND conname = 'program_candidate_approval_reference_check'
    ) THEN
        ALTER TABLE mcp_saas.program_candidate_review_decisions
            ADD CONSTRAINT program_candidate_approval_reference_check
            CHECK (approval_reference IS NULL OR
                   (length(btrim(approval_reference)) BETWEEN 1 AND 512 AND
                    approval_reference !~ '[[:cntrl:]]'));
    END IF;
END $$;

COMMENT ON COLUMN mcp_saas.program_candidate_review_decisions.transaction_id IS
    'Stable operator transaction UUID used to make an exact decision replay idempotent.';
COMMENT ON COLUMN mcp_saas.program_candidate_review_decisions.approval_reference IS
    'Explicit approval reference supplied for the decision; NULL marks a legacy decision without one.';
