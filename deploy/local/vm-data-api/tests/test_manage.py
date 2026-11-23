import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))
MANAGE_PATH = API_DIR / "manage.py"
MANAGE_SPEC = importlib.util.spec_from_file_location("manage", MANAGE_PATH)
assert MANAGE_SPEC is not None and MANAGE_SPEC.loader is not None
manage = importlib.util.module_from_spec(MANAGE_SPEC)
MANAGE_SPEC.loader.exec_module(manage)


class CatalogScopeConfigurationTests(unittest.TestCase):
    def test_default_configuration_does_not_bind_a_pattern_or_tenant(self):
        self.assertEqual(manage.catalog_scope_env_args(), [])

    def test_explicit_complete_scope_is_passed_to_api(self):
        with patch.dict(
            manage.C,
            {
                "catalog_scope_cluster_id": "example-pattern",
                "catalog_scope_pattern_version": "1.2.3",
                "catalog_scope_tenant_id": "example-tenant",
            },
        ):
            self.assertEqual(
                manage.catalog_scope_env_args(),
                [
                    "--env",
                    "API_CATALOG_CLUSTER_ID=example-pattern",
                    "--env",
                    "API_CATALOG_PATTERN_VERSION=1.2.3",
                    "--env",
                    "API_CATALOG_TENANT_ID=example-tenant",
                ],
            )

    def test_partial_scope_is_rejected(self):
        with patch.dict(manage.C, {"catalog_scope_cluster_id": "example-pattern"}):
            with self.assertRaisesRegex(ValueError, "must set cluster"):
                manage.catalog_scope_env_args()


class ProfileFixtureReaderConfigurationTests(unittest.TestCase):
    def test_missing_optional_fixture_reader_does_not_add_a_secret_mount(self):
        with patch.dict(
            manage.C,
            {
                "profile_fixture_read_token_secret": None,
                "profile_fixture_read_token_target": None,
            },
        ):
            self.assertEqual(manage.profile_fixture_reader_secret_args(), [])

    def test_complete_fixture_reader_binding_is_scoped_to_its_own_token(self):
        with patch.dict(
            manage.C,
            {
                "profile_fixture_read_token_secret": "fixture-reader",
                "profile_fixture_read_token_target": "fixture-reader-token",
            },
        ):
            self.assertEqual(
                manage.profile_fixture_reader_secret_args(),
                [
                    "--secret",
                    "fixture-reader,type=mount,target=fixture-reader-token,uid=10001,gid=10001,mode=0400",
                    "--env",
                    "API_PROFILE_FIXTURE_READ_TOKEN_FILE=/run/secrets/fixture-reader-token",
                ],
            )

    def test_partial_fixture_reader_binding_is_rejected(self):
        with patch.dict(
            manage.C,
            {
                "profile_fixture_read_token_secret": "fixture-reader",
                "profile_fixture_read_token_target": None,
            },
        ):
            with self.assertRaisesRegex(ValueError, "must set both"):
                manage.profile_fixture_reader_secret_args()

    def test_unconfigured_fixture_reader_is_not_a_required_secret(self):
        with patch.dict(
            manage.C,
            {
                "profile_fixture_read_token_secret": None,
                "profile_fixture_read_token_target": None,
            },
        ):
            self.assertNotIn(
                None,
                manage.configured_api_secret_names(),
            )


class ApiReconfigurePreflightTests(unittest.TestCase):
    def test_missing_runtime_secret_fails_before_removing_running_api(self):
        with (
            patch.object(manage, "exists", return_value=True),
            patch.object(
                manage,
                "preflight_api_reconfigure",
                side_effect=RuntimeError("missing configured secret"),
            ),
            patch.object(manage, "run") as run,
            patch.object(manage, "create_api") as create_api,
        ):
            with self.assertRaisesRegex(RuntimeError, "missing configured secret"):
                manage.reconfigure_api()
        run.assert_not_called()
        create_api.assert_not_called()

    def test_runtime_preflight_checks_image_network_and_only_configured_secrets(self):
        with (
            patch.dict(
                manage.C,
                {
                    "profile_fixture_read_token_secret": None,
                    "profile_fixture_read_token_target": None,
                },
            ),
            patch.object(
                manage.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0),
            ) as run,
        ):
            manage.preflight_api_reconfigure()
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(
            commands[0], ["podman", "image", "exists", manage.C["api_image"]]
        )
        self.assertEqual(
            commands[1], ["podman", "network", "exists", manage.C["database_network"]]
        )
        self.assertTrue(
            all(
                command[:3] == ["podman", "secret", "exists"]
                for command in commands[2:]
            )
        )


if __name__ == "__main__":
    unittest.main()
