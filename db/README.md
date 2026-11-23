# Database

> Version 1.0 · Updated 2026-11-21 · Folder index  
> [Documentation index](../docs/README.md) · [Persistence Access API](../deploy/local/vm-data-api/README.md)

The schema and the seed data for the persistence layer.

| Folder | What it is |
|---|---|
| `migrations/` | The database schema, one numbered SQL file per change, 001 to 047. Apply them in numeric order and only add to them |
| `seeds/` | Seed data: the demonstration seed, the public datasets, and the dated submission seed with its [README](seeds/README-submission-seed.md) |

A diagram of the persistence tables is linked from the [documentation index](../docs/README.md). The service that reads and writes these tables is the [Persistence Access API](../deploy/local/vm-data-api/PERSISTENCE-ACCESS-API.md).

---

[Documentation index](../docs/README.md)
