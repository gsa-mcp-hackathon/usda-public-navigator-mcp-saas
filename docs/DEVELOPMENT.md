# How it was built

> Version 1.0 · Updated 2026-11-21 · Reading order 7 of 7  
> [Previous: Evaluation](EVALUATION.md) · [Documentation index](README.md) · [Then: Enhancements](enhancements/README.md)

This project was built by a four-person team in which every person had AI assistants, and several AI tools worked in defined lanes alongside them. It ran on ordinary subscription plans with real tokens and real cost, over roughly 60 days. This document records the method, who and what did each part, and how the work was controlled. See [Architecture](ARCHITECTURE.md) for the design it delivered.

## Why AI was essential

This could not have been built without AI. A platform, a full pattern, a data layer, an evaluation harness, agent integrations and documentation were delivered in about 60 days by a small team with AI in every lane. Without it, the same scope would have needed a much larger engineering, testing and documentation team and a funded environment. The project is also evidence for the platform's premise: governed, evaluated AI can do work at a scale and speed that was not otherwise available.

## Method

The design was written first, decomposed into layers, mapped onto a product roadmap of capabilities, features and tools, and delivered in value slices grouped into phases. The enterprise design was collapsed into a demonstration that went live in about 30 days and was refined and tested over about 30 more (see [Architecture, Part 3](ARCHITECTURE.md#part-3-demonstration-proof-of-concept-and-proof-of-value)).

## How the work flowed

1. **Intent.** The team contributed ideas, direction and goals. Microsoft 365 Copilot helped find the top-level design intent in the team's design document and helped with research.
2. **Design.** The intent was translated into architecture, features and tools using best practices, working with Copilot.
3. **Independent review.** GPT and Claude each mapped the design into layers separately, refined it for implementation, then reconciled their results and cross-reviewed one another into a single implementation map.
4. **Planning.** Codex took the layer map and broke it into the roadmap and small deliverable slices, and recorded the work in Git.
5. **Build.** Codex wrote about 98% of the code. Early work was tracked through pull requests alone, then through issues, and as scope grew it moved to a program method with distributed AI lanes and human lanes.
6. **Lead design handover.** Once the core product was live, Claude took the lead design role, overseeing discrete design tasks so the product kept working while the engine and tiers changed underneath it.
7. **Test and review.** AI and human reviewers tested through every client the platform serves and reviewed the MCP results (see below).
8. **Documentation and reporting.** Codex, Claude and GPT documented the work in Git. Claude and GPT also produced the daily status and tracking reports and the team and email updates. The team also used Microsoft Copilot to review, plan and document inside the agency.
9. **Submission materials.** Claude and GPT designed and drafted the submission materials, scripts, demos and tests, and each peer reviewed the other's work. Claude contributed the polish, look and flow; GPT contributed the substance and framing.
10. **Publication.** The public repository was planned and reviewed by GPT and Claude. They planned the final move, then refined the final artifact together, and it went to publication with the team lead and human reviewers before submission.

## Working by voice

The same idea shaped how the project was run. The lead designer refined the design and checked status daily from a mobile phone, using voice with Claude and GPT. The pattern was to ask the AI to check Git and the work backlog, and to update Git. The AI then wrote a handoff to an AI agent working the backlog, and a few minutes later the change could be seen on the live MCP server in real time. The user experience was also tested by voice, hands free and on the move. It made it possible to stay productive away from a desk, and it proved the platform's own claim: a standard AI client, a connection and a skill are enough to turn a phone into a working interface.

## A setback and the rebuild

Persistence was a requirement from day three, and the sandbox offered no managed database. We designed for that: PostgreSQL in a container behind one access API, no credentials in the runtime, an outbound-only tunnel, and a database that could be rebuilt from Git.

Three weeks before the due date the development VM went offline for four days, and the database went with it. Only the container images and the repository remained. We chose to rebuild rather than wait: the database came back in a container on a local server, the tunnel was repointed, and the service was restored after about 16 hours. The data and mappings were rebuilt within five days.

The rebuild confirmed the design and sharpened it.

- **Git is the source of truth.** Migrations, seeds and mapped criteria rebuild the data tier on demand. The loss tested that for real, and it held.
- **The source is the source.** An early reload tried to load full normalized source data, and we stopped it after about 20 hours. The catalog is meant to hold identity, mappings and provenance, and to read the source at request time. That is now how the SAM.gov listings are registered, as metadata, and it is the rule in [Data and sources](DATA-AND-SOURCES.md#ingestion-behavior).
- **Smaller and verified beats larger.** We rebuilt a focused set of programs and verified each record against its source, with its scope and limits stated. Partial and cited is the standard.
- **Criteria belong in rule sets.** Mapping criteria per program was the hard part, so the logic moved into versioned rule sets and namespaces, and the schema stopped changing with every new rule (see [Data and sources](DATA-AND-SOURCES.md#namespaces-rule-sets-and-correlation)).
- **Ownership moved to the tenant and cluster.** Each now owns its catalog, with controls to match. The approach in [Architecture](ARCHITECTURE.md) is partly a product of that recovery.

## The AI team

| Tool | Role in this project |
|---|---|
| **Codex (OpenAI)** | Primary builder: about 98% of the code. Also roadmap and slice breakdown, loading work into Git, tests, deployment and repository administration |
| **Claude** | Layer mapping and cross-review of the design; the original automated test suites and peer development and test alongside Codex; the Navigator app (the Navigator service turned into a web app); code, test-suite and documentation review; lead design role once the core product was live; daily status and tracking reports and team and email updates; submission materials, demos and tests (polish, look and flow); planning, review and refinement of the public repository |
| **GPT (OpenAI)** | Layer mapping and cross-review of the design; many of the documents and planning artifacts that helped Codex plan and organize work; review of items; daily status and tracking reports and team and email updates; submission materials, demos and tests (substance and framing); planning, review and refinement of the public repository; a connected agent used for testing |
| **GitHub Copilot** | Peer reviews and code reviews, automated testing, and the Microsoft Copilot Studio agent |
| **IBM Bob** | The IBM watsonx Orchestrate agents, plus review, code, test-suite and documentation assistance |
| **Microsoft 365 Copilot** | Inside the agency: finding the design intent, research, review, planning and documenting the work |
| **Microsoft Copilot Studio agent** | The Teams-facing agent in the USDA environment that calls the Core over MCP |
| **IBM watsonx Orchestrate** | Agent platform connected to the Core over MCP in the demonstration environment |
| **Arize Phoenix** | Tracing and LLM-as-judge evaluation, introduced to extend test reach |
| **Cloudflare AI** | Introduced to provide connectivity between the data layer and external clients such as mobile apps, OpenAI, Anthropic and other agent platforms |

Different providers were used deliberately. Design review, code review and judging cross provider lines, so no model reviews or grades only its own work.

## The human team

Four people worked alongside the AI tools, and each had an AI assistant for some task.

| Person | Role |
|---|---|
| Mike Puntumapanitch | Project lead; architecture and design |
| Gary Paxson | Team member |
| Stephanie Mora | Team member |
| Paul Whitfield | Team member |

Credit goes to the whole team for their participation. They shaped the design, supported the work, helped move it forward, attended meetings the project lead could not, and shared out the work among themselves.

People supplied ideas, direction, goals, judgment and acceptance. They tested the platform with the Copilot Studio agent, the watsonx Orchestrate agent, GPT agents and, once it was working, the Navigator web app, and they reviewed the MCP results. AI handled much of the writing, building and first-pass review; people decided what was accepted.

## Cost and effort

The work ran on paid subscription plans rather than free credits or a funded project environment, so the cost is real and personal. Usage was heavy: on several days a single tool, Codex, consumed more than 20 million tokens working toward one goal.

### What the record shows

- **Delivery.** The project ran 64 days, from 2026-09-16 to 2026-11-19. It produced 1,313 merged pull requests and 223 closed issues. Every change went through automated lint, format, tests and a security scan before it merged. These counts come from the private development history; this public repository is a clean snapshot and does not carry that history. The lint, format and test checks run on every change in this repository; the security scan ran in the private development pipeline.
- **Who did the work.** AI tools wrote most of the code, tests and first drafts of the documents, including parts of this one. People set the direction, reviewed the results and made the decisions. AI-written work was reviewed by a person and by a different AI model, as described under controls below.
- **Pace.** AI runs were scheduled around the clock, including overnight, so the tools kept working between reviews.
- **Human effort.** Tracked internally and not reported here.
- **AI usage, measured.** Codex's goal meter is the one complete record: about 300.8 million tokens through 2026-11-06.

### What is not claimed

Treat the figures above as floors, not totals. Usage after 2026-11-06 and usage in other tools is only partly measured, because the tools count differently and some do not expose usage at all. Shared team accounts cannot be split by person, some counts are estimates from visible text, and some are unknown. Token counts from different tools are never added together. No cost figure is claimed, because most access was through flat-rate subscriptions whose limits reset on rolling windows, so token counts show how much of a plan's capacity was used, not what it cost.

We would rather state that confidence plainly than present a precise-looking number that nobody could check. One usage record per pull request, checked in CI, would close most of the gap.

## Controls on AI-written work

- A human reviewed and accepted the design and every change.
- Work was split into small, reviewable slices with acceptance checks.
- Different models reviewed one another's work, and review findings were reconciled before changes were accepted.
- Lint, format and tests run on every change in this repository; the security scan ran only in the private development pipeline.
- Agent behavior is measured with the evaluation harness and recorded with the run date and deployed commit (the ledger is not part of this snapshot).
- Applicant data is synthetic and program data is public, so no real personal information entered any AI tool.

---

[Previous: Evaluation](EVALUATION.md) · [Documentation index](README.md) · [Then: Enhancements](enhancements/README.md)
