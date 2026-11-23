-- The profile-specific report contract adds synthetic report inputs while
-- retaining the immutable package boundary.
ALTER TABLE mcp_saas.eligibility_requirements_packages
    DROP CONSTRAINT IF EXISTS eligibility_requirements_packages_check;
ALTER TABLE mcp_saas.eligibility_requirements_packages
    DROP CONSTRAINT IF EXISTS eligibility_requirements_packages_contract_check;

ALTER TABLE mcp_saas.eligibility_requirements_packages
    ADD CONSTRAINT eligibility_requirements_packages_contract_check CHECK (
        definition->>'contract_version' IS NOT NULL
        AND
        definition->>'contract_version' IN (
            'eligibility-requirements-package-v1',
            'eligibility-requirements-package-v2'
        )
        AND definition->>'package_id' = package_id::text
        AND definition->>'tenant_id' = tenant_id
        AND definition->>'official_eligibility_determination' = 'false'
        AND definition->>'official_application_submitted' = 'false'
        AND NOT (definition ? 'profile_id')
        AND NOT (definition ? 'profile_payload')
        AND (
            definition->>'contract_version' <> 'eligibility-requirements-package-v2'
            OR COALESCE((
                jsonb_typeof(definition->'program_title') = 'string'
                AND length(btrim(definition->>'program_title')) > 0
                AND jsonb_typeof(definition->'profile_snapshot') = 'object'
                AND jsonb_typeof(definition->'evaluation') = 'object'
            ), FALSE)
        )
    );

COMMENT ON TABLE mcp_saas.eligibility_requirements_packages IS
    'Immutable source-grounded sandbox reports. V2 includes the synthetic profile snapshot and evaluation used to render a user-ready report; it is not an official determination or application submission.';
