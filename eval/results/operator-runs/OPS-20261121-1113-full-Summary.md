# Operator automated test run R20261121-161343

> Version 1.0 · Updated 2026-11-21 · Record
> [Documentation index](../../../docs/README.md)


- Started (UTC): 2026-11-21 16:13:43
- Core endpoint: usda-ocio-smo-core (host shortened)
- Personas file: expected-outcomes.json
- Totals: PASS 52, SKIP 2
- Persona coverage: 8 exercised, 30 deferred, 38 total

| Suite | Objective | Result |
| --- | --- | --- |
| S01 core-bootstrap | Any client can connect and read the capability map; no private endpoints exposed | PASS |
| S02 operator-auth-fail-closed | Operator tools refuse missing or wrong credentials and accept the valid one | PASS |
| S03 registry-and-routes | Registered templates, tenants and routes are consistent and complete | PASS |
| S04 routing-and-validation-contract | Core rejects unknown routes and validates without persisting | PASS |
| S05 persona-outcomes | Core positive, negative, missing-fact and boundary personas produce source-cited criterion results | PASS |
| S08 wiring-and-contract-drift | Registry wiring and published contracts match runtime behavior | PASS |
| S11 general-profile-outcomes | The Core persona oracle holds when each persona is sent as a sparse general profile instead of a demo Navigator profile | PASS |

## Test results

| Test | Title | Result | Defect | Attempts | ms |
| --- | --- | --- | --- | ---: | ---: |
| S01-T01 | Tool list exposes required Core read and dispatch tools | PASS |  | 1 | 614 |
| S01-T02 | Capability map is available | PASS |  | 1 | 2420 |
| S01-T03 | Private endpoints are not exposed | PASS |  | 1 | 908 |
| S01-T04 | Route, template and tenant registries are available | PASS |  | 1 | 859 |
| S01-T05 | Operator and client workflows are published | PASS |  | 1 | 856 |
| S01-T06 | Progressive discovery workflow resolves end-user path without required profile | PASS |  | 1 | 522 |
| S02-T01 | Fleet report without credential is refused | PASS |  | 1 | 448 |
| S02-T02 | Fleet report with a wrong credential is refused | PASS |  | 1 | 478 |
| S02-T03 | Profile read without credential returns nothing (not found in scope) | PASS |  | 1 | 489 |
| S02-T04 | Evaluation fixture inventory without credential is refused | PASS |  | 1 | 457 |
| S02-T05 | Evaluation fixture read without credential is refused | PASS |  | 1 | 463 |
| S02-T06 | Fleet report with the valid credential passes authorization | PASS |  | 1 | 633 |
| S03-T00 | Exactly one active eligibility-demo tenant binding; capture its template version | PASS |  | 1 | 1793 |
| S03-T01 | Eligibility template the active tenant version is active | PASS |  | 1 | 2596 |
| S03-T02 | Tenant eligibility-demo is active on the active tenant version with the four bound sources | PASS |  | 1 | 612 |
| S03-T03 | Tenant route found for eligibility-demo | PASS |  | 1 | 1928 |
| S03-T04 | Every operation published by the tenant binding is routed | PASS |  | 1 | 1926 |
| S03-T05 | Every active template has a declared input schema reference | PASS |  | 1 | 1637 |
| S03-T06 | Legacy tenantless discover/evaluate routes are retired | PASS |  | 1 | 1625 |
| S03-T07 | Active inventory-search schema (V3) resolves through the live schema registry | PASS |  | 1 | 536 |
| S03-T08 | Only the current inventory-search route version is active | PASS |  | 1 | 1669 |
| S03-T09 | Bound source metadata is readable: grants.gov.opportunities | PASS |  | 1 | 576 |
| S03-T10 | Bound source metadata is readable: kansas.dcf.food-assistance-residency | PASS |  | 1 | 559 |
| S03-T11 | Bound source metadata is readable: missouri.dss.snap | PASS |  | 1 | 525 |
| S03-T12 | Bound source metadata is readable: usda.fsa.beginning-farmers | PASS |  | 1 | 560 |
| S03-T13 | Source-grounded requirements preview returns without writing | PASS |  | 1 | 6412 |
| S03-T14 | Exact Eligibility cluster capability publishes the five tenant operations | PASS |  | 1 | 695 |
| S03-T15 | Unknown cluster template version fails closed | PASS |  | 1 | 625 |
| S03-T16 | Approved public-search source policy is active | PASS |  | 1 | 627 |
| S04-T01 | Unknown route returns unknown_route | PASS |  | 1 | 658 |
| S04-T02 | Unpackaged descriptor (no immutable artifact reference) is rejected without persisting | SKIP |  | 1 | 316 |
| S04-T03 | Empty tenant draft is rejected without persisting | PASS |  | 1 | 588 |
| S04-T04 | Validation rejection explains why (findings returned) | SKIP |  | 1 | 307 |
| S05-P01 | synthetic-farmer-mo-beginning (positive) | PASS |  | 1 | 12235 |
| S05-P02 | synthetic-farmer-mo-veteran-new (positive) | PASS |  | 1 | 11419 |
| S05-P03 | synthetic-household-mo-kc-family (positive) | PASS |  | 1 | 10918 |
| S05-P04 | synthetic-farmer-mo-established (negative) | PASS |  | 1 | 11981 |
| S05-P05 | synthetic-household-ks-kc-family (negative) | PASS |  | 1 | 11067 |
| S05-P06 | synthetic-farmer-mo-unknown-years (edge) | PASS |  | 1 | 9973 |
| S05-P07 | synthetic-farmer-mo-boundary-10 (edge) | PASS |  | 1 | 12474 |
| S05-P08 | synthetic-farmer-mo-boundary-11 (edge) | PASS |  | 1 | 10727 |
| S08-T01 | Exactly one active eligibility template (no stale versions advertised) | PASS |  | 1 | 1497 |
| S08-T02 | General profile schema is published on the active template | PASS |  | 1 | 644 |
| S08-T03 | Inventory search browses with no need phrase (schema and runtime agree) | PASS |  | 1 | 7603 |
| S08-T04 | Out-of-range argument returns a structured validation result, not bare unavailable | PASS |  | 1 | 992 |
| S08-T05 | Candidate normalize accepts arguments shaped like its published schema | PASS |  | 1 | 1032 |
| S11-P01 | synthetic-farmer-mo-beginning (positive) | PASS |  | 1 | 11257 |
| S11-P02 | synthetic-farmer-mo-veteran-new (positive) | PASS |  | 1 | 12090 |
| S11-P03 | synthetic-household-mo-kc-family (positive) | PASS |  | 1 | 10579 |
| S11-P04 | synthetic-farmer-mo-established (negative) | PASS |  | 1 | 10316 |
| S11-P05 | synthetic-household-ks-kc-family (negative) | PASS |  | 1 | 11062 |
| S11-P06 | synthetic-farmer-mo-unknown-years (edge) | PASS |  | 1 | 10150 |
| S11-P07 | synthetic-farmer-mo-boundary-10 (edge) | PASS |  | 1 | 10960 |
| S11-P08 | synthetic-farmer-mo-boundary-11 (edge) | PASS |  | 1 | 10879 |

