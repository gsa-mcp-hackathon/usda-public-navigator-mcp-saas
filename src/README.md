# Source

> Version 1.0 · Updated 2026-11-21 · Folder index  
> [Documentation index](../docs/README.md) · [Repository README](../README.md)

The platform code, in two packages.

| Folder | What it is |
|---|---|
| [`example_server/`](example_server/README.md) | The Core: the MCP server, route and template registry, tenant and profile handling, source policy, telemetry, tracing, and the clients for the persistence layer. `core_app.py` is the Core's entry point; `storage.py` holds the repository interfaces and `persistence_api*.py` the API-backed implementations |
| [`clusters/`](clusters/README.md) | The pattern code a Core hosts. `eligibility_*.py` is the Eligibility cluster (discovery, evaluation, criteria, requirements, results, public search, ingest); `eligibility_v0/` is the first version of the tenant app; `sample_directory/` is the second, generic pattern that shows the Core is not eligibility-specific |

Start with [Architecture](../docs/ARCHITECTURE.md) for how the layers fit, then [the Eligibility pattern](../docs/ELIGIBILITY-PATTERN.md).

---

[Documentation index](../docs/README.md)
