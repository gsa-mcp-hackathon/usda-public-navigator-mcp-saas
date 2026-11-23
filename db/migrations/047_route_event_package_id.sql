-- Optional saved-package cross-reference on the Layer 0 route audit event.
-- Additive and nullable: existing rows and existing writers are unaffected.
-- Rollback: ALTER TABLE mcp_saas.core_route_events DROP COLUMN IF EXISTS package_id;
ALTER TABLE mcp_saas.core_route_events
    ADD COLUMN IF NOT EXISTS package_id uuid;

CREATE INDEX IF NOT EXISTS ix_core_route_events_package
    ON mcp_saas.core_route_events (package_id)
    WHERE package_id IS NOT NULL;

COMMENT ON COLUMN mcp_saas.core_route_events.package_id IS
    'Saved requirements package id returned by the call, when there is one; an identifier only, never content.';
