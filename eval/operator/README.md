# Machine suites and the script engine

> Version 2.1 · Updated 2026-11-21 · Guide  
> [Documentation index](../../docs/README.md) · [Evaluation evidence](../EVIDENCE-SUMMARY.md)

`run_suites.py` runs the suite files in `suites/` against a live Core, read-only, using only the Python standard library. The suites included here are S01 to S05, S08 and S11. What each checks is in [the evaluation method](../../docs/EVALUATION.md#the-test-framework).

```bash
python eval/operator/run_suites.py --token-file <operator-token-file> \
  --personas eval/synthetic-datasets/expected-outcomes.json --out-dir <output-dir>
```

Each run writes three JSON files next to `results.json`, named from `--prefix` (default `OPS-<run id>`; for a scenario run use the convention `UC#-S#-E2E-Run#-<Positive|Negative|Edge>`): `-Input.json` (what was asked), `-Response.json` (what came back, one record per test) and `-Trace.json` (every call with its correlation ID and package IDs; `trace_id` and `span_id` stay empty until the Phoenix pull fills them by correlation ID).


## Run options

| Option | What it does |
| --- | --- |
| `--mode fast` | Skips the persona suites and runs the sub-second read suites (S01 to S04, S08). Use it as a quick pre-check. |
| `--mode full` | Default. Runs every suite. |
| `--profile client-only` | For a stack that runs with operator tools disabled. S01-T01 expects the client tools present and the operator tools absent; operator-only suites and tests, and the suites that need configured routes and tenants (S03, S04, S05, S08, S11, unless picked with `--suite`), are reported `NOT_APPLICABLE`, not skipped silently. The expectations are declared in [`suites/client-only.profile.json`](suites/client-only.profile.json), not in code: the required and absent tools, the workflow checks, and the reason each not-applicable test or suite gives. Another phase is one more `suites/<name>.profile.json`. Default `full` is unchanged. |
| `--workers N` | Runs persona tests in N threads, each with its own client. Default 1 (serial). |

Measured on 2026-11-21 against the IBM Core, same results in every mode (52 pass, 2 skip): a full run takes about 215 s serial, 150 s with `--workers 4`, and 44 s in fast mode. Under four workers each persona call slows from about 11 s to 26 s, so the Core scales sub-linearly (about 1.4 times faster in total, not 4 times). Fast mode is the larger saving, and the full run is what gives the evidence, so run `fast` before a batch and `full` for the record.

## Phoenix preflight

`phoenix_preflight.py` runs four checks before a batch and writes a small sanitized JSON file for `--provenance-file`: Phoenix reachable, the traces project present, a span found by correlation ID, and annotation writing (only with `--write-probe`). It reads the key from `PHOENIX_API_KEY` and records no host name or key.

```bash
PHOENIX_API_KEY=<key> python eval/operator/phoenix_preflight.py \
  --base-url <phoenix-url> --project usda-core-native --out phoenix-preflight.json
python eval/operator/run_suites.py ... --provenance-file phoenix-preflight.json
```

Public suites need no credential. S02 needs the operator token. Recorded runs are in [`../results/`](../results/README.md).
