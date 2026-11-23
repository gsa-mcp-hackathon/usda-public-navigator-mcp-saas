# Performance report

> Version 1.3 · Updated 2026-11-23 · Report · Read 3 of 3 in this folder  
> [Previous: Evidence summary](EVIDENCE-SUMMARY.md) · [Documentation index](../docs/README.md) · [Next: Evaluation folder index](README.md)

How fast the platform answered in the recorded runs, in plain terms. The [evaluation document](EVALUATION-DOCUMENT.md#32-response-times) gives the short version; this report holds the tables. Numbers come from the recorded files named below, measured from the test client, so they include the network round trip. They are a snapshot of one day on one deployment, not a service-level promise.

## The short version
- **Simple reads are fast.** Listing routes, templates, tenants and schemas answered in about a third of a second to under a second at the median.
- **Evaluating a profile takes a few seconds.** Each eligibility call (discover, evaluate, save, read back) took about five seconds at the median and never more than about seven.
- **The result held across reruns.** Four full operator runs on 2026-11-21 gave the same result each time (52 pass, 2 skipped). A fast pre-check takes 44 seconds, a full run about 3.5 minutes one test at a time, and about 2.5 minutes with four workers.
- **No call failed on timing.** All 84 calls in the full operator run completed; the one slow case earlier in the day needed a second attempt and is covered by the retry setting.
- **The demo personas ran end to end on 2026-11-23.** 22 synthetic personas (the 13 in the demo set plus 9 more) made 214 recorded steps against the live service: 202 passed, 11 were flagged and 1 is pending. A median step took about 7 s and the slowest 26 s. Details are in [the 2026-11-23 persona run](#the-2026-11-23-persona-run).

## What was measured
| Run | File | Calls | Result |
|---|---|---|---|
| Full operator suites S01 to S11 with persona checks | [OPS-20261121-1113-full](results/operator-runs/README.md) | 84 | 52 pass, 2 skipped, 0 failed |
| Same full suite, three reruns later the same day (one with four parallel workers) | [run settings in the operator README](operator/README.md) | 84 each | 52 pass, 2 skipped each, 0 failed |
| Fast pre-check (read suites only, no persona tests) | [run settings in the operator README](operator/README.md) | 52 | 36 pass, 2 skipped, 0 failed |
| 23-question run through the Navigator test client (direct to the Core, no model calls) | [DS-R20261121T162618Z-direct-mcp](results/replay-bundles/DS-R20261121T162618Z-direct-mcp.json) | 22 | 19 of 23 questions match; the 4 misses are listed in the evidence summary |

## The 2026-11-23 persona run
Run through the client endpoint only (no operator tools) against the live service, after the binder and data changes of 2026-11-22 and 2026-11-23. Each persona goes through the whole path: search, discover questions, save profile, score, binder preview, binder write and read back.

| Measure | Result |
|---|---|
| Personas run | 22 synthetic personas across all nine scenarios (UC1-S1 to UC3-S3), including the 13 in the [demo set](synthetic-datasets/demo-set.json) |
| Steps recorded | 214: 202 pass, 7 data, 4 oracle mismatch, 1 pending |
| Binders written and read back | 18, each verified on read back; 17 judged complete, 1 flagged for missing next-step guidance (UC1-S2 drought) |
| Personas that correctly got no program | 4: the Kansas, Maryland and New Mexico households (out of state) and the Kansas caregiver. They previously got a Missouri SNAP binder; they no longer do |

| Kind of step | Steps | Median | Slowest |
|---|---:|---:|---:|
| Profile writes and reads (database) | 39 | 0.8 s | 1.4 s |
| Binder read back and quality check | 36 | 1.0 s | 1.2 s |
| Discover questions | 18 | 8.2 s | 12.2 s |
| Score | 18 | 9.4 s | 15.7 s |
| Search | 23 | 10.3 s | 26.0 s |
| Binder preview | 18 | 9.2 s | 15.3 s |
| Binder write | 18 | 7.7 s | 15.6 s |
| All timed steps together | 170 | 6.9 s | 26.0 s |

Eight of the 22 personas have at least one flagged step; none is a call that failed. Five are "discover questions" steps marked for data work (the rules for that case are not mapped yet: the VA pension, two farm boundary cases, drought and Maryland heating). Four are scores that differ from the expected outcome (the personal-data offer, a missing fact, the working renter and Maryland heating), and one drought score is still pending. The drought binder is the one binder flagged for missing next-step guidance. Five personas lost their connection mid-run and were rerun one at a time, which gave the same kind of result.

## Response time by kind of call (full operator run, 2026-11-21)
| Kind of call | Calls | Median | Slowest |
|---|---:|---:|---:|
| Eligibility operations through the router (discover, evaluate, write, read) | 37 | 5.4 s | 7.3 s |
| Capability and template listing | 12 | 0.7 s | 2.1 s |
| Tenant, route, schema and source reads | 35 | 0.3 s | 0.6 s |
| All calls together | 84 | 0.7 s | 7.3 s |

The whole run took about 3.5 minutes of call time end to end. The 23-question run had a median of 5.1 s per call and a slowest of 6.7 s.

## Run time by mode
| Mode | Core calls | Wall time | Result |
|---|---:|---:|---|
| Full, one test at a time | 84 | about 3.5 min (208 s of call time) | 52 pass, 2 skipped |
| Full, four parallel workers | 84 | 150 s | 52 pass, 2 skipped |
| Fast pre-check (no persona tests) | 52 | 44 s | 36 pass, 2 skipped |

Four workers finished the run about 1.4 times sooner, not four times. The Core slows under parallel load: the eligibility router calls went from a 5.4 s median (7.3 s slowest) to a 12.7 s median (14.7 s slowest), while the lookup calls stayed about where they were (0.3 s to 0.6 s). So parallel runs save time, with a cost per call, and the fast mode is the larger saving when a quick check is all that is needed. Use fast before a batch and the full run for the record.

## Agent runs (Phoenix, 23 November)
The same 23 questions were also answered by two AI agents using the platform's tools, with every call traced in Phoenix. Median time per question was 4.0 s (Claude Sonnet 5.5) and 2.4 s (GPT-4.1) on the ten platform questions, 24.7 s and 13.5 s on the ten eligibility questions, and 17.6 s and 14.9 s on the three data-coverage questions. These include the model's own thinking time, so they are not comparable with the direct-call numbers above. Claude used 1.80M input tokens in total and GPT-4.1 0.72M; most of it is the eligibility set, where search results are large. Details: [results/phoenix-agent-eval](results/phoenix-agent-eval/README.md).

## Reading these numbers
- The router calls are slower because each one reads the program rules and the profile, evaluates them and returns the cited sources in one step. The reads that only look something up are fast.
- Persona checks send a full profile to the Core, so a persona test takes roughly ten seconds for its discover and evaluate pair.
- Timing here is from a laptop over the public internet. Measured on the host itself it would be lower.

## Limits
- The 2026-11-23 persona run used four parallel workers for most of it, so its search, score and binder times include parallel load. Five personas lost their connection mid-run and were rerun one at a time. Times are from a laptop over the public internet.
- The persona step counts cover the steps recorded by the persona runner, not the operator suites; the two sets of numbers are not added together.
- One day, one deployment, synthetic profiles. The four-worker run is a small parallel test, not a load test, so these figures say little about many users at once.
- The Navigator test client leaves the Phoenix trace ID empty when it captures a call; the IDs were joined afterwards by correlation ID, which is why 14 of 14 correlated calls show a trace.
- Server-side time per span is in Phoenix (project `usda-core-native`); this report uses client-side time only.

[Previous: Evidence summary](EVIDENCE-SUMMARY.md) · [Documentation index](../docs/README.md) · [Next: Evaluation folder index](README.md)
