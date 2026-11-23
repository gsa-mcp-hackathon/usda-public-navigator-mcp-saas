-- Scope the active criteria pointer, immutable history and replay receipt to a
-- cluster namespace. Existing Eligibility rows are retained under `eligibility`.
ALTER TABLE mcp_saas.eligibility_criteria_rulesets
    ADD COLUMN IF NOT EXISTS cluster_id TEXT NOT NULL DEFAULT 'eligibility'
    CHECK (cluster_id ~ '^[a-z][a-z0-9-]{0,63}$');
ALTER TABLE mcp_saas.eligibility_criteria_ruleset_heads
    ADD COLUMN IF NOT EXISTS cluster_id TEXT NOT NULL DEFAULT 'eligibility'
    CHECK (cluster_id ~ '^[a-z][a-z0-9-]{0,63}$');
ALTER TABLE mcp_saas.eligibility_criteria_ruleset_events
    ADD COLUMN IF NOT EXISTS cluster_id TEXT NOT NULL DEFAULT 'eligibility'
    CHECK (cluster_id ~ '^[a-z][a-z0-9-]{0,63}$');

DO $$
DECLARE
    dependent_fk RECORD;
BEGIN
    FOR dependent_fk IN
        SELECT conrelid::regclass AS table_name, conname
        FROM pg_constraint
        WHERE contype = 'f'
          AND confrelid = 'mcp_saas.eligibility_criteria_rulesets'::regclass
          AND conrelid IN (
              'mcp_saas.eligibility_criteria_ruleset_heads'::regclass,
              'mcp_saas.eligibility_criteria_ruleset_events'::regclass
          )
    LOOP
        EXECUTE format(
            'ALTER TABLE %s DROP CONSTRAINT %I',
            dependent_fk.table_name,
            dependent_fk.conname
        );
    END LOOP;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'mcp_saas.eligibility_criteria_rulesets'::regclass
          AND conname = 'eligibility_criteria_rulesets_pkey'
          AND pg_get_constraintdef(oid) NOT LIKE 'PRIMARY KEY (cluster_id,%'
    ) THEN
        ALTER TABLE mcp_saas.eligibility_criteria_rulesets
            DROP CONSTRAINT eligibility_criteria_rulesets_pkey;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'mcp_saas.eligibility_criteria_rulesets'::regclass
          AND conname = 'eligibility_criteria_rulesets_pkey'
    ) THEN
        ALTER TABLE mcp_saas.eligibility_criteria_rulesets
            ADD CONSTRAINT eligibility_criteria_rulesets_pkey
            PRIMARY KEY (cluster_id, program_id, content_sha256);
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'mcp_saas.eligibility_criteria_ruleset_heads'::regclass
          AND conname = 'eligibility_criteria_ruleset_heads_pkey'
          AND pg_get_constraintdef(oid) NOT LIKE 'PRIMARY KEY (cluster_id,%'
    ) THEN
        ALTER TABLE mcp_saas.eligibility_criteria_ruleset_heads
            DROP CONSTRAINT eligibility_criteria_ruleset_heads_pkey;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'mcp_saas.eligibility_criteria_ruleset_heads'::regclass
          AND conname = 'eligibility_criteria_ruleset_heads_pkey'
    ) THEN
        ALTER TABLE mcp_saas.eligibility_criteria_ruleset_heads
            ADD CONSTRAINT eligibility_criteria_ruleset_heads_pkey
            PRIMARY KEY (cluster_id, program_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'mcp_saas.eligibility_criteria_ruleset_heads'::regclass
          AND conname = 'eligibility_criteria_ruleset_heads_ruleset_fkey'
    ) THEN
        ALTER TABLE mcp_saas.eligibility_criteria_ruleset_heads
            ADD CONSTRAINT eligibility_criteria_ruleset_heads_ruleset_fkey
            FOREIGN KEY (cluster_id, program_id, content_sha256)
            REFERENCES mcp_saas.eligibility_criteria_rulesets
                (cluster_id, program_id, content_sha256);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'mcp_saas.eligibility_criteria_ruleset_events'::regclass
          AND conname = 'eligibility_criteria_ruleset_events_ruleset_fkey'
    ) THEN
        ALTER TABLE mcp_saas.eligibility_criteria_ruleset_events
            ADD CONSTRAINT eligibility_criteria_ruleset_events_ruleset_fkey
            FOREIGN KEY (cluster_id, program_id, content_sha256)
            REFERENCES mcp_saas.eligibility_criteria_rulesets
                (cluster_id, program_id, content_sha256);
    END IF;
END $$;

COMMENT ON TABLE mcp_saas.eligibility_criteria_ruleset_heads IS
    'One active exact-digest pointer per cluster and program; changed rulesets remain in immutable history.';
