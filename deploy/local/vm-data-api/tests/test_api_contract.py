import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import sys
import threading
import unittest
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

API_DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIRECTORY))
API_PATH = str(API_DIRECTORY / "api.py")
API_SPEC = importlib.util.spec_from_file_location("api", API_PATH)
assert API_SPEC is not None and API_SPEC.loader is not None
api = importlib.util.module_from_spec(API_SPEC)
sys.modules["api"] = api
API_SPEC.loader.exec_module(api)

Handler = api.Handler
configured_catalog_scope = api.configured_catalog_scope
read_core_route = api.read_core_route
read_core_routes = api.read_core_routes
register_template_package = api.register_template_package
list_template_packages = api.list_template_packages
deactivate_template_package_governed = api.deactivate_template_package_governed
set_template_package_state = api.set_template_package_state
bind_template_operation = api.bind_template_operation
read_pattern_catalog = api.read_pattern_catalog
read_pattern_cluster_bindings = api.read_pattern_cluster_bindings
read_tenant_public_dataset = api.read_tenant_public_dataset
read_public_dataset = api.read_public_dataset
read_tenant_programs = api.read_tenant_programs
valid_public_catalog_ingestion = api.valid_public_catalog_ingestion
value_matches_attribute = api._value_matches_attribute
ingest_public_dataset = api.ingest_public_dataset
validate_public_dataset_ingestion = api.validate_public_dataset_ingestion
summarize_public_dataset_freshness = api.summarize_public_dataset_freshness
valid_probe = api.valid_probe
valid_tenant_endpoint = api.valid_tenant_endpoint
valid_pattern_catalog_binding = api.valid_pattern_catalog_binding
register_pattern_catalog_binding = api.register_pattern_catalog_binding


class CriteriaRulesetApiTests(unittest.TestCase):
    def test_writer_token_mapping_is_cluster_scoped_and_legacy_default_is_narrow(self):
        with patch.object(api, "CRITERIA_RULESET_WRITE_TOKEN_FILES_JSON", None):
            self.assertEqual(
                api.criteria_ruleset_writer_token_file("eligibility"),
                api.CRITERIA_RULESET_WRITE_TOKEN_FILE,
            )
            self.assertIsNone(api.criteria_ruleset_writer_token_file("eligibility-alt"))

        with patch.object(
            api,
            "CRITERIA_RULESET_WRITE_TOKEN_FILES_JSON",
            '{"eligibility-alt":"/run/secrets/alt-ruleset-token"}',
        ):
            self.assertEqual(
                api.criteria_ruleset_writer_token_file("eligibility-alt"),
                Path("/run/secrets/alt-ruleset-token"),
            )
            self.assertIsNone(api.criteria_ruleset_writer_token_file("unknown"))
        with patch.object(
            api,
            "CRITERIA_RULESET_WRITE_TOKEN_FILES_JSON",
            '{"cluster-one":"/run/secrets/shared","cluster-two":"/run/secrets/shared"}',
        ):
            self.assertIsNone(api.criteria_ruleset_writer_token_file("cluster-one"))

        with patch.object(api, "CRITERIA_RULESET_READ_TOKEN_FILES_JSON", None):
            self.assertEqual(
                api.criteria_ruleset_reader_token_file("eligibility"),
                api.CATALOG_READ_TOKEN_FILE,
            )
            self.assertIsNone(api.criteria_ruleset_reader_token_file("eligibility-alt"))

        with patch.object(
            api,
            "CRITERIA_RULESET_READ_TOKEN_FILES_JSON",
            '{"eligibility-alt":"/run/secrets/alt-ruleset-reader"}',
        ):
            self.assertEqual(
                api.criteria_ruleset_reader_token_file("eligibility-alt"),
                Path("/run/secrets/alt-ruleset-reader"),
            )
            self.assertIsNone(api.criteria_ruleset_reader_token_file("unknown"))

    def test_ruleset_and_active_schema_routes_use_separate_capabilities(self):
        from urllib.error import HTTPError
        from urllib.request import Request

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        payload = {
            "request_id": str(uuid.uuid4()),
            "cluster_id": "eligibility-alt",
            "expected_current_content_sha256": None,
            "expected_content_sha256": "a" * 64,
            "ruleset": {"program_id": "usda-csfp"},
        }
        ruleset_result = {
            "status": "ok",
            "cluster_id": "eligibility-alt",
            "program_id": "usda-csfp",
            "content_sha256": "a" * 64,
            "ruleset": payload["ruleset"],
        }
        with (
            patch(
                "api.read_secret",
                side_effect=lambda path: (
                    "alt-writer-token"
                    if "alt-ruleset-token" in str(path)
                    else "writer-token"
                    if "criteria-ruleset-write" in str(path)
                    else "alt-reader-token"
                    if "alt-ruleset-reader" in str(path)
                    else "catalog-reader-token"
                ),
            ),
            patch.object(
                api,
                "CRITERIA_RULESET_READ_TOKEN_FILES_JSON",
                '{"eligibility-alt":"/run/secrets/alt-ruleset-reader"}',
            ),
            patch.object(
                api,
                "CRITERIA_RULESET_WRITE_TOKEN_FILES_JSON",
                '{"eligibility-alt":"/run/secrets/alt-ruleset-token"}',
            ),
            patch("api.connect_public_catalog_ingest_db", return_value=nullcontext()),
            patch(
                "api.read_active_criteria_ruleset", return_value=ruleset_result
            ) as read_ruleset,
            patch(
                "api.register_criteria_ruleset",
                return_value={"status": "stored", "program_id": "usda-csfp"},
            ) as write_ruleset,
            patch(
                "api.list_active_profile_schemas",
                return_value=[
                    {
                        "registry_id": "core-schema-registry",
                        "schema_version": "core-v4",
                        "attributes": {"core.person.age_band": {}},
                        "dimensions": {"core.person": "Person"},
                    }
                ],
            ) as read_schemas,
        ):
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                read_url = (
                    f"{base}/v1/eligibility/clusters/eligibility-alt/"
                    "criteria-rulesets/usda-csfp"
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(
                        Request(
                            read_url,
                            headers={"Authorization": "Bearer catalog-reader-token"},
                        ),
                        timeout=2,
                    )
                self.assertEqual(error.exception.code, 401)
                with urlopen(
                    Request(
                        read_url,
                        headers={"Authorization": "Bearer alt-reader-token"},
                    ),
                    timeout=2,
                ) as response:
                    read_result = json.loads(response.read())

                schema_url = f"{base}/v1/profile-schemas/core-schema-registry/active"
                with urlopen(
                    Request(
                        schema_url,
                        headers={"Authorization": "Bearer catalog-reader-token"},
                    ),
                    timeout=2,
                ) as response:
                    schema_result = json.loads(response.read())

                write_url = (
                    f"{base}/v1/eligibility/clusters/eligibility-alt/criteria-rulesets"
                )
                body = json.dumps(payload).encode()
                with self.assertRaises(HTTPError) as error:
                    urlopen(
                        Request(
                            write_url,
                            data=body,
                            headers={"Authorization": "Bearer catalog-reader-token"},
                            method="POST",
                        ),
                        timeout=2,
                    )
                self.assertEqual(error.exception.code, 401)
                with self.assertRaises(HTTPError) as error:
                    urlopen(
                        Request(
                            write_url,
                            data=body,
                            headers={"Authorization": "Bearer writer-token"},
                            method="POST",
                        ),
                        timeout=2,
                    )
                self.assertEqual(error.exception.code, 401)
                with urlopen(
                    Request(
                        write_url,
                        data=body,
                        headers={"Authorization": "Bearer alt-writer-token"},
                        method="POST",
                    ),
                    timeout=2,
                ) as response:
                    write_result = json.loads(response.read())
                    self.assertEqual(response.status, 201)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(read_result, ruleset_result)
        self.assertEqual(schema_result["schemas"][0]["schema_version"], "core-v4")
        self.assertEqual(write_result["status"], "stored")
        read_ruleset.assert_called_once()
        read_schemas.assert_called_once_with("core-schema-registry")
        write_ruleset.assert_called_once_with(None, payload)


class PublicDatasetPaginationTests(unittest.TestCase):
    def test_record_jurisdiction_request_requires_explicit_geographic_scope(self):
        request = {
            "dataset_id": "sam-assistance-listings-current",
            "source_record_id": "sam.gov.assistance_listings.10.539",
            "proposed_jurisdiction": {
                "level": "territory",
                "country_code": "US",
                "state_code": "MP",
            },
            "evidence_url": "https://www.fns.usda.gov/nap/cnmi-summary",
            "evidence_observed_at": "2026-11-04T12:00:00+00:00",
        }
        self.assertEqual(api._validated_record_jurisdiction_request(request), request)
        self.assertIsNone(
            api._validated_record_jurisdiction_request(
                {
                    **request,
                    "proposed_jurisdiction": {
                        "level": "territory",
                        "country_code": "US",
                    },
                }
            )
        )
        self.assertIsNone(
            api._validated_record_jurisdiction_request(
                {**request, "unexpected": "mutation"}
            )
        )

    def test_public_dataset_run_summary_projects_rejected_count_from_details(self):
        timestamp = datetime(2026, 10, 24, tzinfo=timezone.utc)
        run = api._public_dataset_run_summary(
            (
                "run-id",
                "succeeded",
                {
                    "validation_outcome": "partial",
                    "rejected_count": 2,
                },
                timestamp,
                timestamp,
                3,
                0,
                0,
                0,
            )
        )

        self.assertEqual(run["rejected_count"], 2)
        freshness = summarize_public_dataset_freshness(
            run, run, refresh_mode="operator_triggered"
        )
        self.assertEqual(freshness["counts"]["rejected_count"], 2)

    def test_public_dataset_read_returns_bounded_page_and_total_count(self):
        timestamp = datetime(2026, 10, 24, tzinfo=timezone.utc)
        dataset = (
            "test-dataset",
            "test.source",
            "Test dataset",
            "public-listing-v2",
            {"catalog_namespace": "programs", "refresh_mode": "operator_triggered"},
        )
        rows = [
            (
                f"test.source.{index}",
                "public_listing",
                {"listing.title": f"Program {index}"},
                {"catalog_namespace": "programs"},
                {"source_id": "test.source"},
                "https://example.gov/data.csv",
                f"hash-{index}",
                "public-listing-v2",
                timestamp,
            )
            for index in range(2)
        ]

        class Result:
            def __init__(self, query):
                self.query = query

            def fetchone(self):
                if "FROM mcp_saas.public_datasets AS dataset" in self.query:
                    return dataset
                if (
                    "SELECT count(*) FROM mcp_saas.active_public_dataset_records"
                    in self.query
                ):
                    return (3,)
                return None

            def fetchall(self):
                if "FROM mcp_saas.active_public_dataset_records" in self.query:
                    return rows
                return []

        class Connection:
            query = ""
            parameters = ()
            queries = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters=()):
                self.query = query
                self.parameters = parameters
                self.queries.append((query, parameters))
                return Result(query)

        connection = Connection()
        with patch("api.connect_db", return_value=connection):
            result = read_public_dataset("test-dataset", limit=2, offset=0)

        self.assertEqual(len(result["records"]), 2)
        self.assertEqual(
            result["pagination"],
            {"limit": 2, "offset": 0, "total_count": 3, "next_offset": 2},
        )
        page_query = next(
            item for item in connection.queries if "LIMIT %s OFFSET %s" in item[0]
        )
        self.assertEqual(page_query[1], ("test-dataset", 2, 0))

    def test_public_dataset_exact_record_read_filters_page_and_total(self):
        timestamp = datetime(2026, 10, 24, tzinfo=timezone.utc)
        dataset = (
            "test-dataset",
            "test.source",
            "Test dataset",
            "public-listing-v2",
            {"catalog_namespace": "programs"},
        )
        record = (
            "snap-residency",
            "public_listing",
            {"listing.title": "Residency"},
            {"catalog_namespace": "programs"},
            {"source_id": "test.source"},
            "https://example.gov/snap",
            "record-hash",
            "public-listing-v2",
            timestamp,
        )

        class Result:
            def __init__(self, query):
                self.query = query

            def fetchone(self):
                if "FROM mcp_saas.public_datasets AS dataset" in self.query:
                    return dataset
                if (
                    "SELECT count(*) FROM mcp_saas.active_public_dataset_records"
                    in self.query
                ):
                    return (1,)
                return None

            def fetchall(self):
                if "LIMIT %s OFFSET %s" in self.query:
                    return [record]
                return []

        class Connection:
            queries = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters=()):
                self.queries.append((query, parameters))
                return Result(query)

        connection = Connection()
        with patch("api.connect_db", return_value=connection):
            result = read_public_dataset(
                "test-dataset", source_record_id="snap-residency"
            )

        self.assertEqual(result["records"][0]["source_record_id"], "snap-residency")
        self.assertEqual(result["pagination"]["total_count"], 1)
        page_query = next(
            item for item in connection.queries if "LIMIT %s OFFSET %s" in item[0]
        )
        self.assertIn("AND source_record_id = %s", page_query[0])
        self.assertEqual(page_query[1], ("test-dataset", "snap-residency", 100, 0))


class PatternClusterCatalogTests(unittest.TestCase):
    def test_cluster_projection_includes_active_compatible_tenant_versions(self):
        rows = [
            (
                "federal-tenant",
                "shared-dataset",
                "0.1.4",
                {"catalog_namespace": "programs", "source_id": "federal.source"},
                "federal.source",
                "Federal listing",
                "eligibility-program-v1",
            ),
            (
                "state-tenant",
                "shared-dataset",
                "0.1.5",
                {"catalog_namespace": "programs", "source_id": "federal.source"},
                "federal.source",
                "Federal listing",
                "eligibility-program-v1",
            ),
        ]
        connection = unittest.mock.Mock()
        connection.__enter__ = unittest.mock.Mock(return_value=connection)
        connection.__exit__ = unittest.mock.Mock(return_value=False)
        connection.execute.return_value.fetchall.return_value = rows

        with patch("api.connect_db", return_value=connection):
            result = read_pattern_cluster_bindings("eligibility", "programs")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            [binding["pattern_version"] for binding in result["bindings"]],
            ["0.1.4", "0.1.5"],
        )
        binding_query = connection.execute.call_args_list[0].args[0]
        self.assertIn("binding.cluster_id = %s", binding_query)
        self.assertNotIn("binding.pattern_version = %s", binding_query)
        self.assertEqual(
            connection.execute.call_args_list[0].args[1],
            ("eligibility", "programs"),
        )

    def test_cluster_projection_reports_binding_cap(self):
        row = (
            "tenant-a",
            "dataset-a",
            "0.1.4",
            {"catalog_namespace": "programs", "source_id": "source.a"},
            "source.a",
            "Listing",
            "eligibility-program-v1",
        )
        connection = unittest.mock.Mock()
        connection.__enter__ = unittest.mock.Mock(return_value=connection)
        connection.__exit__ = unittest.mock.Mock(return_value=False)
        connection.execute.return_value.fetchall.return_value = [row] * 101

        with patch("api.connect_db", return_value=connection):
            result = read_pattern_cluster_bindings("eligibility", "programs")

        self.assertEqual(len(result["bindings"]), 100)
        self.assertTrue(result["truncated"])

    def test_cluster_projection_includes_available_unbound_inventory(self):
        connection = unittest.mock.Mock()
        connection.__enter__ = unittest.mock.Mock(return_value=connection)
        connection.__exit__ = unittest.mock.Mock(return_value=False)
        binding_result = unittest.mock.Mock()
        binding_result.fetchall.return_value = []
        inventory_result = unittest.mock.Mock()
        inventory_result.fetchall.return_value = [
            (
                "sam-assistance-listings-current",
                "sam.gov.assistance_listings",
                "SAM.gov Assistance Listings",
                "public-listing-v2",
                {"catalog_namespace": "programs", "visibility_scope": "cluster_public"},
            )
        ]
        connection.execute.side_effect = [binding_result, inventory_result]

        with patch("api.connect_db", return_value=connection):
            result = read_pattern_cluster_bindings("eligibility", "programs")

        self.assertEqual(result["bindings"], [])
        self.assertEqual(
            result["inventory_datasets"],
            [
                {
                    "dataset_id": "sam-assistance-listings-current",
                    "source_id": "sam.gov.assistance_listings",
                    "title": "SAM.gov Assistance Listings",
                    "schema_version": "public-listing-v2",
                    "metadata": {
                        "catalog_namespace": "programs",
                        "visibility_scope": "cluster_public",
                    },
                }
            ],
        )
        self.assertFalse(result["inventory_truncated"])
        self.assertIn("NOT EXISTS", connection.execute.call_args_list[1].args[0])
        self.assertEqual(
            connection.execute.call_args_list[1].args[1],
            ("programs", "eligibility", "programs"),
        )


