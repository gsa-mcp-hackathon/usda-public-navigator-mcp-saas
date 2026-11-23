-- Keep caller/schema errors and downstream tool errors distinct from outages.
ALTER TABLE mcp_saas.core_route_events
    DROP CONSTRAINT IF EXISTS core_route_events_result_status_check,
    DROP CONSTRAINT IF EXISTS core_route_events_error_class_check;

ALTER TABLE mcp_saas.core_route_events
    ADD CONSTRAINT core_route_events_result_status_check CHECK (
        result_status IN (
            'ok', 'unknown_route', 'incompatible', 'invalid_request',
            'downstream_error', 'unavailable'
        )
    ),
    ADD CONSTRAINT core_route_events_error_class_check CHECK (
        error_class IS NULL OR error_class IN (
            'registry_unavailable', 'tenant_unavailable', 'unknown_route',
            'incompatible_route', 'tenant_argument_validation', 'tenant_tool_error'
        )
    );
