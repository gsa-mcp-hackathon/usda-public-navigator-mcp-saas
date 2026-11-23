# Evaluation evidence

> Version 2.1 · Updated 2026-11-23 · Guide · Read 2 of 3 in this folder  
> [Previous: Evaluation document](EVALUATION-DOCUMENT.md) · [Documentation index](../docs/README.md) · [Next: Performance report](PERFORMANCE-REPORT.md)

Read the [evaluation document](EVALUATION-DOCUMENT.md) first: it explains the tests and gives the results by claim. This file is the index to the evidence behind it. The method is in [the evaluation guide](../docs/EVALUATION.md).

This folder holds what proves the platform works: a small set of tests, the data they use, the script that runs them, and the recorded results. It does not hold every test written while building it. The development repository keeps the full suites (see the last section).

## What is here

| Path | What it is |
|---|---|
| [`datasets/`](datasets/README.md) | The 23 questions with expected answers: platform capability (10), eligibility pattern (10) and data coverage (3). |
| [`synthetic-datasets/`](synthetic-datasets/README.md) | 23 fictional people with starter profiles and expected outcomes: 18 edge, 3 positive, 2 negative. Every value is made up. |
| [`operator/run_suites.py`](operator/README.md) | The script engine. It runs the suite files against a live Core using only the Python standard library. |
| [`operator/suites/`](operator/suites/) | Suites S01 to S05, S08 and S11 (see below). |
| [`results/`](results/README.md) | The recorded runs, with a verifier. Start with the results guide. The final Phoenix agent evaluation is in [`results/phoenix-agent-eval/`](results/phoenix-agent-eval/README.md). |
| [`INSTRUCTIONS-USED.md`](INSTRUCTIONS-USED.md) | The agent instructions used on each platform. |
| [`MAPPING-PROMPT.md`](MAPPING-PROMPT.md) | The instructions that map a program's cited requirements to typed criteria. |

## The tests, by type

| Type | What it proves | Examples |
|---|---|---|
| **Positive** | The platform answers correctly when asked correctly. | Capability map, route and tenant lookups, a matched program with criteria met. |
| **Negative** | It refuses or says no when it should. | An unknown route returns a clean refusal; operator tools reject a missing or wrong credential. |
| **Edge** | It does not guess when information is missing. | A person who has not said whether they qualify gets "unknown" and a question, not a result. |

## Recorded results

Run `python3 eval/results/verify_bundles.py`. It reads the saved runs and prints cases, calls, correlation IDs and trace joins with a short hash, with no network and no keys.

| Run | Method | Cases | Result |
|---|---|---|---|
| 23 questions, direct MCP, 2026-11-17 | Scripted calls to the Core, no model | 23 | 20 pass, 3 fail. N11 and N17 failed on naming only (the Core returned the official title with its acronym). N16 had no answer because the test client sent a malformed request (`invalid_request`); the Core itself answers correctly, see eval/datasets/README.md |
| 23 questions, direct MCP rerun, 2026-11-21 | Navigator scripted endpoint, no model | 23 | 19 pass, 4 fail: N11 and N17 (naming only), N16 (test client made no call; the Core answers `not_applicable` when asked directly), N09 (expected 0.1.47, live Core reports 0.1.48). Phoenix join: the Navigator's capture-time join found none, but a later join by correlation ID matched all 10 distinct IDs to native Core spans, and the results are saved on them as span annotations |
| 23 questions, direct-call check, 2026-11-23 | Free check of the live Core, no model | 23 | 23 pass (final) |
| Operator suite, 2026-11-17 | Recorded in Phoenix | 20 calls | 20 traced |
| Guided demo, 2026-11-17 | Recorded in Phoenix | 10 calls | 10 traced |
| Scenario coverage, 2026-11-17 | Agent-assisted (ChatGPT/Codex assistant) | 13 scenarios, 61 calls | 9 partial, 4 not scored; trace join not captured |
| Phoenix agent evaluation, 2026-11-23 (final) | Claude Sonnet 5.5 and GPT-4.1 as agents, cross-provider judges, every question traced in Phoenix ([results](results/phoenix-agent-eval/README.md)) | 23 questions per agent | Claude 23 of 23 exact; GPT-4.1 17 of 23 (4 of 10 on the eligibility set, same as 19 Nov). 3 Nov and 19 Nov runs are compared in the results guide |
| IBM watsonx Orchestrate agent, 2026-11-20 | Agent run ([`results/agent-runs/`](results/agent-runs/)) | Scenario S1 | Partial: 21 of 44 tools exposed, which blocked other scenarios |

## Platforms

The platform was exercised from several clients. The recorded evidence is what is listed above: direct MCP, Phoenix-recorded sessions, a ChatGPT/Codex assistant run and the IBM agent run. Claude and GPT-4.1 were also run as agents on the 23 questions in Phoenix (see the 2026-11-23 row). Microsoft Copilot Studio was used during development; this package holds no saved run for it.

## Limits

- Results are dated. Check the date before relying on one.
- A `met` result is informational screening, not an agency determination.
- Load and scale are not measured.

## The full suites

The development team's repository keeps the complete automated suites (S01 to S14, including the end-to-end journey suite), the Phoenix evaluation harness, the agent benchmark, the persona generators and the mapper tools. That repository is private and is not part of this package. They are build and development scope, and the platform does not need them.

---

[Previous: Evaluation document](EVALUATION-DOCUMENT.md) · [Documentation index](../docs/README.md) · [Next: Performance report](PERFORMANCE-REPORT.md)
