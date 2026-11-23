# Eligibility pattern

> Version 1.0 · Updated 2026-11-21 · Reading order 3 of 7  
> [Previous: How the use cases led to the design](USE-CASES.md) · [Documentation index](README.md) · [Next: Data and sources](DATA-AND-SOURCES.md)

Eligibility is the first full pattern on the platform and the focus of the demonstration. This document explains the use cases it serves, how real requirements were translated into it, the user journey it delivers, and how testing and the demo follow that journey. It covers Layers 2, 4 and 5 of the [architecture](ARCHITECTURE.md).

## What the pattern does

A person describes a need in plain language. The pattern finds public programs that may fit, checks what is known about the person against each program's published criteria, asks only for facts that are missing, and returns a cited application-readiness binder: the steps, forms, documents, deadlines and contacts the source publishes. Every statement carries its source, and anything the source does not say stays unknown.

The pattern is domain-neutral. A profile, a set of criteria, a rule-matching function and a requirements packet describe agricultural loans as well as housing, food or child care assistance.

## Why eligibility was chosen

The wider story, with the pattern counts and a diagram, is in [How the use cases led to the design](USE-CASES.md).

Eligibility was not the top-ranked pattern by use-case count. In the use-case analysis (49 use cases mapped to 15 pattern templates), documentation and policy retrieval was the most common pattern, with 15 of the 49 use cases. Geospatial and environmental data access (6) and economic, market and statistical data access (5) followed. Program eligibility and rules retrieval had 3 directly, and a related grants, loans and funding guidance pattern had 1 more.

It was chosen anyway, for these reasons.

- **Related patterns scale from it.** Document retrieval and data analysis rank higher, and they are closely and deeply related to eligibility, so adding them later extends the same pattern instead of starting over. Eligibility is the pattern where those capabilities have to work together.
- **It is a two-sided coin.** The same pattern answers two questions. For the citizen: am I eligible, and do I qualify? For the applicant and the program: does the application meet the criteria, is all the information present and correct, is it aligned to the program's goals, and was the submission completed properly (compliance). That gives it a dual benefit.
- **It is a large pattern in the market.** Public-sector and private-sector organizations alike offer something that not everyone qualifies for, so the same shape reaches well beyond one agency.
- **It reaches the people the programs exist to serve.** Farmers and producers are a core USDA participant group, but a much smaller population than seniors or low-income households. About 3.4 million people are farm producers, about 61.2 million Americans are 65 or older, and about 34.5 million are in poverty.[^demo] Low income affects both of the other groups, seniors and veterans face similar challenges, and 38% of farm producers are 65 or older, so one person can need programs from several groups. A pattern that helps these groups find programs reaches a large share of the population.
- **It connects the mission to the need, and the money to both.** Program money is sometimes left unspent or reallocated because it is under-used: too few qualified applications arrive, or funds are not applied to the mission goal they were meant for. Fraud, waste and abuse exist, and controls against them matter, but they are not this story. The story is the gap between a funded program and the people it was funded to serve. Helping more people find programs they qualify for, and helping applicants submit complete and correct applications, connects the mission to the need and puts program money to its intended use. That also helps mission-driven organizations serve their communities better. The size of the effect needs real program-level data (allocations against obligations and awards), which this demonstration does not claim to measure.

[^demo]: U.S. Census Bureau, Vintage 2024 population estimates (age 65 and older); U.S. Census Bureau report P60-290, *Poverty in the United States: 2025*; USDA NASS, *2022 Census of Agriculture*.

The choice combined the use-case research with an analysis of who the pattern would help.

## Use cases

The use cases were supplied by the team as real requirements, not invented for the demo. Each was written as a person-centred advisor, and each is exercised through three scenarios.

