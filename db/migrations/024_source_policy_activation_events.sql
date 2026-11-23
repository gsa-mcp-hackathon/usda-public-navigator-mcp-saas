-- Append-only lifecycle events select an already approved source-policy digest.
CREATE TABLE IF NOT EXISTS mcp_saas.source_policy_activation_events (
    generation BIGINT PRIMARY KEY CHECK (generation > 0),
    request_id UUID NOT NULL UNIQUE,
    action TEXT NOT NULL CHECK (action IN ('activate', 'deactivate', 'rollback')),
    expected_manifest_id TEXT,
    expected_manifest_version TEXT,
    expected_content_sha256 CHAR(64),
    target_manifest_id TEXT,
    target_manifest_version TEXT,
    target_content_sha256 CHAR(64),
    rationale TEXT NOT NULL CHECK (length(btrim(rationale)) BETWEEN 1 AND 2048),
    authorization_capability TEXT NOT NULL
        CHECK (authorization_capability = 'registry_writer'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        (expected_manifest_id IS NULL
            AND expected_manifest_version IS NULL
            AND expected_content_sha256 IS NULL)
        OR
        (expected_manifest_id IS NOT NULL
            AND expected_manifest_version IS NOT NULL
            AND expected_content_sha256 ~ '^[0-9a-f]{64}$')
    ),
    CHECK (
        (action = 'deactivate'
            AND target_manifest_id IS NULL
            AND target_manifest_version IS NULL
            AND target_content_sha256 IS NULL)
        OR
        (action IN ('activate', 'rollback')
            AND target_manifest_id IS NOT NULL
            AND target_manifest_version IS NOT NULL
            AND target_content_sha256 ~ '^[0-9a-f]{64}$')
    ),
    FOREIGN KEY (
        expected_manifest_id, expected_manifest_version, expected_content_sha256
    ) REFERENCES mcp_saas.source_policy_manifests (
        manifest_id, manifest_version, content_sha256
    ),
    FOREIGN KEY (
        target_manifest_id, target_manifest_version, target_content_sha256
    ) REFERENCES mcp_saas.source_policy_manifests (
        manifest_id, manifest_version, content_sha256
    )
);

COMMENT ON TABLE mcp_saas.source_policy_activation_events IS
    'Append-only, compare-and-set lifecycle events for approved source-policy digests. Effective active state is the event with the greatest generation; no event or a deactivate event means no active policy.';
