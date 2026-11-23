# Submission Seed

> Version 1.0 · Updated 2026-11-21 · Guide  
> [Documentation index](../../docs/README.md)

This is a compact, current-state public-data seed for reproducing the catalog and criteria mapping paths after applying the repository migrations. It is a data starting point, not a replacement for the evaluation suites or their results.

## Contents

| Table | Rows |
|---|---:|
| public_data_sources | 25 |
| public_datasets | 39 |
| public_dataset_records (active projection) | 87 |
| eligibility_criteria_rulesets (current heads only) | 20 |
| eligibility_criteria_ruleset_heads | 20 |
| Total | 191 |

Current state only, no history, no tenant/route/profile/audit data. The seed omits inactive dataset records and prior criteria versions/events. It also omits the transitional tenant-scoped program_catalog projection.

The source contact found in one active record was kept because the agency/program contact address is published on its own public .org source page. The record identifier, domain, and disposition are recorded in eval/results/seed-snapshot-REPORT.md; the address itself is not repeated there. The USDA and IBM metadata markers are retained as requested.

## Load

Apply all SQL migrations in db/migrations in filename order, then run this file once against the new database. The statements are transactional and can be re-applied: mutable source/dataset metadata and current criteria pointers upsert; immutable records and rulesets insert only when absent.

## Integrity

SHA-256: dbbed585d0aa78a609dbfdca8925a6cf3fc9208002149e770d2d78c4c0c6df11

---

[Documentation index](../../docs/README.md)
