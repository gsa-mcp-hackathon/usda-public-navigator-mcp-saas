-- Keep stable cluster identity separate from pattern release version.
ALTER TABLE mcp_saas.pattern_catalog_bindings
    ADD COLUMN IF NOT EXISTS pattern_version text;

ALTER TABLE mcp_saas.pattern_catalog_bindings
    DROP CONSTRAINT IF EXISTS pattern_catalog_bindings_pkey;

ALTER TABLE mcp_saas.pattern_catalog_bindings
    DROP CONSTRAINT IF EXISTS pattern_catalog_bindings_pattern_version_check;

ALTER TABLE mcp_saas.pattern_catalog_bindings
    ADD CONSTRAINT pattern_catalog_bindings_pattern_version_check
    CHECK (pattern_version IS NULL OR pattern_version ~ '^[0-9]+\.[0-9]+\.[0-9]+$');

-- Preserve versioned coexistence while giving legacy unversioned rows one stable key.
CREATE UNIQUE INDEX IF NOT EXISTS uq_pattern_catalog_binding_identity
    ON mcp_saas.pattern_catalog_bindings
       (dataset_id, cluster_id, tenant_id, (COALESCE(pattern_version, '')));

-- Eligibility 0.1.0 used a version-bearing cluster ID in the initial sandbox load.
-- Copy its bindings to the stable ID, then remove only those obsolete binding rows.
INSERT INTO mcp_saas.pattern_catalog_bindings
    (dataset_id, cluster_id, pattern_version, tenant_id, binding_context)
SELECT dataset_id, 'eligibility', '0.1.0', tenant_id, binding_context
FROM mcp_saas.pattern_catalog_bindings
WHERE cluster_id = 'eligibility-v0'
ON CONFLICT (dataset_id, cluster_id, tenant_id, (COALESCE(pattern_version, '')))
DO UPDATE SET binding_context = EXCLUDED.binding_context;

DELETE FROM mcp_saas.pattern_catalog_bindings
WHERE cluster_id = 'eligibility-v0';

COMMENT ON COLUMN mcp_saas.pattern_catalog_bindings.cluster_id IS
    'Stable, versionless pattern cluster identifier.';
COMMENT ON COLUMN mcp_saas.pattern_catalog_bindings.pattern_version IS
    'SemVer release identity kept separate from cluster_id; NULL marks legacy data with unknown version.';
