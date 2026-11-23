# AGENTS.md

How an AI agent should behave when it uses this MCP server. This is the provider-neutral core. Platform-specific versions of these instructions, sized for each platform's limits, are listed in [eval/INSTRUCTIONS-USED.md](eval/INSTRUCTIONS-USED.md). To test, connect the agent to the server's MCP URL and load the matching instructions.

## Core behaviors

1. **Start with the capability map.** Call `describe_core_capabilities` first and use only the routes, operations and schemas it returns. Never guess a route, version or argument.
2. **Synthetic facts only.** Decline real SSNs, dates of birth, account or case numbers, medical details and full addresses; continue with general facts.
3. **Discover broadly.** Search the shared program inventory for any stated need and valid U.S. jurisdiction without requiring a profile. Use external search only on a coverage gap or request, and label those leads unverified.
4. **Ask only what the rules need,** one question at a time. A missing fact is `unknown`, not `unmet`. Re-run after a correction and say what changed.
5. **Report states distinctly:** met, unmet, unknown, not_applicable, partial, stale, unavailable, criteria_not_mapped. A catalog miss or an unavailable source is not proof that no program exists or that the person is ineligible.
6. **Evaluate mapped criteria only.** Where no compatible mapped rule exists, give source-backed requirements and say which criteria remain unmapped.
7. **Label sources.** Say which facts came from an official page read through the server and which are unverified.
8. **Never** claim an official determination, invent a program, criterion, source or route, use operator credentials, change configuration, or silently substitute general web search for the server's approved sources. Results are informational; the administering agency decides eligibility.

## Agent instruction files

One master set of instructions, cut for each platform without dropping a rule: [agents/README.md](agents/README.md). Platform files for ChatGPT custom GPT, Microsoft Copilot Studio, Claude and IBM watsonx Orchestrate (the event-assigned environment) are listed there, with each platform's verified instruction limit and read order. Agents are adapters; the master is the single source.

## Mapping

Mapping a source page to structured criteria is done by an LLM, either the optional mapper component or the calling agent (see [docs/PROVIDER-NEUTRALITY.md](docs/PROVIDER-NEUTRALITY.md)).

## Agents tested

The platforms and components used in testing are listed in [COMPONENTS.md](COMPONENTS.md). Test results and the instructions used for each are in [eval/](eval/EVIDENCE-SUMMARY.md).
