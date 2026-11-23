# Provider neutrality

> Version 1.0 · Updated 2026-11-21 · Reference, read after Data and sources  
> [Documentation index](README.md) · [Related: Data and sources](DATA-AND-SOURCES.md) · [Criteria mapping](eligibility-reference/CRITERIA-MAPPING.md)

The platform uses a language model for one task: mapping a public program source
page into structured eligibility criteria. The model drafts criteria; it does not
decide eligibility. Output must match a fixed schema and is validated, and
eligibility is then evaluated by deterministic code. Any model that can produce the
schema can fill this role: a hosted LLM, a local model, an agent platform, or the AI
agent that is calling the server.

In the hackathon environment this step ran on the assigned platform, IBM watsonx
Orchestrate. It is one replaceable component (see [COMPONENTS.md](../COMPONENTS.md)).
The first mapping run in this project was done by an interactive AI session
following [`eval/MAPPING-PROMPT.md`](../eval/MAPPING-PROMPT.md), which shows the step is not tied to one
provider.

## The toggle

`MCP_LLM_MAPPER_ENABLED` (default `true`):

- `true`: mapping is routed to the LLM mapper component
  (`src/clusters/eligibility_mapper.py`) when the module is present.
- `false`: the mapper tool is not registered. A source with no stored criteria is
  returned as "needs mapping", and the calling AI agent maps it through the client
  mapping workflow, then evaluation proceeds as normal.

Removing the mapper module has the same effect as `false`. The test suite passes
with the module removed; tests that exercise the mapper skip.

## Choosing an LLM

The mapping step takes any LLM. Two options, both with the evaluator's own account and
key:

- set `MCP_LLM_MAPPER_ENABLED=false` and let the AI agent that is calling the server
  (any model) map sources through the client mapping workflow; or
- point the mapper module at an LLM of your choice. It is the single place to add one.

The included adapter is a reference implementation built for the event-assigned
environment (see [COMPONENTS.md](../COMPONENTS.md)). It is one example, not a
requirement.

## When mapping runs

Ingestion, normalization, requirements packaging and criteria persistence are
deterministic code with no model call. The model enters only at the mapping step,
which can be run offline before or after catalog load (reviewed criteria stored as
versioned rulesets) or on demand for a source with no stored criteria.

---

[Documentation index](README.md) · [Related: Data and sources](DATA-AND-SOURCES.md) · [Criteria mapping](eligibility-reference/CRITERIA-MAPPING.md)
