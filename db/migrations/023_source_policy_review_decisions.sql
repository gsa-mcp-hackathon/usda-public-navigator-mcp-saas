-- Record one append-only, digest-bound review outcome per candidate version.
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
    UNIQUE (manifest_id, manifest_version),
    FOREIGN KEY (manifest_id, manifest_version, content_sha256)
        REFERENCES mcp_saas.source_policy_manifests
            (manifest_id, manifest_version, content_sha256)
);

COMMENT ON TABLE mcp_saas.source_policy_review_decisions IS
    'One immutable, exact-digest approval or rejection per candidate version. Records the writer capability, not a person identity; caller-supplied actor identity is rejected.';
