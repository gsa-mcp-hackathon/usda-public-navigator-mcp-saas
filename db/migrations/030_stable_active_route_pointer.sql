-- Keep one active target behind each stable route and operation identity.
BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT tenant_id
        FROM mcp_saas.tenant_registry
        WHERE lifecycle_state = 'active'
        GROUP BY tenant_id
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION
            'Stable route migration found tenants with multiple active package bindings';
    END IF;
END
$$;

-- Preserve the active tenant binding while moving legacy tenant.pattern route IDs
-- to the stable tenant ID used by current clients.
INSERT INTO mcp_saas.core_route_metadata
    (route_id, route_version, operation_id, endpoint, mcp_tool_name,
     protocol, lifecycle_state, template_id, tenant_id)
SELECT tenant.tenant_id, route.route_version, route.operation_id,
       route.endpoint, route.mcp_tool_name, route.protocol, 'active',
       route.template_id, route.tenant_id
FROM mcp_saas.core_route_metadata AS route
JOIN mcp_saas.tenant_registry AS tenant
  ON tenant.tenant_id = route.tenant_id
 AND tenant.pattern_id = route.template_id
 AND tenant.pattern_version = route.route_version
 AND tenant.lifecycle_state = 'active'
JOIN mcp_saas.pattern_template_packages AS package
  ON package.template_id = tenant.pattern_id
 AND package.template_version = tenant.pattern_version
 AND package.lifecycle_state = 'active'
WHERE route.lifecycle_state = 'active'
  AND route.route_id = tenant.tenant_id || '.' || tenant.pattern_id
ON CONFLICT (route_id, route_version, operation_id) DO UPDATE
SET endpoint = EXCLUDED.endpoint,
    mcp_tool_name = EXCLUDED.mcp_tool_name,
    protocol = EXCLUDED.protocol,
    lifecycle_state = 'active',
    template_id = EXCLUDED.template_id,
    tenant_id = EXCLUDED.tenant_id,
    updated_at = now()
WHERE mcp_saas.core_route_metadata.tenant_id = EXCLUDED.tenant_id
  AND mcp_saas.core_route_metadata.template_id = EXCLUDED.template_id;

-- A shared pattern route follows the highest active package version. Tenant routes
-- remain independently pinned by tenant_registry and are keyed by tenant_id.
WITH current_shared_route AS (
    SELECT DISTINCT ON (route.route_id, route.operation_id)
           route.route_id, route.operation_id, route.route_version
    FROM mcp_saas.core_route_metadata AS route
    JOIN mcp_saas.pattern_template_packages AS package
      ON package.template_id = route.template_id
     AND package.template_version = route.route_version
     AND package.lifecycle_state = 'active'
    WHERE route.lifecycle_state = 'active'
      AND route.tenant_id IS NULL
      AND route.route_id = route.template_id
    ORDER BY route.route_id, route.operation_id,
             split_part(route.route_version, '.', 1)::integer DESC,
             split_part(route.route_version, '.', 2)::integer DESC,
             split_part(route.route_version, '.', 3)::integer DESC
)
UPDATE mcp_saas.core_route_metadata AS route
SET lifecycle_state = 'inactive', updated_at = now()
WHERE route.lifecycle_state = 'active'
  AND route.tenant_id IS NULL
  AND route.route_id = route.template_id
  AND NOT EXISTS (
      SELECT 1
      FROM current_shared_route AS current
      WHERE current.route_id = route.route_id
        AND current.operation_id = route.operation_id
        AND current.route_version = route.route_version
  );

-- A tenant's route follows only its active tenant registration. Other versions stay
-- in the table as rollback history, but cannot be selected by a normal client call.
UPDATE mcp_saas.core_route_metadata AS route
SET lifecycle_state = 'inactive', updated_at = now()
WHERE route.lifecycle_state = 'active'
  AND route.tenant_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM mcp_saas.tenant_registry AS tenant
      WHERE tenant.tenant_id = route.tenant_id
        AND tenant.lifecycle_state = 'active'
        AND tenant.pattern_id = route.template_id
        AND tenant.pattern_version = route.route_version
        AND route.route_id = tenant.tenant_id
  );

DO $$
BEGIN
    IF EXISTS (
        SELECT route_id, operation_id
        FROM mcp_saas.core_route_metadata
        WHERE lifecycle_state = 'active'
        GROUP BY route_id, operation_id
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION
            'Stable route migration found ambiguous active route operations';
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_core_route_metadata_active_pointer
    ON mcp_saas.core_route_metadata (route_id, operation_id)
    WHERE lifecycle_state = 'active';

COMMIT;
