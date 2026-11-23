# Clusters

> Version 1.0 · Updated 2026-11-21 · Folder index  
> [Documentation index](../../docs/README.md) · [Source](../README.md) · [Eligibility pattern](../../docs/ELIGIBILITY-PATTERN.md)

A cluster is a versioned set of MCP tools for one pattern. A tenant application mounts a cluster; the Core routes to it. This folder holds the Eligibility cluster (a person finds programs and checks what they meet) and a small generic cluster that shows the Core is not eligibility-specific. Read the [Eligibility pattern](../../docs/ELIGIBILITY-PATTERN.md) first for what these modules are trying to do.

## Where the tools are defined

| Path | What it is |
|---|---|
| `eligibility_v0/__init__.py` | The Eligibility tool definitions: the 0.1 runtime line registers its tools here. The folder holds only this file because the package is imported by that name; the logic it calls is in the `eligibility_*.py` modules below |
| `sample_directory/__init__.py` | The generic directory example, a second pattern with a handful of tools |
| `registry.py` | Chooses which cluster and version a tenant registers |
| `eligibility_versions.py` | Compatibility checks for the 0.1 runtime line |

## Eligibility modules, by job

| Job | Module | What it does |
|---|---|---|
| **Find programs** | `eligibility_discovery.py` | Normalizes a fictional profile and lists source-backed candidates. It discovers; it never scores |
| | `eligibility_inventory.py` | Read-only inventory tools: search, list and read programs, and external search |
| | `eligibility_public_search.py` | Bounded search over explicitly approved public-source paths |
| | `eligibility_geography.py` | County-name matching that tolerates formatting differences |
| **Read a source** | `eligibility_source_reader.py` | Bounded read of one public page the person selected |
| | `eligibility_hrsa_source.py` | HRSA health-center locator search, transient |
| **Check a profile against rules** | `eligibility_evaluation.py` | Evaluates bounded criteria against a profile: met, unmet or unknown |
| | `eligibility_criteria_persistence.py` | Saves validated mapped criteria and evaluates the active rule set |
| | `eligibility_results.py` | Assembles program details and the evidence for each criterion |
| | `eligibility_profile_readiness.py` | Works out which facts are still missing for a program |
| | `eligibility_profile_projection.py` | Resolves shared profile fixtures into the cluster's own fields |
| **Handle a lead that is not in the catalog** | `eligibility_candidate.py` | Turns a public listing into a normalized, unpublished candidate |
| | `eligibility_candidate_evaluation.py` | Evaluates and previews requirements for a selected lead without saving it |
| | `eligibility_mapper.py` | Optional adapter that maps a source page to typed criteria. Off by default; any provider that returns the same schema can fill it |
| **Package the answer** | `eligibility_requirements.py` | Versioned requirements packages with their sources |
| | `eligibility_contracts.py` | The data models the cluster uses. Boundaries only, no behavior |
| **Load data** | `eligibility_ingest.py` | Operator-run ingest of approved program pages |
| | `grants_gov_inventory.py`, `sam_assistance_inventory.py` | Normalize the Grants.gov and SAM.gov listing snapshots |
| | `eligibility_persistence.py` | Hands the catalog to the Persistence Access API |
| **Authorization** | `content_authorization.py` | Who may change content owned by a runtime |

Tests for each module are in [`tests/`](../../tests/README.md), named after it.

---

[Documentation index](../../docs/README.md) · [Source](../README.md)
