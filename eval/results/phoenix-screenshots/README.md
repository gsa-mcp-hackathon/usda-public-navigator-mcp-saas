# Phoenix screenshots

> Version 1.0 · Updated 2026-11-21 · Folder index  
> [Documentation index](../../../docs/README.md)

Screenshots of the Phoenix trace viewer taken on 2026-11-21 after the full operator run `OPS-20261121-1113-full`, for the deck and for reviewers who do not have Phoenix access. Each shows what the recorded files in [operator-runs](../operator-runs/README.md) describe.

| Read | What it shows |
|---|---|
| `01-phoenix-projects-overview.jpg` | The Phoenix projects, with trace counts and median latency for the Core project `usda-core-native` and the other test projects |
| `02-usda-core-native-spans-and-annotations.jpg` | The Core project's spans over seven days, with the spike from today's runs, and the operator-suite result annotation in the side panel |
| `03-trace-S05-P01-with-correlation-id-and-annotation.jpg` | One persona test's trace: the `mcp.tool.call` span, its 6.3 s latency, its `mcp.correlation.id` and the PASS annotation added from the run results |

Read 01, then 02, then 03: project, then spans, then one trace. The correlation ID on the span is the same one saved in the run's Trace file, which is how a recorded call is matched to its trace.

[Documentation index](../../../docs/README.md) · [Evaluation results](../README.md)
