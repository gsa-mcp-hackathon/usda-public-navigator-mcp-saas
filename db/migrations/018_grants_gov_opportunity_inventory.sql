-- Register a bounded, criteria-free public source and shared opportunity dataset.
-- Ingestion and tenant catalog binding remain explicit operator actions.
INSERT INTO mcp_saas.public_data_sources
  (source_id, source_name, source_url, access_status, source_context)
VALUES
  ('grants.gov.opportunities',
   'Grants.gov funding opportunities API',
   'https://api.grants.gov/v1/api/search2',
   'available',
   '{"content_type":"structured_api","retrieval_mode":"operator_triggered","scheduled_refresh":false,"criteria_extraction":false,"maximum_records_per_run":25,"non_endorsement_notice":"This product uses the Grants.gov API but is not endorsed or certified by the U.S. Department of Health and Human Services."}')
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
  ('grants-gov-current-opportunities',
   'grants.gov.opportunities',
   'grants-gov-posted-opportunities',
   'Current Grants.gov funding opportunities',
   'https://www.grants.gov/search-grants.html',
   'public-listing-v1',
   ARRAY['core','listing'],
   '{"catalog_namespace":"programs","content_type":"federal_funding_opportunity_metadata","schema_registry_ids":{"core":"core-schema-registry","listing":"eligibility-public-listing-schema"},"schema_versions":{"core":"core-v1","listing":"public-listing-v1"},"refresh_mode":"operator_triggered","criteria_available":false,"maximum_records_per_run":25,"maximum_records_total":1000,"notice":"This product uses the Grants.gov API but is not endorsed or certified by the U.S. Department of Health and Human Services."}')
ON CONFLICT (dataset_id) DO UPDATE SET
  source_id = EXCLUDED.source_id,
  source_dataset_id = EXCLUDED.source_dataset_id,
  title = EXCLUDED.title,
  landing_page = EXCLUDED.landing_page,
  schema_version = EXCLUDED.schema_version,
  namespace_ids = EXCLUDED.namespace_ids,
  metadata = EXCLUDED.metadata,
  updated_at = now();
