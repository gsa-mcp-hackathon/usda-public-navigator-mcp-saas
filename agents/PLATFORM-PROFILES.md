# Platform profiles: limits, layers and read order

> Version 8.0 (draft) · Updated 2026-11-20 · Reading order 2 of 8  
> [Previous: Master instructions](MASTER-INSTRUCTIONS-v8.md) · [Agent instructions index](README.md) · [Next: K1 core rules](knowledge/K1-CORE-RULES.md)

## The three layers

Every platform reads its guidance in up to three layers. Each has its own limit and its own way of being read, so the master is cut to fit the layers, never shortened.

1. **Instructions (resident).** Sent with every turn. Hard character limit on most platforms. Holds the rules that govern every turn, plus a READ ORDER line that says which file to open and when.
2. **Guidance or skills (loaded on demand).** Opened by the agent when a step needs them (Claude skill references). Fewer limits, but only read when the instructions say so.
3. **Knowledge (retrieved).** Uploaded files the platform searches. Not read in a fixed order and not guaranteed to be read, so nothing the agent must do every turn lives only here.

Rule numbers (1.1 to 18.3) are the line numbers. They are the same in the master, in every knowledge file and in every platform file, so a trace note such as "Instruction note: 12.6" points to one rule anywhere.

## Read order (every platform)

R0 resident block, every turn. Then by need: K1 start of chat; K2 searching, rules, plans; K3 mapping rules or after a tool error; K4 before any Core write, evaluate or save; K5 end of every turn (trace) and any emergency or high-stakes topic; K6 test runs and voice.

## Limits, as verified on 2026-11-20

| Platform | Instruction limit | Guidance / skills | Knowledge | Status |
|---|---|---|---|---|
| ChatGPT custom GPT | 8,000 characters in the Instructions field; the builder refuses to save more (OpenAI Help Center and developer forum threads) | none | Uploaded files, retrieved by search; not read in a fixed order | Instruction limit verified. Check the current OpenAI Help Center page for file count and size. |
| Microsoft Copilot Studio | 8,000 characters for agent instructions (Microsoft Learn, "Copilot Studio requirements and quotas") | none | Up to 500 files, 512 MB each on that page | Limit verified. Microsoft Q&A threads report that the combined request (instructions, topics, tools, history) has a lower internal limit than 8,000; v7-C at 7,949 characters ran in testing. Keep a margin. |
| IBM watsonx Orchestrate | No agent-instruction limit found in IBM's agent documentation. A 1,000-character limit is reported for a different field (Conversational Search / AI-guided Actions instructions). | none | Knowledge sources per IBM docs | Not verified. v7 (about 38,000 characters) ran on this platform, so the full master is used. Confirm with IBM or the platform owner before relying on it. |
| Claude (skill) | Skill description 200 characters on claude.ai (1,024 in the open specification). SKILL.md body: keep under 500 lines (Anthropic skill authoring guidance). | SKILL.md plus references/ files, loaded on demand | Project knowledge, retrieved | Verified from Anthropic documentation. Skill name must not contain reserved words. |

Sources checked: OpenAI developer forum and Help Center summaries on the 8,000-character GPT limit; Microsoft Learn (Copilot Studio requirements and quotas) and Microsoft Q&A; IBM watsonx Orchestrate ADK and ideas portal; Anthropic skills documentation (claude.com and platform.claude.com). Platform limits change; re-check before each release.

## Read ceiling: 12,000 characters per file

Some LLM clients read only about 12,000 characters of a file per read. Every knowledge file is kept under that (K1 9,607; K2 10,606; K3 5,861; K4 11,169; K5 7,073; K6 3,038), so one read gets a whole file and nothing is cut off. If a section grows past the ceiling, split it into a new numbered file; do not let a file run over. K4 is closest to the ceiling.

## Keeping instructions from going stale (design note)

Instructions pasted into a client do not update when Core changes. The long-term fix is for the MCP itself to carry the orientation: describe_core_capabilities returns a short bootstrap (current routes, tool shapes, workflow versions, and the read order) so the agent orients itself from live state. Then the resident block can stay small and stable, and the tool reference in K4 becomes a fallback that the live schema overrides (rule 12.19). This is a Core change for a later release, not part of this package.

## Files per platform

| Platform | Paste into the platform | Also load | Characters |
|---|---|---|---|
| IBM watsonx Orchestrate | [IBM-WATSONX-ORCHESTRATE-INSTRUCTIONS-v8.md](IBM-WATSONX-ORCHESTRATE-INSTRUCTIONS-v8.md) (full master, profile PRE-WIRED) | none | about 43,800 (no verified limit) |
| ChatGPT custom GPT | [GPT-INSTRUCTIONS-8K.md](GPT-INSTRUCTIONS-8K.md) (profile DISCOVER) | knowledge/K1 to K6 as knowledge files | 7,257 of 8,000 |
| Copilot Studio | [COPILOT-STUDIO-INSTRUCTIONS-8K.md](COPILOT-STUDIO-INSTRUCTIONS-8K.md) (profile PRE-WIRED) | knowledge/K1 to K6 as knowledge sources | 7,812 of 8,000 |
| Claude | [claude/usda-eligibility-navigator/SKILL.md](claude/usda-eligibility-navigator/SKILL.md) (profile DISCOVER) | references/K1 to K6 inside the skill folder | 47 lines |

## Profiles

- **DISCOVER:** call describe_core_capabilities first and use only the routes, operations and schemas it returns (GPT, Claude). Tool shapes are not in the resident block.
- **PRE-WIRED:** the tool reference is already in the instructions (IBM, Copilot). Do not call describe_core_capabilities or the list and read Core tools (11.2, 11.6), because each one spends part of a small per-turn call budget.

---

[Previous: Master instructions](MASTER-INSTRUCTIONS-v8.md) · [Agent instructions index](README.md) · [Next: K1 core rules](knowledge/K1-CORE-RULES.md)
