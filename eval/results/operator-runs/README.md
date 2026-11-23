# Operator suite runs

> Version 1.0 · Updated 2026-11-21 · Guide
> [Documentation index](../../../docs/README.md)

Full operator-suite runs against the live Core, kept as the three files each run produces so a reviewer can check them without our systems.

## What is here
| File | Contents |
|---|---|
| `OPS-20261121-1113-full-Input.json` | Every request the run sent, tagged by test. |
| `OPS-20261121-1113-full-Response.json` | The Core's responses and each test's result. |
| `OPS-20261121-1113-full-Trace.json` | Correlation IDs, package IDs and the ordered call list, for the Phoenix join. |
| [`OPS-20261121-1113-full-Summary.md`](OPS-20261121-1113-full-Summary.md) | The run summary: 52 pass, 2 skipped, 0 failed, template 0.1.48. |

Read the summary first, then the trace file to follow a call to its Phoenix span by `mcp.correlation.id`.

[Documentation index](../../../docs/README.md) · [Evaluation results](../README.md)
