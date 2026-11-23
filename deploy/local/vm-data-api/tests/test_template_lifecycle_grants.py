from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def test_lifecycle_grant_migration_is_scoped_and_repeatable() -> None:
    migration = (
        ROOT / "db/migrations/036_template_lifecycle_api_grants.sql"
    ).read_text(encoding="utf-8")

    assert (
        "FOREACH api_role IN ARRAY ARRAY['mcp_saas_core_api', 'mcp_saas_data_api']"
        in migration
    )
    assert "IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = api_role)" in migration
    assert migration.count("GRANT UPDATE (lifecycle_state, updated_at)") == 2
    assert "GRANT SELECT, INSERT" in migration
    assert "pattern_template_lifecycle_events TO %I" in migration
    assert "GRANT UPDATE ON mcp_saas.pattern_template_lifecycle_events" not in migration
    assert "GRANT DELETE ON mcp_saas.pattern_template_lifecycle_events" not in migration


def test_data_api_provisioning_reasserts_receipt_access_only() -> None:
    provisioner = (ROOT / "deploy/local/vm-data-api/provision_secrets.py").read_text(
        encoding="utf-8"
    )

    assert 'f"mcp_saas.pattern_template_lifecycle_events, "' in provisioner
    assert 'f"GRANT SELECT, INSERT ON "' in provisioner
    assert 'f"mcp_saas.pattern_template_lifecycle_events TO {ROLE};\\n"' in provisioner


def test_governed_catalog_migration_grants_append_only_receipt_access() -> None:
    migration = (
        ROOT / "db/migrations/037_governed_public_catalog_registration.sql"
    ).read_text(encoding="utf-8")

    assert (
        "CREATE TABLE IF NOT EXISTS mcp_saas.public_catalog_registration_events"
        in migration
    )
    assert "GRANT SELECT, INSERT" in migration
    assert "public_catalog_registration_events" in migration
    assert "GRANT UPDATE" not in migration
    assert "GRANT DELETE" not in migration


def test_tenant_lifecycle_migration_and_provisioner_grant_receipts_only() -> None:
    migration = (ROOT / "db/migrations/038_tenant_lifecycle_audit.sql").read_text(
        encoding="utf-8"
    )
    provisioner = (ROOT / "deploy/local/vm-data-api/provision_secrets.py").read_text(
        encoding="utf-8"
    )

    assert "CREATE TABLE IF NOT EXISTS mcp_saas.tenant_lifecycle_events" in migration
    assert "outcome text NOT NULL CHECK (outcome = 'applied')" in migration
    assert "FOREIGN KEY (tenant_id, template_id, template_version)" in migration
    assert "GRANT SELECT, INSERT ON mcp_saas.tenant_lifecycle_events TO %I" in migration
    assert "GRANT UPDATE ON mcp_saas.tenant_lifecycle_events" not in migration
    assert "GRANT DELETE ON mcp_saas.tenant_lifecycle_events" not in migration
    assert 'f"mcp_saas.tenant_lifecycle_events, "' in provisioner
    assert 'f"mcp_saas.tenant_lifecycle_events TO {ROLE};\\n"' in provisioner
