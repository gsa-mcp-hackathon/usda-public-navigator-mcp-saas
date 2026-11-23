-- Add the narrowly reviewed Kansas Food Assistance residency source and its
-- reusable dataset identity. Tenant binding and live ingestion remain explicit
-- onboarding/ingestion actions; this migration does not alter tenant lifecycles.
INSERT INTO mcp_saas.public_data_sources
  (source_id, source_name, source_url, access_status, source_context)
VALUES
  ('kansas.dcf.food-assistance-residency',
   'Kansas DCF Food Assistance residency guidance',
   'https://content.dcf.ks.gov/ees/KEESM/current/keesm2150.htm',
   'available',
   '{"content_type":"public_program_guidance","retrieval_mode":"operator_triggered","scheduled_refresh":false,"approved_fact_scope":"food_assistance_residency_only"}')
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
  ('kansas-dcf-snap-residency-guidance',
   'kansas.dcf.food-assistance-residency', 'kansas-snap-residency-v1',
   'Kansas Food Assistance residency guidance',
   'https://content.dcf.ks.gov/ees/KEESM/current/keesm2150.htm',
   'eligibility-program-v1', ARRAY['core','eligibility'],
   '{"catalog_namespace":"programs","content_type":"public_program_guidance","schema_registry_ids":{"core":"core-schema-registry","eligibility":"eligibility-program-schema"},"schema_versions":{"core":"core-v1","eligibility":"eligibility-program-v1"},"refresh_mode":"operator_triggered","approved_fact_scope":"food_assistance_residency_only"}')
ON CONFLICT (dataset_id) DO UPDATE SET
  source_id = EXCLUDED.source_id,
  source_dataset_id = EXCLUDED.source_dataset_id,
  title = EXCLUDED.title,
  landing_page = EXCLUDED.landing_page,
  schema_version = EXCLUDED.schema_version,
  namespace_ids = EXCLUDED.namespace_ids,
  metadata = EXCLUDED.metadata,
  updated_at = now();
