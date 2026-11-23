# Phoenix agent evaluation, final run (23 November 2026)

Two AI agents, Claude Sonnet 5.5 and GPT-4.1, answered the same 23 fixed questions using only the platform's read-only tools. Each answer was scored against a stored expected answer (exact match) and by a judge from the other provider. Every question is an experiment run in Phoenix, linked to a trace of its model and tool calls with token counts.

Run `20261123-full-b`. Core 0.2.0, Eligibility template 0.1.48, synthetic profiles, live platform.

| Question set | Agent | Exact | Correct (judge) | In scope (judge) | Tool path (judge) | Calls | Input tokens | Output tokens |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Platform capability (10) | Claude Sonnet 5.5 | 10/10 | 9/10 | 10/10 | 10/10 | 11 | 107,929 | 549 |
| Platform capability (10) | GPT-4.1 | 10/10 | 9/10 | 1/10 | 9/10 | 9 | 39,157 | 185 |
| Eligibility pattern (10) | Claude Sonnet 5.5 | 10/10 | 10/10 | 10/10 | 6/10 | 81 | 1,492,419 | 9,602 |
| Eligibility pattern (10) | GPT-4.1 | 4/10 | 4/10 | 9/10 | 5/10 | 77 | 545,576 | 1,796 |
| Data coverage (3) | Claude Sonnet 5.5 | 3/3 | 2/3 | 3/3 | 3/3 | 16 | 200,739 | 1,864 |
| Data coverage (3) | GPT-4.1 | 3/3 | 3/3 | 3/3 | 3/3 | 17 | 134,577 | 549 |

**Totals:** Claude Sonnet 5.5 matched **23 of 23** expected answers; GPT-4.1 matched **17 of 23**.

## Three runs

| | 3 Nov (earlier, smaller sets, 20 questions) | 19 Nov (23 questions) | **23 Nov (23 questions)** |
|---|---|---|---|
| Claude Sonnet 5.5, exact | 20/20 | 23/23 | **23/23** |
| GPT-4.1, exact | 9/20 | 17/23 | **17/23** |

The 3 Nov run used earlier question sets, so it shows progression and is not a like-for-like score.

## How to read it

- Exact match is the gate. Claude matched every expected answer after the weekend's data fix and a template version change.
- With the same tools, GPT-4.1 scored the same 4 of 10 on the eligibility pattern on 19 and 23 Nov. It answers `unknown` where Claude returns the exact outcome (`met`, `unmet`, `not_applicable`), picked a directory instead of the Housing Choice Voucher program on one search, and had one empty answer. The gap is in the agent, not the platform.
- The judge's "correct" score marked one platform answer and one coverage answer for Claude as not correct although both matched exactly; this is judge variance.
- GPT-4.1's platform scope score of 1/10 is a judge artifact: the Claude judge rates questions about the platform's own capability map as out of scope. Scope scores on that set are not comparable across judges.
- Cost is dominated by the eligibility set (search results are large): 1.49M of Claude's 1.80M input tokens.
- Median seconds per question: Claude 4.0 (platform), 24.7 (eligibility), 17.6 (coverage); GPT-4.1 2.4, 13.5, 14.9.

Machine-readable figures: [RESULTS.json](RESULTS.json).
