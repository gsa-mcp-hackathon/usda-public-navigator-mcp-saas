from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))
MODULE_SPEC = importlib.util.spec_from_file_location(
    "provision_secrets", API_DIR / "provision_secrets.py"
)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
provision_secrets = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(provision_secrets)


class TenantRevisionGrantTests(unittest.TestCase):
    def test_grants_select_only_on_the_jurisdiction_revision_conflict_key(self):
        with patch.object(
            provision_secrets.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0),
        ) as run:
            provision_secrets.grant_api_scoped_permissions()

        sql = " ".join(run.call_args.kwargs["input"].split())
        self.assertIn(
            "GRANT SELECT (tenant_id, pattern_id, pattern_version, "
            "revised_spec_sha256) ON mcp_saas.tenant_registry_jurisdiction_revisions "
            f"TO {provision_secrets.ROLE};",
            sql,
        )
        self.assertIn(
            "GRANT INSERT ON mcp_saas.tenant_registry_jurisdiction_revisions "
            f"TO {provision_secrets.ROLE};",
            sql,
        )


class PublicCatalogPermissionTests(unittest.TestCase):
    def test_initial_writer_setup_gets_read_only_publication_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(Path, "home", return_value=Path(directory)),
                patch.object(
                    provision_secrets.subprocess,
                    "run",
                    return_value=SimpleNamespace(returncode=0),
                ) as run,
                patch.object(
                    provision_secrets.secrets, "token_hex", return_value="test-password"
                ),
                patch.object(provision_secrets, "create_secret"),
            ):
                provision_secrets.ensure_public_catalog_ingest_database_secret(set())

        sql = " ".join(run.call_args.kwargs["input"].split())
        self.assertIn(
            "IF to_regclass('mcp_saas.program_candidate_publications') "
            "IS NOT NULL THEN",
            sql,
        )
        self.assertIn(
            "GRANT SELECT ON mcp_saas.program_candidate_publications",
            sql,
        )
        self.assertNotIn("GRANT INSERT ON mcp_saas.program_candidate_publications", sql)

    def test_existing_writer_role_gets_only_the_required_catalog_permissions(self):
        with patch.object(
            provision_secrets.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0),
        ) as run:
            provision_secrets.grant_public_catalog_ingest_permissions()

        sql = " ".join(run.call_args.kwargs["input"].split())
        self.assertIn(
            "GRANT INSERT ON mcp_saas.public_data_sources, mcp_saas.public_datasets, "
            "mcp_saas.public_dataset_records, mcp_saas.public_dataset_ingestion_runs",
            sql,
        )
        self.assertIn(
            "GRANT UPDATE (source_name, source_url, access_status, "
            "source_context, updated_at) ON mcp_saas.public_data_sources",
            sql,
        )
        self.assertNotIn("GRANT DELETE", sql)
        self.assertNotIn("GRANT UPDATE ON mcp_saas.public_datasets", sql)
        self.assertIn(
            "IF to_regclass('mcp_saas.public_catalog_registration_events') "
            "IS NOT NULL THEN",
            sql,
        )
        self.assertIn(
            "GRANT SELECT, INSERT ON mcp_saas.public_catalog_registration_events",
            sql,
        )
        self.assertIn(
            "IF to_regclass('mcp_saas.program_candidate_publications') "
            "IS NOT NULL THEN",
            sql,
        )
        self.assertIn(
            "GRANT SELECT ON mcp_saas.program_candidate_publications",
            sql,
        )
        self.assertNotIn("GRANT INSERT ON mcp_saas.program_candidate_publications", sql)
        self.assertNotIn("GRANT UPDATE ON mcp_saas.program_candidate_publications", sql)
        self.assertNotIn("GRANT DELETE ON mcp_saas.program_candidate_publications", sql)
        self.assertIn(
            "IF to_regclass('mcp_saas.eligibility_criteria_rulesets') IS NOT NULL THEN",
            sql,
        )
        self.assertIn(
            "GRANT SELECT, INSERT ON mcp_saas.eligibility_criteria_rulesets",
            sql,
        )
        self.assertIn(
            "GRANT SELECT, INSERT, UPDATE (content_sha256, request_id, updated_at) "
            "ON mcp_saas.eligibility_criteria_ruleset_heads",
            sql,
        )
        self.assertIn(
            "GRANT SELECT, INSERT ON mcp_saas.eligibility_criteria_ruleset_events",
            sql,
        )
        self.assertNotIn(
            "GRANT UPDATE ON mcp_saas.public_catalog_registration_events", sql
        )
        self.assertNotIn(
            "GRANT DELETE ON mcp_saas.public_catalog_registration_events", sql
        )


