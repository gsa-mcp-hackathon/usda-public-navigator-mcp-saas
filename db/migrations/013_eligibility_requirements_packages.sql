-- Immutable Layer 5 sandbox output; never store profiles or copied source data.
CREATE TABLE IF NOT EXISTS mcp_saas.eligibility_requirements_packages (
    package_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL CHECK (tenant_id <> ''),
    definition JSONB NOT NULL CHECK (
        definition->>'contract_version' = 'eligibility-requirements-package-v1'
        AND definition->>'package_id' = package_id::text
        AND definition->>'tenant_id' = tenant_id
        AND definition->>'official_eligibility_determination' = 'false'
        AND definition->>'official_application_submitted' = 'false'
        AND NOT (definition ? 'profile_id')
        AND NOT (definition ? 'profile_payload')
    ),
    content_sha256 CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE mcp_saas.eligibility_requirements_packages IS
    'Immutable source-grounded Layer 5 requirements packets for sandbox use only; excludes profile values and government application submissions.';
