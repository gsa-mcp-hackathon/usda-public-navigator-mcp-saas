# Evaluation

> Version 1.0 · Updated 2026-11-21 · Reading order 6 of 7  
> [Previous: Connecting agents and clients](CONNECT.md) · [Documentation index](README.md) · [Next: How it was built](DEVELOPMENT.md)

How the platform and the Eligibility pattern are tested, what each test proves, and how to run it. Terms follow the [design vocabulary](ARCHITECTURE.md#design-vocabulary).

## What is under test

| Layer | The question | Depends on |
|---|---|---|
| **Platform** | Can an organization install a pattern and onboard a tenant through one governed MCP server, and can any AI client use it? | Nothing pattern-specific |
| **Eligibility pattern** | Does it take a person from a need to a cited application-readiness binder, and does it say what it does not know? | The platform routing correctly |

They are scored separately because they fail independently. A gap in program data cannot lower the platform score, so the platform tests contain no program content.

## Three measures that are never mixed

| Measure | Question | Changes when |
|---|---|---|
| **Capability** | Does the code path work? | Code is deployed |
| **Data coverage** | How many programs have mapped, source-cited criteria? | Mapping work runs |
| **Curation trend** | Do results improve as data is curated? | The same questions are re-run after each data revision |

A program with no mapped criteria is not a failure. The correct answer is `mapping_required`, the capability test passes by returning it, and the coverage number shows how much mapping remains.

## The test framework

One script engine, `eval/operator/run_suites.py`, runs suite files against a live Core. A suite is a JSON file with a name, an objective, start and stop conditions and a list of tests. Each test is one call (a tool or a routed operation) plus assertions on what comes back. The engine uses only the Python standard library. It treats a read-only `unavailable` response as a possible cold start and retries once. It never retries an authorization failure, an invalid request or a write, and it never prints or saves the operator token.

| Suite | What it checks |
|---|---|
| S01 | Core bootstrap and capability map |
| S02 | Operator tools fail closed without the right credential |
| S03 | Registry, routes and tenants |
| S04 | Routing and validation contract |
| S05, S11 | Persona outcomes: each fictional person is run through discovery and screening and compared with the expected result |
| S08 | Wiring and contract drift |

## The validation framework

Execution proves a call works. Validation proves the answer is right. Two kinds of reference answer are used:

- **Question set.** 23 questions each have one expected answer. Platform questions use no program content. Eligibility questions state facts and ask for the outcome. Coverage questions ask what the catalog holds.
- **Personas.** Each fictional person starts with an incomplete profile. The platform has to ask for what is missing and save it after confirmation. The end-state profile and expected programs and criterion results are compared with what the platform produced.

Platform and eligibility results are scored separately, because a gap in program data must not lower the platform score. A program with no mapped criteria is not a failure; the correct answer is `mapping_required`.

## A scenario walkthrough

A synthetic farmer in Missouri says she wants help buying equipment but has not said how long she has been farming.

1. The Core publishes its workflow, and the client follows it: discover programs first, then ask.
2. The platform returns source-backed programs with criteria status. It does not score her yet.
3. It asks for the missing fact, years farming, and saves it only after she confirms.
4. If she declines to say, the criterion stays `unknown`. The persona's expected outcome is "unknown, with a question", and the test fails if the platform returns a met or unmet result instead.
5. The suite compares the saved profile and the criterion results with the expected end state.

This is an edge case. The positive version supplies the fact and expects `met`; the negative version supplies a fact that fails the rule and expects `unmet`.

## Depth of testing, and what is not in this package

| Layer | Where | In this package |
|---|---|---|
| Unit and contract tests | `tests/`, `deploy/local/vm-data-api/tests/` | Yes |
| Machine acceptance suites S01 to S05, S08, S11 | `eval/operator/` | Yes |
| 23-question set and fictional personas | `eval/datasets/`, `eval/synthetic-datasets/` | Yes |
| Recorded runs and the verifier | `eval/results/` | Yes |
| Machine suites S06, S07, S09, S10, S12 to S14 (discovery, telemetry, general journey, search relevance, mapped criteria, end-to-end journeys) | Development repository | No |
| Phoenix evaluation harness, agent benchmark, persona generators, mapper tools | Development repository | No |

The development repository is private and is not part of this package. Those suites are development scope. This package carries the tests and results that show the platform works.

## Running it

```bash
# Unit and contract tests
uv run pytest -q tests deploy/local/vm-data-api/tests

# Machine suites against a live Core
python eval/operator/run_suites.py --token-file <operator-token-file> \
  --personas eval/synthetic-datasets/expected-outcomes.json --out-dir <output-dir>

# Check the recorded runs
python3 eval/results/verify_bundles.py
```

## What this evaluation does not claim

- Data coverage is not program-wide eligibility coverage.
- A `met` outcome is informational screening, not an agency determination.
- Load and scale are not measured.
- Test fixtures say nothing about live public-source coverage; connected-agent and public-data acceptance is a separate check.

---

[Previous: Connecting agents and clients](CONNECT.md) · [Documentation index](README.md) · [Next: How it was built](DEVELOPMENT.md)
