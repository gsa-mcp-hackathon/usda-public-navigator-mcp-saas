# Package changes: v6 -> v7.1

v7.1 = the team-built v6 package, refreshed from current `main` (a97c3ae).

## Added from main
- Follow-up loop: workflow `navigator_discovery_then_question` v1.1.0 (`follow_up_loop`)
- Geography eligibility cluster + tests
- Route-event package id (migration 047) + tests
- Source-policy candidates (public-search domains 1.2.3 to 1.2.5)
- Submission seed data + README

## Updated (3-way merged, v6 decisions kept)
- core_app, eligibility_discovery, eligibility_public_search, vm-data-api, Phoenix eval client/harness/preflight, public-sources config, related tests

## Deliberately unchanged from v6
- No LICENSE; README/strategy edits; Navigator app excluded
- Phoenix dependencies decoupled; vendor-agnostic; COMPONENTS.md; bare-bones design

## Left out (need schemas v6 dropped)
- Profile schema projector and its tests, core-v2 and eligibility-program-v1 schemas

## Verification
- 1,128 tests passed, 31 skipped; sanitize scan clean

## Added in v7.2: agents/ folder (master v8, platform files, knowledge files K1 to K6), listed in AGENTS.md.
- agents/ folder: master instructions + IBM, GPT, Copilot (<=8,000 chars), Claude files; list in AGENTS.md
