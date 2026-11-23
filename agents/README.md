# Agent instructions

> Version 8.0 (draft) · Updated 2026-11-20 · Index of 8  
> [Documentation index](../docs/README.md) · [Next: Master instructions](MASTER-INSTRUCTIONS-v8.md)

One master, cut per platform. Agents are adapters: the root [AGENTS.md](../AGENTS.md) lists them, and each platform loads the instruction file for its provider. Nothing here is tied to one vendor. IBM watsonx Orchestrate was the event-assigned environment; any platform with the needed capabilities can be used by cutting a new resident block from the master.

## Reading order

| # | File | What it is |
|---|---|---|
| 1 | [MASTER-INSTRUCTIONS-v8.md](MASTER-INSTRUCTIONS-v8.md) | The full master, 18 sections, 141 numbered rules |
| 2 | [PLATFORM-PROFILES.md](PLATFORM-PROFILES.md) | Limits, layers, read order, which file goes where |
| 3 | [knowledge/K1-CORE-RULES.md](knowledge/K1-CORE-RULES.md) | Sections 1 to 4 |
| 4 | [knowledge/K2-JOURNEY-AND-PLAN.md](knowledge/K2-JOURNEY-AND-PLAN.md) | Sections 5 to 8 |
| 5 | [knowledge/K3-RULES-AND-LIMITS.md](knowledge/K3-RULES-AND-LIMITS.md) | Sections 9 to 11 |
| 6 | [knowledge/K4-TOOL-REFERENCE.md](knowledge/K4-TOOL-REFERENCE.md) | Section 12 |
| 7 | [knowledge/K5-SAFETY-AND-TRACE.md](knowledge/K5-SAFETY-AND-TRACE.md) | Sections 13 to 16 |
| 8 | [knowledge/K6-TEST-AND-VOICE.md](knowledge/K6-TEST-AND-VOICE.md) | Sections 17 and 18 |
| optional | [knowledge/K7-PHOENIX-RECORDING.md](knowledge/K7-PHOENIX-RECORDING.md) | Phoenix chat recording block; skip it if you do not run Phoenix. Setup: [phoenix folder](../phoenix/README.md) |

## Platform files

| Platform | File | Size |
|---|---|---|
| IBM watsonx Orchestrate | [IBM-WATSONX-ORCHESTRATE-INSTRUCTIONS-v8.md](IBM-WATSONX-ORCHESTRATE-INSTRUCTIONS-v8.md) | full master |
| ChatGPT custom GPT | [GPT-INSTRUCTIONS-8K.md](GPT-INSTRUCTIONS-8K.md) | 7,257 of 8,000 characters |
| Microsoft Copilot Studio | [COPILOT-STUDIO-INSTRUCTIONS-8K.md](COPILOT-STUDIO-INSTRUCTIONS-8K.md) | 7,812 of 8,000 characters |
| Claude skill | [claude/usda-eligibility-navigator/SKILL.md](claude/usda-eligibility-navigator/SKILL.md) | 47 lines plus references |

The knowledge files are the master's sections, verbatim, so nothing is lost when the resident block is shorter. A git change does not update a running client: paste or upload the files into each actual client.

Index of 8. Next: [MASTER-INSTRUCTIONS-v8.md](MASTER-INSTRUCTIONS-v8.md).

---

[Documentation index](../docs/README.md) · [Next: Master instructions](MASTER-INSTRUCTIONS-v8.md)
