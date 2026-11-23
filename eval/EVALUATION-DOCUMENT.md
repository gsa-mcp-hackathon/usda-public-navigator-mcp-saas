# Evaluation document

> Version 2.3 · Updated 2026-11-23 · Evaluation document · Read 1 of 3 in this folder  
> [Previous: Evaluation folder index](README.md) · [Documentation index](../docs/README.md) · [Next: Evidence summary](EVIDENCE-SUMMARY.md)

Evaluation document for the MCP SaaS Demo, our submission to the GSA MCP Server and AI Agent Government Hackathon.

| Item | Detail |
|---|---|
| Submission | MCP SaaS Demo: an eligibility pattern and the platform that governs it |
| Server under test | MCP SaaS platform (Core 0.2.0) with the Eligibility pattern (template 0.1.48). The platform first went live in mid-October 2026. These versions were live from 21 November and are the ones tested here. |
| Evaluation tools | Scripted test suites and Arize Phoenix for recording what happens inside the platform |
| AI model in the scripted runs | None. Two AI agents, Claude Sonnet 5.5 and GPT-4.1, were tested separately: [Section 2.7](#27-the-agent-evaluation-in-phoenix-added-in-version-23) and [Section 3.6](#36-agent-evaluation-in-phoenix-23-november-final) |
| Test data | 23 questions with expected answers; 23 fictional people with expected outcomes |
| Version and date | 2.3, 2026-11-23. Final results are from 23 November ([Section 3.6](#36-agent-evaluation-in-phoenix-23-november-final)). The 21 November scripted runs are the baseline for the scripted suites. |

## At a glance

The demonstration set out to prove two things: that an eligibility pattern can find programs, check a person against cited criteria and say "unknown" instead of guessing, and that one governed platform can serve many organizations. It was deployed in about 30 days, refined over 30 more, and tested with scripted checks, 23 fixed questions, 23 fictional people and two AI agents. Every run is saved so a reviewer can re-check it without our systems.

- **What held up.** The scripted checks gave the same result in four runs on 21 November 2026: 52 passed, 2 were skipped on purpose and none failed ([Section 3.1](#31-results-by-claim)). On 23 November, Claude Sonnet 5.5 answered all 23 fixed questions as expected, up from 19 of 23 matched by direct calls on 21 November ([Section 3.6](#36-agent-evaluation-in-phoenix-23-november-final)).
- **What to watch.** The AI agent changes the result. With the same tools, GPT-4.1 matched 17 of 23 and 4 of 10 on the eligibility questions, where Claude matched all. Agent instructions and client set-up were tuned weekly and are the lever for improving this. On 21 November, 19 of 23 direct-call questions matched; the four misses were on the test side ([Section 3.4](#34-what-went-wrong)). One hosted agent reached 21 of the 44 tools. Only some programs have their requirements prepared for checking.
- **Not measured.** Heavy load and a comparison with an unaided AI assistant ([Section 2.6](#26-about-baselines-what-we-searched-for-and-what-we-found)). Both belong to the path to scale.
- **Why six claims, not one score.** The code, the program data, the outside websites and the AI assistant each change on their own schedule ([Section 2.1](#21-why-this-evaluation-looks-different-from-a-standard-performance-report)).

The results show the capability works. Scale is a designed pathway ([Architecture, Part 4](../docs/ARCHITECTURE.md#part-4-pathway-to-scale)), not something this evaluation measures.

## 1. Overview and Objectives

### 1.1 Where this document fits in the submission

GSA's hackathon invited government employees to build MCP servers: programs that let AI assistants reach real government data and services through a shared standard. A submission has three deliverables. This document is one of them. It is the data behind the other two.

| Deliverable | What GSA asks it to cover (paraphrased) | Our submission |
|---|---|---|
| GitHub repository | Complete, documented code for the MCP server. | The public submission repository. [Appendix D](#appendix-d-repository-map-for-the-technical-side) maps the parts a technical reviewer will want. |
| Slide deck | The problem, how the server solves it, the technical approach and key decisions, and a demonstration. | Submitted separately. It tells the story and shows the demo, and its evaluation appendix draws on this document. |
| Evaluation document (this document) | Testing methodology, performance metrics, security considerations, and lessons learned with recommendations. | [Section 2](#2-testing-methodology) methodology, [Section 3](#3-performance-metrics) metrics, [Section 4](#4-security-considerations) security, [Section 5](#5-lessons-learned-and-recommendations) lessons and recommendations. |

Supporting files: the [evidence summary](EVIDENCE-SUMMARY.md), the [performance report](PERFORMANCE-REPORT.md), the [results guide](results/README.md) and the [23 November agent results](results/phoenix-agent-eval/README.md).

GSA judges on four criteria: technical quality (server performance and reliability, code readability, MCP design), innovation, mission alignment, and presentation including evidence of testing. This document speaks mainly to performance, reliability and evidence of testing. The repository carries code and design, and the deck carries innovation, mission and story.

### 1.2 The problem the tests are about

The deck tells the full story. In short: people cannot easily find the government programs they qualify for; giving AI assistants that information is hard to do well, because answers must cite sources instead of guessing; and if every team builds its own MCP server, government gets duplicated work and weak oversight. The team started from 49 USDA use cases, which fell into about 15 recurring patterns, and built one pattern well, eligibility, on a governed platform. More detail: [`docs/USE-CASES.md`](../docs/USE-CASES.md), [`docs/ELIGIBILITY-PATTERN.md`](../docs/ELIGIBILITY-PATTERN.md).

### 1.3 What we built: two proofs

- **Proof 1, the eligibility pattern.** A person describes a need in plain language. The pattern finds public programs that may fit, checks what is known about the person against each program's published criteria, asks only for facts that are missing, and returns a cited application-readiness binder. Anything the source does not say stays "unknown". It is information only and never an official eligibility decision.
- **Proof 2, the platform.** The MCP SaaS Core is one governed layer that registers patterns and tenants, routes each request and keeps the fleet of MCP servers in one place instead of letting it sprawl. An organization adds its own sources and rules as a tenant without forking the server.

**MCP + SI.** The hackathon is MCP plus SI, super intelligence: AI tools and agentic tools powered by large language models. The platform supplies the governed, cited tools. The agent and its instructions supply the conversation. The result a person sees depends on both, which is why this document reports claims separately instead of one score, and why it records agent behavior as well as platform behavior ([Section 3.6](#36-agent-evaluation-in-phoenix-23-november-final)).

**Scope of the demonstration.** This is a compressed demonstration, built in a shared sandbox with limited access to resources: the USDA team was one of many teams using the same environment. It implements the registry, routing, lifecycle governance and telemetry foundations and proves the capability end to end where it was tested. Production sign-in, quotas, billing, high availability and a managed data service are not implemented. [Architecture, Part 3](../docs/ARCHITECTURE.md#part-3-demonstration-proof-of-concept-and-proof-of-value) lists what was built and what was not, and [Part 4](../docs/ARCHITECTURE.md#part-4-pathway-to-scale) says how each compromise would be undone at scale.

### 1.4 What we set out to prove

Six claims, each checkable. Tests per claim: [Section 2.3](#23-which-tests-check-which-claim). Results: [Section 3.1](#31-results-by-claim).

| Claim | Statement | Proof |
|---|---|---|
| C1 | It finds programs, checks a person against cited criteria and says "unknown" instead of guessing. | 1: pattern |
| C2 | It gives a usable result: a cited binder that saves and reads back unchanged. | 1: pattern |
| C3 | One governed platform serves many organizations, refuses what it should, and fails safely. | 2: platform |
| C4 | It works from different AI assistants. | 1 and 2 |
| C5 | Which programs are ready for checking is reported separately from whether the code works. | 1: pattern |
| C6 | It answers in reasonable time and gives the same result when repeated. | 1 and 2 |

## 2. Testing Methodology

### 2.1 Why this evaluation looks different from a standard performance report

A standard report asks one question and gives one number. We had six claims about two different products, a pattern and a platform, running on data that changes at its own pace. Four different things can make an answer wrong: the **code**, the **program data**, the **outside websites** the platform reads, and the **AI assistant** that is using it. They change on different clocks. Code changes when a version is deployed. Data changes when preparation work is done. Outside websites change on their own. Assistants change with each product.

If everything is rolled into one score you cannot tell which of the four to fix. A program whose requirements have not been prepared returns mapping_required. The code did exactly what it should, so the capability test passes, and the data is incomplete, so the coverage measure shows a gap. Rolled together, a working system would look like a failing one.

| A standard report usually | This evaluation, and why |
|---|---|
| Gives one score, such as accuracy percent or requests per second | Keeps the claims apart, because code, data, outside sources and assistants change on different schedules. |
| Compares against a baseline | Could not use a standard baseline ([Section 2.6](#26-about-baselines-what-we-searched-for-and-what-we-found)). It compares with answers written in advance from official sources and with earlier runs of the same test. |
| Tests how well an AI model uses tools | Tests the platform. The assistant is something that varies, not the thing under test. The scripted runs use no AI model, so wording differences cannot change the result. The Phoenix agent run ([Section 2.7](#27-the-agent-evaluation-in-phoenix-added-in-version-23)) then uses two assistants to show how much they vary. |
| Measures speed on fixed, controlled data | Measures speed on the real job, which includes reading real public sources that we do not control, so timing varies. |
| Counts only pass and fail | Treats "I do not know, and here is the fact I need" and "I refuse" as correct answers when they are. A system that guesses fails. |
| Runs once | Reran the same suite several times on one day and saved every run as files that can be re-checked after the live environment is gone. |

### 2.2 How a test works

One script runs a list of checks (a "suite") against the live platform. Each check is one request plus rules for what the answer must contain. The script writes three files per run: what was asked, what came back, and a record of every call with its correlation ID, a unique tag on each request. Each ID is matched to the platform's own record in Phoenix, a tool that records what happens inside the platform, so a reviewer can follow any answer back to what happened. A separate read-only checker recounts everything from the saved files with no network and no passwords. Details: [Appendix C](#appendix-c-saved-evidence).

The script retries a read-only request once when the platform is waking from idle. It never retries a refusal, a malformed request or a write.

| Suite | What it checks |
|---|---|
| S01 | Any assistant can connect and read the platform's capability list, and no private internal addresses are exposed. |
| S02 | Administrator-only functions refuse a missing or wrong credential and accept the right one. |
| S03 | The register of templates, organizations and routes is complete and consistent. |
| S04 | Requests for something that does not exist are refused cleanly, and checking a request does not save anything. |
| S05, S11 | Each fictional person is run through discovery and screening and compared with the expected outcome. S05 uses a full demonstration profile. S11 sends the same people as sparse, general profiles. |
| S08 | What the platform publishes about itself matches how it actually behaves. |

### 2.3 Which tests check which claim

| Claim | Tests | Detail |
|---|---|---|
| C1 | 10 eligibility questions; 23 fictional people (suites S05 and S11); the Phoenix agent run | [Appendix B](#appendix-b-where-the-tests-are) rows 5, 6, 8, 15 and 16 |
| C2 | Binder save and read-back in the person suites; Phoenix-recorded runs | [Appendix B](#appendix-b-where-the-tests-are) rows 5, 6, 11 and 13 |
| C3 | Suites S01 to S04 and S08; 10 platform questions; the Phoenix agent run | [Appendix B](#appendix-b-where-the-tests-are) rows 1 to 4, 9, 10 and 16 |
| C4 | Direct calls; Phoenix-recorded sessions; Claude Sonnet 5.5 and GPT-4.1 as agents; a ChatGPT and Codex assistant; an IBM agent | [Appendix B](#appendix-b-where-the-tests-are) rows 11 to 13 and 16 |
| C5 | 3 data-coverage questions; reruns on 17, 21 and 23 November | [Appendix B](#appendix-b-where-the-tests-are) rows 7 and 16 |
| C6 | Timing of every call; four repeated runs | [Appendix B](#appendix-b-where-the-tests-are) row 14 |

### 2.4 What the tests do and do not do

| They do | They do not |
|---|---|
| Check that the platform returns the right structure, the right refusal or the right "unknown" | Judge whether a person would actually qualify. That is an agency decision. |
| Check that administrator functions fail closed | Test the platform under heavy load |
| Check results against answers written in advance, using the same fictional people each time | Cover every program. Only some have prepared requirements. |
| Show that a saved binder reads back identically | Prove the outside websites stay available or unchanged |
| Let a reviewer re-check the saved runs without our systems | Compare against a no-tool baseline. Two AI agents are compared separately in [Section 3.6](#36-agent-evaluation-in-phoenix-23-november-final). |

### 2.5 The test data

- **23 questions** with one expected answer each: 10 about the platform (no program content), 10 about the eligibility pattern and 3 about data coverage. A question answered without using the server fails.
- **23 fictional people**, each starting with an incomplete profile: 18 edge, 3 positive and 2 negative cases. The platform has to ask for what is missing and save it only after the person confirms.

**Positive** tests show it answers correctly when asked correctly. **Negative** tests show it refuses when it should, for example an unknown route or a wrong administrator credential. **Edge** tests show it does not guess when information is missing. The use cases behind the people were supplied by the team as real requirements: a farmer and producer advisor, a senior and caregiver benefits navigator, and a low-income assistance navigator.

**Example.** A fictional farmer in Missouri wants help buying equipment but has not said how long she has farmed. The platform finds programs and shows their requirements but does not score her yet. It asks for the missing fact and saves it only after she confirms. If she declines, that requirement stays unknown, and the test fails if the platform returns "met" or "not met" instead. The positive version supplies the fact and expects "met". The negative version supplies a fact that fails the rule and expects "not met". More detail: [`eval/datasets/README.md`](datasets/README.md), [`eval/synthetic-datasets/README.md`](synthetic-datasets/README.md).

### 2.6 About baselines: what we searched for and what we found

A baseline answers "compared with what?". We searched published work in November 2026 for anyone evaluating, or setting a baseline for, a similar solution. We found useful methods but no published baseline for a platform like this one. This is "we did not find one", not proof that none exists. The closest work falls into four groups, listed with links in [Appendix E](#appendix-e-sources):

- **MCP benchmarks** (MCP-AgentBench, MCP-Atlas, MCP-Bench, MCPEval) score how well an AI model uses tools across many existing servers. They score the model, not one platform.
- **Offline evaluation of one server** (GitHub's own MCP server) uses curated questions with the expected tool calls. We follow the method in spirit: answers written in advance.
- **Eligibility benchmarks** (BeNYfits, Public Benefits Bench) score general AI models on narrow sets of programs, with the bare model as the baseline. They do not score a shared platform, a binder or an administrator workflow.
- **Gateway performance tests** measure the extra time a gateway adds against simple test tools. Our calls read real outside sources, so a fixed-tool overhead number does not describe them.

**What we did instead.** Every answer was compared with an expected answer written in advance and with earlier runs of the same questions. Refusal and "unknown" were tested as correct outcomes. The suite was rerun several times, and two AI agents were compared on the same questions. **What we recommend next:** the same 23 questions answered by an AI assistant with no platform, and a measure of the platform's own overhead ([Section 5.3](#53-next-steps-toward-scale)). Neither was run for this submission.

### 2.7 The agent evaluation in Phoenix (added in version 2.3)

On 23 November we added a second kind of test, on top of the scripted suites. Two AI agents, Claude Sonnet 5.5 and GPT-4.1, answered the same 23 fixed questions using only the platform's read-only tools. Every answer was scored three ways: an exact match against the stored expected answer (the gate), and three scores from an AI judge from the other provider (is it correct, is it in scope, did it use the right tools). Every question is a run in Phoenix linked to a trace of the agent's model and tool calls, with token counts. The questions are the same three sets as in [Section 2.5](#25-the-test-data): 10 platform, 10 eligibility and 3 data coverage.

We kept exact match as the gate because an AI judge is itself an opinion that can drift. The judge scores are reported beside it, with the cases where the two disagree. The run is saved in [`eval/results/phoenix-agent-eval/`](results/phoenix-agent-eval/README.md). The same questions were also run on 3 and 19 November, which gives the progression in [Section 3.6](#36-agent-evaluation-in-phoenix-23-november-final).

## 3. Performance Metrics

### 3.1 Results by claim

Results are from 21 November 2026 unless noted; they are the baseline for the scripted suites. The final agent results, from 23 November, are in [Section 3.6](#36-agent-evaluation-in-phoenix-23-november-final). The run-by-run record is in [Section 3.3](#33-run-by-run-record).

#### C1. It finds programs, checks against cited criteria and says "unknown" instead of guessing

- **7 of 10** eligibility questions matched on 21 November. Two misses were wording only and **one, N16, was a test-client miss, not a Core defect**; all three are explained in [Section 3.4](#34-what-went-wrong). On 23 November, Claude matched all 10 as an agent ([Section 3.6](#36-agent-evaluation-in-phoenix-23-november-final)).
- The person suites recorded **0 failures** within the 52 passes, including the edge cases where "unknown" is the correct answer. The recorded operator run exercised 8 of the personas.

In what was tested, the pattern did not turn a missing fact into a yes or a no. The one unexplained miss (N16) traced to the test client, not the Core. Only a sample of people and programs was tested.

#### C2. It gives a usable result: a cited binder that saves and reads back

- Where a run saved a package, it was read back through the Core. Preview-only runs are labeled as preview-only in the results guide. The Phoenix-recorded operator suite matched **20 of 20** calls to a trace and the guided demo **10 of 10** (17 November).

The binder step works where it was exercised. Not every run saves a package, so this claim is demonstrated, not exhaustively proven.

#### C3. One governed platform serves many organizations, refuses what it should and fails safely

- Suites S01 to S04 and S08 had **0 failures** in four runs (the same 52 passes and 2 skips).
- **9 of 10** platform questions matched. The one miss, N09, was an out-of-date expectation, not a defect ([Section 3.4](#34-what-went-wrong)).
- **The tests found a real governance problem, and it was fixed.** Ten versions of the eligibility template were all active, which failed suite check S08-T01 and made "which version is active" ambiguous. Versions 0.1.39 to 0.1.47 were deactivated one at a time through the operator connector, each with a recorded digest, transaction ID and approval reference. Version 0.1.48 stays active, and S01, S03 and S08 pass against the live platform after the change.

The registry, routing and lifecycle controls behave as designed, and the test approach caught and recorded a real issue. It is a prototype: see "Honest scope" in [Section 1.3](#13-what-we-built-two-proofs).

#### C4. It works from different AI assistants

Five kinds of client were recorded: direct calls with no AI model (all suites above), Phoenix-recorded sessions (operator suite 20 of 20, guided demo 10 of 10), Claude Sonnet 5.5 and GPT-4.1 as agents (23 November, [Section 3.6](#36-agent-evaluation-in-phoenix-23-november-final)), a ChatGPT and Codex assistant (13 scenarios: 9 partial, 4 not scored) and an IBM watsonx Orchestrate agent (partial: 21 of the 44 tools exposed). The rows are in [Section 3.3](#33-run-by-run-record).

Microsoft Copilot Studio was also used during development and has no saved run in this package. The platform answered correctly from the clients that could reach it, but the agent changes the result: with the same tools, Claude matched 23 of 23 and GPT-4.1 17 of 23. How much of the platform an assistant can reach also depends on what that assistant exposes: the IBM limit was in the hosted agent, not the platform.

#### C5. The state of the data is reported separately from whether the code works

- All **3 of 3** data-coverage questions matched. Only some programs have prepared requirements. The others correctly return mapping_required or unknown.
- Improvement over time is now shown. Direct calls on the 23 questions matched 20 of 23 (17 November), 19 of 23 (21 November) and 23 of 23 (23 November). The question sets changed between runs, so this is progression, not a controlled trend ([Section 3.6](#36-agent-evaluation-in-phoenix-23-november-final)).

A gap in the data shows up as a data gap, not as broken code, and it can be closed by preparation work without a new deployment. The 23 November result came from that kind of fix: a data fix and a template version change, with expectations updated to match.

#### C6. It answers in reasonable time and gives the same result when repeated

| Kind of call | Calls | Median | Slowest |
|---|---|---|---|
| Eligibility steps through the router (find programs, check a person, save, read back) | 37 | 5.4 s | 7.3 s |
| Capability and template listings | 12 | 0.7 s | 2.1 s |
| Organization, route, schema and source lookups | 35 | 0.3 s | 0.6 s |
| All calls together | 84 | 0.7 s | 7.3 s |

Four runs of the full suites gave the same result each time. Lookups are fast. A full eligibility step takes about five seconds in the middle of the range and up to about seven at the slowest. [Section 3.2](#32-response-times) explains these numbers.

### 3.2 Response times

1. **Lookups are fast and eligibility steps are slower, by design.** A lookup reads something the platform already holds. An eligibility step reads a program's rules and the person's profile, checks one against the other and returns the cited sources, all in one step.
2. **Much of the waiting is on outside sources.** Our understanding is that most of the extra time in an eligibility step goes to reading official program pages and rules, which the platform does not control. The recorded files hold timing from the test computer and do not split out how much of each call was spent waiting on outside sources, so this is our explanation and not a measured split. The platform's own per-step timing is in Phoenix.
3. **This is a snapshot, not a promise.** The numbers are from one day, one deployment, a laptop over the public internet and fictional profiles. Measured on the server itself they would be lower. They are not a service-level commitment.

The 23-question run had a median of 5.1 s per call and a slowest of 6.7 s. A full suite takes about 3.5 minutes one test at a time and 150 seconds with four workers. Four workers were only about 1.4 times faster, because eligibility steps slowed under parallel load. This was a small parallel test, not a load test. The full timing tables are in the [performance report](PERFORMANCE-REPORT.md).

### 3.3 Run-by-run record

| Run (21 November 2026 unless noted) | Calls | Result |
|---|---|---|
| Full suites S01 to S11, one test at a time | 84 | 52 pass, 2 skipped, 0 failed |
| Same suites, three reruns (one with four workers at once) | 84 each | 52 pass, 2 skipped, 0 failed each time |
| Fast pre-check (lookup suites only) | 52 | 36 pass, 2 skipped, 0 failed |
| 23 questions, direct calls, no AI model | 22 | 19 of 23 match (17 November: 20 of 23) |
| 23 questions, direct-call check on the live platform (23 November) | 23 | 23 of 23 match |
| Phoenix agent evaluation, Claude Sonnet 5.5 and GPT-4.1 (23 November, final) | 23 per agent | Claude 23 of 23 exact; GPT-4.1 17 of 23 ([Section 3.6](#36-agent-evaluation-in-phoenix-23-november-final)) |
| Operator suite recorded in Phoenix (17 Nov) | 20 | 20 of 20 matched to a trace |
| Guided demo recorded in Phoenix (17 Nov) | 10 | 10 of 10 matched to a trace |
| Scenario coverage with a ChatGPT and Codex assistant (17 Nov) | 61 | 13 scenarios: 9 partial, 4 not scored |
| IBM watsonx Orchestrate agent, scenario S1 (20 Nov) | S1 | Partial: 21 of 44 tools exposed |

"Skipped" means a check that does not apply to that set-up. It is reported openly and not counted as a pass.

### 3.4 What went wrong

These are the 21 November findings. The question numbers (N09 and so on) refer to the questions listed in [`eval/datasets/README.md`](datasets/README.md). By 23 November all 23 direct-call questions matched ([Section 3.6](#36-agent-evaluation-in-phoenix-23-november-final)); the four misses were on the test side, not in the platform.


- **N11 and N17** were marked wrong on wording only. The expected answer for N11 is corrected in version 4 of the question set. N17 asks for the name without the acronym, so its expected answer is right and the runner's extraction needs the fix.
- **N09** expected an older template version. This was an out-of-date expectation, not a defect.
- **N16** had no answer because the test client did not get a result from the Core: on 17 November it sent a malformed request, and on 21 November it made no call. Asked directly on 22 November (Kansas household, income $34,000, St. Louis program), the Core returned `not_applicable` with reason `jurisdiction_mismatch`, the expected answer. The runner needs the fix; the Core does not.
- **The scenario-coverage run** was partial (9 of 13 scenarios, 4 not scored) and its trace match was not captured at the time.
- **Matching facts is not the same as a good experience.** A human-guided, end-to-end journey with the agent instructions in place felt much better than the automated end-to-end suites, even though the suites matched the facts. The suites had drifted toward a machine reading answers, when the platform is built to talk with a person. Getting them to pass took many rounds of fixing data and adapting the way the MCP guides a person so an AI-run suite could follow it. That exposed several user-experience elements and caused some early issues, which better tests then resolved. Part of this signal is in the team's observation of the journey and not in the saved data. Human-guided runs belong beside the scripted suites ([Section 5.3](#53-next-steps-toward-scale)).
- **The IBM agent run** reached only 21 of 44 tools, which blocked its other scenarios. The limit was in what that hosted agent exposed.
- **Trace matching:** 10 distinct correlation IDs from the latest 23-question run were matched to the platform's records after the run. The test page leaves the trace ID empty at the moment of capture, so older bundles show lower match counts exactly as recorded. Unmatched calls are reported, not filled in.

### 3.5 Source audit (added in version 2.2)

On 22 November we asked a second question of the same programs: does each rule the platform holds match what the official source says today? We re-read each source in a real browser and compared it with the stored rule. The scores in [Section 3.3](#33-run-by-run-record) were not changed by this audit.

- **Partial answers can look complete.** A result can say a person meets the rules it checked while most of a program's requirements are not mapped. The platform reports how many rules it could check, and a partly mapped result is not an eligibility finding.
- **One fix worked.** The Missouri food-assistance (SNAP) income limit by household size is now a stored, cited rule. A fictional household of four with $45,000 a year now returns residency met and income unmet against a $41,796 limit; before, the income limit was unknown.
- **One fix broke something, and we recorded it.** For the St. Louis housing voucher, a stored rule cited a page the platform could read while the program record still named a PDF it could not. The platform now refuses to evaluate that program (`criteria_link_conflict`). This is a data error from our own correction, not a product fault, and the repair is a data reload.
- **Sources disagree with themselves.** Examples: Missouri child care (150% of the poverty level on the program page, 85% of state median income in the manual) and a Missouri food-assistance net limit that differs between the web page and the linked PDF. We did not settle any of these by choosing a number; each is recorded as unresolved.

The platform gaps the audit exposed (it reads plain page text only, cannot correct a record whose source was wrong from the start, and does not check that a source looks the way it should) are design changes after submission. They are recommendations 9 to 11 in [Section 5.3](#53-next-steps-toward-scale).

### 3.6 Agent evaluation in Phoenix, 23 November (final)

Run `20261123-full-b`, platform Core 0.2.0 with Eligibility template 0.1.48, synthetic profiles, live platform. Method: [Section 2.7](#27-the-agent-evaluation-in-phoenix-added-in-version-23). Full tables and machine-readable figures: [results](results/phoenix-agent-eval/README.md).

| Question set | Agent | Exact match | Judge: correct |
|---|---|---|---|
| Platform capability (10) | Claude Sonnet 5.5 | 10 of 10 | 9 of 10 |
| Platform capability (10) | GPT-4.1 | 10 of 10 | 9 of 10 |
| Eligibility pattern (10) | Claude Sonnet 5.5 | 10 of 10 | 10 of 10 |
| Eligibility pattern (10) | GPT-4.1 | **4 of 10** | 4 of 10 |
| Data coverage (3) | Claude Sonnet 5.5 | 3 of 3 | 2 of 3 |
| Data coverage (3) | GPT-4.1 | 3 of 3 | 3 of 3 |
| **All 23** | **Claude Sonnet 5.5** | **23 of 23** | 21 of 23 |
| **All 23** | **GPT-4.1** | **17 of 23** | 16 of 23 |

**Progression.** The same questions were run on three dates. The 3 November run used earlier, smaller question sets, so it shows direction, not a like-for-like score.

| | 3 Nov (baseline, 20 questions) | 19 Nov (23 questions) | **23 Nov (23 questions, final)** |
|---|---|---|---|
| Direct calls, no AI model | not run | 20 of 23 on 17 Nov, 19 of 23 on 21 Nov | **23 of 23** |
| Claude Sonnet 5.5, exact match | 20 of 20 | 23 of 23 | **23 of 23** |
| GPT-4.1, exact match | 9 of 20 | 17 of 23 | **17 of 23** |
| Claude input tokens per question | about 107,000 | about 70,000 | **about 78,000** |

**What this shows**

- **Claude held 100% as the suite got harder**, from 20 questions to 23, while using about 27% fewer input tokens per question than on 3 November.
- **GPT-4.1 improved from 9 of 20 to 17 of 23, then stopped.** It scored 4 of 10 on the eligibility set on both 19 and 23 November. It answers `unknown` where Claude returns the exact outcome (`met`, `unmet`, `not_applicable`), once picked a housing-authority directory instead of the Housing Choice Voucher program, and once gave an empty answer. The tools were identical, so the gap is in the agent, not the platform.
- **Instructions matter, and models respond differently.** The agent instruction set was tuned weekly. These are team and user observations, not scored results. Several agents improved as instructions matured, including Claude and the IBM agent. GPT held, with some degradation as controls were added, and became stricter. Microsoft Copilot Studio degraded under the same additions in our set-up. Gemini was not scored. Experience also varied by person and by client. One untested explanation for GPT-4.1's 4 of 10 on eligibility is that tighter controls made it more conservative. A controlled run with minimal instructions is the test ([Section 5.3](#53-next-steps-toward-scale)).
- **Judge scores need care.** The AI judge marked one Claude platform answer and one coverage answer "not correct" although both matched exactly, which is judge variance. GPT-4.1's low "in scope" score on the platform set (1 of 10) is also a judge artifact: the other provider's judge treats questions about the platform's own capability map as out of scope. Exact match is the gate for this reason.
- **Cost sits in the eligibility set.** 1.49M of Claude's 1.80M input tokens (GPT-4.1: 0.72M in total) went to the ten eligibility questions, where search results are large. Timing per question is in the [performance report](PERFORMANCE-REPORT.md).
- **A free pre-check came first.** Before the paid run, a free check of the live platform passed 40 of 43 checks, including all 23 official questions. The other three were extra, non-official checks.

### 3.7 From a running server to a working journey

The platform itself was up in days. Making the designed journey work end to end took about five weeks. Early journeys were partial because program criteria were unmapped or wrong, not because the pattern failed. Each fix to data, mapping rules, template version, agent instructions or code was followed by a rerun of the same questions, which is the progression in [Section 3.6](#36-agent-evaluation-in-phoenix-23-november-final). The lesson is that deploying a server and getting a person's journey to work as designed are different efforts, and most of the second is data and configuration, which is time-bound and repeatable. At scale that work becomes a pipeline run with program-area experts, not a continuous manual loop.

## 4. Security Considerations

The central mission risk is telling someone they qualify, or do not, when the source does not say so. The first four points below address that risk. The rest cover data, credentials and the sandbox.

- **No rule without a source.** A rule exists only when a cited clause on an approved source page supports it, and the excerpt must appear verbatim in the source.
- **No invented answers.** Unknown or unprepared requirements return unknown or mapping_required with the exact fact needed. The platform does not guess and does not issue a determination.
- **Informational only.** Results carry the flag official_eligibility_determination=false.
- **Hostile input.** User text and tool output are treated as untrusted. Negative and edge tests cover refusals and missing data. A dedicated test for attempts to trick the assistant (prompt injection) is not part of this package. It is the first recommendation in [Section 5](#5-lessons-learned-and-recommendations).
- **Data.** Only public program information and fictional people are used. No personal or controlled data appears in questions, test data, traces or saved results.
- **Administrator functions** need a separate credential and refuse to run without it. Suite S02 tests a missing credential, a wrong one and the right one on every run.
- **Secrets.** No passwords or keys are stored in the repository. Settings are read from files or environment values at run time, and the example file lists names only. The test script never prints or saves the administrator credential.
- **Outside sources are bounded.** The platform reads only public secure web addresses without embedded credentials, checks where each address really points, follows a few redirects and re-checks each one, applies time and size limits and returns sanitized failures.
- **Audit record.** Every routed request records metadata only: the correlation ID, route, operation, status, timing and error class. It does not store request contents or personal information.
- **Sandbox substitutes.** The demonstration runs in a sandbox, and each substitute control is named. Purpose-limited tokens stand in for enterprise sign-in. A shared reader is limited by active organization, not by a separate credential for each. The demonstration test page and Phoenix are open by design so reviewers can inspect them. A production system adds enterprise sign-in, a credential per organization, retention rules and access controls before any real person's data is allowed ([Architecture, Part 4](../docs/ARCHITECTURE.md#part-4-pathway-to-scale)). Posture and limits: [`SECURITY.md`](../SECURITY.md).

## 5. Lessons Learned and Recommendations

### 5.1 What worked

- **Standing up versus working end to end.** The server was live in days, and the journey took weeks of data and configuration work, which is repeatable ([Section 3.7](#37-from-a-running-server-to-a-working-journey)).
- **Measuring separately.** Keeping code, data, outside sources and assistants apart meant a data gap never hid a working piece of code.
- **Scripted checks on structure, not wording.** Four runs on one day gave the same 52 passes and 2 skips, so there is no judge whose opinion can drift. The agent run keeps exact match as the gate for the same reason.
- **Rerunning the same questions on three dates.** With traces on every run, it showed what changed: a data fix and a template version change moved the result, Claude held 100%, and GPT-4.1 did not follow.
- **The tests found a real problem.** The ten-active-versions finding in [Section 3.1](#31-results-by-claim) was caught by a suite, fixed with a recorded approval trail and re-tested.
- **A record for every call.** Matching each call to its trace turns a pass or fail into something a reviewer can follow, and the saved files can be re-checked after the live environment is gone.
- **Specific errors.** An error that says what is missing or which argument is wrong costs an assistant fewer retries than a generic failure.

### 5.2 Demo scope, and what an enterprise team would add

The demonstration shows the capability. It was built in a compressed schedule on shared resources, so several things were done the quick way that an enterprise team would do differently.

- **Scale and load.** One day of scripted runs on one deployment, fictional people and no load test. Scale is the designed pathway in [Architecture, Part 4](../docs/ARCHITECTURE.md#part-4-pathway-to-scale).
- **Program coverage.** Only some programs have prepared requirements. Others correctly return mapping_required or unknown.
- **Question set and rules.** The question set is small and was written by the team from public sources, and 8 personas were exercised in the recorded operator run. With more access to resources, program-area experts would write and review the questions and the rules.
- **Baseline.** No baseline was run, so the benefit over an unaided AI assistant is not quantified.
- **Agents.** The agent run covers two agents, one run each, scored partly by AI judges. The 3 November run used different question sets, so it shows progression, not a controlled trend. Hosted assistants differ in which tools they expose, so results do not carry from one assistant to another.
- **Source checks.** Done by a person with a browser, once, on 22 November. The platform does not yet verify that a source has the right shape, date and publisher ([Section 3.5](#35-source-audit-added-in-version-22)).

### 5.3 Next steps toward scale

1. Add tests for attempts to trick the assistant, and for out-of-scope requests, and report them separately.
2. Run the same 23 questions with an AI assistant and no platform, to show what the platform adds.
3. Measure the platform's own share of each response as total time minus time spent reading outside sources.
4. Grow the question set by type and difficulty, have program-area experts review the questions and rules, and run all personas, not a sample.
5. Pair every automated suite with a human-guided journey, and record what people experience as well as what matches.
6. Keep rerunning the questions after each data revision, as done on 3, 19 and 23 November, so improvement stays visible.
7. Repeat each query several times and report consistency, not a single pass.
8. Run a load test before any claim about many users at once.
9. Have a person review failures before trusting any automated score.
10. Add source-shape checks: expected sections and tables, value types, effective date and publisher, checked on every read.
11. Add a path to correct a record whose source was wrong, and refuse to store a rule whose cited address differs from its record.
12. Add shared data tools (render a page, extract PDF text and tables, select a section, compare two readings) so sources the platform cannot read today can be checked by the platform.
13. Find out why GPT-4.1 scores 4 of 10 on the eligibility questions with the same tools, starting with a controlled run using minimal instructions, and run more agents and clients the same way.

The platform steps come from [Architecture, Part 4](../docs/ARCHITECTURE.md#part-4-pathway-to-scale): a managed database in place of the Persistence Access API, enterprise identity with per-user authorization, an adapter per channel, more patterns and per-organization tenants, and multi-instance operation with quotas and metering. Each keeps the layer boundary and contract, so these results carry over.

## Appendix A: Full glossary

| Term | What it means here |
|---|---|
| MCP (Model Context Protocol) | A common way for AI assistants to connect to outside tools and data, so one tool can work with many assistants. |
| MCP server | A program that offers tools to AI assistants over MCP. |
| AI assistant or client | The chat app or agent a person uses, such as Claude, ChatGPT, Microsoft Copilot or an IBM agent. It connects to the server and uses its tools. |
| The platform (the "Core") | The central server we built. It keeps a register of tools, decides who may use them and sends each request to the right place. |
| Pattern | A reusable recipe for one kind of problem. Eligibility screening is the first pattern: take a need, find programs, check a person against the requirements. |
| Template | The saved definition of a pattern that the platform loads. |
| Tenant | One organization's own set-up (its programs, sources and rules) on the shared platform. No separate copy of the server is needed. |
| Sprawl | Many separate servers built by many teams with duplicated work, different standards and weak oversight. |
| Eligibility screening | Comparing a person's facts with a program's published requirements. It is information only. It is not an official decision. |
| Binder | A saved package for one person and one program: steps, forms, documents, deadlines and who to contact, with the sources cited. |
| Fictional (synthetic) person | A made-up person used for testing. No real person's data is used anywhere. |
| Mapped requirements | A program's written requirements turned into checks a computer can run. If they are not prepared yet, the platform answers mapping_required. If a needed fact about the person is missing, it answers unknown and asks for that fact. |
| Test suite | A scripted list of checks that run automatically and record what happened. |
| Persona | A fictional person used as a test case, with the outcome we expect written down in advance. |
| Positive, negative, edge tests | Positive: correct when asked correctly. Negative: refuses when it should. Edge: does not guess when information is missing. |
| Correlation ID | A unique tag on each request. It lets us find the matching record in the tracing tool afterward. |
| Phoenix, trace and span | Arize Phoenix is a tool that records what happens inside the platform. A trace is the record of one request. A span is one step inside it. |
| Median and slowest | The median is the middle response time: half the calls were faster. The slowest is the longest single call. |
| Latency | The waiting time between asking and getting an answer. |
| Baseline | A reference point to compare against, for example the same questions answered by an AI with no tools. |
| Parallel workers | Running several tests at the same moment instead of one after another. |

## Appendix B: Where the tests are

The recorded results for every row are under [`eval/results/`](results/).

| # | Test group | What it proves | Where to look | Claim |
|---|---|---|---|---|
| 1 | S01 capability map | Any assistant can read the platform; no private addresses exposed | [`eval/operator/suites/`](operator/suites/) | C3 |
| 2 | S02 administrator credentials | Admin functions fail closed | [`eval/operator/suites/`](operator/suites/) | C3 |
| 3 | S03 register consistency | Templates, tenants and routes agree | [`eval/operator/suites/`](operator/suites/) | C3 |
| 4 | S04 unknown routes | Clean refusals; checking saves nothing | [`eval/operator/suites/`](operator/suites/) | C3 |
| 5 | S05 full-profile people | Screening matches expected outcomes | [`eval/operator/suites/`](operator/suites/) | C1, C2 |
| 6 | S11 sparse-profile people | Same people, general profiles | [`eval/operator/suites/`](operator/suites/) | C1, C2 |
| 7 | Data-coverage questions (3) | What the inventory holds for a need and place | [`eval/datasets/data-coverage-eval-1.csv`](datasets/data-coverage-eval-1.csv) | C5 |
| 8 | Eligibility questions (10) | Right outcome including "unknown" | [`eval/datasets/eligibility-pattern-eval-4.csv`](datasets/eligibility-pattern-eval-4.csv) | C1 |
| 9 | S08 published versus behavior | The platform describes itself correctly | [`eval/operator/suites/`](operator/suites/) | C3 |
| 10 | Platform questions (10) | Capability map, routes, tenants, source policy | [`eval/datasets/platform-capability-eval-1.csv`](datasets/platform-capability-eval-1.csv) | C3 |
| 11 | Phoenix-recorded runs | Calls match the platform's own traces | [`eval/results/replay-bundles/`](results/replay-bundles/), [`eval/results/phoenix-screenshots/`](results/phoenix-screenshots/) | C2, C4 |
| 12 | Assistant runs (IBM agent) | What a hosted agent can reach | [`eval/results/agent-runs/`](results/agent-runs/) | C4 |
| 13 | Test-page chats (7 scenarios) | Scripted chats on the hosted test page. The test page is a fixture and is not part of the submission | [`eval/results/navigator-chat-runs/`](results/navigator-chat-runs/) | C2, C4 |
| 14 | Timing and repeat runs | Response time and consistency | [`eval/results/operator-runs/`](results/operator-runs/) | C6 |
| 15 | 23 fictional people | The test cases for rows 5 and 6 | [`eval/synthetic-datasets/`](synthetic-datasets/) | C1 |
| 16 | Phoenix agent evaluation, 23 November | Claude Sonnet 5.5 and GPT-4.1 on the 23 questions, with progression from 3 and 19 November | [`eval/results/phoenix-agent-eval/`](results/phoenix-agent-eval/README.md) | C1, C3, C4, C5 |

## Appendix C: Saved evidence

### Reading order

1. This document (1 of 3), for the data and what it means.
2. [Evidence summary](EVIDENCE-SUMMARY.md) (2 of 3): what is in the evaluation folder and the tests by type.
3. [Performance report](PERFORMANCE-REPORT.md) (3 of 3): the timing data in plain terms.
4. [`eval/results/README.md`](results/README.md): the guide to the recorded runs and their known limits.
5. [`eval/results/phoenix-agent-eval/README.md`](results/phoenix-agent-eval/README.md): the 23 November agent evaluation, with the full tables.
6. The human summary of the full scripted run: [`eval/results/operator-runs/OPS-20261121-1113-full-Summary.md`](results/operator-runs/OPS-20261121-1113-full-Summary.md).
7. The raw JSON, only if you want to check a single call.

### What the files are

| File pattern | What it holds |
|---|---|
| `*-Input.json` | What was asked: every request in the run |
| `*-Response.json` | What came back, in the platform's own words |
| `*-Trace.json` | A record of every call: its correlation ID, how long it took and its result |
| `*-Summary.md` | A short human-readable summary of the run |
| replay-bundles/ | Eight recorded runs in one format, with trace joins where available |
| agent-runs/ | The IBM agent run and what blocked other scenarios |
| phoenix-screenshots/ | Screenshots of Phoenix showing spans, an annotated trace and the 23 November comparison view |
| phoenix-agent-eval/ | The 23 November agent evaluation: a readable results page and the figures as JSON |

### How to check it yourself

Run `python3 eval/results/verify_bundles.py`. It reads the saved runs and prints, for each one, the cases, calls, correlation IDs and matched trace IDs with a short hash. It needs no network and no keys. Calls that were not matched to a trace are reported as they are, not filled in. A "skipped" check means it did not apply to that set-up and is not counted as a pass. Results are dated and regenerated for each release.

## Appendix D: Repository map for the technical side

Code quality and MCP design are judged from the repository: [`README.md`](../README.md) and [`docs/README.md`](../docs/README.md).

| Repository part | What it shows |
|---|---|
| [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) | Vision, enterprise design, the demonstration and the pathway to scale |
| [`docs/USE-CASES.md`](../docs/USE-CASES.md) | How 49 use cases became one pattern and one platform |
| [`docs/ELIGIBILITY-PATTERN.md`](../docs/ELIGIBILITY-PATTERN.md) | Why eligibility, the use cases, the user journey |
| [`docs/DATA-AND-SOURCES.md`](../docs/DATA-AND-SOURCES.md) | How public sources become searchable; rule sets |
| [`docs/CONNECT.md`](../docs/CONNECT.md) | How AI clients connect |
| [`docs/EVALUATION.md`](../docs/EVALUATION.md) | The test and validation framework |
| [`docs/DEVELOPMENT.md`](../docs/DEVELOPMENT.md) | The AI team, the human team and the method |
| [`src/example_server/`](../src/example_server/) (core files) | The Core router; it holds no program logic |
| [`configs/templates/`](../configs/templates/) | Versioned patterns, including the second minimal directory pattern |
| [`configs/eligibility/`](../configs/eligibility/), [`configs/registry/`](../configs/registry/) | Tenant configuration, without forking the server |
| [`tests/`](../tests/) | The code test suite |
| [`deploy/`](../deploy/), [`Dockerfile`](../Dockerfile) | Container-based deployment |
| [`SECURITY.md`](../SECURITY.md) | Security posture and sandbox limits |
| [`QUICKSTART.md`](../QUICKSTART.md) | Install, run the tests, run the local smoke test |
| [`eval/`](./) | Questions, fictional people, suites and recorded runs |

## Appendix E: Sources

Hackathon deliverables and judging criteria, from the event pages on gsa.gov (checked 21 November 2026):

- https://www.gsa.gov/artificial-intelligence/ai-community-of-practice/events-and-training/2026-ai-hackathon
- https://www.gsa.gov/mcp-server-and-si-agent-government-hackathon

Baseline research for [Section 2.6](#26-about-baselines-what-we-searched-for-and-what-we-found), searched 21 November 2026:

- MCP-AgentBench: https://arxiv.org/pdf/2509.09734v1
- MCP-Atlas: https://arxiv.org/html/2602.00933v1
- MCP-Bench: https://openreview.net/pdf/4772e5bcb331f428afb2532379d1ff5da4e3a5f0.pdf
- MCPEval: https://arxiv.org/pdf/2507.12806v2
- GitHub, offline evaluation of the GitHub MCP Server: https://github.blog/ai-and-ml/github-copilot/measuring-what-matters-how-offline-evaluation-of-github-mcp-server-works/
- Code-search MCP server benchmark with a no-server baseline: https://dev.to/luuuc/how-do-you-benchmark-an-mcp-server-you-built-2e8j
- BeNYfits (public benefits eligibility benchmark): https://arxiv.org/html/2502.19610v3
- Public Benefits Bench (Vals AI): https://www.vals.ai/benchmarks/public-benefits-bench-v1
- MCP gateway performance test plan (IBM MCP Context Forge): https://glama.ai/mcp/servers/@IBM/mcp-context-forge/blob/2fdac0c014d2fb0726ccbccdf12631f5216a9149/tests/performance/PERFORMANCE_STRATEGY.md
- Review of MCP gateways noting unstated benchmark methods: https://dev.to/sahajmeet_kaur_/what-i-learned-evaluating-five-mcp-gateways-for-production-2clg

---

[Previous: Evaluation folder index](README.md) · [Documentation index](../docs/README.md) · [Next: Evidence summary](EVIDENCE-SUMMARY.md)
