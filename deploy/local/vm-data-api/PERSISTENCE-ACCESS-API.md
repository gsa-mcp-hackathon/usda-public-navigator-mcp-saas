# Persistence Access API: design overview

> Version 2.0 · Updated 2026-11-21 · Reference, read only if you run the Persistence Access API  
> [Documentation index](../../../docs/README.md) · [Folder index](README.md) · [Data and sources](../../../docs/DATA-AND-SOURCES.md)

This directory holds the Persistence Access API, the component that sits between the platform and its database. A new installation does not need it unless it adopts the same persistence design. The platform only requires that something durably stores its data, so a managed database, direct role-scoped connections or another API can replace it.

## Why it exists

The design persists data through a database service with direct, role-scoped connections. The environment the hackathon assigned blocked direct database connections by design, so there was no way to get the database to work natively there. The Persistence Access API is the compensating control: the platform's applications call a small, bounded HTTP API, and only the API holds the database credential. It ran first on a virtual machine and was later moved to a container host. The name of this directory comes from that first home. The component is a persistence layer, not a VM tool, and it is not tied to any provider.

PostgreSQL sits behind it because the team knew it, not because the platform requires it. Rebuilding this layer into a native, data-agnostic model was a planned enhancement that did not fit the build window (see [Enhancements](../../../docs/enhancements/README.md)).

## How it works

- **The API is the only application with a database credential.** The Core and the tenants never receive a connection string.
- **Each caller holds one narrow capability.** A capability is a separate bearer token, mounted only in the runtime that needs it, and each grants only the fixed operations its handlers need.
- **Writes are bounded and audited.** Packages, rule sets and lifecycle changes are immutable and append-only, repeated saves are idempotent, and changed content under the same identity is a conflict.
- **Reads are scoped.** A tenant reads catalog records only for its own registered tenant, pattern and version.
- **It fails closed.** The default configuration names no cluster, pattern version or tenant, so catalog reads are refused until an operator supplies all three.

The main capabilities, and what each one allows:

| Capability | What it allows | Typically held by |
|---|---|---|
| Registry reader | Read routes, templates and tenants; validate drafts | The Core |
| Catalog reader | Read catalog records for one registered tenant, pattern and version | Tenant applications |
| Registry, template and tenant writers | Register endpoints, templates and tenants, and activate or deactivate them by exact digest | Operator and deployment workflows |
| Public catalog writer | Write source, dataset, record and ingestion-run rows, through a separate least-privilege database identity | Operator-triggered ingestion |
| Requirements package and criteria rule set writers | Save immutable packages and rule sets, and read them back | Tenant applications and the mapping step |
| Profile reader and writer | Synthetic profile reads, confirmed writes and revisions | The Core's profile tools |
| Route telemetry writer | Post privacy-safe route events | The Core |

## What it exposes

The routes fall into a few groups. Each route and its rules are in `api.py` and the contract tests.

- Health and synthetic probes.
- Core route reads and tenant inventory without private endpoints.
- Tenant catalog reads by dataset and namespace.
- Tenant registry lifecycle: validate, register, activate and deactivate.
- Pattern template packages: validate, register, activate and deactivate.
- Public source, dataset and record intake, with governed jurisdiction metadata and audit readback.
- Synthetic profiles with opaque references and expiring resume claims.
- Requirements packages and criteria rule sets: save, read back and enrich.
- Route telemetry, as aggregates only.

## Where it fits: one adapter behind a persistence contract

The platform does not talk to a database directly. Each part that needs stored data calls a small repository interface (in most places a Python `Protocol`), and a configuration setting chooses which implementation sits behind it. The Persistence Access API is one such implementation, the one for **remote data**: use it when the applications run somewhere that cannot, or should not, connect to the database themselves.

| What is stored | Interface | Implementations today | Chosen by |
|---|---|---|---|
| Catalog records | `CatalogRepository` (`src/example_server/storage.py`) | Configuration only, direct PostgreSQL, or the API | `CATALOG_BACKEND` |
| Routes, templates, tenants (the registry) | `RegistryRepository` and the Core route, template and tenant interfaces | Direct PostgreSQL or the API | `REGISTRY_BACKEND` |
| Profiles, source-policy review, public catalog registration, template lifecycle, requirements packages, criteria rule sets | One repository per area in `src/example_server/` and `src/clusters/` | The API only | `PERSISTENCE_API_URL` and the capability tokens |

