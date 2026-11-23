# Scripts

> Version 1.0 · Updated 2026-11-21 · Folder index  
> [Documentation index](../docs/README.md) · [Repository README](../README.md)

Command-line tools for building, checking, registering and ingesting. Each file starts with a description of what it does. Several of the ingest and registration tools have a dry-run mode; check a tool's own description before running it.

| File | What it does |
|---|---|
| `bind_template_operation.py` | Activate one reviewed template operation after its tenant passes checks. |
| `build_guides.py` | Build self-contained HTML guides from Markdown sources (docs-as-code projection). |
| `build_template_package.py` | Build a validated immutable pattern package descriptor from a registry digest. |
| `check_repo.py` | Check the repository against the structure rules in the repository standard: folder indexes, one-file folders, index completeness, doc headers and footers, links. |
| `check_naming.py` | Check a stack manifest against docs/NAMING-STANDARD.md, and detect drift. |
| `detect_release_scope.py` | Determine which immutable runtime images a main-branch release must publish. |
| `ingest_eligibility_sources.py` | Fetch approved public pages and send normalized records through the Data API. |
| `ingest_grants_gov_opportunities.py` | Dry-run or explicitly ingest a bounded Grants.gov listing snapshot. |
| `ingest_hrsa_health_center_sites.py` | Validate HRSA's source snapshot; bulk persistence is disabled by policy. |
| `ingest_public_catalog_seed.py` | Validate and apply one approved normalized catalog seed dataset. |
| `ingest_sam_assistance_listings.py` | Dry-run or import an operator-provided SAM Assistance Listings CSV snapshot. |
| `provision_vm_postgres.sh` | Provision the demonstration PostgreSQL service and its protected credentials. |
| `reconcile_registry.py` | Reconcile a reviewed registry manifest into the active runtime projection. |
| `reconcile_schemas_only.py` | Activate the reviewed shared and Eligibility schemas without tenant writes. |
| `reconcile_schemas_via_podman.py` | Project reviewed schemas into a Podman-hosted PostgreSQL registry only. |
| `persist_criteria_ruleset_seed.py` | Persist one reviewed criteria ruleset seed through the Data API. |
| `register_grants_gov_catalog_binding.py` | Dry-run or register the reviewed Grants.gov catalog binding. |
| `register_pattern_catalog_bindings.py` | Register reviewed tenant catalog bindings through the Persistence API. |
| `register_public_catalog_source.py` | Register source and dataset metadata without importing source records. |
| `select_eligibility_release.py` | Select the highest versioned Eligibility template and matching cluster manifest. |
| `smoke_local_mcp_stack.py` | Run a real Streamable HTTP smoke test across core and two tenant processes. |
| `smoke_mcp_process.py` | Start one synthetic MCP process for smoke_local_mcp_stack.py only. |
| `smoke_running_core.py` | Verify the running core across Eligibility and a generic cluster tenant. |
| `probe_source_primer.py` | Bounded verification probe for source-primer records (v0.2). |

---

[Documentation index](../docs/README.md)
