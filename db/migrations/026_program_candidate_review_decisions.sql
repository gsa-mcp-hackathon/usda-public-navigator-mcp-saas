-- Record one immutable, exact-digest review decision per pending program candidate.
CREATE UNIQUE INDEX IF NOT EXISTS uq_program_candidate_digest
    ON mcp_saas.program_candidate_records (candidate_id, content_sha256);

CREATE TABLE IF NOT EXISTS mcp_saas.program_candidate_review_decisions (
    decision_id UUID PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    content_sha256 CHAR(64) NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
    rationale TEXT NOT NULL CHECK (length(btrim(rationale)) BETWEEN 1 AND 2048),
    authorization_capability TEXT NOT NULL
        CHECK (authorization_capability = 'registry_writer'),
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (candidate_id),
    FOREIGN KEY (candidate_id, content_sha256)
        REFERENCES mcp_saas.program_candidate_records (candidate_id, content_sha256)
);

COMMENT ON TABLE mcp_saas.program_candidate_review_decisions IS
    'One immutable exact-digest approve/reject decision per pending public-program candidate. Records the service capability, not a person identity; Core enforces operator authorization.';
