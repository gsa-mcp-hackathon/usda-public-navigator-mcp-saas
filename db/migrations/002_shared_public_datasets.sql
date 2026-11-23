-- Shared, source-aware public dataset catalog. Definitions/mappings remain in Git.
CREATE TABLE IF NOT EXISTS mcp_saas.public_data_sources (
    source_id text PRIMARY KEY,
    source_name text NOT NULL,
    source_url text NOT NULL CHECK (source_url ~ '^https?://'),
    access_status text NOT NULL CHECK (access_status IN ('available', 'blocked', 'metadata_only')),
    source_context jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(source_context) = 'object'),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mcp_saas.public_datasets (
    dataset_id text PRIMARY KEY,
    source_id text NOT NULL REFERENCES mcp_saas.public_data_sources(source_id),
    source_dataset_id text NOT NULL,
    title text NOT NULL,
    landing_page text CHECK (landing_page IS NULL OR landing_page ~ '^https?://'),
    schema_version text NOT NULL,
    namespace_ids text[] NOT NULL DEFAULT ARRAY[]::text[],
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, source_dataset_id)
);

CREATE TABLE IF NOT EXISTS mcp_saas.public_dataset_records (
    dataset_id text NOT NULL REFERENCES mcp_saas.public_datasets(dataset_id) ON DELETE CASCADE,
    source_record_id text NOT NULL,
    record_type text NOT NULL,
    canonical_attributes jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(canonical_attributes) = 'object'),
    semantic_context jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(semantic_context) = 'object'),
    source_metadata jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(source_metadata) = 'object'),
    source_url text CHECK (source_url IS NULL OR source_url ~ '^https?://'),
    content_hash text NOT NULL,
    schema_version text NOT NULL,
    observed_at timestamptz NOT NULL,
    PRIMARY KEY (dataset_id, source_record_id)
);
CREATE INDEX IF NOT EXISTS ix_public_dataset_records_attributes
    ON mcp_saas.public_dataset_records USING gin (canonical_attributes jsonb_path_ops);
CREATE INDEX IF NOT EXISTS ix_public_dataset_records_hash
    ON mcp_saas.public_dataset_records (content_hash);

CREATE TABLE IF NOT EXISTS mcp_saas.pattern_catalog_bindings (
    dataset_id text NOT NULL REFERENCES mcp_saas.public_datasets(dataset_id) ON DELETE CASCADE,
    cluster_id text NOT NULL,
    pattern_version text,
    tenant_id text NOT NULL,
    binding_context jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(binding_context) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (dataset_id, cluster_id, tenant_id)
);

CREATE TABLE IF NOT EXISTS mcp_saas.public_dataset_ingestion_runs (
    run_id text PRIMARY KEY,
    source_id text NOT NULL REFERENCES mcp_saas.public_data_sources(source_id),
    requested_limit integer NOT NULL CHECK (requested_limit BETWEEN 1 AND 100),
    fetched_count integer NOT NULL DEFAULT 0 CHECK (fetched_count BETWEEN 0 AND 100),
    inserted_count integer NOT NULL DEFAULT 0 CHECK (inserted_count BETWEEN 0 AND 100),
    updated_count integer NOT NULL DEFAULT 0 CHECK (updated_count BETWEEN 0 AND 100),
    duplicate_count integer NOT NULL DEFAULT 0 CHECK (duplicate_count BETWEEN 0 AND 100),
    status text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'blocked')),
    details jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(details) = 'object'),
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);

COMMENT ON TABLE mcp_saas.public_data_sources IS 'Allowlisted public sources and access status; no credentials are stored here.';
COMMENT ON TABLE mcp_saas.public_datasets IS 'Reusable source-level datasets shared across pattern catalogs without per-pattern copies.';
COMMENT ON TABLE mcp_saas.public_dataset_records IS 'Idempotent source-scoped records with canonical attributes, context, provenance, and source identity.';
COMMENT ON TABLE mcp_saas.pattern_catalog_bindings IS 'Many-to-many links from shared datasets to versioned pattern/tenant catalog views; bindings carry interpretation context.';
COMMENT ON TABLE mcp_saas.public_dataset_ingestion_runs IS 'Bounded, auditable import runs; each run is capped at 100 records.';


-- Serialize inserts per dataset and refuse record 1,001 while allowing idempotent updates.
CREATE OR REPLACE FUNCTION mcp_saas.enforce_public_dataset_record_cap()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    current_count integer;
BEGIN
    PERFORM 1 FROM mcp_saas.public_datasets WHERE dataset_id = NEW.dataset_id FOR UPDATE;
    SELECT count(*) INTO current_count FROM mcp_saas.public_dataset_records
      WHERE dataset_id = NEW.dataset_id;
    IF current_count >= 1000 AND NOT EXISTS (
        SELECT 1 FROM mcp_saas.public_dataset_records
        WHERE dataset_id = NEW.dataset_id AND source_record_id = NEW.source_record_id
    ) THEN
        RAISE EXCEPTION 'dataset % reached the 1000-record prototype cap', NEW.dataset_id;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_public_dataset_record_cap ON mcp_saas.public_dataset_records;
CREATE TRIGGER trg_public_dataset_record_cap
BEFORE INSERT ON mcp_saas.public_dataset_records
FOR EACH ROW EXECUTE FUNCTION mcp_saas.enforce_public_dataset_record_cap();
