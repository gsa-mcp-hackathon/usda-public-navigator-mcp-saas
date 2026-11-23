# Synthetic datasets

> Version 2.0 · Updated 2026-11-21 · Guide  
> [Documentation index](../../docs/README.md) · [Evaluation evidence](../EVIDENCE-SUMMARY.md)

Fictional test people for the Eligibility proof. **Every value here is made up.** Do not add or use real personal data.

| File | What it is |
|---|---|
| `starter-profiles.json` | What each person knows or says at the start. Deliberately incomplete. |
| `expected-outcomes.json` | The end-state profile, the expected programs and criterion results, and the "should" and "must not" behaviors. |
| `demo-set.json` | The small product-demo set: a positive, a negative and an edge persona per scenario where the dataset has one. [`tests/test_demo_set.py`](../../tests/test_demo_set.py) checks it. |

There are 23 people: 18 edge cases, 3 positive and 2 negative. Testing starts from the incomplete profile. The platform has to ask for what is missing and save it after confirmation, and the stored profile is then compared with the expected end state. The generator that built these files is in the development repository.

---

[Documentation index](../../docs/README.md) · [Evaluation folder](../README.md)
