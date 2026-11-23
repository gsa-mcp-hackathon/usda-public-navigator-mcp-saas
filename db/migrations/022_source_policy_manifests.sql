-- Immutable, digest-pinned source and criteria candidates awaiting review.
CREATE TABLE IF NOT EXISTS mcp_saas.source_policy_manifests (
    manifest_id text NOT NULL CHECK (manifest_id ~ '^[a-z][a-z0-9.-]{0,127}$'),
    manifest_version text NOT NULL CHECK (
        manifest_version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'
    ),
    candidate_id text NOT NULL CHECK (candidate_id ~ '^[a-z][a-z0-9.-]{0,127}$'),
    content_sha256 char(64) NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    manifest jsonb NOT NULL CHECK (
        jsonb_typeof(manifest) = 'object'
        AND manifest->>'manifest_id' = manifest_id
        AND manifest->>'manifest_version' = manifest_version
        AND manifest->>'candidate_id' = candidate_id
        AND manifest->>'review_status' = 'pending_review'
        AND manifest->'approval' = 'null'::jsonb
    ),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (manifest_id, manifest_version)
);

COMMENT ON TABLE mcp_saas.source_policy_manifests IS
    'Append-only, digest-pinned source/criteria candidate manifests. Review decisions and activation are separate lifecycle records.';
