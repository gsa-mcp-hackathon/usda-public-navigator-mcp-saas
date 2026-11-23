-- Pattern- and tenant-agnostic route contract required by the shared Core.
CREATE SCHEMA IF NOT EXISTS mcp_saas;

CREATE TABLE IF NOT EXISTS mcp_saas.core_route_metadata (
    route_id text NOT NULL CHECK (length(route_id) > 0),
    route_version text NOT NULL CHECK (length(route_version) > 0),
    operation_id text NOT NULL CHECK (length(operation_id) > 0),
    endpoint text NOT NULL CHECK (endpoint ~ '^https?://'),
    mcp_tool_name text NOT NULL CHECK (length(mcp_tool_name) > 0),
    protocol text NOT NULL CHECK (protocol = 'mcp'),
    lifecycle_state text NOT NULL DEFAULT 'active'
        CHECK (lifecycle_state IN ('active', 'inactive')),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (route_id, route_version, operation_id)
);

CREATE INDEX IF NOT EXISTS ix_core_route_metadata_active
    ON mcp_saas.core_route_metadata (route_id, route_version, operation_id)
    WHERE lifecycle_state = 'active';

COMMENT ON TABLE mcp_saas.core_route_metadata IS
    'Core-owned opaque route-to-endpoint and MCP tool compatibility metadata; contains no pattern or tenant registration state.';