| Use case | Who it serves | Scenarios |
|---|---|---|
| **UC-1 Farmer and producer opportunity advisor** | Farmers and ranchers looking for USDA help | Beginning-farmer classification (FSA); a livestock forage loss (FSA Livestock Forage program); conservation cost-share where land control matters (NRCS EQIP) |
| **UC-2 Senior and caregiver benefits navigator** | Older adults and the people who care for them | Senior food assistance (state CSFP agency); VA pension with aid and attendance; VA caregiver support (PCAFC) |
| **UC-3 Low-income assistance navigator** | Families with limited income | Food assistance (SNAP, Missouri); housing assistance (a public housing authority, St. Louis City); child care assistance (Missouri DESE) |

The use cases deliberately cross agencies and levels of government. A farmer's need touches FSA and NRCS; a caregiver's touches USDA, VA and state agencies; a family's touches federal and state programs. That is why discovery works across a shared inventory of federal and state sources instead of one agency's list.

## How real requirements were translated

A requirement written as a story about a person has to become something a machine can check without guessing. The translation has five steps, and each produces an artifact that can be tested.

| Requirement as written | Becomes | Where it lives |
|---|---|---|
| "I started farming three years ago and want to grow. What help is there?" | A plain-language need plus optional jurisdiction, matched against the shared inventory | `program.inventory.search`, `program.discover` |
| Facts a person states or is asked for (years farming, county, household size, veteran status) | Typed profile attributes in a versioned schema, saved only after the person confirms | Profile schema and `write_profile`; see [profile projection](../configs/templates/eligibility-profile-projection-v6.json) |
| A program page's eligibility prose ("beginning farmers have operated a farm for 10 years or less") | A typed rule bound to a profile attribute, with the verbatim source excerpt and locator | Criteria packages, persisted rulesets |
| "What do I need to apply?" | A binder of application steps, forms, documents and deadlines, each cited to the source page | `program.requirements.preview` and `program.requirements.write` |
| "Don't tell me I qualify if you don't know" | Explicit outcomes `met`, `unmet`, `unknown` and `not_applicable`, with `mapping_required` when no rule exists yet | Evaluation results contract |

Three rules govern the translation.

1. **No rule without a source.** A rule exists only when a cited clause on an approved source page supports it. The excerpt must appear verbatim in the source.
2. **Unknown is a result.** A fact the person has not given, or a rule not yet mapped, returns `unknown` or `mapping_required`. It is never reported as unmet and never guessed.
3. **Informational, not a determination.** The pattern screens and informs. It never calls a result an official eligibility decision or promises approval.

Requirements that the current schema cannot express are recorded, not hidden. For example, a tribal-tier household cannot be fully described because the household profile has no field for tribal citizenship or residence on or near a reservation, so that scenario is held until the schema is extended.

## The user journey

Both a person using a client and an AI agent using any MCP connection follow the same stable journey through the same routes. Neither substitutes its own search or scoring for a missing platform step.

| Step | Operation | What success means |
|---|---|---|
| 1. Discover | `program.inventory.search` | A plain-language need, with optional jurisdiction, returns the target program with its source, freshness and criteria status |
| 2. Select | `program.discover` for the chosen program | The program comes back with no evaluation yet and a readiness list naming only the facts that program's rules use |
| 3. Score | `program.evaluate` | At least one source-cited rule returns the expected outcome. Missing facts return `unknown` with the exact attribute and an ask action; out-of-scope places return `not_applicable` |
| 4. Preview the package | `program.requirements.preview` | The same program identity, marked as a preview and not written, with criteria outcomes and source limitations |
| 5. Package content | the same call | An actionable binder: the screening result plus at least one cited step, form, document or deadline. A closed or waitlisted program still gets its dated context |
| 6. Save and retrieve | `program.requirements.write`, then a profile-scoped read | The synthetic package is saved and read back exactly; the person can find it later through the saved profile |

Between steps 2 and 3 the profile fills in by conversation: the pattern asks only for facts the selected program's rules need, saves them after confirmation, and re-evaluates. A person who answers fewer questions gets more `unknown` results, not wrong ones.

### Results a person or agent can rely on