Two things follow from this, and they make the design safe to change.

- **The platform is already separated from the database.** Moving to a native data layer, either a data layer service or direct role-scoped database connections, means writing new implementations of these interfaces. It should not change the Core, the tenants or the tools. The first two rows already have a direct-database option. The last row is the larger piece of work, because those areas exist only as API clients today.
- **The API keeps a role in that future.** A native data layer serves applications that sit next to the database. The Persistence Access API remains the way to serve remote data to applications that do not, with its narrow capabilities and bounded routes. It becomes one adapter among several, not the persistence layer itself.

### Keeping a redeploy or a rewrite safe

The released system depends on the following, so treat them as a compatibility contract. Change them only additively, or by building a new stack and moving over (see the [naming standard](../../../docs/NAMING-STANDARD.md)).

- **The routes under `/v1/`.** Add routes and fields, and do not rename or remove existing ones. A client built for this release must still work.
- **The capability token names and what each one allows.** A new capability is additive. Narrowing or renaming an existing one breaks the applications that hold it.
- **The configuration setting names** (`CATALOG_BACKEND`, `REGISTRY_BACKEND`, `PERSISTENCE_API_URL` and the token file settings) and their values.
- **The service host name the code allows for the local profile** (`mcp-saas-vm-data-api`), and the container, image and network names in `config.json`. Both the code and the compose files use them.
- **The database schema in `db/migrations/`.** Apply migrations in numeric order and only add to them. Take a verified backup before applying one to an existing database. The requirements package revisions are added by `046_eligibility_requirements_package_revisions.sql`; that migration only grants select and insert.
- **The immutability rules.** Saved packages, rule sets and audit receipts are append-only, and the same content saved twice returns the same record.

A rewrite is safe when it keeps the interfaces above, passes the existing tests in `deploy/local/vm-data-api/tests/` and `tests/` against the new implementation, and is switched on with the same configuration settings. That lets the old and new implementations run side by side and be compared before anything is cut over.

## Running it

1. **Create the database schema.** Apply the files in `db/migrations/` in numeric order, then load any seed data you want.
2. **Provision the capabilities.** `python3 provision_secrets.py` creates the missing tokens and the least-privilege database grants, and writes owner-only token files and container secrets. It keeps existing credentials.
3. **Describe your environment.** The defaults in `config.json` are the local container names. Point `MCP_SAAS_DATA_API_CONFIG_FILE` at a file of your own to set the database host, networks, secret names and API container.
4. **Build and run.** `python3 manage.py build`, then `install` (or `reconfigure` to update an existing container), and `start`, `stop` and `status` to control it. The image is rebuilt only when the code changes.
5. **Connect the applications.** Give the Core the registry reader and each tenant the catalog reader, and set `PERSISTENCE_API_URL`. The local compose profile in the [local runbook](../README.md) does this for you.
6. **Reach it from another network.** If the applications run somewhere that cannot see the API directly, publish it through an outbound-only tunnel or a private network route. The demonstration used an outbound-only tunnel from the VM to its cloud applications. Never expose the database.

The tests for this component are in `tests/` and run with the rest of the suite (`uv run pytest tests deploy/local/vm-data-api/tests`). The PostgreSQL integration tests are skipped unless `MCP_TEST_DATABASE_URL` is set.

## Why the directory is still called `vm-data-api`

The container and host names, the Dockerfile and ignore paths, the tests and a probe table in the database all carry the old name, and the running system depends on them. Renaming them in place would break the demonstration, and the [naming standard](../../../docs/NAMING-STANDARD.md) says names on a running system change only by building a new stack. The rename belongs with that migration.

The name was left on purpose, for two reasons. First, the code, the container and network names, the configuration and the tests all reference it, and changing them risks breaking the codebase for no functional gain. Second, the planned migration moves persistence to a dedicated stack and, in a later version, rewrites this layer as a native data service, so any rename of the current directory would soon be obsolete.

---

[Documentation index](../../../docs/README.md) · [Folder index](README.md)
