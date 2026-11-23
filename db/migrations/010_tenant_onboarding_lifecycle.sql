-- Tenant-owned catalog semantics and operation bindings remain references to
-- shared datasets, namespace metadata, and a pattern-owned template contract.
ALTER TABLE mcp_saas.tenant_registry
    DROP CONSTRAINT IF EXISTS tenant_registry_lifecycle_state_check;

ALTER TABLE mcp_saas.tenant_registry
    ADD CONSTRAINT tenant_registry_lifecycle_state_check
        CHECK (lifecycle_state IN ('pending', 'active', 'inactive', 'deprecated'));

-- New generic tenants reference the C2 package registry. NOT VALID preserves
-- older environment-specific registrations while enforcing all new writes.
ALTER TABLE mcp_saas.tenant_registry
    DROP CONSTRAINT IF EXISTS tenant_registry_pattern_id_pattern_version_fkey;

ALTER TABLE mcp_saas.tenant_registry
    DROP CONSTRAINT IF EXISTS tenant_registry_template_package_fkey;

ALTER TABLE mcp_saas.tenant_registry
    ADD CONSTRAINT tenant_registry_template_package_fkey
        FOREIGN KEY (pattern_id, pattern_version)
        REFERENCES mcp_saas.pattern_template_packages (template_id, template_version)
        NOT VALID;

ALTER TABLE mcp_saas.tenant_registry
    ADD COLUMN IF NOT EXISTS tenant_spec_sha256 text
        CHECK (tenant_spec_sha256 IS NULL OR tenant_spec_sha256 ~ '^[0-9a-f]{64}$');

ALTER TABLE mcp_saas.tenant_registry
    ADD COLUMN IF NOT EXISTS tenant_context jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(tenant_context) = 'object');

ALTER TABLE mcp_saas.pattern_catalog_bindings
    ADD COLUMN IF NOT EXISTS catalog_namespace text;

UPDATE mcp_saas.pattern_catalog_bindings
SET catalog_namespace = binding_context->>'catalog_namespace'
WHERE catalog_namespace IS NULL
  AND binding_context ? 'catalog_namespace';

ALTER TABLE mcp_saas.pattern_catalog_bindings
    DROP CONSTRAINT IF EXISTS pattern_catalog_bindings_catalog_namespace_check;

ALTER TABLE mcp_saas.pattern_catalog_bindings
    ADD CONSTRAINT pattern_catalog_bindings_catalog_namespace_check
        CHECK (catalog_namespace IS NULL OR catalog_namespace ~ '^[a-z][a-z0-9_-]{0,63}([.][a-z][a-z0-9_-]{0,63})*$');

DROP INDEX IF EXISTS mcp_saas.uq_pattern_catalog_binding_identity;

CREATE UNIQUE INDEX IF NOT EXISTS uq_pattern_catalog_binding_identity
    ON mcp_saas.pattern_catalog_bindings
       (dataset_id, cluster_id, tenant_id,
        (COALESCE(pattern_version, '')),
        (COALESCE(catalog_namespace, '')));

COMMENT ON COLUMN mcp_saas.tenant_registry.tenant_spec_sha256 IS
    'Digest of the exact approved tenant onboarding specification; the private endpoint and specification payload are not exposed in Core inventory.';
COMMENT ON COLUMN mcp_saas.tenant_registry.tenant_context IS
    'Small tenant-owned jurisdiction context; shared source records and namespace semantics remain in their normalized catalog binding records.';
COMMENT ON COLUMN mcp_saas.pattern_catalog_bindings.catalog_namespace IS
    'Pattern-owned, tenant-scoped semantic namespace; one shared dataset can be reused under distinct namespace contexts.';
