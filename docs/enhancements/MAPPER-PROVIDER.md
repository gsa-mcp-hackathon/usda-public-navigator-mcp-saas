# Mapper provider pointer

> Version 1.0 · Updated 2026-11-21 · Reference  
> [Documentation index](../README.md)

## The idea

The platform uses a language model for one job: drafting eligibility criteria from a
public source page. The draft is validated against a fixed schema and scored by
deterministic code. Because the model does only that job, it should be swappable the
way the model in a chat session is swappable: change a pointer, nothing else.

The pointer lives in a declared config file, in the same way the deployed stack is
declared in `configs/stack.example.json`. Providers are entries in the file.
Secrets are never in the file; each entry names the environment variable that holds
its endpoint or key.

## Today

- `MCP_LLM_MAPPER_ENABLED` turns the mapper on or off.
- One adapter exists, for the assigned event environment (IBM watsonx Orchestrate),
  with IBM-named settings in `src/clusters/eligibility_mapper.py`.
- With the mapper off, the calling AI agent maps through the client workflow and
  persists the result with `program.criteria.persist` and its readback receipt.

## Planned

One file, `configs/mapper.json`, with an `active` pointer:

```json
{
  "active": "none",
  "providers": {
    "none": { "type": "none" },
    "event-platform": {
      "type": "ibm_orchestrate",
      "endpoint_env": "MAPPER_ENDPOINT",
      "key_env": "MAPPER_KEY"
    },
    "hosted-llm": {
      "type": "openai_compatible",
      "endpoint_env": "MAPPER_ENDPOINT",
      "key_env": "MAPPER_KEY",
      "model": "set-per-deployment"
    }
  }
}
```

See [mapper.example.json](mapper.example.json) and the interface sketch in
[mapper_provider_sketch.py](mapper_provider_sketch.py).

The same pointer serves all three ways mapping happens:

1. **Before catalog load.** A batch run maps sources with the active provider. Reviewed
   criteria are stored as versioned rule sets and loaded with the catalog.
2. **Background service.** The same provider runs over the catalog on a schedule, so
   most programs already have criteria when a person arrives.
3. **At runtime, no provider (`active: none`).** The source is returned as "needs
   mapping". The calling AI agent maps it and writes it back through
   `program.criteria.persist`. The server checks the readback before scoring.

## Rules the change must keep

- Default behavior unchanged: `none` until an operator points at a provider.
- Output validated against the same schema and citation checks for every provider.
- A provider never decides eligibility; it only drafts criteria.
- The existing adapter becomes one entry and is not rewritten.

## Done when

- Changing `active` switches the provider with no code change.
- A stub provider test proves the swap.
- The full existing test suite and the load and smoke checks pass unchanged.

---

[Documentation index](../README.md)
