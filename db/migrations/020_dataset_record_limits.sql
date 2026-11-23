-- Read a bounded per-dataset total from metadata while keeping the historical
-- 1,000-record default for datasets that do not declare a specific limit.
-- The hard ceiling prevents a catalog configuration from disabling the guard.
CREATE OR REPLACE FUNCTION mcp_saas.enforce_public_dataset_record_cap()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    current_count integer;
    configured_limit integer;
    dataset_metadata jsonb;
BEGIN
    SELECT metadata INTO dataset_metadata
      FROM mcp_saas.public_datasets
      WHERE dataset_id = NEW.dataset_id
      FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'dataset % is not registered', NEW.dataset_id;
    END IF;

    IF dataset_metadata ? 'maximum_records_total' THEN
        IF jsonb_typeof(dataset_metadata->'maximum_records_total') <> 'number' THEN
            RAISE EXCEPTION 'dataset % has an invalid maximum_records_total policy',
              NEW.dataset_id;
        END IF;
        configured_limit := (dataset_metadata->>'maximum_records_total')::integer;
    ELSE
        configured_limit := 1000;
    END IF;

    IF configured_limit < 1 OR configured_limit > 5000 THEN
        RAISE EXCEPTION 'dataset % maximum_records_total must be between 1 and 5000',
          NEW.dataset_id;
    END IF;

    SELECT count(*) INTO current_count
      FROM mcp_saas.public_dataset_records
      WHERE dataset_id = NEW.dataset_id;

    IF current_count >= configured_limit AND NOT EXISTS (
        SELECT 1 FROM mcp_saas.public_dataset_records
        WHERE dataset_id = NEW.dataset_id
          AND source_record_id = NEW.source_record_id
    ) THEN
        RAISE EXCEPTION 'dataset % reached its configured % record cap',
          NEW.dataset_id, configured_limit;
    END IF;

    RETURN NEW;
END;
$$;

-- The approved operator-provided SAM snapshot has 2,871 rows. The explicit
-- 3,000-record ceiling allows that snapshot while retaining a small bound.
UPDATE mcp_saas.public_datasets
   SET metadata = jsonb_set(
       metadata,
       '{maximum_records_total}',
       '3000'::jsonb,
       true
   ),
       updated_at = now()
 WHERE dataset_id = 'sam-assistance-listings-current';
