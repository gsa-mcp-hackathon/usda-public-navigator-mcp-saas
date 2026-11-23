# Enhancements: planned, not yet implemented

> Version 1.0 · Updated 2026-11-21 · Reference, read after How it was built  
> [Documentation index](../README.md) · [Related: How it was built](../DEVELOPMENT.md) · [Provider neutrality](../PROVIDER-NEUTRALITY.md)

This folder holds work that was designed during the hackathon but not built into the
running system. Each item is written down so a team with more time, a free choice of
environment and more resources can pick it up. Nothing here is imported by the
platform, and nothing here changes how it runs today.

## Why these are not in the submission

The build ran against a fixed deadline, in an environment assigned by the event, with
a small team. Three constraints decided what shipped:

- **Time.** The core pattern (discover, map, evaluate, prepare the application) had to
  work end to end and be tested before anything else.
- **Environment.** The assigned environment, and the plan to move off it afterwards,
  made some provider-level changes risky to make late.
- **Resources.** Review and testing capacity was limited, so working, tested code was
  not reopened for design improvements.

At scale these items are the path forward. They are listed in the order a team would
likely take them.

## Planned enhancements

| Item | What it adds | Status | Document |
|---|---|---|---|
| Native persistence | Replace the Persistence Access API (an environment compromise) with native database services and direct, role-scoped connections, so persistence is data-agnostic as designed. | Not started; outside the build window | [ARCHITECTURE.md](../ARCHITECTURE.md) |
| Mapper provider pointer | The model that drafts criteria is chosen by one pointer in a config file, with no code change. A provider can be a hosted model, a local model, an agent platform, or none (the calling AI maps and writes back). | Designed, sketch included | [MAPPER-PROVIDER.md](MAPPER-PROVIDER.md) |
| Standalone mapping verifier | A read-only operation that re-checks every excerpt and number in a mapping before it is scored. | Specified in [Criteria mapping](../eligibility-reference/CRITERIA-MAPPING.md) | Spec only |
| Published client mapping workflow | The Core publishes the mapping instructions and output schema so every client gets the same text. | Specified in [Criteria mapping](../eligibility-reference/CRITERIA-MAPPING.md) | Spec only |

## Known items not fixed

These were found during review and testing and were not fixed before submission. They
sit in the platform layer, not in the agent instructions, so they need a Core change
and a redeploy. They are listed so evaluators see the limits as the team found them.

| Area | Item | Effect |
|---|---|---|
| Jurisdiction | Handling for one housing-assistance program's jurisdiction | A program can be matched to the wrong jurisdiction |
| Normalization | One source scenario returns a server error on the normalize step | That source needs mapping by the client |
| Observability | Trace correlation between the evaluation harness and the server | Harder to follow one request across both |
| Access | Scope of profile reads | Reads return more than the least-privilege design intends |

Confirm each row against the issue tracker before relying on it.

---

[Documentation index](../README.md) · [Related: How it was built](../DEVELOPMENT.md) · [Provider neutrality](../PROVIDER-NEUTRALITY.md)
