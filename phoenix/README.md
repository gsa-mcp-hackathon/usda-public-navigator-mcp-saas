# Phoenix folder

> Version 1.0 · Updated 2026-11-22 · Folder index  
> [Documentation index](../docs/README.md) · [Agent instructions](../agents/README.md)

Optional. Phoenix is the trace viewer used to see which tool calls reached the Core and, if you choose, to replay a chat. Nothing here is needed to run the platform, answer questions or use the agents. Read it only if your deployment runs Phoenix.

## What is here

| Read | File | What it is |
|---|---|---|
| 1 | [SERVER-SETUP-GUIDE.md](SERVER-SETUP-GUIDE.md) | Stand up a Phoenix server and point a Core like this one at it, so calls from internal and external agents are traced |
| 2 | [AGENT-CONFIG-SPEC.md](AGENT-CONFIG-SPEC.md) | The settings, span fields and naming each side must use, and how to match a Core span to an agent's record |

The agent-side recording block lives with the other knowledge files: [K7-PHOENIX-RECORDING.md](../agents/knowledge/K7-PHOENIX-RECORDING.md). Read the guide first, then the spec, then add the block.

## Two kinds of record

| Record | Written by | Project | Shows |
|---|---|---|---|
| Core call span (`mcp.tool.call`) | The Core, automatically | the Core project | Operation, outcome, duration, correlation ID, declared client name |
| Chat turn span | The agent, using the K7 block | a separate agent project | The question, the answer and the Core calls in one view |

---

[Documentation index](../docs/README.md) · [Agent instructions](../agents/README.md)
