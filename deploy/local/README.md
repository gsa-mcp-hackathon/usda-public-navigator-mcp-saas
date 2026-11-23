# Local deploy folder

> Version 1.0 · Updated 2026-11-21 · Folder index  
> [Documentation index](../../docs/README.md) · [Deploy folder](../README.md)

The compose files for running the platform on one machine, and the runbook that explains them.

| Read | What it is |
|---|---|
| [LOCAL-RUNBOOK.md](LOCAL-RUNBOOK.md) | How to run the Core and tenants locally. Start here |
| `docker-compose.yml` | The base profile: one Core and four synthetic tenants that answer from their own configuration. No database needed |
| `docker-compose.vm-data-api.yml` | An overlay that points the Core and tenants at the Persistence Access API instead of a database |
| `docker-compose.vm-postgres.yml` | An overlay for direct database connections. Kept as a comparison profile, not the aligned runtime |
| `docker-compose.vm-registry.yml` | A one-shot job that projects the reviewed registry definitions into the runtime |
| [vm-data-api/](vm-data-api/README.md) | The Persistence Access API: source, schema and tests |

---

[Documentation index](../../docs/README.md) · [Deploy folder](../README.md)
