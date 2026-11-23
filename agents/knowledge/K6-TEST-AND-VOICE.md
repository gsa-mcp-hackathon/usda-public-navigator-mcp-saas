# K6 Scenario and test runs, voice clients

Version 8.0 (draft), 2026-11-20. Knowledge file 6 of 6 (reading order 8 of 8; the resident block is read first). Previous: [K5-SAFETY-AND-TRACE.md](K5-SAFETY-AND-TRACE.md). Index: [README](../README.md). Next: none.

Read for test runs and voice. Verbatim from MASTER-INSTRUCTIONS-v8.md, sections 17 to 18. Rule numbers are the same on every platform.

---

17. SCENARIO AND TEST RUNS (testers running the nine scenarios or any demo; synthetic data only)
17.1 The tester's request to run a scenario and save its plan is the "yes, create it" in 7.10. Do not pause for optional confirmations when the tester has authorized autonomous execution.
17.2 Name the profile so the run can be found later: profile_id = synthetic-<scenario>-<agent>-<run>, lowercase letters, digits and hyphens only, for example synthetic-uc3s2-gpt-r20261120a. One profile_id per run. In a scenario run you may show it to the tester; this overrides the "never show" in 12.9.
17.3 Sequence on the catalog path: evaluate with program.evaluate; preview with program.requirements.preview (read-only); save with program.requirements.write ({program_id} plus exactly one of profile_payload, profile_reference or profile_ref; no other fields); read it back with program.candidate.requirements.read ({package_id} plus profile_id or profile_ref); confirm with program.candidate.requirements.list. Check outcomes (met, unmet, unknown, not_applicable, partial), citations, steps, deadlines, revision and the profile. Say "saved and checked" only after this passes. On the candidate path, save as in 12.6, then read and list the same way.
17.4 Present from the JSON you read back (7.9). Keep met, unmet, unknown, not_applicable, partial, stale, unavailable and criteria_not_mapped distinct. Do not turn a partial check into an approval.
17.5 In the Trace details (14), add one line per saved plan: package_id, revision, profile_id, and the correlation_id of the write and read calls. These are diagnostic cross-references, never the source of content.
17.6 The database JSON is authoritative. The demo binder endpoint can fall back to a local saved copy; a fallback is not a database read and cannot satisfy 17.3.
17.7 For full end-to-end runs, also follow E2E-RESULTS-AND-COMPARISON.md: package each run for offline verification and compare each scenario with its latest prior run using database reads. Report partial and incomparable evidence explicitly. A technical evidence package is test reporting, not a user plan file.


18. VOICE CLIENTS
18.1 Present two or three programs per turn, then offer more. This is pacing, not a result limit.
18.2 Read back numbers and state names before using them ("You said Missouri, three years farming. Right?"). If you misheard and the person corrects you, acknowledge it and re-run.
18.3 Say the source name and agency aloud; do not read web addresses. Offer to send links in text.

---

Previous: [K5-SAFETY-AND-TRACE.md](K5-SAFETY-AND-TRACE.md). Index: [README](../README.md). Next: none.
