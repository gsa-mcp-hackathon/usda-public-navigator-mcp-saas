-- source identities are public and reusable; records are populated by the
-- bounded Data API ingestion operation, never by Core or a tenant runtime.
-- The generic catalog mechanism stores one source record; Eligibility's
-- template defines this dataset's namespace and semantic projection.
INSERT INTO mcp_saas.public_data_sources
  (source_id, source_name, source_url, access_status, source_context)
VALUES
  ('usda.fsa.beginning-farmers', 'USDA FSA Beginning Farmers and Ranchers Loans',
   'https://www.fsa.usda.gov/resources/beginning-farmers-and-ranchers-loans',
   'available',
   '{"content_type":"public_program_guidance","retrieval_mode":"operator_triggered","scheduled_refresh":false}'),
  ('missouri.dss.snap', 'Missouri DSS Apply for SNAP',
   'https://dss.mo.gov/food-assistance/apply-for-snap', 'available',
   '{"content_type":"public_program_guidance","retrieval_mode":"operator_triggered","scheduled_refresh":false}')
ON CONFLICT (source_id) DO UPDATE SET
  source_name = EXCLUDED.source_name,
  source_url = EXCLUDED.source_url,
  access_status = EXCLUDED.access_status,
  source_context = EXCLUDED.source_context,
  updated_at = now();

INSERT INTO mcp_saas.public_datasets
  (dataset_id, source_id, source_dataset_id, title, landing_page, schema_version,
   namespace_ids, metadata)
VALUES
  ('usda-fsa-beginning-farmers-guidance', 'usda.fsa.beginning-farmers',
   'fsa-beginning-farmers-guidance', 'FSA Beginning Farmer classification guidance',
   'https://www.fsa.usda.gov/resources/beginning-farmers-and-ranchers-loans',
   'eligibility-program-v1', ARRAY['core','eligibility'],
   '{"catalog_namespace":"programs","content_type":"public_program_guidance","schema_registry_ids":{"core":"core-schema-registry","eligibility":"eligibility-program-schema"},"schema_versions":{"core":"core-v1","eligibility":"eligibility-program-v1"},"refresh_mode":"operator_triggered"}'),
  ('missouri-dss-snap-guidance', 'missouri.dss.snap',
   'missouri-snap-guidance', 'Missouri SNAP application guidance',
   'https://dss.mo.gov/food-assistance/apply-for-snap', 'eligibility-program-v1',
   ARRAY['core','eligibility'],
   '{"catalog_namespace":"programs","content_type":"public_program_guidance","schema_registry_ids":{"core":"core-schema-registry","eligibility":"eligibility-program-schema"},"schema_versions":{"core":"core-v1","eligibility":"eligibility-program-v1"},"refresh_mode":"operator_triggered"}')
ON CONFLICT (dataset_id) DO UPDATE SET
  source_id = EXCLUDED.source_id,
  source_dataset_id = EXCLUDED.source_dataset_id,
  title = EXCLUDED.title,
  landing_page = EXCLUDED.landing_page,
  schema_version = EXCLUDED.schema_version,
  namespace_ids = EXCLUDED.namespace_ids,
  metadata = EXCLUDED.metadata,
  updated_at = now();
