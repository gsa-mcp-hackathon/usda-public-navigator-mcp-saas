-- Allow the Data API runtime to execute governed template deactivation.
-- Keep the receipt table append-only and limit lifecycle updates to state fields.
DO $$
DECLARE
    api_role text;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['mcp_saas_core_api', 'mcp_saas_data_api'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format(
                'GRANT UPDATE (lifecycle_state, updated_at) '
                'ON mcp_saas.pattern_template_packages TO %I',
                api_role
            );
            EXECUTE format(
                'GRANT UPDATE (lifecycle_state, updated_at) '
                'ON mcp_saas.core_route_metadata TO %I',
                api_role
            );
            EXECUTE format(
                'GRANT SELECT, INSERT '
                'ON mcp_saas.pattern_template_lifecycle_events TO %I',
                api_role
            );
        END IF;
    END LOOP;
END $$;

COMMENT ON TABLE mcp_saas.pattern_template_lifecycle_events IS
    'Append-only receipts for exact operator-approved cluster-template activation and deactivation; Data API roles may read and append receipts but cannot update or delete them.';
