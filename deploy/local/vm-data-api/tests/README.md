# Persistence Access API tests

> Version 1.0 · Updated 2026-11-21 · Folder index  
> [Documentation index](../../../../docs/README.md) · [API folder](../README.md) · [Design overview](../PERSISTENCE-ACCESS-API.md)

These tests check that the Persistence Access API keeps its contract: who may call which route, what each request must contain, what is stored, and that stored records cannot be changed after the fact. Read the [design overview](../PERSISTENCE-ACCESS-API.md) first; its compatibility contract is what these tests hold in place.

Run them with `PYTHONPATH=src python -m pytest -q deploy/local/vm-data-api/tests`. Files ending `_postgres.py` need a real PostgreSQL and are skipped unless `MCP_TEST_DATABASE_URL` is set. The others need nothing external. Run them before changing the API, a migration or a database role, not as part of deploying.

## Routes and configuration

| File | What it verifies |
|---|---|
| `test_api_contract.py` | The route contract: each capability token maps to its own cluster and routes; requests missing required fields are refused; reads return bounded pages; summaries match what was stored. The largest file, with 118 checks |
| `test_config_loader.py` | Container configuration loads from an absolute file path and must be a JSON object |
| `test_manage.py` | The container manager passes only the scope it is given; a partial scope is refused; optional secrets are added only when needed |
| `conftest.py` | Shared test setup |

## Validation of what is stored

| File | What it verifies |
|---|---|
| `test_criteria_rulesets.py` | A criteria rule set must cite a verified source, match its canonical digest and map to the active schema |
| `test_source_policy_manifests.py` | Source-policy candidates have a stable canonical digest, and broad or unsafe search scopes are rejected |
| `test_public_catalog_registry.py` | Source and dataset registrations match the active contract; the catalog namespace is independent of schema namespaces |
| `test_program_candidate_records.py` | A program candidate must be pending and unpublished, carry a valid identity and bind to its exact digest |
| `test_template_packages.py` | A pattern template package is organization-neutral and immutable, and its projection mapping must be declared |
| `test_tenant_packages.py` | A tenant onboarding specification has a valid endpoint, namespace and bindings |
| `test_profile_interests.py` | A person can save only programs that match the current approved source policy |

## Database roles and migrations

| File | What it verifies |
|---|---|
| `test_provision_secrets.py` | Each database role gets only the access it needs, such as select and insert but never update or delete on append-only tables |
| `test_template_lifecycle_grants.py` | The lifecycle migrations grant receipt access only, and are repeatable |
| `test_profile_store_foundation_migration.py`, `test_profile_relationship_migration.py` | The profile tables and namespaced relationships are created as intended |
| `test_public_dataset_record_limit_migration.py` | The record limit per dataset is bounded and keeps its default |
| `test_requirements_package_v2_migration.py` | The requirements package table accepts the earlier and the current shape |

## Against a real database (skipped without `MCP_TEST_DATABASE_URL`)

| File | What it verifies |
|---|---|
| `test_criteria_rulesets_postgres.py` | Cluster-scoped rule sets migrate repeatably and read back |
| `test_template_packages_postgres.py` | A template registers, survives a restart and reads back |
| `test_tenant_packages_postgres.py` | Tenant onboarding is namespaced, immutable and routable |
| `test_profile_resume_claims_postgres.py` | A resume claim rotates once; revoked or expired claims fail; a replayed claim succeeds exactly once |
| `test_public_catalog_governed_postgres.py` | Governed catalog behavior against the database |

---

[Documentation index](../../../../docs/README.md) · [API folder](../README.md)