| Outcome | Meaning |
|---|---|
| `met` | The profile satisfies a mapped, source-cited rule |
| `unmet` | The profile contradicts a mapped, source-cited rule |
| `unknown` | A needed fact is missing; the result names the attribute to ask for |
| `not_applicable` | The rule is for a different place or situation than the person's |
| `mapping_required` | The program was found with its source, but no rule has been mapped yet |

A program found with a source but no mapped criteria is still a useful result: the person gets the source and the binder content, and the system says screening was not performed.

## Tools by journey step

| Step | Operations |
|---|---|
| Discover | `program.inventory.search`, `program.inventory.list`, `program.inventory.read`, `program.discovery.external.search` |
| Select and ask | `program.discover`, plus `write_profile`, `read_profile`, `preview_profile_refinement` |
| Score | `program.evaluate`, `program.results`, `program.candidate.evaluate`, `program.criteria.evaluate.persisted` |
| Package | `program.requirements.preview`, `program.requirements.write`, `program.candidate.requirements.preview`, `.write`, `.list`, `.read`, `.enrich` |
| Map criteria (operator-assisted) | `program.criteria.source.read`, `program.criteria.extract`, `program.criteria.prepare`, `program.criteria.persist`, `program.candidate.normalize` |

## Data the pattern uses

The pattern's catalog is a shared inventory of approved public program records from federal and state sources, each with source identity, jurisdiction, retrieval time and criteria status. A tenant adds criteria, semantic mappings and context over it. See [Data and sources](DATA-AND-SOURCES.md) for how sources are approved, loaded and kept fresh.

Program data comes only from public government sources. Applicant profiles are synthetic and fictional.

## Where coverage stands

Honest coverage is part of the proof.

- Every scenario's source is in the shared inventory, so every scenario can be discovered and can produce a cited binder.
- The seed data holds mapped, source-cited rules across all three use cases: for example the FSA beginning-farmer years rule, NRCS EQIP land control, CSFP minimum age, VA aid and attendance, VA caregiver support, Missouri SNAP residency, Missouri temporary assistance residency, and Missouri child care. Programs without a mapped rule are discovered with their source and report `mapping_required`.
- Widening coverage is a repeatable mapping workflow, not new platform work: read a source with provenance, extract cited clauses, compile a package, validate, persist.

## Testing and the demonstration follow the journey

The demonstration and the tests are built around the same six steps, so what is shown is what is verified.

**Demonstration.** The demo is the user journey, not a tour of tools. A synthetic person starts with a deliberately incomplete profile and says the opening need. The platform discovers programs, asks for what it needs, scores against cited rules, and produces the binder. The Navigator web app, a Teams agent, and Claude or GPT connected over MCP (including mobile and voice) all drive the same journey.

**Testing.** [Evaluation](EVALUATION.md) describes the layers. For this pattern, success is defined in levels.

| Level | Steps that must pass | Meaning |
|---|---|---|
| **L1 Screening** | 1 to 4 | The platform finds, scores and previews a package for the scenario |
| **L2 Complete** | 1 to 5 | The person gets a usable, cited requirements package |
| **L3 Saved** | 1 to 6 | The package is saved and the server-side readback matches |
| **L4 Accepted** | L3 plus a human run | A person completes the scenario through a real client |

Each scenario also needs its negative and missing-fact paths: one `unmet`, one `unknown` for a missing fact, and `not_applicable` where a rule is place-specific. A scenario without a mapped rule passes only by saying `mapping_required`, so the test rewards honesty over a confident wrong answer.

Test personas are synthetic and start incomplete on purpose. Positive, negative and edge cases cover boundary values (exactly 10 and 11 years of farming), missing, declined and corrected facts, a personal identifier offered unprompted, and an income question the catalog cannot answer. Humans test the edge cases that automation cannot judge; positive and negative cases are automated.

Tests read the live environment instead of pinning it, so a deployment change does not silently invalidate them, and any change that loads or alters a rule, dataset or contract carries its tests in the same change.

---

[Previous: How the use cases led to the design](USE-CASES.md) · [Documentation index](README.md) · [Next: Data and sources](DATA-AND-SOURCES.md)
