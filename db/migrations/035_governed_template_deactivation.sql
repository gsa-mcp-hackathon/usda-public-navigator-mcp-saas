-- Extend the template lifecycle receipt contract to governed deactivation.
ALTER TABLE mcp_saas.pattern_template_lifecycle_events
    DROP CONSTRAINT IF EXISTS pattern_template_lifecycle_events_action_check,
    DROP CONSTRAINT IF EXISTS pattern_template_lifecycle_events_before_state_check,
    DROP CONSTRAINT IF EXISTS pattern_template_lifecycle_events_after_state_check,
    DROP CONSTRAINT IF EXISTS pattern_template_lifecycle_events_transition_check;

ALTER TABLE mcp_saas.pattern_template_lifecycle_events
    ADD CONSTRAINT pattern_template_lifecycle_events_action_check
        CHECK (action IN ('activate', 'deactivate')),
    ADD CONSTRAINT pattern_template_lifecycle_events_before_state_check
        CHECK (before_state IN ('pending', 'active')),
    ADD CONSTRAINT pattern_template_lifecycle_events_after_state_check
        CHECK (after_state IN ('active', 'inactive')),
    ADD CONSTRAINT pattern_template_lifecycle_events_transition_check
        CHECK (
            (action = 'activate' AND before_state = 'pending' AND after_state = 'active')
            OR
            (action = 'deactivate' AND before_state = 'active' AND after_state = 'inactive')
        );

COMMENT ON TABLE mcp_saas.pattern_template_lifecycle_events IS
    'Append-only receipts for exact operator-approved template activation and deactivation, including compare-and-set state digests and privacy-safe MCP correlation hashes.';
