-- Deterministic, synthetic-only fixtures; canonical IDs resolve in core-v1.json.
INSERT INTO mcp_saas.program_catalog (
    tenant_id, cluster_id, program_id, title, source_id, source_url,
    jurisdiction, catalog_version, schema_version, attributes, provenance, updated_at
)
VALUES
    (
        'federal-agency', 'eligibility', 'federal-farm-sample',
        'Synthetic Federal Farm Support', 'synthetic-federal-fixture', NULL,
        'federal', 'demo-v1', 'core-v1',
        '{"core.program.category":"agriculture"}',
        '{"record_type":"synthetic","seed_version":"demo-v1"}',
        '2026-10-13T00:00:00Z'
    ),
    (
        'state-kansas', 'eligibility', 'ks-senior-sample',
        'Synthetic Kansas Senior Support', 'synthetic-kansas-fixture', NULL,
        'state', 'demo-v1', 'core-v1',
        '{"core.program.category":"senior_support","core.geography.state_code":"KS"}',
        '{"record_type":"synthetic","seed_version":"demo-v1"}',
        '2026-10-13T00:00:00Z'
    )
ON CONFLICT (tenant_id, program_id) DO UPDATE SET
    cluster_id = EXCLUDED.cluster_id,
    title = EXCLUDED.title,
    source_id = EXCLUDED.source_id,
    source_url = EXCLUDED.source_url,
    jurisdiction = EXCLUDED.jurisdiction,
    catalog_version = EXCLUDED.catalog_version,
    schema_version = EXCLUDED.schema_version,
    attributes = EXCLUDED.attributes,
    provenance = EXCLUDED.provenance,
    updated_at = EXCLUDED.updated_at;

INSERT INTO mcp_saas.synthetic_profiles (
    profile_id, profile_kind, schema_version, provenance, created_at
)
VALUES
    ('demo-farmer-001', 'person', 'core-v1',
     '{"record_type":"synthetic","seed_version":"demo-v1"}', '2026-10-13T00:00:00Z'),
    ('demo-senior-001', 'person', 'core-v1',
     '{"record_type":"synthetic","seed_version":"demo-v1"}', '2026-10-13T00:00:00Z'),
    ('demo-multidimension-001', 'person', 'core-v1',
     '{"record_type":"synthetic","seed_version":"demo-v1"}', '2026-10-13T00:00:00Z'),
    ('demo-household-001', 'household', 'core-v1',
     '{"record_type":"synthetic","seed_version":"demo-v1"}', '2026-10-13T00:00:00Z')
ON CONFLICT (profile_id) DO UPDATE SET
    profile_kind = EXCLUDED.profile_kind,
    schema_version = EXCLUDED.schema_version,
    provenance = EXCLUDED.provenance,
    created_at = EXCLUDED.created_at;

-- Replace only this seed's owned rows so reruns exactly reproduce its values and links.
DELETE FROM mcp_saas.profile_relationships
WHERE subject_profile_id IN (
    'demo-farmer-001', 'demo-senior-001', 'demo-multidimension-001', 'demo-household-001'
);
DELETE FROM mcp_saas.profile_attribute_values
WHERE profile_id IN (
    'demo-farmer-001', 'demo-senior-001', 'demo-multidimension-001', 'demo-household-001'
);

INSERT INTO mcp_saas.profile_attribute_values (
    profile_id, attribute_id, dimension_id, value_type, value, source_id,
    schema_version, provenance, observed_at
)
VALUES
    ('demo-farmer-001', 'core.person.age_band', 'core.person', 'string', '"adult"', 'synthetic-demo-v1', 'core-v1', '{"record_type":"synthetic"}', '2026-10-13T00:00:00Z'),
    ('demo-farmer-001', 'core.farm_business.operation_type', 'core.farm_business', 'string', '"row_crop"', 'synthetic-demo-v1', 'core-v1', '{"record_type":"synthetic"}', '2026-10-13T00:00:00Z'),
    ('demo-farmer-001', 'core.geography.state_code', 'core.geography', 'string', '"KS"', 'synthetic-demo-v1', 'core-v1', '{"record_type":"synthetic"}', '2026-10-13T00:00:00Z'),
    ('demo-senior-001', 'core.person.age_band', 'core.person', 'string', '"65_plus"', 'synthetic-demo-v1', 'core-v1', '{"record_type":"synthetic"}', '2026-10-13T00:00:00Z'),
    ('demo-senior-001', 'core.household.size_band', 'core.household', 'string', '"single"', 'synthetic-demo-v1', 'core-v1', '{"record_type":"synthetic"}', '2026-10-13T00:00:00Z'),
    ('demo-senior-001', 'core.geography.state_code', 'core.geography', 'string', '"MD"', 'synthetic-demo-v1', 'core-v1', '{"record_type":"synthetic"}', '2026-10-13T00:00:00Z'),
    ('demo-multidimension-001', 'core.person.age_band', 'core.person', 'string', '"adult"', 'synthetic-demo-v1', 'core-v1', '{"record_type":"synthetic"}', '2026-10-13T00:00:00Z'),
    ('demo-multidimension-001', 'core.household.housing_status', 'core.household', 'string', '"owner"', 'synthetic-demo-v1', 'core-v1', '{"record_type":"synthetic"}', '2026-10-13T00:00:00Z'),
    ('demo-multidimension-001', 'core.farm_business.operation_type', 'core.farm_business', 'string', '"small_farm"', 'synthetic-demo-v1', 'core-v1', '{"record_type":"synthetic"}', '2026-10-13T00:00:00Z'),
    ('demo-multidimension-001', 'core.geography.state_code', 'core.geography', 'string', '"NM"', 'synthetic-demo-v1', 'core-v1', '{"record_type":"synthetic"}', '2026-10-13T00:00:00Z'),
    ('demo-household-001', 'core.household.size_band', 'core.household', 'string', '"small"', 'synthetic-demo-v1', 'core-v1', '{"record_type":"synthetic"}', '2026-10-13T00:00:00Z');

INSERT INTO mcp_saas.profile_relationships (
    subject_profile_id, relationship_id, object_profile_id, provenance, created_at
)
VALUES (
    'demo-farmer-001', 'core.relationship.member_of_household', 'demo-household-001',
    '{"record_type":"synthetic","seed_version":"demo-v1"}', '2026-10-13T00:00:00Z'
);
