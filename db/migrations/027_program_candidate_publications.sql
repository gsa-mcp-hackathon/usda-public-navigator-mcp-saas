-- Versioned publication records and append-only activation pointers for reviewed
-- program candidates. Public catalog rows remain in the shared record store.
CREATE UNIQUE INDEX IF NOT EXISTS uq_public_dataset_record_exact_content
    ON mcp_saas.public_dataset_records
        (dataset_id, source_record_id, content_hash);

CREATE TABLE IF NOT EXISTS mcp_saas.program_candidate_publications (
    publication_key CHAR(64) NOT NULL CHECK (publication_key ~ '^[0-9a-f]{64}$'),
    candidate_id TEXT NOT NULL,
    candidate_content_sha256 CHAR(64) NOT NULL
        CHECK (candidate_content_sha256 ~ '^[0-9a-f]{64}$'),
    dataset_id TEXT NOT NULL REFERENCES mcp_saas.public_datasets(dataset_id),
    source_record_id TEXT NOT NULL,
    record_content_sha256 TEXT NOT NULL
        CHECK (record_content_sha256 ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (dataset_id, source_record_id),
    UNIQUE (publication_key, candidate_id, candidate_content_sha256),
    UNIQUE (candidate_id, candidate_content_sha256, dataset_id),
    FOREIGN KEY (candidate_id, candidate_content_sha256)
        REFERENCES mcp_saas.program_candidate_records(candidate_id, content_sha256),
    FOREIGN KEY (dataset_id, source_record_id, record_content_sha256)
        REFERENCES mcp_saas.public_dataset_records
            (dataset_id, source_record_id, content_hash)
);

COMMENT ON TABLE mcp_saas.program_candidate_publications IS
    'Immutable links from approved candidate digests to source-aware shared public catalog records.';

CREATE OR REPLACE FUNCTION mcp_saas.reject_published_candidate_record_mutation()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM mcp_saas.program_candidate_publications AS publication
        WHERE publication.dataset_id = OLD.dataset_id
          AND publication.source_record_id = OLD.source_record_id
    ) THEN
        RAISE EXCEPTION 'Published program candidate records are immutable.'
            USING ERRCODE = '55000';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_immutable_published_candidate_record
    ON mcp_saas.public_dataset_records;
CREATE TRIGGER trg_immutable_published_candidate_record
BEFORE UPDATE OR DELETE ON mcp_saas.public_dataset_records
FOR EACH ROW EXECUTE FUNCTION mcp_saas.reject_published_candidate_record_mutation();

CREATE TABLE IF NOT EXISTS mcp_saas.program_candidate_publication_events (
    generation BIGINT PRIMARY KEY CHECK (generation > 0),
    request_id UUID NOT NULL UNIQUE,
    publication_key CHAR(64) NOT NULL CHECK (publication_key ~ '^[0-9a-f]{64}$'),
    action TEXT NOT NULL CHECK (action IN ('activate', 'deactivate', 'rollback')),
    expected_candidate_id TEXT,
    expected_candidate_content_sha256 CHAR(64),
    target_candidate_id TEXT,
    target_candidate_content_sha256 CHAR(64),
    rationale TEXT NOT NULL CHECK (length(btrim(rationale)) BETWEEN 1 AND 2048),
    authorization_capability TEXT NOT NULL
        CHECK (authorization_capability = 'registry_writer'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        (expected_candidate_id IS NULL AND expected_candidate_content_sha256 IS NULL)
        OR (expected_candidate_id IS NOT NULL
            AND expected_candidate_content_sha256 ~ '^[0-9a-f]{64}$')
    ),
    CHECK (
        (action = 'deactivate' AND target_candidate_id IS NULL
            AND target_candidate_content_sha256 IS NULL)
        OR (action IN ('activate', 'rollback') AND target_candidate_id IS NOT NULL
            AND target_candidate_content_sha256 ~ '^[0-9a-f]{64}$')
    ),
    FOREIGN KEY (publication_key, expected_candidate_id,
                 expected_candidate_content_sha256)
        REFERENCES mcp_saas.program_candidate_publications
            (publication_key, candidate_id, candidate_content_sha256),
    FOREIGN KEY (publication_key, target_candidate_id,
                 target_candidate_content_sha256)
        REFERENCES mcp_saas.program_candidate_publications
            (publication_key, candidate_id, candidate_content_sha256)
);

COMMENT ON TABLE mcp_saas.program_candidate_publication_events IS
    'Append-only compare-and-set activation history; the latest event selects the one visible shared inventory version per source record.';

CREATE OR REPLACE VIEW mcp_saas.active_public_dataset_records AS
SELECT record.*
FROM mcp_saas.public_dataset_records AS record
LEFT JOIN mcp_saas.program_candidate_publications AS publication
  ON publication.dataset_id = record.dataset_id
 AND publication.source_record_id = record.source_record_id
LEFT JOIN LATERAL (
    SELECT event.action, event.target_candidate_id,
           event.target_candidate_content_sha256
    FROM mcp_saas.program_candidate_publication_events AS event
    WHERE event.publication_key = publication.publication_key
    ORDER BY event.generation DESC
    LIMIT 1
) AS active ON publication.publication_key IS NOT NULL
WHERE (
    record.source_metadata ? 'program_candidate_id'
    AND publication.publication_key IS NOT NULL
    AND active.action IN ('activate', 'rollback')
    AND active.target_candidate_id = publication.candidate_id
    AND active.target_candidate_content_sha256 =
        publication.candidate_content_sha256
    AND record.content_hash = publication.record_content_sha256
    AND record.source_metadata->>'program_candidate_id' = publication.candidate_id
    AND record.source_metadata->>'program_candidate_sha256' =
        publication.candidate_content_sha256
    AND record.source_metadata->>'program_candidate_publication_key' =
        publication.publication_key
)
OR NOT (record.source_metadata ? 'program_candidate_id');

