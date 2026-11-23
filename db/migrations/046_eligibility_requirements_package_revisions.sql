-- Persist append-only, compare-and-set revisions for enriched requirement packages.
CREATE TABLE IF NOT EXISTS mcp_saas.eligibility_requirements_package_revisions (
    package_id UUID NOT NULL REFERENCES mcp_saas.eligibility_requirements_packages(package_id)
        ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK (revision >= 2),
    definition JSONB NOT NULL CHECK (
        definition->>'package_id' = package_id::text
        AND definition->>'official_eligibility_determination' = 'false'
        AND definition->>'official_application_submitted' = 'false'
    ),
    details_sha256 CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (package_id, revision)
);

COMMENT ON TABLE mcp_saas.eligibility_requirements_package_revisions IS
    'Append-only, digest-pinned revisions that enrich an immutable requirements package.';

REVOKE ALL ON mcp_saas.eligibility_requirements_package_revisions FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mcp_saas_core_api') THEN
        EXECUTE 'GRANT SELECT, INSERT ON mcp_saas.eligibility_requirements_package_revisions TO mcp_saas_core_api';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mcp_saas_data_api') THEN
        EXECUTE 'GRANT SELECT, INSERT ON mcp_saas.eligibility_requirements_package_revisions TO mcp_saas_data_api';
    END IF;
END
$$;
