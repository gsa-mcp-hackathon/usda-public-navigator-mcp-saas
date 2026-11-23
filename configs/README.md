# Configuration

> Version 1.0 · Updated 2026-11-21 · Folder index  
> [Documentation index](../docs/README.md) · [Repository README](../README.md)

Versioned configuration the platform reads: registry manifests, templates, catalog seeds and source policy. Everything here is data, not code.

| Folder or file | What it is |
|---|---|
| `registry/` | The current registry manifests: the latest cluster discovery manifest (`eligibility-cluster-discovery-v42`), the latest tenant manifest (`eligibility-tenant-v24`), the schema projection (`vs8-schema-projection.v6`), the demo and runtime manifests, and the Grants.gov catalog binding. `registry/examples/` holds two small example descriptors. Superseded versions are not kept here |
| `templates/` | The pattern templates: Eligibility and the generic sample directory |
| `eligibility/` | Eligibility data: approved public sources and the search policy, catalog seeds (`catalog-seeds/`), and mapped criteria (`criteria-mappings/`) |
| `evaluation-fixture-sets.v1.json` | The named synthetic fixture sets the evaluation reads |
| `stack.example.json` | An example stack manifest for the [naming standard](../docs/NAMING-STANDARD.md) |

The files carry their version in the name; a newer version replaces the older one rather than sitting beside it, and the history stays in Git. Where the data comes from and how it is governed: [Data and sources](../docs/DATA-AND-SOURCES.md).

---

[Documentation index](../docs/README.md)
