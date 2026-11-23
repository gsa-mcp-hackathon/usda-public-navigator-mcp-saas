# Persistence Access API folder

> Version 1.0 · Updated 2026-11-21 · Reference, read only if you run the Persistence Access API  
> [Documentation index](../../../docs/README.md) · [Deployment guide](../../DEPLOYMENT-GUIDE.md) · [Local runbook](../LOCAL-RUNBOOK.md)

This folder holds the Persistence Access API: the small HTTP service that sits between the platform and its database in the demonstration. You do not need it unless you adopt the same persistence design. Any other persistence method replaces it.

## Read in this order

| Order | Read | To learn |
|---|---|---|
| 1 | [Design overview](PERSISTENCE-ACCESS-API.md) | Why the API exists, how it works, what it exposes, where it fits behind the persistence contract, and what must stay compatible across a redeploy or a rewrite |
| 2 | [`schema.sql`](schema.sql) and [`db/migrations/`](../../../db/migrations/) | The database objects the API reads and writes. Apply migrations in numeric order |
| 3 | [`api.py`](api.py) | The routes and the authorization each one needs |
| 4 | [`config.json`](config.json) and [`config_loader.py`](config_loader.py) | The non-secret settings: container, network and host names, port |
| 5 | [`provision_secrets.py`](provision_secrets.py) | How scoped database roles and the per-caller bearer tokens are created |
| 6 | [`tests/`](tests/) | The contract the API keeps, as executable checks |

## What each file does

| File | Role |
|---|---|
| [`PERSISTENCE-ACCESS-API.md`](PERSISTENCE-ACCESS-API.md) | The design overview |
| `api.py` | The HTTP boundary to the database |
| `config.json`, `config_loader.py` | Non-secret container configuration and its loader |
| `provision_secrets.py` | Creates scoped database roles and protected credentials |
| `manage.py` | Builds and toggles the API container and its tunnel |
| `schema.sql`, `schema_tags.py` | Database schema, and validation for the versioned schema registry tags |
| `template_packages.py`, `tenant_packages.py` | Validation for pattern template packages and tenant onboarding specifications |
| `source_policy_manifests.py`, `public_catalog_registry.py` | Validation for source-policy candidates and for source and dataset registration |
| `program_candidate_records.py`, `program_candidate_publications.py` | Validation for unpublished program candidates, and for promoting approved ones to shared inventory |
| `criteria_rulesets.py` | Persistence for validated Eligibility criteria metadata |
| `profile_interests.py` | Validation for programs a person saves to a profile |
| `evaluation_fixtures.py` | Contracts for named synthetic evaluation fixture sets |
| `operator_audit.py` | Validation for privacy-safe operator request correlation metadata |
| `Dockerfile`, `requirements.txt` | The container image and its dependencies |
| `tests/` | Contract and migration tests |

The folder and several names inside it still say `vm-data-api`, from the virtual machine where the API first ran. They are kept on purpose: the code and configuration reference them, and the planned migration would make a rename obsolete. The [design overview](PERSISTENCE-ACCESS-API.md#why-the-directory-is-still-called-vm-data-api) explains this.

---

[Documentation index](../../../docs/README.md) · [Deployment guide](../../DEPLOYMENT-GUIDE.md) · [Local runbook](../LOCAL-RUNBOOK.md)
