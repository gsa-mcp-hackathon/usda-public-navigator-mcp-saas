-- Source/dataset directory is separate from any ingested data records.
INSERT INTO mcp_saas.public_data_sources (source_id, source_name, source_url, access_status, source_context)
VALUES
  ('datagov.catalog', 'Data.gov Catalog API', 'https://api.gsa.gov/technology/datagov/v4/', 'available', '{"content_type":"catalog_metadata"}'),
  ('farmers.gov.loans', 'Farmers.gov farm loans page', 'https://www.farmers.gov/loans', 'metadata_only', '{"content_type":"public_web_reference"}'),
  ('gsa.perdiem', 'GSA Per Diem API', 'https://open.gsa.gov/api/perdiem/', 'available', '{"content_type":"structured_api"}'),
  ('maryland.farmers_markets', 'Maryland Farmers Markets API', 'https://opendata.maryland.gov/resource/sw6s-2vmw.json', 'available', '{"content_type":"structured_state_api"}'),
  ('ssa.oasdi', 'Social Security Administration OASDI statistics', 'https://www.ssa.gov/policy/docs/statcomps/oasdi_sc/flat-series.html', 'blocked', '{"content_type":"aggregated_statistics","reason":"official bulk endpoint returned HTTP 403 from VM"}')
ON CONFLICT (source_id) DO UPDATE SET source_name = EXCLUDED.source_name,
  source_url = EXCLUDED.source_url, access_status = EXCLUDED.access_status,
  source_context = EXCLUDED.source_context, updated_at = now();

INSERT INTO mcp_saas.public_datasets
  (dataset_id, source_id, source_dataset_id, title, landing_page, schema_version, namespace_ids, metadata)
VALUES
  ('datagov-catalog', 'datagov.catalog', 'datagov-catalog-v4', 'Data.gov catalog search results', 'https://catalog.data.gov/', 'public-data-v1', ARRAY['core','datagov'], '{"content_type":"catalog_metadata"}'),
  ('farmers-fsa-loans-reference', 'farmers.gov.loans', 'farmers-fsa-loans-reference', 'Farmers.gov farm loans page', 'https://www.farmers.gov/loans', 'public-data-v1', ARRAY['core','farmers'], '{"content_type":"public_web_reference"}'),
  ('gsa-perdiem-rates', 'gsa.perdiem', 'gsa-perdiem-v2', 'GSA Per Diem API', 'https://open.gsa.gov/api/perdiem/', 'public-data-v1', ARRAY['core','gsa'], '{"content_type":"structured_api"}'),
  ('maryland-farmers-markets', 'maryland.farmers_markets', 'maryland-farmers-market-directory', 'Maryland Farmers Markets', 'https://opendata.maryland.gov/d/sw6s-2vmw', 'public-data-v1', ARRAY['core','md'], '{"content_type":"structured_state_api"}'),
  ('ssa-oasdi-statistics', 'ssa.oasdi', 'ssa-oasdi-flat-series', 'SSA OASDI public statistics', 'https://www.ssa.gov/policy/docs/statcomps/oasdi_sc/flat-series.html', 'public-data-v1', ARRAY['core','ssa'], '{"content_type":"aggregated_statistics","access_status":"blocked"}')
ON CONFLICT (dataset_id) DO NOTHING;
