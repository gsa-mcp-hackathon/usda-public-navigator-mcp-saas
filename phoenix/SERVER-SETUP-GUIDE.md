# Phoenix server setup guide

> Version 1.0 · Updated 2026-11-22 · Optional guide, 1 of 2 in this folder  
> [Folder index](README.md) · [Next: Agent configuration spec](AGENT-CONFIG-SPEC.md)

A short path to run a Phoenix server, connect a Core MCP server like this one so its tool calls are traced, and let internal and external agents be inspected. Use made-up or synthetic data only. Replace every `example` host below with your own.

## 1. What you are building

```
agents (internal or external)  --MCP-->  Core MCP server  --OTLP/HTTPS-->  Phoenix  <--MCP--  reviewer's chat client
```

- The Core exports one span per tool call to Phoenix. Agents do not export anything themselves: a hosted chat app (ChatGPT, Claude, Copilot) cannot send traces. The Core sees the calls from internal and external agents alike, which is why one export covers both.
- Phoenix also offers its own MCP endpoint for reading and, if you allow it, writing spans. That is how the optional [chat recording block](../agents/knowledge/K7-PHOENIX-RECORDING.md) works.

## 2. Run Phoenix

Phoenix ships as one container. This is the shape used by this project's test stack; pin an image digest for anything you keep.

```yaml
services:
  phoenix:
    image: arizephoenix/phoenix:version-21.1.0   # pin by digest in real use
    restart: unless-stopped
    environment:
      PHOENIX_WORKING_DIR: /data/.phoenix
      PHOENIX_ENABLE_AUTH: "true"                # see section 5 before exposing it
      PHOENIX_SECRET: ${PHOENIX_SECRET}          # long random value, kept out of Git
    ports:
      - "127.0.0.1:6006:6006"                    # bind to localhost; publish through a TLS proxy
    volumes:
      - phoenix-data:/data/.phoenix
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:6006/healthz', timeout=3)"]
volumes:
  phoenix-data:
```

Port 6006 serves the web UI, the OTLP trace endpoint (`/v1/traces`) and the MCP endpoint (`/mcp`). Keep the port on localhost and publish it through a reverse proxy or tunnel that terminates HTTPS. The Core refuses a non-HTTPS endpoint.

## 3. Point the Core at Phoenix

Set one environment variable on the Core:

```
PHOENIX_ENDPOINT=https://phoenix.example.org/v1/traces
```

The Core accepts the value only if it is an HTTPS URL whose path is exactly `/v1/traces`, with no credentials, query or fragment in it. Anything else disables tracing and logs a warning; the Core keeps serving. Leave the variable unset to run without Phoenix.

If Phoenix authentication is on, the OTLP exporter needs an API key. The standard OpenTelemetry exporter reads `OTEL_EXPORTER_OTLP_HEADERS` (for example `authorization=Bearer <key>`); set it from your secret store and never put the key in the URL. This is standard exporter behavior and is not set by this repository's code, so confirm it with the check in section 6.

## 4. Where spans land

The Core tags its spans with the project `usda-core-native` and the service name `mcp-core`, so they appear in that Phoenix project. The project name is set in `src/example_server/core_tracing.py`. Create separate projects for agent chat recordings (see the [configuration spec](AGENT-CONFIG-SPEC.md)); keep them apart from the Core's own spans.

What a Core span contains is deliberately small: tool and operation, outcome, duration, an opaque request ID, the correlation ID, route and the client name the connecting software declared. It never contains prompts, tool arguments or results, or profile data.

## 5. Access boundary before agents connect

Do this before giving any chat client the Phoenix MCP address. The Phoenix MCP endpoint exposes tools that can create, transfer and delete traces.

1. Turn authentication on (`PHOENIX_ENABLE_AUTH=true`) and sign in with a user or system key before exposing the address.
2. Give agents a key limited to a test project. Do not enable trace deletion or transfer for them.
3. Provision the Core's own key through your secret store.
4. Test with synthetic data only.

## 6. Check it works

1. Open the Phoenix UI and confirm the Core project exists.
2. Make one Core call from any agent or a test script.
3. In the Core project, find a span named `mcp.tool.call` within a minute. Open it and confirm `mcp.operation.id`, `mcp.result.status` and `mcp.client.name`.
4. For a scripted check, run `eval/operator/phoenix_preflight.py` (see [the operator folder](../eval/operator/README.md)).
5. No span after a minute usually means one of: `PHOENIX_ENDPOINT` is unset or not HTTPS, the key is missing or wrong, the Core you called is a different deployment from the one configured, or the proxy is blocking `/v1/traces`.

---

[Folder index](README.md) · [Next: Agent configuration spec](AGENT-CONFIG-SPEC.md)