class ProfileAccessReferenceGrantTests(unittest.TestCase):
    def test_profile_access_references_remain_select_insert_only(self):
        with patch.object(
            provision_secrets.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0),
        ) as run:
            provision_secrets.grant_api_scoped_permissions()

        sql = " ".join(run.call_args.kwargs["input"].split())
        self.assertIn(
            "GRANT SELECT ON mcp_saas.synthetic_profiles, "
            "mcp_saas.profile_attribute_values, mcp_saas.profile_relationships, "
            "mcp_saas.profile_attribute_history, mcp_saas.schema_registry, "
            "mcp_saas.profile_access_references TO "
            f"{provision_secrets.ROLE};",
            sql,
        )
        self.assertIn(
            "GRANT INSERT ON mcp_saas.profile_access_references "
            f"TO {provision_secrets.ROLE};",
            sql,
        )
        self.assertIn(
            "GRANT INSERT, UPDATE ON mcp_saas.schema_registry "
            f"TO {provision_secrets.ROLE};",
            sql,
        )
        self.assertNotIn("GRANT DELETE ON mcp_saas.schema_registry", sql)
        self.assertNotIn("GRANT UPDATE ON mcp_saas.profile_access_references", sql)
        self.assertNotIn(
            "GRANT UPDATE (revoked_at) ON mcp_saas.profile_access_references", sql
        )

    def test_source_policy_manifest_role_is_read_insert_only(self):
        with patch.object(
            provision_secrets.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0),
        ) as run:
            provision_secrets.grant_api_scoped_permissions()

        sql = " ".join(run.call_args.kwargs["input"].split())
        self.assertIn(
            "mcp_saas.source_policy_manifests, "
            "mcp_saas.program_candidate_records, "
            "mcp_saas.program_candidate_review_decisions, "
            "mcp_saas.program_candidate_publications, "
            "mcp_saas.program_candidate_publication_events, "
            "mcp_saas.source_policy_review_decisions, "
            "mcp_saas.source_policy_activation_events, "
            "mcp_saas.tenant_registry_jurisdiction_revisions,",
            sql,
        )
        self.assertIn(
            "GRANT SELECT, INSERT ON mcp_saas.source_policy_manifests "
            f"TO {provision_secrets.ROLE};",
            sql,
        )
        self.assertNotIn("GRANT UPDATE ON mcp_saas.source_policy_manifests", sql)
        self.assertNotIn("GRANT DELETE ON mcp_saas.source_policy_manifests", sql)
        self.assertIn(
            "GRANT SELECT, INSERT ON mcp_saas.program_candidate_records "
            f"TO {provision_secrets.ROLE};",
            sql,
        )
        self.assertNotIn("GRANT UPDATE ON mcp_saas.program_candidate_records", sql)
        self.assertNotIn("GRANT DELETE ON mcp_saas.program_candidate_records", sql)
        self.assertIn(
            "GRANT SELECT, INSERT ON "
            "mcp_saas.program_candidate_review_decisions "
            f"TO {provision_secrets.ROLE};",
            sql,
        )
        self.assertNotIn(
            "GRANT UPDATE ON mcp_saas.program_candidate_review_decisions", sql
        )
        self.assertNotIn(
            "GRANT DELETE ON mcp_saas.program_candidate_review_decisions", sql
        )
        self.assertIn(
            "GRANT SELECT, INSERT ON mcp_saas.program_candidate_publications, "
            "mcp_saas.program_candidate_publication_events "
            f"TO {provision_secrets.ROLE};",
            sql,
        )
        self.assertIn(
            "GRANT SELECT ON mcp_saas.public_data_sources, mcp_saas.public_datasets, "
            "mcp_saas.public_dataset_records, "
            "mcp_saas.active_public_dataset_records, "
            "mcp_saas.public_dataset_ingestion_runs",
            sql,
        )
        self.assertNotIn("GRANT UPDATE ON mcp_saas.program_candidate_publications", sql)
        self.assertNotIn("GRANT DELETE ON mcp_saas.program_candidate_publications", sql)
        self.assertNotIn(
            "GRANT UPDATE ON mcp_saas.program_candidate_publication_events", sql
        )
        self.assertNotIn(
            "GRANT DELETE ON mcp_saas.program_candidate_publication_events", sql
        )
        self.assertIn(
            "GRANT SELECT, INSERT ON "
            "mcp_saas.source_policy_review_decisions "
            f"TO {provision_secrets.ROLE};",
            sql,
        )
        self.assertNotIn("GRANT UPDATE ON mcp_saas.source_policy_review_decisions", sql)
        self.assertNotIn("GRANT DELETE ON mcp_saas.source_policy_review_decisions", sql)
        self.assertIn(
            "GRANT SELECT, INSERT ON "
            "mcp_saas.source_policy_activation_events "
            f"TO {provision_secrets.ROLE};",
            sql,
        )
        self.assertNotIn(
            "GRANT UPDATE ON mcp_saas.source_policy_activation_events", sql
        )
        self.assertNotIn(
            "GRANT DELETE ON mcp_saas.source_policy_activation_events", sql
        )


if __name__ == "__main__":
    unittest.main()
