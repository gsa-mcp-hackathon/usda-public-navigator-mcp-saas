# Tests

> Version 3.0 · Updated 2026-11-23 · Folder index  
> [Documentation index](../docs/README.md) · [Evaluation](../eval/README.md) · [Evaluation guide](../docs/EVALUATION.md)

The product-demo tests: a small set of positive, negative and edge personas for each scenario, not the internal unit suite. The set is defined in [`eval/synthetic-datasets/demo-set.json`](../eval/synthetic-datasets/demo-set.json) and each persona's expected outcome is in `expected-outcomes.json` beside it.

| File | What it checks |
|---|---|
| `test_demo_set.py` | The demo set names real synthetic personas, matches their scenario and case type, covers all nine scenarios and pairs every positive case with a negative one |
| `test_demo_starts.py` | Every demo persona has an opening question and a starter profile that holds no more facts than its expected end state |

Run it with no network and no credentials:

```bash
PYTHONPATH=src python -m pytest -q tests
```

The live run of the same personas against a deployed Core is in [the evaluation folder](../eval/README.md), and its latest numbers are in [the performance report](../eval/PERFORMANCE-REPORT.md).

---

[Documentation index](../docs/README.md)
