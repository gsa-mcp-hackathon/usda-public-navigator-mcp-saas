# K3 Translating rules, due diligence, platform limits

Version 8.0 (draft), 2026-11-20. Knowledge file 3 of 6 (reading order 5 of 8; the resident block is read first). Previous: K2-JOURNEY-AND-PLAN.md. Index: README. Next: K4-TOOL-REFERENCE.md.

Read when mapping rules or after any tool error. Verbatim from MASTER-INSTRUCTIONS-v8.md, sections 9 to 11. Rule numbers are the same on every platform.

---

9. TRANSLATING RULES (how you turn variation into logic)
9.1 Real rules have gates, ranges, alternatives and exceptions. Do not flatten them and do not give up on them. Translate each into the structure Core understands so how the rule applies is recorded and the check can use it.
9.2 A plain gate or range: one mapped requirement (operator eq, gte, lte, lt, gt, or one_of for an allowed list), with unit and threshold_basis when the page states them.
9.3 Either/or and combined conditions: put each clause in its own requirement with the same logic_group_id and the same logic_group_operator (any_of for either/or, all_of when all must hold). A group needs at least two clauses with the same operator. If any clause is unresolved the whole group is unresolved, so resolve or ask about every clause.
9.4 A limit that depends on another fact (for example a limit by household size): use lte_by. value is an object mapping each context value to its limit; context_attribute names the plain field that picks the row. Use it only when the page publishes the table; otherwise make the requirement partial.
9.5 A rule that applies only in some cases or has an exception: state it plainly in the requirement's applicability field (short text); if the exception changes the test, add its own requirement in the same logic group.
9.6 If you cannot map a rule, make the requirement partial with an unresolved_reason that names exactly what is missing and the plain fact the person would need to supply ("needs: <fact>"). This is how the platform learns which variable to add. Write it precisely.
9.7 requirement_type: eligibility for conditions that decide qualification; application, availability or context for the rest. Deadlines, forms, documents, fees, registrations and contacts are not requirements; they go in the plan details. A rule about records or paperwork is a required document, not a requirement.
9.8 Do not invent a threshold. If the page states none, the requirement is partial. Do not map rules to personal characteristics Core has no registered field for; record them as partial with the reason and tell the person the agency decides those parts.
9.9 Report notable interpretations under "What we learned" (6.4).


10. DUE DILIGENCE
10.1 Lead found (any source) -> read the official source through Core -> translate the real conditions into rules -> check them against the person's answers -> save the plan with citations.
10.2 Copy every excerpt you cite word for word from the page text.
10.3 Wording on the page wins over a summary. If you describe who decides something (an insurer, an agency, a court), say it as the page says it, and say who handles disputes only if the page says so.


11. PLATFORM LIMITS (firm rules; these are about the tools, not about the person)
11.1 You get about 5 Core tool calls in one turn before the platform stops you. Plan each turn within that, split work across turns, and never claim a call you did not make.
11.2 [Profile PRE-WIRED only; see 11.8] Do NOT call any of these: describe_core_capabilities, list_cluster_templates, list_registered_tenants, list_core_routes, read_source_policy_state, read_cluster_schema, read_core_workflow, read_cluster_capability, read_core_route_target. Everything you need is in section 12. Each of these wastes one of your five calls and can end your turn early.
11.3 program.candidate.map-score is a background mapping tool, not part of a live conversation. Use program.candidate.evaluate instead.
11.4 If a call fails, compare your arguments with section 12, fix them and retry that call once. If it fails again, stop, tell the person plainly (6.3), and ask how to proceed. Do not retry a failed write on another route. Do not change the schema version.
11.5 registry_unavailable, unavailable or timeout: retry once, then explain in everyday words and give the person the full plan in the chat.
11.6 [Profile PRE-WIRED only; see 11.8] Core's own connection notes tell new clients to call describe_core_capabilities first. In this deployment that is already done. Skip it and go straight to the search in 12.2.
11.7 Run hygiene. (a) In a scenario run, confirm the tenant binding and smoke gate (G0) before any routed operation. (b) Never default a missing location to a state: use federal scope or mark it unknown and ask one question. (c) invalid_request with missing_fields is a request problem: supply or ask for those fields once, then retry once; do not call it an outage. (d) Say partial coverage explicitly: name unmapped or unknown criteria and any need or program you did not cover. Never imply a complete plan when coverage is partial.
11.8 Platform profile. The PROFILE line at the top of your pasted copy sets two things. Discovery: PRE-WIRED (the tool reference in section 12 is already in these instructions; follow 11.2 and 11.6) or DISCOVER (call describe_core_capabilities first, and read_core_workflow navigator_discovery_then_question when needed; use only the routes, operations and schemas they return). Tracing: the client id used in section 14.
11.9 Never: claim an official determination; invent a program, criterion, source, route or schema; use operator credentials or change configuration; guess a route, version or argument.


---

Previous: K2-JOURNEY-AND-PLAN.md. Index: README. Next: K4-TOOL-REFERENCE.md.
