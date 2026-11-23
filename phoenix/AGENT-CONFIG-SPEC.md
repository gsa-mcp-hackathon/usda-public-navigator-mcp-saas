# Agent configuration spec for Phoenix

> Version 1.0 · Updated 2026-11-22 · Optional spec, 2 of 2 in this folder  
> [Folder index](README.md) · [Previous: Server setup guide](SERVER-SETUP-GUIDE.md)

What each side sets so a tool call from an internal or external agent can be found in Phoenix and matched to the agent's own record. Synthetic data only.

## 1. Settings

| Side | Setting | Value | Notes |
|---|---|---|---|
| Core | `PHOENIX_ENDPOINT` | `https://<host>/v1/traces` | HTTPS, path exactly `/v1/traces`, no credentials or query. Unset means no tracing. |
| Core | Phoenix project | `usda-core-native` | Set in `src/example_server/core_tracing.py`. |
| Core | `service.name` | `mcp-core` | OpenTelemetry resource attribute. |
| Core | OTLP key | `OTEL_EXPORTER_OTLP_HEADERS` | Only when Phoenix authentication is on. From a secret store, never in Git. |
| Agent | Client identity | the software name sent when the client connects over MCP | Recorded as `mcp.client.name`. Declared by the client, not authenticated. |
| Agent | `PROFILE` line | one per pasted instruction file | Sets the client id used in the in-chat Trace details block. |
| Agent | Recording project | a project of your choosing, kept apart from the Core's | Only if you use the [K7 recording block](../agents/knowledge/K7-PHOENIX-RECORDING.md). |
| Agent | Tester ID | a short code, not a name | Written to `tester.id`. |

## 2. Core call span

One span named `mcp.tool.call` per tool call, written by the Core. It never carries prompts, tool arguments or results, or profile data.

| Attribute | Meaning |
|---|---|
| `mcp.tool.name` | The Core MCP tool that ran |
| `mcp.operation.id` | The route operation, for example `program.inventory.search` |
| `mcp.route.id`, `mcp.route.version` | The route and its version |
| `mcp.route.outcome`, `mcp.route.error_class` | Outcome class, and error class when it failed |
| `mcp.route.duration_ms` | Time spent in the route |
| `mcp.result.status` | `ok` or `error` |
| `mcp.request.id`, `mcp.request.source` | Opaque per-call ID, and whether the client or server made it |
| `mcp.correlation.id` | The correlation ID also shown in the agent's Trace details table |
| `mcp.client.name`, `mcp.client.version` | Software name and version the client declared |
| `mcp.client.identity.status`, `mcp.client.identity.provenance` | `client_declared`, with its source, or `unavailable` |
| `mcp.session.sha256` | Shortened hash used only to group calls of one session |
| `mcp.provider.usage.*` | Provider usage when measured; otherwise status `unavailable` |

## 3. Agent chat span (optional)

Written by the agent with the K7 block. A conversation is one `trace_id`; each turn adds spans to it.

| Span | Name | Required attributes |
|---|---|---|
| Turn | `gpt-chat-turn` or `claude-chat-turn` (kind `AGENT`) | `input.value`, `output.value` (each at most 1,500 characters), `navigator.thread.id`, `navigator.client`, `tester.id` |
| Core call | `tool <operation id>` (kind `TOOL`, child of the turn) | `tool.name`, `mcp.result.status`, and `mcp.correlation.id` when the Core response shows one |

The agent masks real names, SSNs, addresses, emails and phone numbers before recording. Use synthetic data regardless.

## 4. Matching a Core span to an agent record

1. The agent's Trace details table shows a Correlation ID for each Core call.
2. Search the Core project for a span whose `mcp.correlation.id` equals it. That is the Core's side of the same call.
3. If the agent used the recording block, its `tool <operation>` span carries the same ID, so the chat and the Core call join up.
4. With no ID, match by time (within about two minutes), `mcp.operation.id` and `mcp.client.name`.
5. A call with no Core span means the Core that answered is not exporting to this Phoenix. See the [setup guide](SERVER-SETUP-GUIDE.md), section 6.

## 5. Limits

- Spans confirm activity at the Core. They do not show a hosted model's reasoning, prompts or token use.
- `mcp.client.name` is what the client said about itself. It does not prove which person or agent made the call.
- The recording block depends on a Phoenix connection that allows `createSpans`, which this project has not yet verified live.

---

[Folder index](README.md) · [Previous: Server setup guide](SERVER-SETUP-GUIDE.md)
