# K7 Phoenix conversation recording (optional)

Version 8.0 (draft), 2026-11-22. Optional addendum to the six knowledge files; not part of the reading order and not required. Previous: [K6-TEST-AND-VOICE.md](K6-TEST-AND-VOICE.md). Index: [README](../README.md). Setup and configuration: [phoenix folder](../../phoenix/README.md).

Most deployments do not use Phoenix and can ignore this file. Use it only when your deployment runs a Phoenix server and you want a reviewer to replay a chat.

## Why it exists

The Core records every tool call (operation, outcome, duration, correlation ID) but never the words. Hosted chat apps do not export the conversation. This block asks the agent to write each turn to Phoenix itself, so a reviewer sees the question, the answer and the Core calls together. The Core span and the agent's tool span share the correlation ID, which is how they are matched.

## Before you add it

- The agent needs a Phoenix MCP connection with `createSpans` allowed, scoped to a test project. Do not expose trace deletion or transfer. See [the setup guide](../../phoenix/SERVER-SETUP-GUIDE.md), section 5.
- The project name `usda-external-clients` below is the one this project uses. Replace it with the project your Phoenix owner names for agent recordings. The field layout is in [the configuration spec](../../phoenix/AGENT-CONFIG-SPEC.md).
- GPT and Copilot instruction files are limited to 8,000 characters and the block is about 2,900. Put it in a knowledge file, not in the instruction text.
- Synthetic, non-sensitive test data only.
- Status: not yet verified with a live `createSpans` call.

## The block

```
=== CONVERSATION RECORDING (add at the end of your Instructions) ===

Purpose: the USDA Core records only the tool calls you make. It never sees the words. So that testers and reviewers can replay this chat, you record each turn in Phoenix.

Rules
- Use synthetic, non-sensitive test data only. Never put real names, SSNs, addresses, emails or phone numbers in a recording. If the user typed any, replace them with [redacted] in the recording only.
- Record after you have composed your final answer for the turn and BEFORE you send it. Record the exact answer text you are about to send.
- Do this silently. Do not describe it to the user. If the recording call fails, send your answer anyway and add one short line: "(Turn not recorded.)" Do not retry more than once.

Setup, once per conversation
- Choose a conversation ID: "gpt-" or "claude-" followed by 8 random lowercase letters or digits.
- Choose a trace_id: 32 random hex characters. Reuse it for every turn of this conversation.
- Ask the tester for their tester ID (a short code, not a name) if they have not given one. Use "unknown" if they decline.
- You need the current UTC time. If you cannot tell it, use the timestamp from the most recent Core tool result; if there is none, ask the tester to type the current UTC time once.

Each turn, call Phoenix createSpans with project_identifier "usda-external-clients" and data = a list containing:

1. One turn span:
   - name: "gpt-chat-turn" (use "claude-chat-turn" if you are Claude)
   - span_kind: "AGENT"
   - context: { trace_id: <the conversation trace_id>, span_id: <16 new random hex characters> }
   - parent_id: null
   - start_time / end_time: UTC ISO-8601 times for when the user asked and when you answered
   - status_code: "OK"
   - attributes:
       "input.value": the user's message, at most 1500 characters
       "output.value": your answer, at most 1500 characters
       "navigator.thread.id": the conversation ID
       "navigator.client": "gpt-builder" or "claude"
       "tester.id": the tester ID

2. One span per USDA Core tool call you made this turn:
   - name: "tool " + the operation ID (for example "tool program.inventory.search")
   - span_kind: "TOOL"
   - context: { trace_id: <same trace_id>, span_id: <16 new random hex characters> }
   - parent_id: the span_id of the turn span above
   - start_time / end_time: when the call ran, UTC ISO-8601
   - status_code: "OK", or "ERROR" if the call failed
   - attributes:
       "tool.name": the Core operation ID, for example "program.inventory.search" (use the tool name if there is no operation ID)
       "mcp.correlation.id": the correlation ID from the Core's response if the response contains one; otherwise leave this attribute out
       "mcp.result.status": "ok" or "error"

If you made no Core calls this turn, record only the turn span.
=== END CONVERSATION RECORDING ===
```

## Check that it works

1. Send one synthetic question that triggers a Core search.
2. In Phoenix, open the recording project and look for a span named `gpt-chat-turn` (or `claude-chat-turn`) carrying your tester ID, with a child `tool program.inventory.search` span.
3. Check that the tool span's `mcp.correlation.id` matches a `mcp.tool.call` span in the Core project. If the Core span is missing, the Core is not exporting to this Phoenix; see the setup guide, section 6.

---

Previous: [K6-TEST-AND-VOICE.md](K6-TEST-AND-VOICE.md). Index: [README](../README.md). Next: none.
