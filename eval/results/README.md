# Evaluation runs and traces

> Version 1.0 · Updated 2026-11-21 · Guide  
> [Documentation index](../../docs/README.md)

Recorded test runs, kept as JSON so a reviewer can check them without our systems. The summary is in [the evaluation evidence page](../EVIDENCE-SUMMARY.md).

## Tested across platforms
The platform was tested broadly, on purpose. We used the tools and environments we were given and more than one provider: IBM watsonx Orchestrate agents (the assigned environment), Claude, ChatGPT, Codex, Microsoft Copilot Studio, and our own test client (see [docs/NAVIGATOR.md](../../docs/NAVIGATOR.md)), plus automated suites and human checks. Using these platforms to test is how we know the Core works from each of them. It says nothing about who owns what.

## What is here
| Folder | Contents |
|---|---|
| `replay-bundles/` | Eight recorded runs in one schema: MCP calls, correlation IDs and, where joined, the Phoenix trace ID. |
| `agent-runs/` | IBM agent S1 run and results, operator connection results, and the findings from the same run, including what blocked other scenarios. |
| `operator-runs/` | Full operator-suite runs on the live Core in three files each (input, response, trace) plus a summary. |
| `phoenix-screenshots/` | Screenshots of the Phoenix projects, spans and one annotated trace from the full operator run. |
| `navigator-chat-runs/` | Scripted chats with the Navigator assistant on the hosted test page: input, response and trace files per scenario. |
| `verify_bundles.py` | Read-only check: counts cases, calls, correlation IDs and joined trace IDs per bundle, with a short hash. No network, no keys. |

Run `python3 eval/results/verify_bundles.py` to see the join rate for every bundle. Unmatched calls are reported as they are, not filled in.

## How runs link
Each MCP call carries a correlation ID. Phoenix spans carry the same value as `mcp.correlation.id`, so a call and its trace are joined by that ID. Runs also record the synthetic profile reference and package ID, so saved packages can be listed and read back through the Core.

## Known limits
- Some recorded calls were captured before their Phoenix spans were matched, so their trace join is empty.
- One IBM run exposed 21 of 44 expected tools, which blocked several scenarios. The findings file says which.
- Saved-binder readback is shown only for runs that wrote a package. Preview-only runs are labeled as such.

---

[Documentation index](../../docs/README.md)
