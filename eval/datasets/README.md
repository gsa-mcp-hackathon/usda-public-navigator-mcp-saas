# Question sets

> Version 1.0 · Updated 2026-11-22 · Folder index  
> [Documentation index](../../docs/README.md) · [Evaluation folder](../README.md)

The 23 questions with expected answers that the evaluation asks of the platform. Each row has the question, the expected answer, a category, and the tool or routed operation that should answer it (`expected_tool_path`). A question that is answered without using the server fails.

| File | Questions | What it asks |
|---|---|---|
| `platform-capability-eval-1.csv` | 10 | The platform alone: capability map, route inventory, unknown routes, tenants, source policy. No program content |
| `eligibility-pattern-eval-4.csv` | 10 | The Eligibility pattern: a stated household or farm situation and the expected outcome, including cases where a fact is unknown |
| `data-coverage-eval-1.csv` | 3 | Catalog coverage: what the program inventory holds for a need and a place |

The file name ends in the set version. The recorded run of these questions is in [`../results/`](../results/README.md). How they are scored is in [the evaluation guide](../../docs/EVALUATION.md).

## Version 4 of the Eligibility set, and the negative test designed for the rerun

The 2026-11-17 run scored N11 and N17 as failures, and the 2026-11-21 rerun did too. Both are naming differences, not wrong answers: the Core returns the catalog's official title with its acronym ("Housing Choice Voucher (HCV) Program", "Environmental Quality Incentives Program (EQIP)"). Version 4 corrects N11's expected answer to the official title. N17 is not changed: its question asks for the name "without the acronym", so its expected answer is right and the runner's extraction should strip the acronym; that fix belongs in the runner, which is not in this package. N16 is a test-client miss, not a Core miss. On 17 November the client sent a malformed `program.evaluate` request (the Core answered `invalid_request`); on 21 November it made no call. Asked directly on 2026-11-22 with a Kansas profile, the Core returned `not_applicable` with reason `jurisdiction_mismatch`, which is the expected answer. N09 failed only in the rerun: it expects template version 0.1.47 as active and the live Core now reports 0.1.48, so the expected value follows the deployed state and needs a decision before the next run.

The match stays strict: an answer passes only when it equals the expected official name. So a similar but wrong program must still fail. The rerun adds two designed negative cases, not yet run:

| Case | Question asks | Expected | Why it is there |
|---|---|---|---|
| NEG-A | Search the Missouri inventory for a program named "Federal Dragon Subsidy" and name the top result. | `not_found`: the Core reports no such program and does not invent one | An answer that names any program fails. Proves the platform declines to make one up |
| NEG-B | The same HUD voucher question as N11, scored against a different real program name ("Section 8 Moderate Rehabilitation") | fail | Proves the strict match rejects a plausible wrong answer |

---

## Template-version cleanup (2026-11-21)

Ten Eligibility template versions (0.1.39 to 0.1.48) were all active, which failed S08-T01 and made the "which version is active" question (platform-capability-eval-1, row 10) ambiguous. Using the operator connector, versions 0.1.39 to 0.1.47 were deactivated one at a time, each with its descriptor digest, a before-state digest, a transaction ID and an approval reference, so each change is recorded in the Core's lifecycle log. Version 0.1.48 stays active and the tenant binding is unchanged. The expected answer in row 10 is now 0.1.48, and S01, S03 and S08 pass against the live Core after the change.

[Documentation index](../../docs/README.md) · [Evaluation folder](../README.md)
