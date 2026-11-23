# Example server (the Core and shared runtime)

> Version 1.0 · Updated 2026-11-21 · Folder index  
> [Documentation index](../../docs/README.md) · [Source](../README.md) · [Architecture](../../docs/ARCHITECTURE.md)

The platform runtime: the Core MCP server that routes requests to clusters, the registry it reads, the clients for the persistence layer, and the shared settings and telemetry. The folder name is from the project template; the contents are the real Core. Read [Architecture](../../docs/ARCHITECTURE.md) first for how the layers fit.

## Start here

| Module | What it is |
|---|---|
| `core_app.py` | The Core itself and its entry point (`Dockerfile.core` runs it): the tools it publishes, the route dispatcher and the operator-gated functions. The largest file; read the sections for the area you need |
| `platform.py` | Builds the Core and router, or a separately configured tenant application |
| `app.py` | The entry point for the platform and tenant image (`Dockerfile` runs it). Deliberately thin: creates the server, registers tools and chooses the transport |
| `config.py` | Environment-backed settings, validated at the boundary |
| `routes.py` | The HTTP routes beside `/mcp`, including `/health` |

## By job

| Job | Module | What it does |
|---|---|---|
| **Routes and registry** | `core_routes.py` | The route metadata contract and its persistence boundary |
| | `registry.py` | Validated registry definitions from Git and the active registry projection |
| | `core_templates.py`, `core_tenants.py` | Managing cluster templates, tenant registration and onboarding |
| **Source policy** | `core_source_policy.py` | Operator-gated review of source-policy and program candidates |
| **Profiles** | `core_profiles.py` | The client for the synthetic profile service |
| | `profile_refinement.py` | Fixed numeric refinements declared in a schema |
| **Persistence** | `storage.py` | The repository interfaces the runtime depends on |
| | `persistence_api.py`, `persistence_api_catalog.py`, `persistence_api_client.py` | The HTTP adapters that implement those interfaces through the [Persistence Access API](../../deploy/local/vm-data-api/PERSISTENCE-ACCESS-API.md) |
| | `public_ingest.py` | Operator-run ingest of public catalog metadata |
| **Observability** | `telemetry.py`, `core_telemetry.py` | Privacy-safe route events and bounded reads of them |
| | `core_tracing.py` | Optional, privacy-safe tracing of tool calls |
| **Shared types** | `models.py`, `request_context.py` | Request and response models, and validated request context |

Tests for each module are in [`tests/`](../../tests/README.md), named after it: `test_core_*` for the `core_*` modules.

---

[Documentation index](../../docs/README.md) · [Source](../README.md)
