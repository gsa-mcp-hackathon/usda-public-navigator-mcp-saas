# Navigator test fixture

> Version 2.0 · Updated 2026-11-20 · Reference, read after Evaluation  
> [Documentation index](README.md) · [Related: Evaluation](EVALUATION.md)

## What it is

Navigator is a small web app we wrote to test the MCP-SaaS Core as a custom client. We used it as a test fixture during development and in the demonstration. It is not part of this submission and its code is not included. Nothing in this package depends on it.

## Why we built it

In the assigned agent environments we could not see what the agents were doing, and there was no scripted way to get a predictable, repeatable experience. Navigator gave us one client we controlled:

- Every run was traced in Phoenix, for every agent we tested.
- The team could review traces side by side with the agent prompts.
- Sessions could be replayed, so the demonstration was repeatable.
- The chat backend was selectable, so the same Core was exercised through different agents and models with no change to the platform. That is the provider-neutral design working as intended.

## What to look at

Judges can check that the runs and tests were done from the recorded runs and the verifier under `eval/results/`. The Core, the eligibility cluster and the evaluation evidence in this package are the submission.

## Planned extension point

Navigator picks its chat backend from a short fixed list today. The intended design is a manifest, for example `configs/navigator/backends.json`. Each entry gives an id, a kind (`api-model` or `hosted-agent`), a label, the adapter to call and the names of the environment variables it reads. Changing a backend then means editing configuration, in line with the component manifest in [COMPONENTS.md](../COMPONENTS.md). This is a design direction, not something this package ships.

---

[Documentation index](README.md) · [Related: Evaluation](EVALUATION.md)