## Scenario coverage not exercised

- **UC1-S1 · safety** (S05-P09): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC1-S2 · disaster criteria not mapped** (S05-P10): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC1-S3 · conservation criteria not mapped** (S05-P11): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC2-S1 · criteria not mapped** (S05-P12): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC2-S2 · VA criteria not mapped** (S05-P13): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC2-S3 · caregiver is chatting** (S05-P14): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC2-S1 · Maryland heating need** (S05-P15): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC3-S2 · housing criteria not mapped** (S05-P16): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC3-S3 · family-stability criteria not mapped** (S05-P17): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC3-S1 · question burden** (S05-P18): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC3-S1 · rule not in catalog** (S05-P19): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC3-S1 · correction** (S05-P20): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC3-S1 · declines to answer** (S05-P21): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC3-S1 · Maryland** (S05-P22): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC3-S1 · New Mexico tribal (schema gap)** (S05-P23): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC1-S1 · safety** (S11-P09): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC1-S2 · disaster criteria not mapped** (S11-P10): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC1-S3 · conservation criteria not mapped** (S11-P11): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC2-S1 · criteria not mapped** (S11-P12): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC2-S2 · VA criteria not mapped** (S11-P13): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC2-S3 · caregiver is chatting** (S11-P14): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC2-S1 · Maryland heating need** (S11-P15): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC3-S2 · housing criteria not mapped** (S11-P16): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC3-S3 · family-stability criteria not mapped** (S11-P17): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC3-S1 · question burden** (S11-P18): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC3-S1 · rule not in catalog** (S11-P19): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC3-S1 · correction** (S11-P20): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC3-S1 · declines to answer** (S11-P21): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC3-S1 · Maryland** (S11-P22): edge fixture has no approved deterministic outcome in the current source/rule scope
- **UC3-S1 · New Mexico tribal (schema gap)** (S11-P23): edge fixture has no approved deterministic outcome in the current source/rule scope

Read-only run. No credentials, endpoints or profile values are recorded.

[Documentation index](../../../docs/README.md) · [Operator runs](README.md)