class TenantLifecycleApiTests(unittest.TestCase):
    def test_governed_activation_requires_receipt_fields_and_scoped_writer(self):
        from urllib.request import Request

        tenant = "sample-tenant"
        pattern = "sample-pattern"
        version = "1.0.0"
        payload = {
            "approved_tenant_spec_sha256": "a" * 64,
            "expected_before_sha256": "b" * 64,
            "transaction_id": str(uuid.uuid4()),
            "approval_reference": "issue-786 approval",
            "operator_audit_context": {
                "mcp_session_sha256": None,
                "mcp_request_sha256": None,
                "mcp_client_identity_status": "unavailable",
                "operator_credential_label": "core_operator_token",
            },
        }
        expected = {"status": "recorded", "event": {"action": "activate"}}
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with (
            patch("api.read_secret", return_value="tenant-writer"),
            patch(
                "api.set_tenant_package_state_governed", return_value=expected
            ) as transition,
        ):
            thread.start()
            try:
                base = (
                    f"http://127.0.0.1:{server.server_port}/v1/registry/tenants/"
                    f"{tenant}/patterns/{pattern}/{version}/activate"
                )
                unauthorized = Request(
                    base,
                    data=json.dumps(payload).encode(),
                    headers={"Authorization": "Bearer wrong-token"},
                    method="POST",
                )
                from urllib.error import HTTPError

                with self.assertRaises(HTTPError) as error:
                    urlopen(unauthorized, timeout=2)
                self.assertEqual(error.exception.code, 401)
                transition.assert_not_called()

                request = Request(
                    base,
                    data=json.dumps(payload).encode(),
                    headers={"Authorization": "Bearer tenant-writer"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    result = json.loads(response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(result, expected)
        transition.assert_called_once_with(
            tenant,
            pattern,
            version,
            action="activate",
            **payload,
        )

    def test_governed_activation_rejects_missing_compare_and_set_fields(self):
        from urllib.error import HTTPError
        from urllib.request import Request

        payload = {
            "approved_tenant_spec_sha256": "a" * 64,
            "operator_audit_context": {},
        }
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with (
            patch("api.read_secret", return_value="tenant-writer"),
            patch("api.set_tenant_package_state_governed") as transition,
        ):
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/v1/registry/tenants/"
                    "sample-tenant/patterns/sample-pattern/1.0.0/activate",
                    data=json.dumps(payload).encode(),
                    headers={"Authorization": "Bearer tenant-writer"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(request, timeout=2)
                self.assertEqual(error.exception.code, 400)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
        transition.assert_not_called()

    def test_deactivation_is_digest_bound_and_limited_to_tenant_routes(self):
        connection = unittest.mock.Mock()
        connection.__enter__ = unittest.mock.Mock(return_value=connection)
        connection.__exit__ = unittest.mock.Mock(return_value=False)
        connection.execute.return_value.fetchone.return_value = ("active", "a" * 64)
        tenant = {"tenant_id": "sample-tenant", "lifecycle_state": "inactive"}
        with (
            patch("api.connect_db", return_value=connection),
            patch("api._tenant_record", return_value=tenant),
        ):
            rejected = api.deactivate_tenant_package(
                "sample-tenant", "sample-pattern", "1.0.0", "b" * 64
            )
            self.assertIsNone(rejected)
            connection.execute.reset_mock()
            result = api.deactivate_tenant_package(
                "sample-tenant", "sample-pattern", "1.0.0", "a" * 64
            )

        self.assertEqual(result["lifecycle_state"], "inactive")
        updates = [
            call.args[0]
            for call in connection.execute.call_args_list
            if call.args and call.args[0].lstrip().startswith("UPDATE")
        ]
        self.assertEqual(len(updates), 2)
        self.assertIn("UPDATE mcp_saas.tenant_registry", updates[0])
        self.assertIn("UPDATE mcp_saas.core_route_metadata", updates[1])
        self.assertEqual(
            connection.execute.call_args_list[-1].args[1],
            ("sample-tenant", "sample-pattern", "1.0.0"),
        )


class RequirementsPackageValidationTests(unittest.TestCase):
    def sample(self):
        return {
            "contract_version": "eligibility-requirements-package-v2",
            "package_id": str(uuid.uuid4()),
            "tenant_id": "tenant-demo",
            "cluster_id": "eligibility",
            "pattern_version": "0.1.0",
            "program_id": "sample-program",
            "program_title": "Sample Program",
            "catalog_version": "catalog-v1",
            "required_documents": [],
            "forms": [],
            "deadlines": [],
            "procedural_steps": [],
            "unavailable_detail_kinds": ["form"],
            "source_references": [{"source_id": "public-source"}],
            "source_health": [{"status": "current"}],
            "source_limitations": [],
            "profile_snapshot": {
                "profile_id": "synthetic-test",
                "navigator": "general",
                "state_code": "MO",
            },
            "evaluation": {
                "evaluation_performed": True,
                "criteria_evidence": [],
            },
            "created_at": "2026-10-22T00:00:00+00:00",
            "official_eligibility_determination": False,
            "official_application_submitted": False,
        }

    def test_accepts_bounded_source_grounded_packet(self):
        self.assertIsNotNone(api.validate_requirements_package(self.sample()))

    def test_accepts_existing_v1_package_shape(self):
        package = self.sample()
        package["contract_version"] = "eligibility-requirements-package-v1"
        package.pop("program_title")
        package.pop("profile_snapshot")
        package.pop("evaluation")
        self.assertIsNotNone(api.validate_requirements_package(package))

    def test_accepts_user_profile_snapshot_and_rejects_noncurrent_source_health(self):
        package = self.sample()
        package["profile_snapshot"]["annual_household_income_usd"] = 12000
        self.assertIsNotNone(api.validate_requirements_package(package))
        package = self.sample()
        package["source_health"] = [{"status": "stale"}]
        self.assertIsNone(api.validate_requirements_package(package))

    def test_rejects_official_submission_and_extra_fields(self):
        package = self.sample()
        package["official_application_submitted"] = True
        self.assertIsNone(api.validate_requirements_package(package))
        package = self.sample()
        package["extra"] = "not allowed"
        self.assertIsNone(api.validate_requirements_package(package))

    def test_storage_is_idempotent_and_rejects_changed_content(self):
        package = self.sample()
        stored = None

        class Connection:
            def execute(self, query, params):
                nonlocal stored
                if "FROM mcp_saas.tenant_registry" in query:
                    return Cursor(("active",))
                if query.lstrip().startswith("INSERT"):
                    if stored is None:
                        stored = (json.loads(params[2]), params[3])
                        return Cursor((stored[0],))
                    return Cursor(None)
                return Cursor(stored)

        class Cursor:
            def __init__(self, row):
                self.row = row

            def fetchone(self):
                return self.row

        with patch.object(api, "connect_db", return_value=nullcontext(Connection())):
            first, created = api.write_requirements_package(package)
            replay, replay_created = api.write_requirements_package(package)
            self.assertTrue(created)
            self.assertFalse(replay_created)
            self.assertEqual(first, replay)
            package["source_limitations"] = ["changed content"]
            with self.assertRaises(api.TemplatePackageConflict):
                api.write_requirements_package(package)

    def test_storage_rejects_unregistered_or_inactive_tenant(self):
        package = self.sample()

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, params):
                if "FROM mcp_saas.tenant_registry" in query:
                    return Cursor()
                raise AssertionError("Out-of-scope package must not be written.")

        class Cursor:
            def fetchone(self):
                return None

        with patch.object(api, "connect_db", return_value=Connection()):
            with self.assertRaises(api.RequirementsPackageOutOfScope):
                api.write_requirements_package(package)

    def test_http_package_write_and_receipt_readback_use_scoped_token(self):
        from urllib.error import HTTPError
        from urllib.request import Request

        package = self.sample()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with (
            patch(
                "api.read_secret",
                side_effect=lambda path: (
                    "package-token"
                    if "requirements-package-write" in str(path)
                    else "other-token"
                ),
            ),
            patch("api.write_requirements_package", return_value=(package, True)),
            patch(
                "api.read_requirements_package_state",
                return_value=(package, 1),
            ),
        ):
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                unauthorized = Request(
                    f"{base}/v1/eligibility/requirements-packages",
                    data=json.dumps(package).encode(),
                    headers={"Authorization": "Bearer other-token"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(unauthorized, timeout=2)
                self.assertEqual(error.exception.code, 401)
                request = Request(
                    f"{base}/v1/eligibility/requirements-packages",
                    data=json.dumps(package).encode(),
                    headers={"Authorization": "Bearer package-token"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    receipt = json.loads(response.read())
                read_request = Request(
                    f"{base}{receipt['retrieval_path']}",
                    headers={"Authorization": "Bearer package-token"},
                )
                with urlopen(read_request, timeout=2) as response:
                    readback = json.loads(response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
        self.assertEqual(receipt["status"], "stored")
        self.assertEqual(receipt["package_id"], package["package_id"])
        self.assertEqual(readback["package"], package)

    def test_http_package_list_is_profile_scoped_and_bounded(self):
        from urllib.error import HTTPError
        from urllib.request import Request

        listing = {
            "status": "available",
            "packages": [
                {
                    "package_id": str(uuid.uuid4()),
                    "program_id": "extra-help",
                    "program_title": "Extra Help",
                    "created_at": "2026-11-07T00:00:00+00:00",
                }
            ],
            "truncated": False,
            "next_cursor": None,
        }
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with (
            patch(
                "api.read_secret",
                side_effect=lambda path: (
                    "package-token"
                    if "requirements-package-write" in str(path)
                    else "other-token"
                ),
            ),
            patch(
                "api.list_requirements_packages", return_value=listing
            ) as list_packages,
        ):
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                unauthorized = Request(
                    f"{base}/v1/eligibility/requirements-packages?tenant_id=tenant-demo&profile_id=synthetic-test",
                    headers={"Authorization": "Bearer other-token"},
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(unauthorized, timeout=2)
                self.assertEqual(error.exception.code, 401)

                request = Request(
                    f"{base}/v1/eligibility/requirements-packages?tenant_id=tenant-demo&profile_id=synthetic-test&limit=10",
                    headers={"Authorization": "Bearer package-token"},
                )
                with urlopen(request, timeout=2) as response:
                    result = json.loads(response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
        self.assertEqual(result, listing)
        list_packages.assert_called_once_with(
            "tenant-demo", "synthetic-test", limit=10, cursor=None
        )

    def test_http_scoped_package_read_hides_other_profiles(self):
        from urllib.request import Request

        package_id = str(uuid.uuid4())
        package = self.sample()
        package["package_id"] = package_id
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with (
            patch("api.read_secret", return_value="package-token"),
            patch(
                "api.read_requirements_package_state",
                return_value=(package, 1),
            ) as read,
        ):
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                request = Request(
                    f"{base}/v1/eligibility/requirements-packages/{package_id}?tenant_id=tenant-demo&profile_id=synthetic-test",
                    headers={"Authorization": "Bearer package-token"},
                )
                with urlopen(request, timeout=2) as response:
                    result = json.loads(response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
        self.assertEqual(result, {"status": "ok", "package": package, "revision": 1})
        read.assert_called_once_with(package_id, "tenant-demo", "synthetic-test")

    def test_scoped_reader_queries_by_tenant_and_profile(self):
        package = self.sample()
        calls = []

        class Cursor:
            def fetchone(self):
                return (package,)

        class Connection:
            def execute(self, query, params):
                calls.append((query, params))
                return Cursor()

        with patch("api.connect_db", return_value=nullcontext(Connection())):
            result = api.read_scoped_requirements_package(
                package["package_id"], "tenant-demo", "synthetic-test"
            )

        self.assertEqual(result, package)
        self.assertIn("definition->'profile_snapshot'->>'profile_id'=%s", calls[0][0])
        self.assertEqual(
            calls[0][1], (package["package_id"], "tenant-demo", "synthetic-test")
        )

    def test_package_list_uses_keyset_pagination_without_profile_values(self):
        package_id = str(uuid.uuid4())
        rows = [
            (
                package_id,
                "extra-help",
                "Extra Help",
                datetime(2026, 11, 7, tzinfo=timezone.utc),
            ),
            (
                str(uuid.uuid4()),
                "medicare-savings",
                "Medicare Savings",
                datetime(2026, 11, 6, tzinfo=timezone.utc),
            ),
        ]
        captured = []

        class Cursor:
            def fetchall(self):
                return rows

        class Connection:
            def execute(self, query, params):
                captured.append((query, params))
                return Cursor()

        with patch("api.connect_db", return_value=nullcontext(Connection())):
            result = api.list_requirements_packages(
                "tenant-demo", "synthetic-test", limit=1
            )

        self.assertEqual(
            result["packages"],
            [
                {
                    "package_id": package_id,
                    "program_id": "extra-help",
                    "program_title": "Extra Help",
                    "created_at": "2026-11-07T00:00:00+00:00",
                }
            ],
        )
        self.assertTrue(result["truncated"])
        self.assertIsInstance(result["next_cursor"], str)
        selected_columns = captured[0][0].split("FROM", 1)[0]
        self.assertNotIn("profile_snapshot", selected_columns)
        self.assertIn(
            "definition->'profile_snapshot'->>'profile_id'=%s", captured[0][0]
        )
        self.assertEqual(captured[0][1], ("tenant-demo", "synthetic-test", 2))

    def test_package_enrichment_validation_requires_citations_and_revision(self):
        payload = {
            "tenant_id": "tenant-demo",
            "profile_id": "synthetic-test",
            "expected_revision": 1,
            "details": [
                {
                    "kind": "application_step",
                    "text": "Call the program office.",
                    "citation": {
                        "source_url": "https://example.gov/program",
                        "source_record_id": "record-1",
                        "retrieved_at": "2026-11-07T00:00:00+00:00",
                        "source_id": "public-source",
                        "source_version": "2026-11-01",
                        "source_content_sha256": "a" * 64,
                        "locator": "Application",
                        "excerpt": "Call the program office.",
                    },
                }
            ],
        }
        self.assertEqual(api.validate_requirements_package_enrichment(payload), payload)
        payload["expected_revision"] = True
        self.assertIsNone(api.validate_requirements_package_enrichment(payload))

    def test_package_enrichment_reconciles_fresh_observation_by_exact_saved_url(self):
        package = self.sample()
        package["source_references"] = [
            {
                "source_id": "us.slha.housing-choice-voucher",
                "url": "https://www.slha.org/find-a-home/housing-choice-voucher-program/",
                "retrieved_at": "2026-11-04T21:17:53+00:00",
                "publisher_version": "reviewed-2026-11-04",
            }
        ]
        original = copy.deepcopy(package)
        source_hash = "b" * 64
        details = [
            {
                "kind": "application_step",
                "text": "Contact the housing authority.",
                "citation": {
                    "source_id": "public-domain.org",
                    "source_url": package["source_references"][0]["url"],
                    "source_record_id": "source-page-fresh-observation",
                    "retrieved_at": "2026-11-13T18:55:10+00:00",
                    "source_version": f"sha256:{source_hash}",
                    "source_content_sha256": source_hash,
                    "locator": "How to apply",
                    "excerpt": "Contact the housing authority.",
                },
            }
        ]

        latest = None
        inserts = []

        class Cursor:
            def __init__(self, row):
                self.row = row

            def fetchone(self):
                return self.row

        class Connection:
            def execute(self, query, params):
                nonlocal latest
                if "pg_advisory_xact_lock" in query:
                    return Cursor(None)
                if "FROM mcp_saas.eligibility_requirements_packages" in query:
                    return Cursor((package,))
                if "FROM mcp_saas.eligibility_requirements_package_revisions" in query:
                    return Cursor(latest)
                if query.lstrip().startswith("INSERT"):
                    latest = (params[1], json.loads(params[2]), params[3])
                    inserts.append(params)
                    return Cursor(None)
                raise AssertionError(f"Unexpected query: {query}")

        with patch("api.connect_db", return_value=nullcontext(Connection())):
            enriched, revision, _digest, created = api.enrich_requirements_package(
                package["package_id"],
                {
                    "tenant_id": "tenant-demo",
                    "profile_id": "synthetic-test",
                    "expected_revision": 1,
                    "details": details,
                },
            )
            replay, replay_revision, _replay_digest, replay_created = (
                api.enrich_requirements_package(
                    package["package_id"],
                    {
                        "tenant_id": "tenant-demo",
                        "profile_id": "synthetic-test",
                        "expected_revision": 1,
                        "details": details,
                    },
                )
            )

        self.assertEqual(package, original)
        self.assertEqual(revision, 2)
        self.assertTrue(created)
        self.assertEqual(replay_revision, 2)
        self.assertFalse(replay_created)
        self.assertEqual(enriched, replay)
        self.assertEqual(len(inserts), 1)
        self.assertEqual(
            enriched["procedural_steps"][0]["source_id"],
            "us.slha.housing-choice-voucher",
        )
        saved_citation = enriched["evaluation"]["requirements_detail_citations"][0][
            "citation"
        ]
        self.assertEqual(saved_citation["source_id"], "us.slha.housing-choice-voucher")
        self.assertEqual(
            saved_citation["source_record_id"], "source-page-fresh-observation"
        )
        self.assertEqual(saved_citation["retrieved_at"], "2026-11-13T18:55:10+00:00")
        self.assertEqual(saved_citation["source_version"], f"sha256:{source_hash}")
        self.assertEqual(enriched["source_references"], original["source_references"])

    def test_package_enrichment_rejects_unmatched_and_ambiguous_urls_without_insert(
        self,
    ):
        package = self.sample()
        package_id = package["package_id"]
        saved_url = "https://www.slha.org/find-a-home/housing-choice-voucher-program/"
        package["source_references"] = [
            {
                "source_id": "us.slha.housing-choice-voucher",
                "url": saved_url,
                "retrieved_at": "2026-11-04T21:17:53+00:00",
                "publisher_version": "reviewed-2026-11-04",
            }
        ]
        source_hash = "c" * 64

        class Cursor:
            def __init__(self, row):
                self.row = row

            def fetchone(self):
                return self.row

        for case, source_references, citation_url in (
            ("unmatched", package["source_references"], "https://example.org/other"),
            (
                "ambiguous",
                [
                    *package["source_references"],
                    {
                        **package["source_references"][0],
                        "source_id": "another-canonical-id",
                    },
                ],
                saved_url,
            ),
        ):
            with self.subTest(case=case):
                write_count = 0
                package_with_refs = {
                    **package,
                    "source_references": source_references,
                }

                class Connection:
                    def execute(self, query, params):
                        nonlocal write_count
                        if "pg_advisory_xact_lock" in query:
                            return Cursor(None)
                        if "FROM mcp_saas.eligibility_requirements_packages" in query:
                            return Cursor((package_with_refs,))
                        if (
                            "FROM mcp_saas.eligibility_requirements_package_revisions"
                            in query
                        ):
                            return Cursor(None)
                        if query.lstrip().startswith("INSERT"):
                            write_count += 1
                            return Cursor(None)
                        raise AssertionError(f"Unexpected query: {query}")

                payload = {
                    "tenant_id": "tenant-demo",
                    "profile_id": "synthetic-test",
                    "expected_revision": 1,
                    "details": [
                        {
                            "kind": "application_step",
                            "text": "Contact the housing authority.",
                            "citation": {
                                "source_id": "public-domain.org",
                                "source_url": citation_url,
                                "source_record_id": "source-page-fresh-observation",
                                "retrieved_at": "2026-11-13T18:55:10+00:00",
                                "source_version": f"sha256:{source_hash}",
                                "source_content_sha256": source_hash,
                                "locator": "How to apply",
                                "excerpt": "Contact the housing authority.",
                            },
                        }
                    ],
                }
                with patch("api.connect_db", return_value=nullcontext(Connection())):
                    with self.assertRaises(ValueError):
                        api.enrich_requirements_package(package_id, payload)
                self.assertEqual(write_count, 0)

    def test_package_enrichment_appends_revision_and_replays_idempotently(self):
        package = self.sample()
        package["package_id"] = str(uuid.uuid4())
        package["source_references"] = [
            {
                "source_id": "public-source",
                "url": "https://example.gov/program",
                "retrieved_at": "2026-11-07T00:00:00+00:00",
                "publisher_version": "2026-11-01",
            }
        ]
        payload = {
            "tenant_id": "tenant-demo",
            "profile_id": "synthetic-test",
            "expected_revision": 1,
            "details": [
                {
                    "kind": "application_step",
                    "text": "Call the program office.",
                    "citation": {
                        "source_url": "https://example.gov/program",
                        "source_record_id": "record-1",
                        "retrieved_at": "2026-11-07T00:00:00+00:00",
                        "source_id": "public-source",
                        "source_version": "2026-11-01",
                        "source_content_sha256": "a" * 64,
                        "locator": "Application",
                        "excerpt": "Call the program office.",
                    },
                }
            ],
        }
        latest = None
        inserts = []
        calls = []

        class Cursor:
            def __init__(self, row):
                self.row = row

            def fetchone(self):
                return self.row

        class Connection:
            def execute(self, query, params):
                nonlocal latest
                calls.append((query, params))
                if "pg_advisory_xact_lock" in query:
                    return Cursor(None)
                if "FROM mcp_saas.eligibility_requirements_packages" in query:
                    return Cursor((package,))
                if "FROM mcp_saas.eligibility_requirements_package_revisions" in query:
                    return Cursor(latest)
                if query.lstrip().startswith("INSERT"):
                    latest = (params[1], json.loads(params[2]), params[3])
                    inserts.append(params)
                    return Cursor(None)
                raise AssertionError(f"Unexpected query: {query}")

        with patch("api.connect_db", return_value=nullcontext(Connection())):
            first = api.enrich_requirements_package(package["package_id"], payload)
            replay = api.enrich_requirements_package(package["package_id"], payload)

        self.assertEqual(first[1], 2)
        self.assertTrue(first[3])
        self.assertEqual(replay[1], 2)
        self.assertFalse(replay[3])
        self.assertEqual(first[0], replay[0])
        self.assertEqual(len(inserts), 1)
        self.assertIn("pg_advisory_xact_lock", calls[0][0])
        self.assertIn("eligibility-requirements-package:", calls[0][1][0])
        package_select = next(
            query
            for query, _ in calls
            if "FROM mcp_saas.eligibility_requirements_packages" in query
        )
        self.assertNotIn("FOR UPDATE", package_select)
        self.assertEqual(first[0]["evaluation"]["criteria_evidence"], [])
        self.assertEqual(first[0]["procedural_steps"][0]["source_id"], "public-source")

    def test_package_enrichment_stale_revision_returns_conflict_without_insert(self):
        package = self.sample()
        package["package_id"] = str(uuid.uuid4())
        package["source_references"] = [
            {
                "source_id": "public-source",
                "url": "https://example.gov/program",
                "retrieved_at": "2026-11-07T00:00:00+00:00",
                "publisher_version": "2026-11-01",
            }
        ]
        latest = (2, package, "b" * 64)

        class Cursor:
            def __init__(self, row):
                self.row = row

            def fetchone(self):
                return self.row

        class Connection:
            def execute(self, query, params):
                if "pg_advisory_xact_lock" in query:
                    return Cursor(None)
                if "FROM mcp_saas.eligibility_requirements_packages" in query:
                    return Cursor((package,))
                if "FROM mcp_saas.eligibility_requirements_package_revisions" in query:
                    return Cursor(latest)
                raise AssertionError("Stale revision must not write.")

        with patch("api.connect_db", return_value=nullcontext(Connection())):
            with self.assertRaises(api.RequirementsPackageRevisionConflict) as error:
                api.enrich_requirements_package(
                    package["package_id"],
                    {
                        "tenant_id": "tenant-demo",
                        "profile_id": "synthetic-test",
                        "expected_revision": 1,
                        "details": [
                            {
                                "kind": "application_step",
                                "text": "A different step.",
                                "citation": {
                                    "source_url": "https://example.gov/program",
                                    "source_record_id": "record-1",
                                    "retrieved_at": "2026-11-07T00:00:00+00:00",
                                    "source_id": "public-source",
                                    "source_version": "2026-11-01",
                                    "source_content_sha256": "a" * 64,
                                    "locator": "Application",
                                    "excerpt": "A different step.",
                                },
                            }
                        ],
                    },
                )
        self.assertEqual(error.exception.current_revision, 2)

    def test_http_package_enrichment_requires_scoped_token_and_returns_revision(self):
        from urllib.error import HTTPError
        from urllib.request import Request

        package = self.sample()
        package_id = package["package_id"]
        payload = {
            "tenant_id": "tenant-demo",
            "profile_id": "synthetic-test",
            "expected_revision": 1,
            "details": [
                {
                    "kind": "application_step",
                    "text": "Call the program office.",
                    "citation": {
                        "source_url": "https://example.gov/program",
                        "source_record_id": "record-1",
                        "retrieved_at": "2026-11-07T00:00:00+00:00",
                        "source_id": "public-source",
                        "source_version": "2026-11-01",
                        "source_content_sha256": "a" * 64,
                        "locator": "Application",
                        "excerpt": "Call the program office.",
                    },
                }
            ],
        }
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with (
            patch("api.read_secret", return_value="package-token"),
            patch(
                "api.enrich_requirements_package",
                return_value=(package, 2, "c" * 64, True),
            ) as enrich,
        ):
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                unauthorized = Request(
                    f"{base}/v1/eligibility/requirements-packages/{package_id}/enrich",
                    data=json.dumps(payload).encode(),
                    headers={"Authorization": "Bearer other-token"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(unauthorized, timeout=2)
                self.assertEqual(error.exception.code, 401)
                request = Request(
                    f"{base}/v1/eligibility/requirements-packages/{package_id}/enrich",
                    data=json.dumps(payload).encode(),
                    headers={"Authorization": "Bearer package-token"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    receipt = json.loads(response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
        self.assertEqual(receipt["status"], "enriched")
        self.assertEqual(receipt["revision"], 2)
        self.assertEqual(receipt["details_sha256"], "c" * 64)
        enrich.assert_called_once_with(package_id, payload)

    def test_http_package_enrichment_redacts_storage_error_details(self):
        from urllib.error import HTTPError
        from urllib.request import Request

        package = self.sample()
        package_id = package["package_id"]
        payload = {
            "tenant_id": "tenant-demo",
            "profile_id": "synthetic-test",
            "expected_revision": 1,
            "details": [],
        }
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with (
            patch("api.read_secret", return_value="package-token"),
            patch(
                "api.enrich_requirements_package",
                side_effect=RuntimeError("private database detail"),
            ),
            patch("builtins.print") as logged,
        ):
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}"
                    f"/v1/eligibility/requirements-packages/{package_id}/enrich",
                    data=json.dumps(payload).encode(),
                    headers={"Authorization": "Bearer package-token"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(request, timeout=2)
                self.assertEqual(error.exception.code, 503)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        log_text = " ".join(str(call.args[0]) for call in logged.call_args_list)
        self.assertIn(
            "data_api_error operation=requirements_package_enrichment "
            "error_class=RuntimeError sqlstate=unavailable",
            log_text,
        )
        self.assertNotIn("private database detail", log_text)


class PublicDatasetFreshnessTests(unittest.TestCase):
    @staticmethod
    def run_summary(status, *, details=None, duplicate_count=0):
        return {
            "run_id": f"run-{status}",
            "status": status,
            "details": details or {},
            "started_at": "2026-10-20T00:00:00+00:00",
            "completed_at": "2026-10-20T00:00:01+00:00",
            "fetched_count": 1 if status == "succeeded" else 0,
            "inserted_count": 0,
            "updated_count": 0,
            "duplicate_count": duplicate_count,
            "rejected_count": 0,
        }

    def test_success_is_current_only_as_of_last_check(self):
        success = self.run_summary(
            "succeeded",
            details={
                "source_version": '"etag-1"',
                "retrieved_at": "2026-10-20T00:00:00+00:00",
                "validation_outcome": "valid",
            },
            duplicate_count=1,
        )

        result = summarize_public_dataset_freshness(
            success, success, refresh_mode="operator_triggered"
        )

        self.assertEqual(result["health_status"], "healthy")
        self.assertEqual(result["freshness_status"], "fresh_as_of_last_check")
        self.assertEqual(result["source_version"], '"etag-1"')
        self.assertEqual(result["counts"]["duplicate_count"], 1)
        self.assertIsNone(result["time_based_stale_after_seconds"])

    def test_failed_latest_check_is_stale_and_retains_last_success_version(self):
        success = self.run_summary(
            "succeeded",
            details={
                "source_version": '"etag-good"',
                "retrieved_at": "2026-10-19T00:00:00+00:00",
                "validation_outcome": "valid",
            },
        )
        failure = self.run_summary(
            "failed",
            details={"source_version": None, "validation_outcome": "unavailable"},
        )

        result = summarize_public_dataset_freshness(
            failure, success, refresh_mode="operator_triggered"
        )

        self.assertEqual(result["health_status"], "degraded")
        self.assertEqual(result["freshness_status"], "stale")
        self.assertEqual(result["source_version"], '"etag-good"')
        self.assertEqual(
            result["last_successful_retrieval_at"], "2026-10-19T00:00:00+00:00"
        )

    def test_failed_check_without_prior_success_is_unavailable(self):
        failure = self.run_summary(
            "blocked", details={"validation_outcome": "unavailable"}
        )

        result = summarize_public_dataset_freshness(
            failure, None, refresh_mode="operator_triggered"
        )

        self.assertEqual(result["health_status"], "degraded")
        self.assertEqual(result["freshness_status"], "unavailable")

    def test_successful_refresh_recovers_from_stale_state(self):
        recovery = self.run_summary(
            "succeeded",
            details={
                "source_version": '"etag-recovered"',
                "retrieved_at": "2026-10-20T00:10:00+00:00",
                "validation_outcome": "valid",
            },
        )

        result = summarize_public_dataset_freshness(
            recovery, recovery, refresh_mode="operator_triggered"
        )

        self.assertEqual(result["health_status"], "healthy")
        self.assertEqual(result["freshness_status"], "fresh_as_of_last_check")
        self.assertEqual(result["source_version"], '"etag-recovered"')

    def test_no_run_is_unknown(self):
        result = summarize_public_dataset_freshness(
            None, None, refresh_mode="operator_triggered"
        )

        self.assertEqual(result["health_status"], "unknown")
        self.assertEqual(result["freshness_status"], "unknown")

    def test_partial_success_is_degraded_and_reports_rejected_count(self):
        partial = self.run_summary(
            "succeeded",
            details={
                "source_version": "v2",
                "retrieved_at": "2026-10-20T00:00:00+00:00",
                "validation_outcome": "partial",
                "rejected_count": 2,
                "rejection_reasons": [{"code": "missing_required_field", "count": 2}],
            },
        )
        partial["rejected_count"] = 2

        result = summarize_public_dataset_freshness(
            partial, partial, refresh_mode="operator_triggered"
        )

        self.assertEqual(result["health_status"], "degraded")
        self.assertEqual(result["freshness_status"], "partial")
        self.assertEqual(result["counts"]["rejected_count"], 2)


class ProbeContractTests(unittest.TestCase):
    def test_accepts_synthetic_probe(self):
        probe_id = str(uuid.uuid4())
        result = valid_probe({"probe_id": probe_id, "label": "mcp-saas-e2e-readback"})
        self.assertEqual(result[0], probe_id)
        self.assertEqual(result[1]["kind"], "synthetic-connectivity-probe")

    def test_rejects_extra_fields_and_real_data_labels(self):
        probe_id = str(uuid.uuid4())
        self.assertIsNone(
            valid_probe(
                {"probe_id": probe_id, "label": "mcp-saas-e2e-x", "sql": "DROP TABLE"}
            )
        )
        self.assertIsNone(
            valid_probe({"probe_id": probe_id, "label": "real-user-data"})
        )


class CatalogScopeTests(unittest.TestCase):
    def test_pattern_binding_payload_is_bounded_and_exact(self):
        payload = {
            "tenant_id": "eligibility-demo",
            "cluster_id": "eligibility",
            "pattern_version": "0.1.0",
            "dataset_id": "usda-fsa-beginning-farmers-guidance",
            "binding_context": {
                "catalog_namespace": "programs",
                "catalog_version": "eligibility-demo-v1",
                "source_id": "usda.fsa.beginning-farmers",
            },
        }
        self.assertEqual(valid_pattern_catalog_binding(payload), payload)
        self.assertIsNone(
            valid_pattern_catalog_binding({**payload, "sql": "DROP TABLE"})
        )
        self.assertIsNone(
            valid_pattern_catalog_binding(
                {**payload, "binding_context": {"catalog_namespace": "other"}}
            )
        )

    def test_binding_write_requires_active_template_namespace_and_available_dataset(
        self,
    ):
        class Result:
            def __init__(self, row):
                self.row = row

            def fetchone(self):
                return self.row

        class Connection:
            queries = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters):
                self.queries.append((query, parameters))
                if "pattern_template_packages" in query:
                    return Result(({"catalog_namespaces": ["programs"]},))
                if "public_datasets" in query:
                    return Result(("usda.fsa.beginning-farmers",))
                return Result(None)

        connection = Connection()
        binding = valid_pattern_catalog_binding(
            {
                "tenant_id": "eligibility-demo",
                "cluster_id": "eligibility",
                "pattern_version": "0.1.0",
                "dataset_id": "usda-fsa-beginning-farmers-guidance",
                "binding_context": {
                    "catalog_namespace": "programs",
                    "catalog_version": "eligibility-demo-v1",
                    "source_id": "usda.fsa.beginning-farmers",
                },
            }
        )
        with patch("api.connect_db", return_value=connection):
            result = register_pattern_catalog_binding(binding)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(connection.queries), 3)
        self.assertIn("ON CONFLICT", connection.queries[-1][0])
        self.assertEqual(
            connection.queries[-1][1][:4],
            (
                "usda-fsa-beginning-farmers-guidance",
                "eligibility",
                "0.1.0",
                "eligibility-demo",
            ),
        )

    def test_missing_or_invalid_scope_fails_closed(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(configured_catalog_scope())
        with patch.dict(
            "os.environ",
            {
                "API_CATALOG_CLUSTER_ID": "eligibility",
                "API_CATALOG_PATTERN_VERSION": "0.1.0;drop",
                "API_CATALOG_TENANT_ID": "federal-agency",
            },
            clear=True,
        ):
            self.assertIsNone(configured_catalog_scope())

    def test_catalog_read_uses_only_server_configured_scope(self):
        class Result:
            def fetchall(self):
                return [
                    (
                        "farmers-fsa-loans-reference",
                        "loan-ref-1",
                        "program-reference",
                        "public-data-v1",
                        "https://www.farmers.gov/loans",
                        datetime(2026, 10, 18, tzinfo=timezone.utc),
                    )
                ]

        class Connection:
            query = ""
            parameters = ()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters):
                self.query = query
                self.parameters = parameters
                return Result()

        connection = Connection()
        scope = ("eligibility", "0.1.0", "federal-agency")
        with patch("api.connect_db", return_value=connection):
            result = read_pattern_catalog(scope)

        self.assertEqual(connection.parameters, scope)
        self.assertIn("JOIN mcp_saas.active_public_dataset_records", connection.query)
        self.assertIn("binding.cluster_id = %s", connection.query)
        self.assertIn("binding.pattern_version = %s", connection.query)
        self.assertIn("binding.tenant_id = %s", connection.query)
        self.assertEqual(
            result["scope"],
            {
                "cluster_id": "eligibility",
                "pattern_version": "0.1.0",
                "tenant_id": "federal-agency",
            },
        )
        self.assertEqual(
            result["records"][0]["dataset_id"], "farmers-fsa-loans-reference"
        )

    def test_public_dataset_read_requires_exact_tenant_pattern_binding(self):
        class Result:
            def fetchone(self):
                return (
                    {
                        "catalog_namespace": "programs",
                        "source_id": "usda.fsa.beginning-farmers",
                    },
                    "usda.fsa.beginning-farmers",
                )

            def fetchall(self):
                return [self.fetchone()]

        class Connection:
            parameters = ()
            query = ""

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters):
                self.query = query
                self.parameters = parameters
                return Result()

        connection = Connection()
        public_result = {
            "status": "ok",
            "dataset": {"dataset_id": "fsa-dataset"},
            "records": [{"source_record_id": "fsa-record"}],
        }
        with (
            patch("api.connect_db", return_value=connection),
            patch("api.read_public_dataset", return_value=public_result) as read,
        ):
            result = read_tenant_public_dataset(
                "eligibility-demo",
                "eligibility",
                "0.1.0",
                "fsa-dataset",
            )

        self.assertEqual(
            connection.parameters,
            (
                "eligibility-demo",
                "eligibility",
                "0.1.0",
                "fsa-dataset",
                None,
                None,
            ),
        )
        self.assertIn("mcp_saas.pattern_catalog_bindings", connection.query)
        self.assertIn("CAST(%s AS text) IS NULL", connection.query)
        self.assertEqual(
            result["tenant_scope"],
            {
                "tenant_id": "eligibility-demo",
                "cluster_id": "eligibility",
                "pattern_version": "0.1.0",
            },
        )
        self.assertEqual(
            result["binding_context"],
            {
                "catalog_namespace": "programs",
                "source_id": "usda.fsa.beginning-farmers",
            },
        )
        read.assert_called_once_with("fsa-dataset")

    def test_public_dataset_read_denies_an_unbound_dataset(self):
        class Result:
            def fetchone(self):
                return None

            def fetchall(self):
                return []

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters):
                return Result()

        with (
            patch("api.connect_db", return_value=Connection()),
            patch("api.read_public_dataset") as read,
        ):
            result = read_tenant_public_dataset(
                "eligibility-demo",
                "eligibility",
                "0.1.0",
                "unbound-dataset",
            )

        self.assertIsNone(result)
        read.assert_not_called()

    def test_binding_write_requires_registry_writer_capability(self):
        binding = {
            "tenant_id": "eligibility-demo",
            "cluster_id": "eligibility",
            "pattern_version": "0.1.0",
            "dataset_id": "usda-fsa-beginning-farmers-guidance",
            "binding_context": {
                "catalog_namespace": "programs",
                "catalog_version": "eligibility-demo-v1",
                "source_id": "usda.fsa.beginning-farmers",
            },
        }
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with (
            patch("api.read_secret", return_value="registry-writer"),
            patch(
                "api.register_pattern_catalog_binding",
                return_value={
                    "status": "ok",
                    **binding,
                },
            ) as register,
        ):
            thread.start()
            try:
                from urllib.request import Request

                request = Request(
                    f"http://127.0.0.1:{server.server_port}"
                    "/v1/registry/pattern-catalog-bindings",
                    data=json.dumps(binding).encode(),
                    headers={
                        "Authorization": "Bearer registry-writer",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    payload = json.loads(response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
        self.assertEqual(payload["status"], "ok")
        register.assert_called_once_with(valid_pattern_catalog_binding(binding))

    def test_http_catalog_read_ignores_caller_scope_parameters(self):
        class Result:
            def fetchall(self):
                return []

        class Connection:
            parameters = ()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters):
                self.parameters = parameters
                return Result()

        connection = Connection()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with (
            patch.dict(
                os.environ,
                {
                    "API_CATALOG_CLUSTER_ID": "eligibility",
                    "API_CATALOG_PATTERN_VERSION": "0.1.0",
                    "API_CATALOG_TENANT_ID": "federal-agency",
                    "API_TOKEN_FILE": "/unused/api-token",
                },
            ),
            patch("api.read_secret", return_value="test-token"),
            patch("api.connect_db", return_value=connection),
        ):
            thread.start()
            try:
                url = (
                    f"http://127.0.0.1:{server.server_port}"
                    "/v1/pattern-catalog/records?tenant_id=state-kansas"
                )
                from urllib.request import Request

                request = Request(url, headers={"Authorization": "Bearer test-token"})
                with urlopen(request, timeout=2) as response:
                    payload = json.loads(response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(
            connection.parameters, ("eligibility", "0.1.0", "federal-agency")
        )
        self.assertEqual(
            payload["scope"],
            {
                "cluster_id": "eligibility",
                "pattern_version": "0.1.0",
                "tenant_id": "federal-agency",
            },
        )


class CoreRouteLookupTests(unittest.TestCase):
    def test_ambiguous_route_metadata_fails_closed(self):
        class Result:
            def fetchall(self):
                return [
                    (
                        "route.probe",
                        "1.0.0",
                        "lookup",
                        "http://handler-a.svc.cluster.local/mcp",
                        "lookup",
                        "mcp",
                    ),
                    (
                        "route.probe",
                        "1.0.0",
                        "lookup",
                        "http://handler-b.svc.cluster.local/mcp",
                        "lookup",
                        "mcp",
                    ),
                ]

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters):
                return Result()

        with patch("api.connect_db", return_value=Connection()):
            with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                read_core_route("route.probe", "lookup")

    def test_route_inventory_is_bounded_and_omits_endpoint(self):
        class Result:
            def fetchall(self):
                return [
                    (
                        "route.probe",
                        "1.0.0",
                        "lookup",
                        "lookup",
                        "mcp",
                        "active",
                        None,
                        None,
                    ),
                    (
                        "route.probe",
                        "1.0.0",
                        "lookup.next",
                        "lookup_next",
                        "mcp",
                        "inactive",
                        None,
                        None,
                    ),
                ]

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters):
                self.query = query
                self.parameters = parameters
                return Result()

        connection = Connection()
        with patch("api.connect_db", return_value=connection):
            inventory = read_core_routes(limit=1)

        self.assertEqual(connection.parameters, (2,))
        self.assertEqual(inventory["truncated"], True)
        self.assertEqual(len(inventory["routes"]), 1)
        self.assertNotIn("endpoint", inventory["routes"][0])


class TenantCatalogApiTests(unittest.TestCase):
    def test_catalog_query_binds_all_tenant_and_pattern_dimensions(self):
        class Result:
            def fetchall(self):
                return [
                    (
                        "synthetic-program",
                        "Synthetic Program",
                        "fixture-source",
                        "federal",
                        "demo-v1",
                        "core-v1",
                        None,
                        {"core.program.category": "agriculture"},
                        {"record_type": "synthetic"},
                    )
                ]

        class Connection:
            query = ""
            parameters = ()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters):
                self.query = query
                self.parameters = parameters
                return Result()

        connection = Connection()
        with patch("api.connect_db", return_value=connection):
            programs = read_tenant_programs("federal-agency", "eligibility", "0.1.0")

        self.assertEqual(
            connection.parameters,
            ("federal-agency", "eligibility", "0.1.0", "federal-agency", "eligibility"),
        )
        self.assertIn("tenant.lifecycle_state = 'active'", connection.query)
        self.assertEqual(programs[0]["program_id"], "synthetic-program")
        self.assertEqual(
            programs[0]["attributes"]["core.program.category"], "agriculture"
        )

    def test_endpoint_writer_rejects_external_or_extra_fields(self):
        valid = {
            "tenant_id": "federal-agency",
            "pattern_id": "eligibility",
            "pattern_version": "0.1.0",
            "endpoint": "http://tenant.svc.cluster.local/mcp",
        }
        self.assertEqual(
            valid_tenant_endpoint(valid),
            ("federal-agency", "eligibility", "0.1.0", valid["endpoint"]),
        )
        self.assertIsNone(
            valid_tenant_endpoint({**valid, "endpoint": "https://evil.example/mcp"})
        )
        self.assertIsNone(valid_tenant_endpoint({**valid, "sql": "DROP TABLE"}))


class SyntheticProfileContractTests(unittest.TestCase):
    def test_profile_write_contract_rejects_unconfirmed_or_unbounded_fields(self):
        valid = {
            "profile_kind": "person",
            "schema_registry_id": "core-schema-registry",
            "schema_version": "core-v1",
            "attributes": [{"attribute_id": "core.person.age_band", "value": "adult"}],
            "confirmed": True,
        }
        self.assertIsNotNone(api.valid_profile_write(valid, updating=False))
        self.assertIsNone(
            api.valid_profile_write({**valid, "confirmed": False}, updating=False)
        )
        self.assertIsNone(
            api.valid_profile_write({**valid, "transcript": "raw chat"}, updating=False)
        )
        self.assertIsNone(
            api.valid_profile_write(
                {**valid, "profile_id": "real-person-001"}, updating=False
            )
        )
        self.assertIsNone(
            api.valid_profile_write(
                {**valid, "profile_id": "synthetic-person-001"}, updating=False
            )
        )
        self.assertIsNone(
            api.valid_profile_write(
                {**valid, "attributes": valid["attributes"] * 2}, updating=False
            )
        )

    def test_saved_interest_requires_approved_source_and_advances_profile_revision(
        self,
    ):
        import hashlib

        source_entry_id = "federal-dol-training"
        source_url = "https://dol.gov/career/training"
        lead_id = hashlib.sha256(
            f"{source_entry_id}\n{source_url}".encode("utf-8")
        ).hexdigest()[:24]
        payload = {
            "lead_id": lead_id,
            "candidate_ref": "candidate-ref-" + "1" * 32,
            "candidate_id": "candidate-" + "2" * 32,
            "candidate_identity_basis": "publisher_program_number",
            "source_id": "federal.dol",
            "source_record_id": "dol-training-001",
            "source_version": "snapshot-2026-10-25",
            "source_content_sha256": "c" * 64,
            "source_entry_id": source_entry_id,
            "canonical_source_url": source_url,
            "policy_id": "approved-search-policy",
            "policy_version": "1.0.0",
            "retrieved_at_utc": "2026-10-25T12:00:00Z",
            "selected": True,
            "expected_revision": 1,
            "confirmed": True,
        }
        saved_row = (
            lead_id,
            "federal.dol",
            source_entry_id,
            source_url,
            "approved-search-policy",
            "1.0.0",
            "b" * 64,
            datetime(2026, 10, 25, 12, tzinfo=timezone.utc),
            True,
            datetime(2026, 10, 25, 12, tzinfo=timezone.utc),
            "candidate-ref-" + "1" * 32,
            "candidate-" + "2" * 32,
            "publisher_program_number",
            "dol-training-001",
            "snapshot-2026-10-25",
            "c" * 64,
        )

        class Result:
            def __init__(self, row=None):
                self.row = row

            def fetchone(self):
                return self.row

        class Connection:
            revision = 1
            queries = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters=()):
                self.queries.append((query, parameters))
                if "FROM mcp_saas.profile_access_references" in query:
                    return Result(
                        (
                            "synthetic-profile-internal",
                            "core-schema-registry@core-v1",
                            False,
                            None,
                        )
                    )
                if "FROM mcp_saas.synthetic_profiles WHERE profile_id" in query:
                    return Result(
                        (
                            "core-schema-registry",
                            "core-v1",
                            self.revision,
                            {"record_type": "synthetic"},
                        )
                    )
                if query.lstrip().startswith("UPDATE mcp_saas.synthetic_profiles"):
                    self.revision = parameters[0]
                    return Result()
                if query.lstrip().startswith(
                    "INSERT INTO mcp_saas.profile_saved_interests"
                ) or query.lstrip().startswith(
                    "INSERT INTO mcp_saas.profile_saved_interest_history"
                ):
                    return Result()
                if "FROM mcp_saas.profile_saved_interests" in query:
                    return Result(saved_row)
                raise AssertionError(f"Unhandled profile-interest SQL: {query}")

        connection = Connection()
        active_policy = {
            "status": "active",
            "active_manifest": {
                "manifest_id": "approved-search-policy",
                "manifest_version": "1.0.0",
                "content_sha256": "b" * 64,
                "manifest": {
                    "policy_bundle": {
                        "entries": [
                            {
                                "entry_id": source_entry_id,
                                "source_id": "federal.dol",
                                "host": "dol.gov",
                                "path_prefixes": ["/career"],
                                "permitted_uses": ["discovery"],
                                "review_status": "approved_for_bounded_discovery",
                                "owner": "U.S. Department of Labor",
                            }
                        ]
                    }
                },
            },
        }
        with (
            patch("api.read_active_source_policy", return_value=active_policy),
            patch("api.connect_db", return_value=connection),
        ):
            result, status = api.save_scoped_synthetic_profile_interest(
                payload, profile_ref="A" * 43, profile_scope_sha256="c" * 64
            )

        self.assertEqual(status, "ok")
        self.assertEqual(result["revision"], 2)
        self.assertEqual(result["saved_interest"]["lead_id"], lead_id)
        self.assertEqual(
            result["saved_interest"]["candidate_ref"], payload["candidate_ref"]
        )
        self.assertEqual(
            result["saved_interest"]["candidate_id"], payload["candidate_id"]
        )
        self.assertEqual(
            result["saved_interest"]["source_record_id"], payload["source_record_id"]
        )
        self.assertEqual(
            result["saved_interest"]["source_content_sha256"],
            payload["source_content_sha256"],
        )
        self.assertEqual(connection.revision, 2)
        history_write = next(
            params
            for query, params in connection.queries
            if query.lstrip().startswith(
                "INSERT INTO mcp_saas.profile_saved_interest_history"
            )
        )
        self.assertEqual(history_write[2], 2)
        self.assertEqual(history_write[-1], "c" * 64)
        self.assertEqual(
            history_write[12:18],
            (
                payload["candidate_ref"],
                payload["candidate_id"],
                payload["candidate_identity_basis"],
                payload["source_record_id"],
                payload["source_version"],
                payload["source_content_sha256"],
            ),
        )

    def test_saved_interest_revision_conflict_does_not_write(self):
        import hashlib

        source_entry_id = "federal-dol-training"
        source_url = "https://dol.gov/career/training"
        payload = {
            "lead_id": hashlib.sha256(
                f"{source_entry_id}\n{source_url}".encode("utf-8")
            ).hexdigest()[:24],
            "candidate_ref": "candidate-ref-" + "1" * 32,
            "candidate_id": "candidate-" + "2" * 32,
            "candidate_identity_basis": "publisher_program_number",
            "source_id": "federal.dol",
            "source_record_id": "dol-training-001",
            "source_version": "snapshot-2026-10-25",
            "source_content_sha256": "c" * 64,
            "source_entry_id": source_entry_id,
            "canonical_source_url": source_url,
            "policy_id": "approved-search-policy",
            "policy_version": "1.0.0",
            "retrieved_at_utc": "2026-10-25T12:00:00Z",
            "selected": True,
            "expected_revision": 8,
            "confirmed": True,
        }

        class Result:
            def __init__(self, row=None):
                self.row = row

            def fetchone(self):
                return self.row

        class Connection:
            writes = 0

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters=()):
                if "FROM mcp_saas.profile_access_references" in query:
                    return Result(
                        (
                            "synthetic-profile-internal",
                            "core-schema-registry@core-v1",
                            False,
                            None,
                        )
                    )
                if "FROM mcp_saas.synthetic_profiles WHERE profile_id" in query:
                    return Result(
                        (
                            "core-schema-registry",
                            "core-v1",
                            1,
                            {"record_type": "synthetic"},
                        )
                    )
                self.writes += 1
                return Result()

        active_policy = {
            "status": "active",
            "active_manifest": {
                "manifest_id": "approved-search-policy",
                "manifest_version": "1.0.0",
                "content_sha256": "b" * 64,
                "manifest": {
                    "policy_bundle": {
                        "entries": [
                            {
                                "entry_id": source_entry_id,
                                "source_id": "federal.dol",
                                "host": "dol.gov",
                                "path_prefixes": ["/career"],
                                "permitted_uses": ["discovery"],
                                "review_status": "approved_for_bounded_discovery",
                                "owner": "U.S. Department of Labor",
                            }
                        ]
                    }
                },
            },
        }
        connection = Connection()
        with (
            patch("api.read_active_source_policy", return_value=active_policy),
            patch("api.connect_db", return_value=connection),
        ):
            result, status = api.save_scoped_synthetic_profile_interest(
                payload, profile_ref="A" * 43, profile_scope_sha256="c" * 64
            )

        self.assertEqual(status, "revision_conflict")
        self.assertEqual(result, {"status": "revision_conflict", "current_revision": 1})
        self.assertEqual(connection.writes, 0)

    def test_scoped_profile_reference_is_opaque_and_scope_bound(self):
        self.assertIsNone(api._profile_reference_digest("synthetic-person-001"))
        self.assertIsNone(api._profile_reference_digest("A" * 42))
        self.assertTrue(api._profile_reference_digest("A" * 43))

        class Result:
            def fetchone(self):
                return (
                    "synthetic-person-001",
                    "core-schema-registry@core-v1",
                    True,
                    None,
                )

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters=()):
                if "profile_access_references" in query:
                    return Result()
                raise AssertionError("expired capability must not read profile data")

        with patch("api.connect_db", return_value=Connection()):
            expired = api.read_scoped_synthetic_profile("A" * 43, "b" * 64)
        self.assertEqual(expired, {"status": "profile_reference_expired"})

    def test_profile_http_routes_require_scoped_reference_and_hide_raw_ids(self):
        from urllib.error import HTTPError
        from urllib.request import Request

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        reference = "A" * 43
        scope = "b" * 64
        payload = {
            "profile_kind": "person",
            "schema_registry_id": "core-schema-registry",
            "schema_version": "core-v1",
            "attributes": [{"attribute_id": "core.person.age_band", "value": "adult"}],
            "confirmed": True,
        }
        with (
            patch("api.read_secret", return_value="profile-token"),
            patch(
                "api.read_scoped_synthetic_profile",
                side_effect=[
                    {"status": "available", "revision": 1, "attributes": []},
                    {"status": "profile_reference_expired"},
                ],
            ) as read_profile,
            patch(
                "api.create_synthetic_profile",
                return_value=({"status": "available", "profile_ref": reference}, "ok"),
            ) as create_profile,
            patch(
                "api.update_synthetic_profile",
                return_value=({"status": "available", "revision": 2}, "ok"),
            ) as update_profile,
        ):
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                from urllib.request import urlopen

                raw_read = Request(
                    f"{base}/v1/profiles/synthetic-person-001",
                    headers={"Authorization": "Bearer profile-token"},
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(raw_read, timeout=2)
                self.assertEqual(error.exception.code, 404)
                read_profile.assert_not_called()

                scoped_url = f"{base}/v1/profile-sessions/current"
                unauthorized = Request(
                    scoped_url,
                    headers={
                        "X-Profile-Reference": reference,
                        "X-Profile-Scope": scope,
                    },
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(unauthorized, timeout=2)
                self.assertEqual(error.exception.code, 401)
                read_profile.assert_not_called()

                scoped = Request(
                    scoped_url,
                    headers={
                        "Authorization": "Bearer profile-token",
                        "X-Profile-Reference": reference,
                        "X-Profile-Scope": scope,
                    },
                )
                with urlopen(scoped, timeout=2) as response:
                    self.assertEqual(json.loads(response.read())["revision"], 1)
                expired = Request(
                    scoped_url,
                    headers={
                        "Authorization": "Bearer profile-token",
                        "X-Profile-Reference": reference,
                        "X-Profile-Scope": scope,
                    },
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(expired, timeout=2)
                self.assertEqual(error.exception.code, 410)

                missing_scope = Request(
                    f"{base}/v1/profiles",
                    data=json.dumps(payload).encode(),
                    headers={"Authorization": "Bearer profile-token"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(missing_scope, timeout=2)
                self.assertEqual(error.exception.code, 400)
                create_profile.assert_not_called()

                scoped_create = Request(
                    f"{base}/v1/profiles",
                    data=json.dumps(payload).encode(),
                    headers={
                        "Authorization": "Bearer profile-token",
                        "X-Profile-Scope": scope,
                    },
                    method="POST",
                )
                with urlopen(scoped_create, timeout=2) as response:
                    self.assertEqual(
                        json.loads(response.read())["profile_ref"], reference
                    )
                create_profile.assert_called_once_with(
                    payload, profile_scope_sha256=scope
                )

                revision_url = f"{base}/v1/profile-sessions/current/revisions"
                revision = Request(
                    revision_url,
                    data=json.dumps({**payload, "expected_revision": 1}).encode(),
                    headers={
                        "Authorization": "Bearer profile-token",
                        "X-Profile-Reference": reference,
                        "X-Profile-Scope": scope,
                    },
                    method="POST",
                )
                with urlopen(revision, timeout=2) as response:
                    self.assertEqual(json.loads(response.read())["revision"], 2)
                update_profile.assert_called_once()
                legacy_revision = Request(
                    f"{base}/v1/profiles/synthetic-person-001/revisions",
                    data=json.dumps({**payload, "expected_revision": 1}).encode(),
                    headers={"Authorization": "Bearer profile-token"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(legacy_revision, timeout=2)
                self.assertEqual(error.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_evaluation_fixture_requires_explicit_create_only_command(self):
        ordinary = {
            "profile_id": "synthetic-eval-farmer-001",
            "profile_kind": "farm_business",
            "schema_registry_id": "core-schema-registry",
            "schema_version": "core-v1",
            "attributes": [{"attribute_id": "core.person.age_band", "value": "adult"}],
            "confirmed": True,
        }
        command = {
            "operation": "register_evaluation_fixture",
            "fixture_set_id": "phase-ab",
            "fixture_set_version": "1.0.0",
            "fixture_id": "fixture-b123",
            "use_case_id": "farmer_producer",
        }
        self.assertIsNone(api.valid_profile_write(ordinary, updating=False))
        self.assertIsNotNone(
            api.valid_profile_write({**ordinary, "command": command}, updating=False)
        )
        self.assertIsNone(
            api.valid_profile_write(
                {
                    **ordinary,
                    "command": {**command, "use_case_id": "unauthorized-case"},
                },
                updating=False,
            )
        )
        self.assertIsNone(
            api.valid_profile_write(
                {
                    **ordinary,
                    "command": {
                        **command,
                        "use_case_id": "farmer_producer; DROP TABLE profiles",
                    },
                },
                updating=False,
            )
        )
        self.assertIsNone(
            api.valid_profile_write(
                {**ordinary, "tags": ["core.evaluation.purpose"]},
                updating=False,
            )
        )
        self.assertIsNone(
            api.valid_profile_write(
                {**ordinary, "command": {**command, "sql": "DROP TABLE"}},
                updating=False,
            )
        )
        self.assertIsNone(
            api.valid_profile_write(
                {**ordinary, "command": command, "expected_revision": 1},
                updating=True,
            )
        )

    def test_test_purpose_tag_is_metadata_and_must_match_global_schema(self):
        schema = json.loads(
            (
                Path(__file__).resolve().parents[4]
                / "registries/schemas/core-tags-v1.json"
            ).read_text(encoding="utf-8")
        )
        assignment = {
            "registry_id": "core-tag-registry",
            "schema_version": "1.0.0",
            "tag_id": "core.evaluation.purpose",
            "entity_type": "profile",
            "entity_id": "synthetic-eval-farmer-001",
            "context_id": "evaluation.phase-ab.v1-0-0",
            "value": "synthetic_fixture",
            "context": {"fixture_set_id": "phase-ab"},
            "provenance": {"source_id": "profile_service.evaluation_fixture_command"},
        }
        self.assertIsNotNone(api.validate_assignment(assignment, schema))
        self.assertIsNone(
            api.validate_assignment({**assignment, "value": "admin"}, schema)
        )
        self.assertIsNone(
            api.validate_assignment({**assignment, "entity_type": "tenant"}, schema)
        )

    def test_fixture_read_requires_named_set_membership_and_hides_profile_id(self):
        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters=()):
                if "evaluation_fixture_members AS member" in query:
                    if "ORDER BY member.fixture_id" in query:
                        return unittest.mock.Mock(
                            fetchall=lambda: [
                                (
                                    "fixture-b123",
                                    "farmer_producer",
                                    "core-schema-registry",
                                    "core-v1",
                                )
                            ]
                        )
                    return unittest.mock.Mock(
                        fetchone=lambda: (
                            "farmer_producer",
                            "synthetic-eval-farmer-001",
                            "core-schema-registry",
                            "core-v1",
                        )
                    )
                if "FROM mcp_saas.synthetic_profiles WHERE profile_id" in query:
                    return unittest.mock.Mock(
                        fetchone=lambda: (
                            "synthetic-eval-farmer-001",
                            "farm_business",
                            "core-schema-registry",
                            "core-v1",
                            {"record_type": "synthetic"},
                            1,
                            "2026-10-23",
                        )
                    )
                if "FROM mcp_saas.profile_attribute_values" in query:
                    return unittest.mock.Mock(
                        fetchall=lambda: [
                            (
                                "core.farm_business.years_farming",
                                "core.farm_business",
                                "integer",
                                3,
                                "profile.user_confirmed",
                                "core-v1",
                                {"confirmation_status": "confirmed"},
                                "2026-10-23",
                            )
                        ]
                    )
                if "FROM mcp_saas.profile_saved_interests" in query:
                    return unittest.mock.Mock(fetchall=lambda: [])
                raise AssertionError(f"unexpected fixture SQL: {query}")

        connection = Connection()
        with patch("api.connect_db", return_value=connection):
            listed = api.list_evaluation_fixture_members("phase-ab", "1.0.0")
            fixture = api.read_evaluation_fixture_member(
                "phase-ab", "1.0.0", "fixture-b123"
            )
        self.assertEqual(listed["fixtures"][0]["fixture_id"], "fixture-b123")
        self.assertNotIn("profile_id", listed["fixtures"][0])
        self.assertEqual(fixture["profile"]["attributes"][0]["value"], 3)
        self.assertNotIn("profile_id", fixture["profile"])
        self.assertNotIn("expected_outcomes", fixture)

    def test_fixture_read_rejects_schema_identity_mismatch(self):
        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters=()):
                if "evaluation_fixture_members AS member" in query:
                    return unittest.mock.Mock(
                        fetchone=lambda: (
                            "farmer_producer",
                            "synthetic-eval-farmer-001",
                            "core-schema-registry",
                            "core-v1",
                        )
                    )
                raise AssertionError(f"unexpected fixture SQL: {query}")

        profile = {
            "schema_registry_id": "other-schema-registry",
            "schema_version": "core-v1",
            "attributes": [],
        }
        with (
            patch("api.connect_db", return_value=Connection()),
            patch("api._read_synthetic_profile", return_value=profile),
        ):
            fixture = api.read_evaluation_fixture_member(
                "phase-ab", "1.0.0", "fixture-b123"
            )
        self.assertIsNone(fixture)

    def test_unconfigured_fixture_set_does_not_query_profile_store(self):
        with patch("api.connect_db") as connect:
            self.assertIsNone(
                api.list_evaluation_fixture_members("unknown-set", "1.0.0")
            )
        connect.assert_not_called()

    def test_profile_fields_must_match_the_active_versioned_schema(self):
        schema = {
            "registry_id": "core-schema-registry",
            "schema_version": "core-v1",
            "dimensions": {"core.person": {"label": "Person"}},
            "attributes": {
                "core.person.age_band": {
                    "type": "string",
                    "dimension_id": "core.person",
                    "allowed_values": ["adult", "senior"],
                }
            },
        }

        class Result:
            def fetchone(self):
                return (schema,)

            def fetchall(self):
                return []

        class Connection:
            def execute(self, query, parameters=()):
                self.query = query
                self.parameters = parameters
                if "FROM mcp_saas.schema_registry" in query:
                    self.schema_parameters = parameters
                return Result()

        connection = Connection()
        valid = [{"attribute_id": "core.person.age_band", "value": "adult"}]
        resolved = api._active_profile_attributes(
            connection, "core-schema-registry", "core-v1", valid
        )
        self.assertEqual(resolved[0]["dimension_id"], "core.person")
        self.assertEqual(
            connection.schema_parameters, ("core-schema-registry", "core-v1")
        )
        self.assertIsNone(
            api._active_profile_attributes(
                connection,
                "core-schema-registry",
                "core-v1",
                [{"attribute_id": "eligibility.program.income", "value": 10}],
            )
        )

    def test_profile_fields_accept_active_catalog_declared_extension_schema(self):
        root = {
            "registry_id": "core-schema-registry",
            "schema_version": "core-v8",
            "dimensions": {"core.geography": {}},
            "attributes": {
                "core.geography.state_code": {
                    "type": "string",
                    "dimension_id": "core.geography",
                    "allowed_values": ["MO", "KS"],
                }
            },
        }
        extension = {
            "registry_id": "eligibility-profile-extension-schema",
            "schema_version": "eligibility-profile-v1",
            "dimensions": {"nrcs.eqip": {}},
            "attributes": {
                "nrcs.eqip.fsa_records_confirm_eligible_land_control": {
                    "type": "boolean",
                    "dimension_id": "nrcs.eqip",
                }
            },
        }
        metadata = json.dumps(
            {
                "criteria_profile_schema_refs": [
                    {
                        "registry_id": "eligibility-profile-extension-schema",
                        "schema_version": "eligibility-profile-v1",
                    }
                ]
            }
        )

        class Result:
            def __init__(self, row=None, rows=None):
                self.row = row
                self.rows = rows or []

            def fetchone(self):
                return self.row

            def fetchall(self):
                return self.rows

        class Connection:
            def execute(self, query, parameters=()):
                if "FROM mcp_saas.schema_registry" in query:
                    schemas = {
                        ("core-schema-registry", "core-v8"): root,
                        (
                            "eligibility-profile-extension-schema",
                            "eligibility-profile-v1",
                        ): extension,
                    }
                    definition = schemas.get(tuple(parameters))
                    return Result((definition,)) if definition else Result()
                if "FROM mcp_saas.public_datasets" in query:
                    return Result(rows=[(metadata,)])
                raise AssertionError(f"Unexpected schema validation SQL: {query}")

        connection = Connection()
        fields = [
            {"attribute_id": "core.geography.state_code", "value": "MO"},
            {
                "attribute_id": "nrcs.eqip.fsa_records_confirm_eligible_land_control",
                "value": True,
            },
        ]
        resolved = api._active_profile_attributes(
            connection, "core-schema-registry", "core-v8", fields
        )

        self.assertEqual(
            [item["schema_registry_id"] for item in resolved],
            ["core-schema-registry", "eligibility-profile-extension-schema"],
        )
        self.assertEqual(
            [item["schema_version"] for item in resolved],
            ["core-v8", "eligibility-profile-v1"],
        )
        self.assertIsNone(
            api._active_profile_attributes(
                connection,
                "core-schema-registry",
                "core-v8",
                [{"attribute_id": "nrcs.eqip.unregistered", "value": True}],
            )
        )
        self.assertIsNone(
            api._active_profile_attributes(
                connection,
                "core-schema-registry",
                "core-v1",
                [{"attribute_id": "core.person.age_band", "value": "minor"}],
            )
        )

    def test_profile_write_records_confirmed_value_and_rejects_stale_revision(self):
        schema = {
            "registry_id": "core-schema-registry",
            "schema_version": "core-v1",
            "dimensions": {"core.person": {"label": "Person"}},
            "attributes": {
                "core.person.age_band": {
                    "type": "string",
                    "dimension_id": "core.person",
                    "allowed_values": ["adult", "senior"],
                }
            },
        }
        extension_schema = {
            "registry_id": "eligibility-profile-extension-schema",
            "schema_version": "eligibility-profile-v1",
            "dimensions": {"nrcs.eqip": {"label": "EQIP"}},
            "attributes": {
                "nrcs.eqip.fsa_records_confirm_eligible_land_control": {
                    "type": "boolean",
                    "dimension_id": "nrcs.eqip",
                }
            },
        }
        dataset_metadata = json.dumps(
            {
                "criteria_profile_schema_refs": [
                    {
                        "registry_id": "eligibility-profile-extension-schema",
                        "schema_version": "eligibility-profile-v1",
                    }
                ]
            }
        )
        state = {"profile": None, "attributes": {}, "history": [], "access": {}}

        class Result:
            def __init__(self, row=None, rows=None):
                self.row = row
                self.rows = rows or []

            def fetchone(self):
                return self.row

            def fetchall(self):
                return self.rows

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters=()):
                if "FROM mcp_saas.schema_registry" in query:
                    definitions = {
                        ("core-schema-registry", "core-v1"): schema,
                        (
                            "eligibility-profile-extension-schema",
                            "eligibility-profile-v1",
                        ): extension_schema,
                    }
                    return Result((definitions.get(tuple(parameters)),))
                if "FROM mcp_saas.public_datasets" in query:
                    return Result(rows=[(dataset_metadata,)])
                if (
                    "SELECT scope_sha256 FROM mcp_saas.profile_access_references"
                    in " ".join(query.split())
                ):
                    return Result(
                        next(
                            (
                                (key[1],)
                                for key in state["access"]
                                if key[0] == parameters[0]
                            ),
                            None,
                        )
                    )
                if "FROM mcp_saas.profile_access_references" in query:
                    return Result(state["access"].get((parameters[0], parameters[1])))
                if query.lstrip().startswith(
                    "INSERT INTO mcp_saas.profile_access_references"
                ):
                    state["access"][(parameters[0], parameters[2])] = (
                        parameters[1],
                        parameters[3],
                        False,
                        None,
                    )
                    return Result()
                if query.lstrip().startswith("INSERT INTO mcp_saas.synthetic_profiles"):
                    state["profile"] = (
                        parameters[0],
                        parameters[1],
                        parameters[2],
                        parameters[3],
                        json.loads(parameters[4]),
                        1,
                        "2026-10-22T00:00:00+00:00",
                    )
                    return Result((parameters[0],))
                if "SELECT profile_kind, schema_version, revision, provenance" in query:
                    profile = state["profile"]
                    return Result(
                        (profile[1], profile[3], profile[5], profile[4], profile[2])
                    )
                if query.lstrip().startswith("UPDATE mcp_saas.synthetic_profiles"):
                    profile = state["profile"]
                    state["profile"] = (
                        profile[0],
                        profile[1],
                        parameters[0],
                        profile[3],
                        profile[4],
                        parameters[1],
                        "2026-10-22T00:01:00+00:00",
                    )
                    return Result()
                if query.lstrip().startswith(
                    "INSERT INTO mcp_saas.profile_attribute_values"
                ):
                    state["attributes"][parameters[1]] = (
                        parameters[1],
                        parameters[2],
                        parameters[3],
                        json.loads(parameters[4]),
                        parameters[5],
                        parameters[6],
                        json.loads(parameters[7]),
                        "2026-10-22T00:00:00+00:00",
                    )
                    return Result()
                if query.lstrip().startswith(
                    "INSERT INTO mcp_saas.profile_attribute_history"
                ):
                    state["history"].append(parameters)
                    return Result()
                if "FROM mcp_saas.synthetic_profiles WHERE profile_id" in query:
                    return Result(state["profile"])
                if "FROM mcp_saas.profile_attribute_values" in query:
                    return Result(rows=list(state["attributes"].values()))
                if "FROM mcp_saas.profile_saved_interests" in query:
                    return Result(
                        rows=[
                            (
                                "f" * 24,
                                "federal.dol",
                                "federal-dol-training",
                                "https://dol.gov/career/training",
                                "approved-search-policy",
                                "1.0.0",
                                "b" * 64,
                                "2026-10-22T00:00:00+00:00",
                                True,
                                "2026-10-22T00:00:00+00:00",
                                None,
                                None,
                                None,
                                None,
                                None,
                                None,
                            )
                        ]
                    )
                raise AssertionError(f"Unhandled profile SQL: {query}")

        payload = {
            "profile_kind": "person",
            "schema_registry_id": "core-schema-registry",
            "schema_version": "core-v1",
            "attributes": [
                {"attribute_id": "core.person.age_band", "value": "adult"},
                {
                    "attribute_id": (
                        "nrcs.eqip.fsa_records_confirm_eligible_land_control"
                    ),
                    "value": True,
                },
            ],
            "confirmed": True,
        }
        scope = "b" * 64
        with patch("api.connect_db", return_value=Connection()):
            created, result = api.create_synthetic_profile(
                payload, profile_scope_sha256=scope
            )
            scoped_read = api.read_scoped_synthetic_profile(
                created["profile_ref"], scope
            )
            log_buffer = io.StringIO()
            with contextlib.redirect_stdout(log_buffer):
                cross_scope_read = api.read_scoped_synthetic_profile(
                    created["profile_ref"],
                    "c" * 64,
                    "11111111-1111-4111-8111-111111111111",
                )
            reject_log = log_buffer.getvalue()
            corrected, correction_result = api.update_synthetic_profile(
                {
                    **payload,
                    "attributes": [
                        {"attribute_id": "core.person.age_band", "value": "senior"}
                    ],
                    "expected_revision": 1,
                },
                profile_ref=created["profile_ref"],
                profile_scope_sha256=scope,
            )
            stale, conflict = api.update_synthetic_profile(
                {**payload, "expected_revision": 1},
                profile_ref=created["profile_ref"],
                profile_scope_sha256=scope,
            )

        self.assertEqual(result, "ok")
        self.assertEqual(created["revision"], 1)
        self.assertTrue(created["profile_ref"])
        self.assertNotIn("profile_id", created)
        self.assertEqual(created["schema_registry_id"], "core-schema-registry")
        self.assertEqual(scoped_read["status"], "available")
        self.assertEqual(len(scoped_read["saved_interests"]), 1)
        self.assertIsNone(scoped_read["saved_interests"][0]["candidate_ref"])
        self.assertIsNone(scoped_read["saved_interests"][0]["candidate_id"])
        self.assertIsNone(scoped_read["saved_interests"][0]["source_record_id"])
        self.assertNotIn("profile_id", scoped_read)
        self.assertEqual(cross_scope_read, {"status": "profile_not_found_in_scope"})
        self.assertIn("reason=scope_mismatch", reject_log)
        self.assertIn("correlation_id=11111111-1111-4111-8111-111111111111", reject_log)
        self.assertNotIn(created["profile_ref"], reject_log)
        self.assertNotIn("c" * 64, reject_log)
        self.assertEqual(created["attributes"][0]["value"], "adult")
        extension_field = next(
            item
            for item in created["attributes"]
            if item["attribute_id"].startswith("nrcs.eqip.")
        )
        self.assertIs(extension_field["value"], True)
        self.assertEqual(extension_field["schema_version"], "eligibility-profile-v1")
        self.assertEqual(
            extension_field["provenance"]["schema_registry_id"],
            "eligibility-profile-extension-schema",
        )
        self.assertEqual(
            created["attributes"][0]["provenance"]["confirmation_status"],
            "confirmed",
        )
        self.assertEqual(correction_result, "ok")
        self.assertEqual(corrected["revision"], 2)
        self.assertEqual(corrected["attributes"][0]["value"], "senior")
        self.assertEqual(conflict, "conflict")
        self.assertEqual(stale["status"], "revision_conflict")
        self.assertEqual(state["profile"][5], 2)
        self.assertEqual(len(state["history"]), 3)


class PublicCatalogIngestionTests(unittest.TestCase):
    def setUp(self):
        self.source_id = "usda.fsa.beginning-farmers"
        self.dataset_id = "usda-fsa-beginning-farmers-guidance"
        self.source_url = (
            "https://www.fsa.usda.gov/resources/beginning-farmers-and-ranchers-loans"
        )
        self.record = {
            "source_record_id": "fsa-beginning-farmer-classification",
            "record_type": "eligibility_program",
            "canonical_attributes": {
                "eligibility.program.program_id": "fsa-beginning-farmer-classification",
                "core.geography.country_code": "US",
            },
            "semantic_context": {
                "catalog_namespace": "programs",
                "coverage": "partial_source_backed_screening",
            },
            "source_metadata": {
                "source_id": self.source_id,
                "source_version": "etag-1",
                "retrieved_at": "2026-10-19T12:00:00+00:00",
                "transform_version": "eligibility-source-transform-v1",
            },
            "source_url": self.source_url,
            "content_hash": "a" * 64,
            "schema_version": "eligibility-program-v1",
            "observed_at": "2026-10-19T12:00:00+00:00",
        }
        self.payload = {
            "outcome": "succeeded",
            "source_version": "etag-1",
            "retrieved_at": "2026-10-19T12:00:00+00:00",
            "transform_version": "eligibility-source-transform-v1",
            "validation_outcome": "valid",
            "detail": "Complete source page validated and normalized.",
            "records": [self.record],
        }
        self.dataset_row = (
            self.source_id,
            self.source_url,
            "available",
            "eligibility-program-v1",
            ["core", "eligibility"],
            {
                "catalog_namespace": "programs",
                "schema_registry_ids": {
                    "core": "core-schema-registry",
                    "eligibility": "eligibility-program-schema",
                },
                "schema_versions": {
                    "core": "core-v1",
                    "eligibility": "eligibility-program-v1",
                },
            },
        )
        self.schema_definitions = {
            "core-schema-registry": {
                "namespaces": {"core": "Shared concepts"},
                "attributes": {
                    "core.geography.country_code": {
                        "type": "string",
                        "pattern": "^[A-Z]{2}$",
                    }
                },
            },
            "eligibility-program-schema": {
                "namespaces": {"eligibility": "Program semantics"},
                "attributes": {"eligibility.program.program_id": {"type": "string"}},
            },
        }

    def test_validates_complete_records_and_record_free_failure_outcome(self):
        self.assertIsNotNone(valid_public_catalog_ingestion(self.payload))
        failure = {
            **self.payload,
            "outcome": "blocked",
            "validation_outcome": "unavailable",
            "records": [],
        }
        self.assertIsNotNone(valid_public_catalog_ingestion(failure))
        self.assertIsNone(
            valid_public_catalog_ingestion({**failure, "records": [self.record]})
        )
        self.assertIsNone(
            valid_public_catalog_ingestion({**self.payload, "sql": "select"})
        )

    def test_governed_registration_rejects_malformed_transaction_before_db_access(self):
        registration = {
            "contract_version": "public-source-registration-v1",
            "source": {"source_id": self.source_id},
            "datasets": [],
        }
        payload = {
            "registration": registration,
            "expected_before_sha256": "a" * 64,
            "expected_proposed_sha256": "b" * 64,
            "transaction_id": "not-a-uuid",
            "approval_reference": "approved metadata correction",
            "operator_audit_context": {
                "mcp_session_sha256": "b" * 64,
                "mcp_request_sha256": "c" * 64,
                "mcp_client_identity_status": "unavailable",
                "operator_credential_label": "core_operator_token",
            },
        }
        with (
            patch("api.validate_source_registration", return_value=registration),
            patch("api.connect_public_catalog_ingest_db") as connect,
        ):
            result = api.apply_governed_public_source_registration(payload)

        self.assertIsNone(result)
        connect.assert_not_called()

    def test_public_catalog_registration_receipt_readback_is_sanitized(self):
        transaction_id = str(uuid.uuid4())
        audit_context = {
            "mcp_session_sha256": "3" * 64,
            "mcp_request_sha256": "4" * 64,
            "mcp_client_identity_status": "unavailable",
            "operator_credential_label": "core_operator_token",
        }
        receipt = {
            "status": "applied",
            "transaction_id": transaction_id,
            "source_id": "aitest.public-source",
            "dataset_ids": ["aitest-programs"],
            "expected_before_sha256": "a" * 64,
            "before_sha256": "a" * 64,
            "after_sha256": "c" * 64,
            "proposed_sha256": "b" * 64,
            "approval_reference": "AITest approved metadata correction",
        }
        recorded_at = datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc)
        row = (
            transaction_id,
            "aitest.public-source",
            ["aitest-programs"],
            "a" * 64,
            "a" * 64,
            "c" * 64,
            "b" * 64,
            "AITest approved metadata correction",
            "applied",
            audit_context,
            receipt,
            recorded_at,
        )

        class Result:
            def fetchone(self):
                return row

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, query, params):
                if "public_catalog_registration_events" not in query:
                    raise AssertionError("Receipt query targeted the wrong table.")
                self.params = params
                return Result()

        connection = Connection()
        with patch("api.connect_public_catalog_ingest_db", return_value=connection):
            result = api.read_public_catalog_registration_receipt(transaction_id)

        self.assertEqual(connection.params, (transaction_id,))
        self.assertEqual(result["status"], "recorded")
        self.assertEqual(result["operator_audit_context"], audit_context)
        self.assertEqual(result["receipt"], {})
        self.assertEqual(result["recorded_at"], recorded_at.isoformat())
        self.assertNotIn("registration", result)

    def test_partial_ingestion_requires_bounded_reason_codes(self):
        partial = {
            **self.payload,
            "validation_outcome": "partial",
            "rejected_count": 2,
            "rejection_reasons": [{"code": "missing_required_field", "count": 2}],
        }
        self.assertIsNotNone(valid_public_catalog_ingestion(partial))
        all_rejected = {
            **partial,
            "records": [],
            "rejected_count": 1,
            "rejection_reasons": [{"code": "missing_required_field", "count": 1}],
        }
        self.assertIsNotNone(valid_public_catalog_ingestion(all_rejected))
        self.assertIsNone(
            valid_public_catalog_ingestion({**partial, "rejection_reasons": []})
        )
        self.assertIsNone(
            valid_public_catalog_ingestion(
                {**partial, "rejection_reasons": [{"code": "bad", "count": 3}]}
            )
        )

    def test_published_requirements_are_separate_and_strictly_validated(self):
        definition = {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "requirement_id",
                    "statement",
                    "mapping_status",
                    "source_id",
                ],
                "properties": {
                    "requirement_id": {"type": "string", "minLength": 1},
                    "statement": {"type": "string", "minLength": 1},
                    "mapping_status": {
                        "type": "string",
                        "enum": ["not_mapped", "context_only"],
                    },
                    "source_id": {"type": "string", "minLength": 1},
                },
                "additionalProperties": False,
            },
        }
        published = {
            "requirement_id": "county-designation",
            "statement": "Operate in a disaster-designated county.",
            "mapping_status": "not_mapped",
            "source_id": "usda.fsa.disaster-assistance",
        }
        self.assertTrue(value_matches_attribute([published], definition))
        self.assertFalse(
            value_matches_attribute(
                [{**published, "mapping_status": "met"}], definition
            )
        )
        self.assertFalse(
            value_matches_attribute([{**published, "operator": "eq"}], definition)
        )

    def test_hcv_source_references_match_declared_nullable_schema_fields(self):
        repository_root = API_DIRECTORY.parents[2]
        seed = json.loads(
            (
                repository_root
                / "configs/eligibility/catalog-seeds/hud-hcv-income-screen.json"
            ).read_text(encoding="utf-8")
        )
        schema = json.loads(
            (
                repository_root / "registries/schemas/eligibility-program-v4.json"
            ).read_text(encoding="utf-8")
        )
        payload = seed["ingestion_runs"][0]["payload"]
        record = payload["records"][0]
        references = record["canonical_attributes"][
            "eligibility.program.source_references"
        ]
        definition = schema["attributes"]["eligibility.program.source_references"]

        self.assertIsNotNone(valid_public_catalog_ingestion(payload))
        self.assertTrue(value_matches_attribute(references, definition))
        self.assertEqual(
            set(record["source_metadata"]["field_provenance"]),
            set(record["canonical_attributes"]),
        )
        stable_record = {
            "attributes": record["canonical_attributes"],
            "semantic_context": record["semantic_context"],
            "source_metadata": {
                key: value
                for key, value in record["source_metadata"].items()
                if key != "retrieved_at"
            },
        }
        stable_json = json.dumps(
            stable_record, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        self.assertEqual(
            record["content_hash"],
            hashlib.sha256(stable_json.encode("utf-8")).hexdigest(),
        )
        nullable_text = {"type": ["string", "null"], "minLength": 1}
        self.assertTrue(value_matches_attribute(None, nullable_text))
        self.assertTrue(value_matches_attribute("available", nullable_text))
        self.assertFalse(value_matches_attribute(1, nullable_text))

    def test_normalized_record_v1_requires_field_provenance_and_any_state_code(self):
        record = copy.deepcopy(self.record)
        record["semantic_context"].update(
            {
                "record_role": "program_listing",
                "criteria_coverage": "not_assessed",
                "jurisdiction": {
                    "level": "state",
                    "country_code": "US",
                    "state_code": "VT",
                },
            }
        )
        record["source_metadata"].update(
            {
                "effective_at": None,
                "raw_payload_stored": False,
                "field_provenance": {
                    attribute: {
                        "source_url": self.source_url,
                        "observed_at": "2026-10-19T12:00:00+00:00",
                        "extraction_method": "api_field",
                    }
                    for attribute in record["canonical_attributes"]
                },
            }
        )
        record["source_url"] = "https://www.fsa.usda.gov/loans"
        for field_evidence in record["source_metadata"]["field_provenance"].values():
            field_evidence["source_url"] = (
                "https://www.fsa.usda.gov/beginning-farmer-guidance"
            )
        payload = {
            **self.payload,
            "record_contract_version": "normalized-public-record-v1",
            "rejected_count": 0,
            "rejection_reasons": [],
            "records": [record],
        }

        self.assertIsNotNone(valid_public_catalog_ingestion(payload))
        record["source_metadata"]["field_provenance"]["core.geography.country_code"][
            "source_url"
        ] = "https://127.0.0.1/private"
        self.assertIsNone(valid_public_catalog_ingestion(payload))
        record["source_metadata"]["field_provenance"]["core.geography.country_code"][
            "source_url"
        ] = self.source_url
        del record["source_metadata"]["field_provenance"]
        self.assertIsNone(valid_public_catalog_ingestion(payload))

    def test_missouri_snap_application_details_seed_matches_ingestion_contract(self):
        repository_root = API_DIRECTORY.parents[2]
        seed_path = (
            repository_root
            / "configs/eligibility/catalog-seeds/missouri-snap-application-details.json"
        )
        seed = json.loads(seed_path.read_text(encoding="utf-8"))
        payload = seed["ingestion_runs"][0]["payload"]

        self.assertEqual(
            payload["record_contract_version"], "normalized-public-record-v1"
        )
        self.assertIsNotNone(valid_public_catalog_ingestion(payload))
        record = payload["records"][0]
        metadata = record["source_metadata"]
        self.assertIsNone(metadata["effective_at"])
        self.assertEqual(metadata["transform_version"], payload["transform_version"])
        self.assertIs(metadata["raw_payload_stored"], False)
        stable_record = {
            "attributes": record["canonical_attributes"],
            "semantic_context": record["semantic_context"],
            "source_metadata": {
                key: value for key, value in metadata.items() if key != "retrieved_at"
            },
        }
        stable_json = json.dumps(
            stable_record,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        self.assertEqual(
            record["content_hash"],
            hashlib.sha256(stable_json.encode("utf-8")).hexdigest(),
        )

    def test_source_registration_replay_does_not_write_existing_metadata(self):
        metadata = {
            "catalog_namespace": "public",
            "criteria_coverage": "not_assessed",
            "record_contract_version": "normalized-public-record-v1",
            "visibility_scope": "cluster_public",
            "schema_registry_ids": {"public": "public-schema"},
            "schema_versions": {"public": "public-data-v1"},
        }
        registration = {
            "contract_version": "public-source-registration-v1",
            "source": {
                "source_id": "federal.example",
                "source_name": "Example Federal Programs",
                "source_url": "https://example.gov/programs",
                "access_status": "available",
                "source_context": {
                    "authority": {
                        "level": "federal",
                        "owner_name": "Example Federal Agency",
                        "verification_status": "verified",
                        "evidence_url": "https://example.gov/about",
                    }
                },
            },
            "datasets": [
                {
                    "dataset_id": "federal-example-programs",
                    "source_dataset_id": "program-index",
                    "title": "Federal program index",
                    "landing_page": "https://example.gov/programs",
                    "schema_version": "public-data-v1",
                    "namespace_ids": ["public"],
                    "metadata": metadata,
                }
            ],
        }
        source_row = (
            "Example Federal Programs",
            "https://example.gov/programs",
            "available",
            registration["source"]["source_context"],
        )
        dataset_row = (
            "federal.example",
            "program-index",
            "Federal program index",
            "https://example.gov/programs",
            "public-data-v1",
            ["public"],
            metadata,
        )

        class Result:
            def __init__(self, row=None):
                self.row = row

            def fetchone(self):
                return self.row

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, params=()):
                if "SELECT source_name, source_url" in query:
                    return Result(source_row)
                if "SELECT source_id, source_dataset_id, title" in query:
                    return Result(dataset_row)
                if "SELECT dataset_id FROM mcp_saas.public_datasets" in query:
                    return Result(("federal-example-programs",))
                raise AssertionError("Exact registration replay must not write.")

        with (
            patch("api.connect_public_catalog_ingest_db", side_effect=Connection),
            patch(
                "api._registered_dataset_attributes",
                return_value={"public.id": {"type": "string"}},
            ),
        ):
            result = api.register_public_source(registration)

        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(result["datasets"][0]["status"], "unchanged")

    def test_source_record_ids_preserve_upstream_uppercase_suffixes(self):
        record = {
            **self.record,
            "source_record_id": "sam.gov.assistance_listings.11.63A",
        }

        self.assertIsNotNone(
            valid_public_catalog_ingestion({**self.payload, "records": [record]})
        )

    def test_listing_v2_url_schema_accepts_full_https_urls_only(self):
        schema_path = (
            API_DIRECTORY.parents[2] / "registries/schemas/public-listing-v2.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        for attribute_id in ("listing.program_url", "listing.website_url"):
            definition = schema["attributes"][attribute_id]
            self.assertTrue(
                value_matches_attribute("https://sam.gov/programs/example", definition)
            )
            self.assertFalse(
                value_matches_attribute("http://sam.gov/programs/example", definition)
            )

    def test_replay_is_idempotent_and_failure_keeps_last_good_record(self):
        records = {}
        runs = []

        class Result:
            def __init__(self, row=None):
                self.row = row

            def fetchone(self):
                return self.row

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, params=()):
                if "FROM mcp_saas.public_datasets AS dataset" in query:
                    return Result(self_outer.dataset_row)
                if "FROM mcp_saas.schema_registry" in query:
                    return Result((self_outer.schema_definitions[params[0]],))
                if "SELECT content_hash FROM mcp_saas.public_dataset_records" in query:
                    return Result(records.get((params[0], params[1])))
                if "INSERT INTO mcp_saas.public_dataset_records" in query:
                    key = (params[0], params[1])
                    records[key] = (params[7],)
                if "INSERT INTO mcp_saas.public_dataset_ingestion_runs" in query:
                    runs.append((params[6], params[7]))
                return Result()

        self_outer = self
        with patch("api.connect_public_catalog_ingest_db", side_effect=Connection):
            first = ingest_public_dataset(self.dataset_id, self.payload)
            replay = ingest_public_dataset(self.dataset_id, self.payload)
            failure = {
                **self.payload,
                "outcome": "blocked",
                "validation_outcome": "unavailable",
                "records": [],
            }
            failed = ingest_public_dataset(self.dataset_id, failure)
            all_rejected = {
                **self.payload,
                "validation_outcome": "partial",
                "rejected_count": 1,
                "rejection_reasons": [{"code": "missing_required_field", "count": 1}],
                "records": [],
            }
            partial = ingest_public_dataset(self.dataset_id, all_rejected)
            partially_accepted = {
                **self.payload,
                "validation_outcome": "partial",
                "rejected_count": 1,
                "rejection_reasons": [{"code": "missing_required_field", "count": 1}],
                "records": [
                    {
                        **self.record,
                        "content_hash": "b" * 64,
                    },
                    {
                        **self.record,
                        "source_record_id": "new-record-from-partial-refresh",
                        "content_hash": "c" * 64,
                    },
                ],
            }
            partial_with_valid_rows = ingest_public_dataset(
                self.dataset_id, partially_accepted
            )

        self.assertEqual(first["inserted_count"], 1)
        self.assertEqual(replay["duplicate_count"], 1)
        self.assertEqual(failed["status"], "blocked")
        self.assertEqual(partial["status"], "succeeded")
        self.assertEqual(partial["inserted_count"], 0)
        self.assertEqual(partial["updated_count"], 0)
        self.assertEqual(partial_with_valid_rows["status"], "succeeded")
        self.assertEqual(partial_with_valid_rows["validation_outcome"], "partial")
        self.assertEqual(
            partial_with_valid_rows["catalog_update_status"], "not_applied_partial"
        )
        self.assertEqual(partial_with_valid_rows["inserted_count"], 0)
        self.assertEqual(partial_with_valid_rows["updated_count"], 0)
        self.assertNotIn((self.dataset_id, "new-record-from-partial-refresh"), records)
        self.assertEqual(
            records[(self.dataset_id, self.record["source_record_id"])], ("a" * 64,)
        )
        self.assertEqual(
            [run[0] for run in runs],
            [
                "succeeded",
                "succeeded",
                "blocked",
                "succeeded",
                "succeeded",
            ],
        )

    def test_ingestion_rejects_unknown_namespaced_attributes(self):
        self.record["canonical_attributes"]["other_pattern.program_id"] = "wrong"

        self.assertIsNone(self._ingest_without_writes())

    def _ingest_without_writes(self, schema_definitions=None):
        if schema_definitions is None:
            schema_definitions = self.schema_definitions

        class Result:
            def fetchone(self):
                return self.row

            def __init__(self, row=None):
                self.row = row

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, params=()):
                if "FROM mcp_saas.public_datasets AS dataset" in query:
                    return Result(self_outer.dataset_row)
                if "FROM mcp_saas.schema_registry" in query:
                    definition = self_outer.schema_definitions_for_test.get(params[0])
                    return Result(None if definition is None else (definition,))
                raise AssertionError("Invalid schema data must not be written.")

        self_outer = self
        self.schema_definitions_for_test = schema_definitions
        with patch("api.connect_public_catalog_ingest_db", side_effect=Connection):
            return ingest_public_dataset(self.dataset_id, self.payload)

    def test_ingestion_rejects_wrong_attribute_type(self):
        self.record["canonical_attributes"]["core.geography.country_code"] = 7

        self.assertIsNone(self._ingest_without_writes())

    def test_schema_validation_accepts_typed_lte_by_threshold_tables(self):
        record = copy.deepcopy(self.record)
        record["canonical_attributes"]["eligibility.program.criteria"] = [
            {
                "criterion_id": "income-by-household-size",
                "attribute": "core.household.annual_income_usd",
                "operator": "lte_by",
                "value": {"1": 39750},
                "context_attribute": "core.household.size",
                "source_id": self.source_id,
            }
        ]
        attributes = {
            "core.geography.country_code": {
                "type": "string",
                "pattern": "^[A-Z]{2}$",
            },
            "core.household.annual_income_usd": {"type": "integer", "minimum": 0},
            "core.household.size": {"type": "integer", "minimum": 1},
            "eligibility.program.program_id": {"type": "string"},
            "eligibility.program.criteria": {"type": "array"},
        }

        self.assertTrue(
            api._record_matches_dataset_schema(
                self.dataset_row, record, self.source_id, attributes
            )
        )

        record["canonical_attributes"]["eligibility.program.criteria"][0]["value"][
            "1"
        ] = "39750"
        self.assertFalse(
            api._record_matches_dataset_schema(
                self.dataset_row, record, self.source_id, attributes
            )
        )

    def test_criteria_targets_use_declared_profile_schema_not_record_schema(self):
        record = copy.deepcopy(self.record)
        record["canonical_attributes"]["eligibility.program.criteria"] = [
            {
                "criterion_id": "eqip-land-control",
                "attribute": "nrcs.eqip.fsa_records_confirm_eligible_land_control",
                "operator": "eq",
                "value": True,
                "source_id": self.source_id,
            }
        ]
        dataset = list(self.dataset_row)
        dataset[5] = {
            **dataset[5],
            "criteria_profile_schema_refs": [
                {
                    "registry_id": "eligibility-profile-extension-schema",
                    "schema_version": "eligibility-profile-v1",
                }
            ],
        }
        record_attributes = {
            "core.geography.country_code": {"type": "string"},
            "eligibility.program.program_id": {"type": "string"},
            "eligibility.program.criteria": {"type": "array"},
        }
        profile_attributes = {
            "nrcs.eqip.fsa_records_confirm_eligible_land_control": {"type": "boolean"}
        }

        self.assertNotIn(
            "nrcs.eqip.fsa_records_confirm_eligible_land_control", record_attributes
        )
        self.assertTrue(
            api._record_matches_dataset_schema(
                tuple(dataset),
                record,
                self.source_id,
                record_attributes,
                profile_attributes,
            )
        )

        record["canonical_attributes"]["eligibility.program.criteria"][0]["value"] = (
            "true"
        )
        self.assertFalse(
            api._record_matches_dataset_schema(
                tuple(dataset),
                record,
                self.source_id,
                record_attributes,
                profile_attributes,
            )
        )

    def test_criteria_profile_schemas_enforce_core_enum_and_state_extension_values(
        self,
    ):
        record = copy.deepcopy(self.record)
        record["canonical_attributes"]["eligibility.program.criteria"] = [
            {
                "criterion_id": "csfp-minimum-age",
                "attribute": "core.person.age_band",
                "operator": "one_of",
                "value": ["60_64", "65_74", "75_plus", "65_plus"],
                "source_id": self.source_id,
            },
            {
                "criterion_id": "mo-childcare-activity",
                "attribute": "mo.dese.childcare.qualifying_activity",
                "operator": "one_of",
                "value": ["work", "job_search", "job_training", "school"],
                "source_id": self.source_id,
            },
        ]
        record_attributes = {
            "core.geography.country_code": {"type": "string"},
            "eligibility.program.program_id": {"type": "string"},
            "eligibility.program.criteria": {"type": "array"},
        }
        profile_attributes = {
            "core.person.age_band": {
                "type": "string",
                "allowed_values": ["60_64", "65_74", "75_plus", "65_plus"],
            },
            "mo.dese.childcare.qualifying_activity": {
                "type": "string",
                "allowed_values": ["work", "job_search", "job_training", "school"],
            },
        }

        self.assertTrue(
            api._record_matches_dataset_schema(
                self.dataset_row,
                record,
                self.source_id,
                record_attributes,
                profile_attributes,
            )
        )

        record["canonical_attributes"]["eligibility.program.criteria"][1]["value"][
            0
        ] = "volunteering"
        self.assertFalse(
            api._record_matches_dataset_schema(
                self.dataset_row,
                record,
                self.source_id,
                record_attributes,
                profile_attributes,
            )
        )

    def test_criteria_profile_schema_references_resolve_active_attribute_union(self):
        dataset = list(self.dataset_row)
        dataset[5] = {
            **dataset[5],
            "criteria_profile_schema_refs": [
                {"registry_id": "core-schema-registry", "schema_version": "core-v4"},
                {
                    "registry_id": "eligibility-profile-extension-schema",
                    "schema_version": "eligibility-profile-v1",
                },
            ],
        }
        definitions = {
            ("core-schema-registry", "core-v4"): {
                "namespaces": {"core": "Shared concepts"},
                "attributes": {
                    "core.person.age_band": {
                        "type": "string",
                        "allowed_values": ["60_64", "65_74", "75_plus", "65_plus"],
                    }
                },
            },
            ("eligibility-profile-extension-schema", "eligibility-profile-v1"): {
                "namespaces": {"nrcs": "NRCS facts", "mo": "Missouri facts"},
                "attributes": {
                    "nrcs.eqip.fsa_records_confirm_eligible_land_control": {
                        "type": "boolean"
                    },
                    "mo.dese.childcare.qualifying_activity": {
                        "type": "string",
                        "allowed_values": [
                            "work",
                            "job_search",
                            "job_training",
                            "school",
                        ],
                    },
                },
            },
        }
        queries = []

        class Result:
            def __init__(self, row=None):
                self.row = row

            def fetchone(self):
                return self.row

        class Connection:
            def execute(self, query, params=()):
                queries.append(query)
                definition = definitions.get(tuple(params))
                return Result((definition,)) if definition is not None else Result()

        attributes = api._registered_criteria_profile_attributes(
            Connection(), tuple(dataset), {}
        )

        expected_attributes = set().union(
            *(set(item["attributes"]) for item in definitions.values())
        )
        self.assertEqual(set(attributes or {}), expected_attributes)
        self.assertTrue(all("lifecycle_state = 'active'" in query for query in queries))

    def test_dry_run_checks_active_schemas_without_writing(self):
        queries = []

        class Result:
            def __init__(self, row=None):
                self.row = row

            def fetchone(self):
                return self.row

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, params=()):
                queries.append(query)
                if "FROM mcp_saas.public_datasets AS dataset" in query:
                    return Result(self_outer.dataset_row)
                if "FROM mcp_saas.schema_registry" in query:
                    definition = self_outer.schema_definitions.get(params[0])
                    return Result(None if definition is None else (definition,))
                raise AssertionError("A dry run must not issue writes.")

        self_outer = self
        payload = copy.deepcopy(self.payload)
        payload["records"][0]["source_url"] = (
            "https://www.fsa.usda.gov/programs/individual-program"
        )
        with patch("api.connect_public_catalog_ingest_db", return_value=Connection()):
            result = validate_public_dataset_ingestion(self.dataset_id, payload)

        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["accepted_count"], 1)
        self.assertFalse(result["write_performed"])
        self.assertTrue(queries)

    def test_ingestion_requires_active_schema_definitions(self):
        self.assertIsNone(self._ingest_without_writes({}))

    def test_http_write_and_read_use_separate_capabilities(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with (
            patch(
                "api.read_secret",
                side_effect=lambda path: (
                    "writer-token" if "write" in str(path) else "reader-token"
                ),
            ),
            patch(
                "api.ingest_public_dataset",
                return_value={
                    "status": "succeeded",
                    "run_id": "run-1",
                    "inserted_count": 1,
                    "updated_count": 0,
                    "duplicate_count": 0,
                },
            ),
            patch(
                "api.read_public_dataset",
                return_value={
                    "status": "ok",
                    "dataset": {"dataset_id": self.dataset_id},
                    "records": [self.record],
                },
            ) as read_public_dataset,
        ):
            thread.start()
            try:
                from urllib.request import Request

                base = f"http://127.0.0.1:{server.server_port}"
                request = Request(
                    f"{base}/v1/public-datasets/{self.dataset_id}/ingestion-runs",
                    data=json.dumps(self.payload).encode(),
                    headers={
                        "Authorization": "Bearer writer-token",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    written = json.loads(response.read())
                read_request = Request(
                    f"{base}/v1/public-datasets/{self.dataset_id}/records?limit=500&offset=1000",
                    headers={"Authorization": "Bearer reader-token"},
                )
                with urlopen(read_request, timeout=2) as response:
                    readback = json.loads(response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
        self.assertEqual(written["run_id"], "run-1")
        self.assertEqual(readback["dataset"]["dataset_id"], self.dataset_id)
        read_public_dataset.assert_called_once_with(
            self.dataset_id, limit=500, offset=1000
        )

    def test_ingestion_dry_run_is_writer_gated_and_never_calls_ingest(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        dry_run = {
            "status": "valid",
            "dataset_id": self.dataset_id,
            "source_id": self.source_id,
            "accepted_count": 1,
            "rejected_count": 0,
            "write_performed": False,
        }
        with (
            patch(
                "api.read_secret",
                side_effect=lambda path: (
                    "writer-token" if "write" in str(path) else "reader-token"
                ),
            ),
            patch(
                "api.validate_public_dataset_ingestion", return_value=dry_run
            ) as validate,
            patch("api.ingest_public_dataset") as ingest,
        ):
            thread.start()
            try:
                from urllib.error import HTTPError
                from urllib.request import Request, urlopen

                url = (
                    f"http://127.0.0.1:{server.server_port}"
                    f"/v1/public-datasets/{self.dataset_id}/ingestion-runs/validate"
                )
                body = json.dumps(self.payload).encode()
                with self.assertRaises(HTTPError) as error:
                    urlopen(
                        Request(
                            url,
                            data=body,
                            headers={"Authorization": "Bearer reader-token"},
                            method="POST",
                        ),
                        timeout=2,
                    )
                self.assertEqual(error.exception.code, 401)
                with urlopen(
                    Request(
                        url,
                        data=body,
                        headers={"Authorization": "Bearer writer-token"},
                        method="POST",
                    ),
                    timeout=2,
                ) as response:
                    result = json.loads(response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertFalse(result["write_performed"])
        validate.assert_called_once_with(self.dataset_id, self.payload)
        ingest.assert_not_called()

    def test_registration_validation_is_writer_gated_and_does_not_write(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        registration = {"contract_version": "public-source-registration-v1"}
        with (
            patch(
                "api.read_secret",
                side_effect=lambda path: (
                    "writer-token" if "write" in str(path) else "reader-token"
                ),
            ),
            patch(
                "api.validate_public_source_registration",
                return_value={"status": "valid", "write_performed": False},
            ) as validate,
        ):
            thread.start()
            try:
                from urllib.error import HTTPError
                from urllib.request import Request, urlopen

                base = f"http://127.0.0.1:{server.server_port}"
                url = f"{base}/v1/public-catalog/registrations/validate"
                with self.assertRaises(HTTPError) as error:
                    urlopen(
                        Request(
                            url,
                            data=json.dumps(registration).encode(),
                            headers={"Authorization": "Bearer reader-token"},
                            method="POST",
                        ),
                        timeout=2,
                    )
                self.assertEqual(error.exception.code, 401)
                with urlopen(
                    Request(
                        url,
                        data=json.dumps(registration).encode(),
                        headers={"Authorization": "Bearer writer-token"},
                        method="POST",
                    ),
                    timeout=2,
                ) as response:
                    result = json.loads(response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertFalse(result["write_performed"])
        validate.assert_called_once_with(registration)

    def test_governed_registration_preview_and_apply_use_writer_gate(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        registration = {"contract_version": "public-source-registration-v1"}
        envelope = {
            "registration": registration,
            "expected_before_sha256": "a" * 64,
            "expected_proposed_sha256": "b" * 64,
            "transaction_id": "3de3e57d-53c9-455e-b901-9aecaf829cee",
            "approval_reference": "approved metadata edit",
            "operator_audit_context": {
                "mcp_session_sha256": "b" * 64,
                "mcp_request_sha256": "c" * 64,
                "mcp_client_identity_status": "unavailable",
                "operator_credential_label": "core_operator_token",
            },
        }
        with (
            patch(
                "api.read_secret",
                side_effect=lambda path: (
                    "writer-token" if "write" in str(path) else "reader-token"
                ),
            ),
            patch(
                "api.preview_public_source_registration",
                return_value={"status": "preview_ready", "write_performed": False},
            ) as preview,
            patch(
                "api.apply_governed_public_source_registration",
                return_value={"status": "stale_before_state"},
            ) as apply,
        ):
            thread.start()
            try:
                from urllib.error import HTTPError
                from urllib.request import Request, urlopen

                base = f"http://127.0.0.1:{server.server_port}"
                preview_url = f"{base}/v1/public-catalog/registrations/preview"
                with self.assertRaises(HTTPError) as denied:
                    urlopen(
                        Request(
                            preview_url,
                            data=json.dumps(registration).encode(),
                            headers={"Authorization": "Bearer reader-token"},
                            method="POST",
                        ),
                        timeout=2,
                    )
                self.assertEqual(denied.exception.code, 401)
                with urlopen(
                    Request(
                        preview_url,
                        data=json.dumps(registration).encode(),
                        headers={"Authorization": "Bearer writer-token"},
                        method="POST",
                    ),
                    timeout=2,
                ) as response:
                    preview_result = json.loads(response.read())
                with self.assertRaises(HTTPError) as stale:
                    urlopen(
                        Request(
                            f"{base}/v1/public-catalog/registrations/governed",
                            data=json.dumps(envelope).encode(),
                            headers={"Authorization": "Bearer writer-token"},
                            method="POST",
                        ),
                        timeout=2,
                    )
                self.assertEqual(stale.exception.code, 409)
                self.assertEqual(
                    json.loads(stale.exception.read()),
                    {"status": "stale_before_state"},
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertFalse(preview_result["write_performed"])
        preview.assert_called_once_with(registration)
        apply.assert_called_once_with(envelope)

    def test_catalog_endpoint_uses_only_catalog_reader_capability(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with (
            patch(
                "api.read_secret",
                side_effect=lambda path: (
                    "catalog-token" if "catalog-read" in str(path) else "registry-token"
                ),
            ),
            patch(
                "api.read_tenant_programs",
                return_value=[{"program_id": "synthetic-program"}],
            ) as read_catalog,
        ):
            thread.start()
            try:
                from urllib.error import HTTPError
                from urllib.request import Request, urlopen

                url = (
                    f"http://127.0.0.1:{server.server_port}"
                    "/v1/tenants/federal-agency/patterns/eligibility/0.1.0/programs"
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(
                        Request(
                            url, headers={"Authorization": "Bearer registry-token"}
                        ),
                        timeout=2,
                    )
                self.assertEqual(error.exception.code, 401)
                request = Request(
                    url, headers={"Authorization": "Bearer catalog-token"}
                )
                with urlopen(request, timeout=2) as response:
                    payload = json.loads(response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
        self.assertEqual(payload["tenant_id"], "federal-agency")
        self.assertEqual(payload["programs"][0]["program_id"], "synthetic-program")
        read_catalog.assert_called_once_with("federal-agency", "eligibility", "0.1.0")

    def test_endpoint_write_requires_its_separate_writer_capability(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        payload = json.dumps(
            {
                "tenant_id": "federal-agency",
                "pattern_id": "eligibility",
                "pattern_version": "0.1.0",
                "endpoint": "http://tenant.svc.cluster.local/mcp",
            }
        ).encode()
        connection = unittest.mock.Mock()
        connection.__enter__ = unittest.mock.Mock(return_value=connection)
        connection.__exit__ = unittest.mock.Mock(return_value=False)
        connection.execute.return_value.fetchone.return_value = ("federal-agency",)
        with (
            patch(
                "api.read_secret",
                side_effect=lambda path: (
                    "writer-token"
                    if "registry-write" in str(path)
                    else "registry-token"
                ),
            ),
            patch("api.connect_db", return_value=connection) as connect_db,
        ):
            thread.start()
            try:
                from urllib.error import HTTPError
                from urllib.request import Request, urlopen

                url = (
                    f"http://127.0.0.1:{server.server_port}/v1/registry/tenant-endpoint"
                )
                wrong = Request(
                    url,
                    data=payload,
                    headers={
                        "Authorization": "Bearer registry-token",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(wrong, timeout=2)
                self.assertEqual(error.exception.code, 401)
                connect_db.assert_not_called()

                correct = Request(
                    url,
                    data=payload,
                    headers={
                        "Authorization": "Bearer writer-token",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urlopen(correct, timeout=2) as response:
                    result = json.loads(response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(result["status"], "ok")
        query = connection.execute.call_args.args[0]
        self.assertIn("UPDATE mcp_saas.tenant_registry", query)
        self.assertIn("SET mcp_endpoint = %s, updated_at = now()", query)


class CoreRouteApiTests(unittest.TestCase):
    def test_evaluation_fixture_route_requires_fixture_reader_capability(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with (
            patch(
                "api.read_secret",
                side_effect=lambda path: (
                    "fixture-reader-token"
                    if path == api.PROFILE_FIXTURE_READ_TOKEN_FILE
                    else "profile-reader-token"
                ),
            ),
            patch("api.list_evaluation_fixture_members") as list_members,
        ):
            list_members.return_value = {
                "status": "available",
                "fixtures": [],
            }
            thread.start()
            try:
                base_url = (
                    f"http://127.0.0.1:{server.server_port}"
                    "/v1/evaluation/fixture-sets/phase-ab/1.0.0/fixtures"
                )
                from urllib.error import HTTPError
                from urllib.request import Request

                with self.assertRaises(HTTPError) as error:
                    urlopen(base_url, timeout=2)
                self.assertEqual(error.exception.code, 401)
                list_members.assert_not_called()

                profile_reader = Request(
                    base_url,
                    headers={"Authorization": "Bearer profile-reader-token"},
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(profile_reader, timeout=2)
                self.assertEqual(error.exception.code, 401)
                list_members.assert_not_called()

                request = Request(
                    base_url,
                    headers={
                        "Authorization": "Bearer fixture-reader-token",
                    },
                )
                with urlopen(request, timeout=2) as response:
                    payload = json.loads(response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(payload, {"status": "available", "fixtures": []})
        list_members.assert_called_once_with("phase-ab", "1.0.0")

    def test_core_route_read_binds_exact_pattern_neutral_identity(self):
        class Result:
            def fetchall(self):
                return [
                    (
                        "route.directory",
                        "1.0.0",
                        "search",
                        "https://generic-handler.example/mcp",
                        "search_records",
                        "mcp",
                        None,
                        None,
                    )
                ]

        class Connection:
            query = ""
            parameters = ()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters):
                self.query = query
                self.parameters = parameters
                return Result()

        connection = Connection()
        with patch("api.connect_db", return_value=connection):
            route = read_core_route("route.directory", "search")

        self.assertEqual(connection.parameters, ("route.directory", "search"))
        self.assertIn("lifecycle_state = 'active'", connection.query)
        self.assertIn("LIMIT 2", connection.query)
        self.assertIsNone(route["tenant_id"])
        self.assertIsNone(route["template_id"])
        self.assertEqual(route["protocol"], "mcp")


class PatternTemplateLifecycleTests(unittest.TestCase):
    def test_legacy_template_state_helper_cannot_deactivate(self):
        with patch("api.connect_db") as connect_db:
            with self.assertRaisesRegex(ValueError, "governed lifecycle"):
                set_template_package_state("sample-directory", "1.0.0", "inactive")
        connect_db.assert_not_called()

    def test_deactivation_endpoint_returns_active_tenant_reason(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        body = json.dumps(
            {
                "expected_before_sha256": "a" * 64,
                "transaction_id": "c678da7d-dfec-4820-a2b4-df6261592f42",
                "approval_reference": "approved-test-action",
                "operator_audit_context": {},
            }
        ).encode()
        with (
            patch(
                "api.read_secret",
                side_effect=lambda path: (
                    "template-writer-token"
                    if "template-registry-write" in str(path)
                    else "reader-token"
                ),
            ),
            patch(
                "api.deactivate_template_package_governed",
                side_effect=api.TemplateLifecycleConflict(
                    "active tenant dependency", reason="active_tenant_dependency"
                ),
            ),
        ):
            thread.start()
            try:
                from urllib.error import HTTPError
                from urllib.request import Request, urlopen

                request = Request(
                    f"http://127.0.0.1:{server.server_port}"
                    "/v1/registry/pattern-templates/sample-directory/1.0.0/deactivate",
                    data=body,
                    headers={
                        "Authorization": "Bearer template-writer-token",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(request, timeout=2)
                self.assertEqual(error.exception.code, 409)
                self.assertEqual(
                    json.loads(error.exception.read()),
                    {"error": "active_tenant_dependency"},
                )
            finally:
                server.shutdown()
                server.server_close()

    def test_governed_deactivation_records_receipt_and_updates_only_shared_routes(self):
        template_id = "sample-directory"
        version = "1.0.0"
        definition = {"template_id": template_id, "template_version": version}
        descriptor_sha256 = api.hashlib.sha256(
            json.dumps(definition, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        before_sha256 = api._template_lifecycle_state_sha256(
            template_id, version, "active", descriptor_sha256
        )
        after_sha256 = api._template_lifecycle_state_sha256(
            template_id, version, "inactive", descriptor_sha256
        )
        transaction_id = "f08c39de-0600-4ca3-a102-33bc487d8620"
        approval_reference = "operator-approved-deactivation"
        audit = {
            "mcp_session_sha256": None,
            "mcp_request_sha256": None,
            "mcp_client_identity_status": "unavailable",
            "operator_credential_label": "core_operator_token",
        }
        event_row = (
            transaction_id,
            template_id,
            version,
            "deactivate",
            before_sha256,
            approval_reference,
            "active",
            "inactive",
            descriptor_sha256,
            before_sha256,
            after_sha256,
            "template_registry_writer",
            datetime(2026, 10, 31, tzinfo=timezone.utc),
            None,
            None,
            "unavailable",
            "core_operator_token",
        )

        class Connection:
            def __init__(self):
                self.queries = []
                self.package_reads = 0
                self.event_written = False

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters):
                self.queries.append(query)
                if "FROM mcp_saas.pattern_template_packages" in query:
                    state = "active" if self.package_reads == 0 else "inactive"
                    self.package_reads += 1
                    return unittest.mock.Mock(fetchone=lambda: (state, definition))
                if "FROM mcp_saas.pattern_template_lifecycle_events" in query:
                    return unittest.mock.Mock(
                        fetchone=lambda: event_row if self.event_written else None
                    )
                if "FROM mcp_saas.tenant_registry" in query:
                    return unittest.mock.Mock(fetchone=lambda: None)
                if query.lstrip().startswith(
                    "INSERT INTO mcp_saas.pattern_template_lifecycle_events"
                ):
                    self.event_written = True
                return unittest.mock.Mock(fetchone=lambda: None)

        connection = Connection()
        with patch("api.connect_db", return_value=connection):
            result = deactivate_template_package_governed(
                template_id,
                version,
                expected_before_sha256=before_sha256,
                transaction_id=transaction_id,
                approval_reference=approval_reference,
                operator_audit_context=audit,
            )
            replay = deactivate_template_package_governed(
                template_id,
                version,
                expected_before_sha256=before_sha256,
                transaction_id=transaction_id,
                approval_reference=approval_reference,
                operator_audit_context=audit,
            )

        self.assertEqual(result["status"], "recorded")
        self.assertEqual(replay["status"], "idempotent_replay")
        self.assertEqual(result["event"]["action"], "deactivate")
        self.assertEqual(result["readback"]["lifecycle_state"], "inactive")
        self.assertTrue(
            any(
                "tenant_id IS NULL" in query
                and "UPDATE mcp_saas.core_route_metadata" in query
                for query in connection.queries
            )
        )
        self.assertFalse(
            any(
                query.lstrip().startswith("UPDATE mcp_saas.tenant_registry")
                for query in connection.queries
            )
        )

    def test_governed_deactivation_refuses_active_tenant_without_writes(self):
        template_id = "sample-directory"
        version = "1.0.0"
        definition = {"template_id": template_id, "template_version": version}
        descriptor_sha256 = api.hashlib.sha256(
            json.dumps(definition, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        before_sha256 = api._template_lifecycle_state_sha256(
            template_id, version, "active", descriptor_sha256
        )

        class Connection:
            def __init__(self):
                self.queries = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters):
                self.queries.append(query)
                if "FROM mcp_saas.pattern_template_packages" in query:
                    return unittest.mock.Mock(fetchone=lambda: ("active", definition))
                if "FROM mcp_saas.tenant_registry" in query:
                    return unittest.mock.Mock(fetchone=lambda: (1,))
                return unittest.mock.Mock(fetchone=lambda: None)

        connection = Connection()
        with patch("api.connect_db", return_value=connection):
            with self.assertRaises(api.TemplateLifecycleConflict):
                deactivate_template_package_governed(
                    template_id,
                    version,
                    expected_before_sha256=before_sha256,
                    transaction_id="c678da7d-dfec-4820-a2b4-df6261592f42",
                    approval_reference="approved-test-action",
                    operator_audit_context={
                        "mcp_session_sha256": None,
                        "mcp_request_sha256": None,
                        "mcp_client_identity_status": "unavailable",
                        "operator_credential_label": "core_operator_token",
                    },
                )

        self.assertFalse(
            any(query.lstrip().startswith("UPDATE ") for query in connection.queries)
        )

    def test_template_inventory_is_bounded_and_excludes_handler_endpoints(self):
        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters):
                self.query = query
                self.parameters = parameters
                return unittest.mock.Mock(
                    fetchall=lambda: [
                        (
                            "sample-directory",
                            "1.0.0",
                            "active",
                            {"operations": [{"operation_id": "search.entries"}]},
                        )
                    ]
                )

        connection = Connection()
        with patch("api.connect_db", return_value=connection):
            inventory = list_template_packages(25)

        self.assertEqual(connection.parameters, (26,))
        self.assertIn("ORDER BY template_id, template_version", connection.query)
        self.assertEqual(inventory["templates"][0]["lifecycle_state"], "active")
        self.assertFalse(inventory["truncated"])
        self.assertIsNone(inventory["next_cursor"])

    def test_template_inventory_cursor_is_stable_and_scoped(self):
        calls = []

        class Result:
            def __init__(self, rows):
                self.rows = rows

            def fetchall(self):
                return self.rows

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters):
                calls.append((query, parameters))
                if len(calls) == 1:
                    return Result(
                        [
                            ("sample-a", "1.0.0", "active", {}),
                            ("sample-b", "1.0.0", "active", {}),
                            ("sample-c", "1.0.0", "active", {}),
                        ]
                    )
                return Result([("sample-c", "1.0.0", "active", {})])

        with patch("api.connect_db", return_value=Connection()):
            first = list_template_packages(limit=2)
            second = list_template_packages(limit=2, cursor=first["next_cursor"])

        self.assertEqual(
            [item["template_id"] for item in first["templates"]],
            ["sample-a", "sample-b"],
        )
        self.assertTrue(first["truncated"])
        self.assertTrue(first["next_cursor"])
        self.assertEqual(
            [item["template_id"] for item in second["templates"]], ["sample-c"]
        )
        self.assertFalse(second["truncated"])
        self.assertIn("(template_id, template_version) > (%s, %s)", calls[1][0])
        self.assertEqual(calls[1][1], ("sample-b", "1.0.0", 3))
        with self.assertRaises(ValueError):
            list_template_packages(limit=2, cursor=first["next_cursor"] + "bad")

    def test_bound_source_metadata_is_allowlisted_and_requires_binding(self):
        class Result:
            def fetchall(self):
                return [
                    (
                        "federal-source",
                        "Federal source",
                        "https://agency.gov/programs",
                        "available",
                        "federal-programs",
                        "Federal programs",
                        "https://agency.gov/programs/list",
                        "program-list-v1",
                        ["programs"],
                        datetime(2026, 10, 24, tzinfo=timezone.utc),
                        datetime(2026, 10, 24, tzinfo=timezone.utc),
                    )
                ]

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters):
                self.query = query
                self.parameters = parameters
                return Result()

        connection = Connection()
        with patch("api.connect_db", return_value=connection):
            result = api.read_bound_source_metadata(
                "demo-tenant",
                "eligibility",
                "1.0.0",
                "federal-programs",
                "programs",
            )

        self.assertEqual(
            connection.parameters,
            (
                "demo-tenant",
                "eligibility",
                "1.0.0",
                "federal-programs",
                "programs",
            ),
        )
        self.assertIn("pattern_catalog_bindings", connection.query)
        self.assertIn("binding.catalog_namespace = %s", connection.query)
        self.assertNotIn("binding.binding_context ->>", connection.query)
        self.assertEqual(result["source_health"], "not_assessed")
        self.assertFalse(result["records_included"])
        self.assertNotIn("records", result)
        self.assertNotIn("metadata", result)

    def test_template_validation_endpoint_requires_writer_and_returns_digest(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        descriptor = {
            "template_id": "sample-directory",
            "template_version": "1.0.0",
            "artifact_ref": "registry.example.test/directory:release-1",
            "artifact_sha256": "a" * 64,
            "compatible_core_contracts": ["core-route.v1"],
            "input_schema_catalog": {
                "$defs": {
                    "SearchArguments": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    }
                },
            },
            "operations": [
                {
                    "operation_id": "search.entries",
                    "mcp_tool_name": "search_entries",
                    "request_schema_ref": "directory.search.request.v1",
                    "result_contract_ref": "directory.search.result.v1",
                    "summary": "Find matching directory entries.",
                    "user_goal_examples": ["Find a directory entry."],
                    "input_schema_ref": "#/$defs/SearchArguments",
                }
            ],
            "schema_refs": ["directory.search.request.v1"],
            "adapter_refs": [],
            "rule_refs": [],
            "catalog_namespaces": ["directory.entries"],
            "result_contract_refs": ["directory.search.result.v1"],
        }
        descriptor["input_schema_catalog"]["$defs"]["SearchArguments"]["properties"][
            "query"
        ]["description"] = "x" * 20000
        self.assertGreater(len(json.dumps(descriptor).encode()), api.MAX_BODY)
        with (
            patch(
                "api.read_secret",
                side_effect=lambda path: (
                    "template-writer-token"
                    if "template-registry-write" in str(path)
                    else "reader-token"
                ),
            ),
            patch(
                "api.list_template_packages",
                return_value={"templates": [], "truncated": False},
            ),
        ):
            thread.start()
            try:
                from urllib.error import HTTPError
                from urllib.request import Request, urlopen

                endpoint = (
                    f"http://127.0.0.1:{server.server_port}"
                    "/v1/registry/pattern-templates/validate"
                )
                denied = Request(
                    endpoint,
                    data=json.dumps(descriptor).encode(),
                    headers={"Authorization": "Bearer template-writer-token"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(denied, timeout=2)
                self.assertEqual(error.exception.code, 401)

                allowed = Request(
                    endpoint,
                    data=json.dumps(descriptor).encode(),
                    headers={
                        "Authorization": "Bearer reader-token",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urlopen(allowed, timeout=2) as response:
                    result = json.loads(response.read())
                self.assertEqual(result["status"], "valid")
                self.assertEqual(len(result["descriptor_sha256"]), 64)
                self.assertEqual(
                    result["definition"]["template_id"], "sample-directory"
                )
                inventory_request = Request(
                    f"http://127.0.0.1:{server.server_port}/v1/pattern-templates",
                    headers={"Authorization": "Bearer reader-token"},
                )
                with urlopen(inventory_request, timeout=2) as response:
                    inventory = json.loads(response.read())
                self.assertEqual(inventory, {"templates": [], "truncated": False})
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_registration_is_immutable_and_exact_replay_is_idempotent(self):
        definition = {
            "template_id": "sample-directory",
            "template_version": "1.0.0",
            "artifact_ref": "registry.example.test/directory:release-1",
            "artifact_sha256": "a" * 64,
            "compatible_core_contracts": ["core-route.v1"],
            "input_schema_catalog": {
                "$defs": {
                    "SearchArguments": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    }
                },
            },
            "operations": [
                {
                    "operation_id": "search.entries",
                    "mcp_tool_name": "search_entries",
                    "request_schema_ref": "directory.search.request.v1",
                    "result_contract_ref": "directory.search.result.v1",
                    "summary": "Find matching directory entries.",
                    "user_goal_examples": ["Find a directory entry."],
                    "input_schema_ref": "#/$defs/SearchArguments",
                }
            ],
            "schema_refs": ["directory.search.request.v1"],
            "adapter_refs": [],
            "rule_refs": [],
            "catalog_namespaces": ["directory.entries"],
            "result_contract_refs": ["directory.search.result.v1"],
        }

        class Connection:
            def __init__(self):
                self.calls = 0

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters):
                self.calls += 1
                if query.lstrip().startswith("INSERT"):
                    return unittest.mock.Mock(fetchone=lambda: None)
                return unittest.mock.Mock(fetchone=lambda: ("pending", definition))

        connection = Connection()
        with patch("api.connect_db", return_value=connection):
            record, created = register_template_package(definition)

        self.assertFalse(created)
        self.assertEqual(record["lifecycle_state"], "pending")
        self.assertEqual(connection.calls, 2)

    def test_binding_requires_active_declared_operation_and_is_replay_safe(self):
        package_definition = {
            "template_id": "sample-directory",
            "template_version": "1.0.0",
            "artifact_ref": "registry.example.test/directory:release-1",
            "artifact_sha256": "a" * 64,
            "compatible_core_contracts": ["core-route.v1"],
            "input_schema_catalog": {
                "$defs": {
                    "SearchArguments": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    }
                },
            },
            "operations": [
                {
                    "operation_id": "search.entries",
                    "mcp_tool_name": "search_entries",
                    "request_schema_ref": "directory.search.request.v1",
                    "result_contract_ref": "directory.search.result.v1",
                    "summary": "Find matching directory entries.",
                    "user_goal_examples": ["Find a directory entry."],
                    "input_schema_ref": "#/$defs/SearchArguments",
                }
            ],
            "schema_refs": ["directory.search.request.v1"],
            "adapter_refs": [],
            "rule_refs": [],
            "catalog_namespaces": ["directory.entries"],
            "result_contract_refs": ["directory.search.result.v1"],
        }

        class Connection:
            def __init__(self):
                self.calls = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters):
                self.calls.append((query, parameters))
                if "FROM mcp_saas.pattern_template_packages" in query:
                    return unittest.mock.Mock(
                        fetchone=lambda: ("active", package_definition)
                    )
                if query.lstrip().startswith("INSERT"):
                    return unittest.mock.Mock(fetchone=lambda: None)
                return unittest.mock.Mock(
                    fetchone=lambda: (
                        "http://handler.svc.cluster.local/mcp",
                        "search_entries",
                        "mcp",
                        "active",
                        "sample-directory",
                        None,
                    )
                )

        connection = Connection()
        with patch("api.connect_db", return_value=connection):
            route, created = bind_template_operation(
                "sample-directory",
                "1.0.0",
                "search.entries",
                "http://handler.svc.cluster.local/mcp",
            )

        self.assertFalse(created)
        self.assertEqual(route["route_id"], "sample-directory")
        self.assertEqual(route["mcp_tool_name"], "search_entries")
        self.assertIn("pg_advisory_xact_lock", connection.calls[1][0])
        self.assertIn("UPDATE mcp_saas.core_route_metadata", connection.calls[2][0])
        self.assertIn(
            "INSERT INTO mcp_saas.core_route_metadata", connection.calls[3][0]
        )

    def test_template_registration_endpoint_requires_writer_capability(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        body = json.dumps({"unexpected": "field"}).encode()
        with (
            patch(
                "api.read_secret",
                side_effect=lambda path: (
                    "template-writer-token"
                    if "template-registry-write" in str(path)
                    else "reader-token"
                ),
            ),
            patch("api.connect_db") as connect_db,
        ):
            thread.start()
            try:
                from urllib.error import HTTPError
                from urllib.request import Request, urlopen

                endpoint = f"http://127.0.0.1:{server.server_port}/v1/registry/pattern-templates"
                denied = Request(
                    endpoint,
                    data=body,
                    headers={"Authorization": "Bearer reader-token"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(denied, timeout=2)
                self.assertEqual(error.exception.code, 401)
                connect_db.assert_not_called()

                invalid = Request(
                    endpoint,
                    data=body,
                    headers={"Authorization": "Bearer template-writer-token"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(invalid, timeout=2)
                self.assertEqual(error.exception.code, 400)
                connect_db.assert_not_called()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_authenticated_route_api_rejects_bad_query_and_returns_metadata(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        route = {
            "route_id": "route.directory",
            "route_version": "1.0.0",
            "operation_id": "search",
            "endpoint": "https://generic-handler.example/mcp",
            "mcp_tool_name": "search_records",
            "protocol": "mcp",
            "template_id": None,
            "tenant_id": None,
        }
        with (
            patch("api.read_secret", return_value="registry-token"),
            patch("api.read_core_route", return_value=route) as read_route,
        ):
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}/v1/core/routes"
                from urllib.error import HTTPError
                from urllib.request import Request

                unauthorized = Request(
                    base_url + "?route_id=route.directory&route_version=1.0.0"
                    "&operation_id=search"
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(unauthorized, timeout=2)
                self.assertEqual(error.exception.code, 401)
                read_route.assert_not_called()

                invalid = Request(
                    base_url + "?route_id=route.directory&route_id=route.other",
                    headers={"Authorization": "Bearer registry-token"},
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(invalid, timeout=2)
                self.assertEqual(error.exception.code, 400)
                read_route.assert_not_called()

                versioned = Request(
                    base_url + "?route_id=route.directory&route_version=1.0.0"
                    "&operation_id=search",
                    headers={"Authorization": "Bearer registry-token"},
                )
                with urlopen(versioned, timeout=2) as response:
                    legacy_payload = json.loads(response.read())
                self.assertEqual(legacy_payload["route_version"], "1.0.0")
                read_route.assert_called_once_with("route.directory", "search")

                read_route.reset_mock()
                invalid_version = Request(
                    base_url + "?route_id=route.directory&route_version=stale"
                    "&operation_id=search",
                    headers={"Authorization": "Bearer registry-token"},
                )
                with urlopen(invalid_version, timeout=2) as response:
                    stale_payload = json.loads(response.read())
                self.assertEqual(stale_payload["route_version"], "1.0.0")
                read_route.assert_called_once_with("route.directory", "search")

                read_route.reset_mock()
                with self.assertRaises(HTTPError) as error:
                    urlopen(
                        Request(
                            base_url + "?route_id=route.directory&route_version=1.0.0"
                            "&route_version=1.0.1&operation_id=search",
                            headers={"Authorization": "Bearer registry-token"},
                        ),
                        timeout=2,
                    )
                self.assertEqual(error.exception.code, 400)
                read_route.assert_not_called()

                valid = Request(
                    base_url + "?route_id=route.directory&operation_id=search",
                    headers={"Authorization": "Bearer registry-token"},
                )
                with urlopen(valid, timeout=2) as response:
                    payload = json.loads(response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(payload, route)
        read_route.assert_called_once_with("route.directory", "search")

    def test_authenticated_route_inventory_hides_endpoints_and_is_bounded(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        inventory = {
            "routes": [
                {
                    "route_id": "route.directory",
                    "operation_id": "search",
                    "mcp_tool_name": "search_records",
                    "protocol": "mcp",
                    "lifecycle_state": "active",
                    "template_id": None,
                    "tenant_id": None,
                }
            ],
            "truncated": False,
        }
        with (
            patch("api.read_secret", return_value="registry-token"),
            patch("api.read_core_routes", return_value=inventory) as read_routes,
        ):
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}/v1/core/routes"
                from urllib.error import HTTPError
                from urllib.request import Request

                unauthorized = Request(base_url)
                with self.assertRaises(HTTPError) as error:
                    urlopen(unauthorized, timeout=2)
                self.assertEqual(error.exception.code, 401)
                read_routes.assert_not_called()

                authorized = Request(
                    base_url, headers={"Authorization": "Bearer registry-token"}
                )
                with urlopen(authorized, timeout=2) as response:
                    payload = json.loads(response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(payload, inventory)
        self.assertNotIn("endpoint", payload["routes"][0])
        self.assertNotIn("route_version", payload["routes"][0])
        read_routes.assert_called_once_with()

    def test_authenticated_route_inventory_accepts_bounded_page_parameters(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        page = {"routes": [], "truncated": False, "next_cursor": None}
        with (
            patch("api.read_secret", return_value="registry-token"),
            patch("api.read_core_routes", return_value=page) as read_routes,
        ):
            thread.start()
            try:
                from urllib.request import Request

                request = Request(
                    f"http://127.0.0.1:{server.server_port}/v1/core/routes"
                    "?limit=10&cursor=opaque-cursor",
                    headers={"Authorization": "Bearer registry-token"},
                )
                with urlopen(request, timeout=2) as response:
                    payload = json.loads(response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(payload, page)
        read_routes.assert_called_once_with(10, "opaque-cursor")

    def test_bound_source_metadata_requires_registry_auth_and_exact_binding(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        metadata = {
            "status": "available",
            "source_health": "not_assessed",
            "records_included": False,
        }
        with (
            patch("api.read_secret", return_value="registry-token"),
            patch(
                "api.read_bound_source_metadata", return_value=metadata
            ) as read_source,
        ):
            thread.start()
            try:
                from urllib.error import HTTPError
                from urllib.request import Request

                endpoint = (
                    f"http://127.0.0.1:{server.server_port}/v1/tenants/demo-tenant/"
                    "patterns/eligibility/1.0.0/sources/public-programs/"
                    "namespaces/programs"
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(endpoint, timeout=2)
                self.assertEqual(error.exception.code, 401)
                read_source.assert_not_called()

                request = Request(
                    endpoint, headers={"Authorization": "Bearer registry-token"}
                )
                with urlopen(request, timeout=2) as response:
                    payload = json.loads(response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(payload, metadata)
        read_source.assert_called_once_with(
            "demo-tenant", "eligibility", "1.0.0", "public-programs", "programs"
        )


class CoreRouteInventoryTests(unittest.TestCase):
    def test_route_inventory_selects_active_rows_only(self):
        connection = unittest.mock.Mock()
        connection.__enter__ = unittest.mock.Mock(return_value=connection)
        connection.__exit__ = unittest.mock.Mock(return_value=False)
        connection.execute.return_value.fetchall.return_value = []

        with patch("api.connect_db", return_value=connection):
            result = api.read_core_route_page(limit=10)

        self.assertEqual(result["routes"], [])
        query = connection.execute.call_args.args[0]
        self.assertIn("WHERE lifecycle_state = 'active'", query)


class RouteTelemetryValidationTests(unittest.TestCase):
    def test_route_event_allows_only_bounded_privacy_safe_fields(self):
        event = {
            "correlation_id": str(uuid.uuid4()),
            "route_id": "federal-agency.eligibility",
            "route_version": "1.0.0",
            "operation_id": "search-programs",
            "template_id": "eligibility",
            "tenant_id": "federal-agency",
            "result_status": "ok",
            "latency_ms": 12.5,
            "error_class": None,
        }
        normalized = api.valid_route_event(event)
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["template_id"], "eligibility")
        self.assertEqual(normalized["tenant_id"], "federal-agency")
        self.assertIsNone(api.valid_route_event({**event, "arguments": {"ssn": "x"}}))
        self.assertIsNone(api.valid_route_event({**event, "latency_ms": -1}))
        self.assertIsNone(api.valid_route_event({**event, "result_status": "secret"}))

    def test_route_event_package_id_is_optional_and_must_be_a_uuid(self):
        event = {
            "correlation_id": str(uuid.uuid4()),
            "route_id": "federal-usda.eligibility",
            "route_version": "1.0.0",
            "operation_id": "search-programs",
            "template_id": "eligibility",
            "tenant_id": "federal-usda",
            "result_status": "ok",
            "latency_ms": 12.5,
            "error_class": None,
        }
        self.assertIsNone(api.valid_route_event(event)["package_id"])
        package_id = str(uuid.uuid4())
        with_package = api.valid_route_event({**event, "package_id": package_id})
        self.assertEqual(with_package["package_id"], package_id)
        self.assertIsNone(api.valid_route_event({**event, "package_id": "not-a-uuid"}))
        self.assertIsNone(api.valid_route_event({**event, "package_id": 7}))
        self.assertIsNone(
            api.valid_route_event({**event, "package_id": package_id, "extra": 1})
        )

    def test_route_event_allows_shared_cluster_template_without_tenant(self):
        event = {
            "correlation_id": str(uuid.uuid4()),
            "route_id": "eligibility",
            "route_version": "0.1.29",
            "operation_id": "program.inventory.read",
            "template_id": "eligibility",
            "tenant_id": None,
            "result_status": "ok",
            "latency_ms": 12.5,
            "error_class": None,
        }
        normalized = api.valid_route_event(event)
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["template_id"], "eligibility")
        self.assertIsNone(normalized["tenant_id"])
        self.assertIsNone(
            api.valid_route_event(
                {
                    **event,
                    "route_id": None,
                    "route_version": None,
                    "operation_id": None,
                }
            )
        )

    def test_route_event_without_resolved_route_keeps_all_route_dimensions_null(self):
        event = {
            "correlation_id": str(uuid.uuid4()),
            "route_id": None,
            "route_version": None,
            "operation_id": None,
            "template_id": None,
            "tenant_id": None,
            "result_status": "unknown_route",
            "latency_ms": 1,
            "error_class": "unknown_route",
        }
        self.assertIsNotNone(api.valid_route_event(event))
        self.assertIsNone(api.valid_route_event({**event, "route_id": "known-route"}))

    def test_route_event_accepts_distinct_downstream_error_outcomes(self):
        event = {
            "correlation_id": str(uuid.uuid4()),
            "route_id": "federal-agency.eligibility",
            "route_version": "1.0.0",
            "operation_id": "search-programs",
            "template_id": "eligibility",
            "tenant_id": "federal-agency",
            "result_status": "invalid_request",
            "latency_ms": 1,
            "error_class": "tenant_argument_validation",
        }
        self.assertIsNotNone(api.valid_route_event(event))
        self.assertIsNotNone(
            api.valid_route_event(
                {
                    **event,
                    "result_status": "downstream_error",
                    "error_class": "tenant_tool_error",
                }
            )
        )

    def test_route_report_rejects_naive_and_oversized_windows(self):
        self.assertIsNone(
            api.read_core_route_report("2026-10-01T00:00:00", "2026-10-01T01:00:00Z")
        )
        self.assertIsNone(
            api.read_core_route_report("2026-09-01T00:00:00Z", "2026-10-15T00:00:00Z")
        )

    def test_route_event_insert_uses_only_allowlisted_values(self):
        event = {
            "correlation_id": str(uuid.uuid4()),
            "route_id": "federal-agency.eligibility",
            "route_version": "1.0.0",
            "operation_id": "search-programs",
            "template_id": "eligibility",
            "tenant_id": "federal-agency",
            "result_status": "ok",
            "latency_ms": 12.5,
            "error_class": None,
        }

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters):
                self.query = query
                self.parameters = parameters

        connection = Connection()
        with patch("api.connect_db", return_value=connection):
            result = api.record_core_route_event(event)

        self.assertEqual(result, {"status": "recorded"})
        self.assertIn("INSERT INTO mcp_saas.core_route_events", connection.query)
        self.assertNotIn("arguments", connection.query)
        self.assertEqual(connection.parameters[4:6], ("eligibility", "federal-agency"))

    def test_route_report_returns_aggregates_and_unknown_usage_only(self):
        group = (
            "federal-agency.eligibility",
            "1.0.0",
            "search-programs",
            "eligibility",
            "federal-agency",
            2,
            1,
            1,
            0,
            0,
            0,
            0,
            12.5,
            20.0,
        )

        class GroupResult:
            def fetchall(self):
                return [group]

        class CountResult:
            def fetchone(self):
                return (2,)

        class Connection:
            calls = 0

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters):
                self.calls += 1
                return GroupResult() if self.calls == 1 else CountResult()

        with patch("api.connect_db", return_value=Connection()):
            report = api.read_core_route_report(
                "2026-10-21T00:00:00Z", "2026-10-22T00:00:00Z"
            )

        self.assertEqual(report["request_count"], 2)
        self.assertEqual(report["groups"][0]["tenant_id"], "federal-agency")
        self.assertEqual(report["groups"][0]["outcomes"]["unavailable"], 1)
        self.assertEqual(report["groups"][0]["outcomes"]["invalid_request"], 0)
        self.assertEqual(report["groups"][0]["outcomes"]["downstream_error"], 0)
        self.assertEqual(report["groups"][0]["p95_latency_ms"], 20.0)
        self.assertIsNone(report["usage"])
        self.assertIsNone(report["cost"])
        self.assertEqual(report["measurement_status"], "unknown")
        self.assertNotIn("endpoint", report["groups"][0])


class ProfileRefinementContractTests(unittest.TestCase):
    @staticmethod
    def schema():
        return {
            "registry_id": "demo-profile-schema",
            "schema_version": "fixture-v1",
            "dimensions": {"financial": {}},
            "attributes": {
                "income.weekly": {"type": "number", "dimension_id": "financial"},
                "income.annual": {
                    "type": "number",
                    "dimension_id": "financial",
                    "refinement_methods": [
                        {
                            "method_id": "annualize",
                            "method_version": "1.0.0",
                            "method_type": "scale_by_factor",
                            "input_attribute_id": "income.weekly",
                            "factor": 52,
                            "input_unit": "weekly",
                            "output_unit": "annual",
                            "context": {"period": "annual"},
                            "assumptions": ["same amount each week"],
                            "uncertainty": "amounts can vary",
                        }
                    ],
                },
            },
        }

    def test_write_recomputes_derived_value_and_stores_provenance(self):
        schema = self.schema()
        value_rows = []
        history_rows = []

        class Result:
            def __init__(self, row=None, rows=None):
                self.row = row
                self.rows = rows or []

            def fetchone(self):
                return self.row

            def fetchall(self):
                return self.rows

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters=()):
                if "SELECT definition FROM mcp_saas.schema_registry" in query:
                    return Result((schema,))
                if "INSERT INTO mcp_saas.profile_access_references" in query:
                    return Result()
                if "INSERT INTO mcp_saas.synthetic_profiles" in query:
                    return Result(("synthetic-person-001",))
                if "INSERT INTO mcp_saas.profile_attribute_values" in query:
                    value_rows.append(parameters)
                    return Result()
                if "INSERT INTO mcp_saas.profile_attribute_history" in query:
                    history_rows.append(parameters)
                    return Result()
                if "FROM mcp_saas.synthetic_profiles WHERE profile_id" in query:
                    return Result(
                        (
                            "synthetic-person-001",
                            "person",
                            "demo-profile-schema",
                            "fixture-v1",
                            '{"record_type":"synthetic"}',
                            1,
                            None,
                        )
                    )
                if "FROM mcp_saas.profile_attribute_values" in query:
                    return Result(
                        rows=[
                            (
                                row[1],
                                row[2],
                                row[3],
                                json.loads(row[4]),
                                row[5],
                                row[6],
                                json.loads(row[7]),
                                None,
                            )
                            for row in value_rows
                        ]
                    )
                if "FROM mcp_saas.profile_saved_interests" in query:
                    return Result(rows=[])
                raise AssertionError(f"unexpected SQL: {query}")

        payload = {
            "profile_kind": "person",
            "schema_registry_id": "demo-profile-schema",
            "schema_version": "fixture-v1",
            "attributes": [
                {"attribute_id": "income.weekly", "value": 100},
                {
                    "attribute_id": "income.annual",
                    "value": 5200,
                    "derivation": {
                        "method_id": "annualize",
                        "method_version": "1.0.0",
                    },
                },
            ],
            "confirmed": True,
        }
        with patch("api.connect_db", return_value=Connection()):
            result, status = api.create_synthetic_profile(
                payload, profile_scope_sha256="b" * 64
            )

        self.assertEqual(status, "ok")
        self.assertEqual(len(value_rows), 2)
        raw_value = next(row for row in value_rows if row[1] == "income.weekly")
        derived_value = next(row for row in value_rows if row[1] == "income.annual")
        self.assertEqual(raw_value[5], "profile.user_confirmed")
        self.assertEqual(json.loads(raw_value[7])["origin"], "user_confirmed")
        self.assertEqual(derived_value[5], "profile.derived")
        stored = json.loads(derived_value[7])
        self.assertEqual(stored["origin"], "derived")
        self.assertEqual(stored["confirmation_status"], "confirmed")
        self.assertEqual(stored["input_values"], {"income.weekly": 100})
        self.assertEqual(len(history_rows), 2)
        self.assertEqual(result["status"], "available")

    def test_write_rejects_fabricated_derived_value_before_insert(self):
        schema = self.schema()

        class Result:
            def fetchone(self):
                return (schema,)

        class Connection:
            inserted = False

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, parameters=()):
                if "SELECT definition FROM mcp_saas.schema_registry" in query:
                    return Result()
                self.inserted = True
                raise AssertionError(
                    "invalid derivation must be rejected before insert"
                )

        connection = Connection()
        payload = {
            "profile_kind": "person",
            "schema_registry_id": "demo-profile-schema",
            "schema_version": "fixture-v1",
            "attributes": [
                {"attribute_id": "income.weekly", "value": 100},
                {
                    "attribute_id": "income.annual",
                    "value": 5201,
                    "derivation": {"method_id": "annualize", "method_version": "1.0.0"},
                },
            ],
            "confirmed": True,
        }
        with patch("api.connect_db", return_value=connection):
            result, status = api.create_synthetic_profile(
                payload, profile_scope_sha256="b" * 64
            )

        self.assertIsNone(result)
        self.assertEqual(status, "profile_derivation_value_mismatch")
        self.assertFalse(connection.inserted)

    def test_schema_read_endpoint_requires_profile_reader_token(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        schema = self.schema()
        with (
            patch("api.read_secret", return_value="profile-reader"),
            patch("api.read_profile_schema", return_value=schema) as read_schema,
        ):
            thread.start()
            try:
                from urllib.error import HTTPError
                from urllib.request import Request

                url = (
                    f"http://127.0.0.1:{server.server_port}/v1/profile-schemas/"
                    "demo-profile-schema/fixture-v1"
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(url, timeout=2)
                self.assertEqual(error.exception.code, 401)
                read_schema.assert_not_called()
                request = Request(
                    url, headers={"Authorization": "Bearer profile-reader"}
                )
                with urlopen(request, timeout=2) as response:
                    payload = json.loads(response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(payload, schema)
        read_schema.assert_called_once_with("demo-profile-schema", "fixture-v1")


if __name__ == "__main__":
    unittest.main()
