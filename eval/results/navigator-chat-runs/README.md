# Navigator chat runs

> Version 1.0 · Updated 2026-11-21 · Folder index  
> [Documentation index](../../../docs/README.md)

Scripted chats with the Navigator's eligibility assistant on the hosted test page, driven by a browser agent playing a fictional person. Each run is three files named `UC#-S#-E2E-Run#-<Positive|Negative|Edge>-<Input|Response|Trace>.json`: what was sent, what came back, and the identifiers and checks. They are the browser-side companion to the call-level runs in [operator-runs](../operator-runs/README.md).

| Read | What it holds |
|---|---|
| `UC1-S1-E2E-Run1-Positive-*.json` | Beginning farmer in Missouri: classification, not approval |
| `UC1-S2-E2E-Run1-Edge-*.json` | Kansas drought chat where disaster criteria are not mapped and the person is unsure of county and farm number |
| `UC1-S3-E2E-Run1-Edge-*.json` | New Mexico pecan orchard asking for water-saving cost-share; conservation criteria not mapped |
| `UC2-S1-E2E-Run1-Edge-*.json` | Caregiver chatting for an older mother in Missouri, income withheld |
| `UC2-S2-E2E-Run1-Edge-*.json` | Missouri veteran needing daily care at home, who withholds disability rating and healthcare enrollment |
| `UC3-S1-E2E-Run1-Edge-*.json` | Missouri food-assistance chat where the person declines to share income or household size |
| `UC3-S2-E2E-Run1-Edge-*.json` | St. Louis renter at risk of losing housing; the third turn got no reply, recorded as a failed check |

Seven of the nine scenarios are recorded. UC2-S3 (caregiver is the one chatting, second variant) and UC3-S3 (family stability) were not run because the browser-agent budget for this pass ran out; their expectations are in [expected-outcomes.json](../../synthetic-datasets/expected-outcomes.json) and the call-level coverage is in [replay-bundles](../replay-bundles/).

The replies are summarized, not word for word. The browser agent's full capture stays in its run record, whose ID is in each Trace file. Each Trace file lists the Phoenix spans in `usda-core-native` joined on `mcp.correlation.id`, only for IDs the browser agent read from the page. Each joined span carries a `navigator-chat-run` annotation.

---

[Documentation index](../../../docs/README.md)
