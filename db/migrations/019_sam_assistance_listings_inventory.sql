-- Register the bounded, operator-triggered SAM.gov Assistance Listings CSV
-- as a criteria-free shared discovery dataset. No schedule or tenant binding.
INSERT INTO mcp_saas.public_data_sources
  (source_id, source_name, source_url, access_status, source_context)
VALUES
  ('sam.gov.assistance_listings',
   'SAM.gov Assistance Listings Data Services',
   'https://s3.amazonaws.com/falextracts/Assistance%20Listings/datagov/AssistanceListings_DataGov_PUBLIC_CURRENT.csv',
   'available',
   '{"content_type":"official_csv_snapshot","retrieval_mode":"operator_provided_data_services_file","scheduled_refresh":false,"criteria_extraction":false,"criteria_status":"not_assessed","maximum_records_per_batch":25,"maximum_batch_bytes":60000}')
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
  ('sam-assistance-listings-current',
   'sam.gov.assistance_listings',
   'sam-datagov-assistance-listings-public-current',
   'SAM.gov Assistance Listings public current snapshot',
   'https://s3.amazonaws.com/falextracts/Assistance%20Listings/datagov/AssistanceListings_DataGov_PUBLIC_CURRENT.csv',
   'public-listing-v2',
   ARRAY['core','listing'],
   '{"catalog_namespace":"programs","content_type":"federal_assistance_program_metadata","schema_registry_ids":{"core":"core-schema-registry","listing":"eligibility-public-listing-schema"},"schema_versions":{"core":"core-v1","listing":"public-listing-v2"},"refresh_mode":"operator_triggered","scheduled_refresh":false,"criteria_available":false,"maximum_records_per_batch":25,"maximum_batch_bytes":60000,"maximum_records_total":3000,"status_semantics":"unknown_when_not_published","raw_source_file_stored":false}')
ON CONFLICT (dataset_id) DO UPDATE SET
  source_id = EXCLUDED.source_id,
  source_dataset_id = EXCLUDED.source_dataset_id,
  title = EXCLUDED.title,
  landing_page = EXCLUDED.landing_page,
  schema_version = EXCLUDED.schema_version,
  namespace_ids = EXCLUDED.namespace_ids,
  metadata = EXCLUDED.metadata,
  updated_at = now();
