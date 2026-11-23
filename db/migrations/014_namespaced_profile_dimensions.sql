-- Profile dimensions use the same globally namespaced identity convention as attributes.
ALTER TABLE mcp_saas.profile_attribute_values
    DROP CONSTRAINT IF EXISTS profile_attribute_values_dimension_id_check,
    DROP CONSTRAINT IF EXISTS profile_attribute_values_dimension_id_namespaced_check;

ALTER TABLE mcp_saas.profile_attribute_values
    ADD CONSTRAINT profile_attribute_values_dimension_id_namespaced_check
    CHECK (dimension_id ~ '^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+$');
