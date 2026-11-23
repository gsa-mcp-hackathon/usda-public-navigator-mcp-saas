# K4 Tool reference (pre-wired; live schema wins, see 12.19)

Version 8.0 (draft), 2026-11-20. Knowledge file 4 of 6 (reading order 6 of 8; the resident block is read first). Previous: [K3-RULES-AND-LIMITS.md](K3-RULES-AND-LIMITS.md). Index: [README](../README.md). Next: [K5-SAFETY-AND-TRACE.md](K5-SAFETY-AND-TRACE.md).

Read before any Core write, evaluate or save. Verbatim from MASTER-INSTRUCTIONS-v8.md, sections 12 to 12. Rule numbers are the same on every platform.

---

12. TOOL REFERENCE
12.1 Pre-wired reference (live schema wins, see 12.19). Use route_operation(route_id, operation_id, arguments). route_id is a route name, never an operation name. Example of a correct first call: route_operation(route_id="eligibility", operation_id="program.inventory.search", arguments={"need_query":"lost income food assistance","page_size":5}).
- route_id "eligibility": program.inventory.search, program.inventory.list, program.discovery.external.search, program.criteria.source.read, program.candidate.evaluate, program.candidate.normalize.
- route_id "eligibility-demo": program.discover, program.evaluate, program.results, program.requirements.preview, program.requirements.write, program.candidate.requirements.read, .list, .enrich, .write.
- A wrong route returns unknown_route; use the other route name, do not guess new IDs.
12.2 Search: eligibility / program.inventory.search {need_query, state_code (only if you know it), page_size: 5}. Keep each result's listing ID, source URL and criteria state: evaluation_available = catalog path; mapping_required or a lead = candidate path.
12.3 Wider search: eligibility / program.discovery.external.search {need_query, jurisdiction_code:"US-<state>" (two letters, for example "US-DE"), page_size: 5}.
12.4 Read an official page or form: eligibility / program.criteria.source.read {source_url} (only source_url; no other keys). The address must come from a search result or from a page you read (2.6). If a read fails twice, stop reading and say what you could not read.
12.5 Catalog path only (criteria state evaluation_available): eligibility-demo / program.discover {program_id, profile_ref}, program.evaluate {program_id, profile_ref}, then for the plan program.requirements.preview {program_id, profile_ref}, program.requirements.write {program_id, profile_ref}, program.candidate.requirements.read {package_id, profile_ref}. Always include profile_ref. If the package lists unavailable_detail_kinds, read the official page and call program.candidate.requirements.enrich {package_id, profile_ref, expected_revision, details[]}.
12.6 Candidate path (mapping_required or a lead): do NOT call program.requirements.preview or program.requirements.write. Check with eligibility / program.candidate.evaluate {criteria_package, profile_submission}. Save with eligibility-demo / program.candidate.requirements.write {criteria_package, details[], profile_submission, profile_id}. These models reject any other keys. The save returns a package_id. To add forms or steps to a saved candidate plan: read it with eligibility-demo / program.candidate.requirements.read {package_id} to get the current revision, then call eligibility-demo / program.candidate.requirements.enrich {package_id, expected_revision, details[]} with details cited to the same sources as the saved package (12.7, 12.8). A stale revision changes nothing: read again and retry once.
12.7 details[] items are exactly {kind, text, citation}. kind is one of application_step, form, required_document, deadline (an agency contact goes in as application_step). text is the plain statement. citation is {source_url, source_record_id, retrieved_at, source_id, source_version, source_content_sha256, locator, excerpt}, copied from the program.criteria.source.read result of the page that supports the detail, with excerpt word for word (max 500 chars).
12.8 Citation matching rules (the save fails if any is wrong): every detail's citation.source_id must equal the source_id of an entry in criteria_package.sources; the citation's source_url must equal that source's url; the citation's retrieved_at must equal that source's retrieved_at; the citation's source_version must equal that source's publisher_version. If a detail cites a second page (for example a form), add that page to criteria_package.sources first, with publisher_version equal to its source_version from source.read.
12.9 profile_id is "synthetic-<state>-<slug>": lowercase letters, digits and hyphens, 3 to 64 characters after "synthetic-". It is an internal identifier you choose and never show. It must exactly equal profile_submission.profile.profile_id.
12.10 criteria_package = {program_id, catalog_version:"request-scoped", navigator:"<senior_caregiver|low_income_assistance|farmer_producer|general>", title, description, jurisdiction, sources:[{source_id, source_type:"public_source", agency, title, url, jurisdiction, retrieved_at, approval_status:"unverified", publisher_version}], requirements:[REQUIREMENT, ...]}.
12.11 jurisdiction is an object, never a string: {"level":"state","country_code":"US","state_code":"DE"} (federal: {"level":"federal","country_code":"US"}).
12.12 REQUIREMENT = {requirement_id, statement, requirement_type: eligibility|application|availability|context, mapping_status: mapped|partial|not_mapped|context_only, citation, and as needed: concept_id, profile_attribute, operator (eq|gte|lte|lt|gt|one_of|lte_by), value, context_attribute (lte_by only), unit, threshold_basis, applicability, logic_group_id, logic_group_operator (all_of|any_of), unresolved_reason}. mapped needs concept_id, profile_attribute, operator and value all present (lte_by also needs context_attribute and an object value). partial needs unresolved_reason and carries no concept_id, profile_attribute, operator or value. not_mapped and context_only carry no rule. The requirement citation is {source_url, source_record_id, retrieved_at, source_id, source_version, source_content_sha256, locator:"html.visible_text", excerpt}.
12.13 profile_attribute is the plain field name used in profile_submission (for example state_code, household_size), not the core.* ID.
12.14 profile_submission = {profile:{profile_id:"synthetic-<slug>", navigator, schema_version:"senior-profile-v3|household-profile-v1|farmer-profile-v1", ...plain fields..., attributes:{}}}.
12.15 If evaluate or write fails, compare your arguments with 12.6 to 12.14 and section 9, fix them and retry once.
12.16 Saving the person's details: write_profile {profile_kind:"person", schema_registry_id:"core-schema-registry", schema_version:"core-v8", attributes:[{"attribute_id":"core.geography.state_code","value":"DE"}, ...], confirmed:true}. attributes is a LIST of {attribute_id, value} pairs. Allowed keys: profile_kind, schema_registry_id, schema_version, attributes, confirmed; add profile_ref and expected_revision only to correct an earlier save. core-v8 is the only schema version. The reply "invalid_or_inactive_profile_schema" means one attribute value is not allowed: fix it from 12.17 and retry once. Keep the profile_ref from the response and use it in every catalog-path step. To correct a fact, write again with that profile_ref and its expected_revision. If a later call does not recognize the profile_ref, save the details again and use the new one, after you have reported the failure as in 12.18.
12.17 Allowed attributes and values (use only these; use only facts the person gave):
- core.geography.state_code: two capital letters; core.geography.county_name: text
- core.household.size: whole number 1 or more; core.household.adults, core.household.children: whole numbers 0 or more
- core.household.annual_income_usd, monthly_gross_income_usd, monthly_net_income_usd: whole numbers 0 or more
- core.household.housing_status: owner | renter | other
- core.household.disaster_impact, food_assistance_need, utility_assistance_need, transportation_need, childcare_need: true or false
- core.person.age_band: under_60 | 60_64 | 65_74 | 75_plus | 65_plus | adult | unknown
- core.person.employment_status: employed | unemployed | part_time | unknown
- core.person.veteran_status, disability_status: true or false
- core.person.veteran.va_healthcare_status: enrolled | not_enrolled | unknown
- core.person.veteran.service_discharge_status: qualifying | non_qualifying | unknown
- core.person.veteran.service_connected_disability_rating_percent: whole number 0 to 100
- core.person.veteran.personal_care_need: true or false
- core.person.caregiver.age_years: whole number
- core.person.caregiver.relationship_to_veteran: spouse | son | daughter | parent | stepfamily_member | extended_family_member | other | unknown
- core.health.medicare_status: enrolled | not_enrolled | unknown; core.health.coverage_status: insured | uninsured | unknown
- core.farm_business.operation_type: row_crop | small_farm only (use small_farm for any small operation); core.farm_business.farm_type: free text
- core.farm_business.land_tenure: owned | leased | mixed | unknown
- core.farm_business.years_farming: whole number; acreage: number 0 or more; beginning_farmer_status: true or false
- core.farm_business.operation_goal, loss_type: free text; resource_concerns, current_practices, commodities: lists of text
- core.farm.has_fsa_farm_number, core.farm.acreage_report_filed: true or false
12.18 Profile continuity (synthetic profiles only). Keep a profile journey in one chat; you cannot control MCP sessions. Call write_profile, then read_profile with the exact profile_ref it returned. Copy the reference exactly; never shorten, retype or reuse one from an earlier chat. If read_profile returns profile_not_found_in_scope, do not retry the same reference. Report it as a reference or scope failure; a changed session is only one possible cause, so do not say the session changed unless you can show it. Then use a resume claim or start a new profile. Do not use inline profile values as a silent substitute. To carry a profile into a later session (when your client exposes these tools): right after write_profile, in the same session and after the person confirms, call create_profile_resume_claim with that profile_ref. Show the claim once and tell the person to store it safely; it works like a password for that one synthetic profile. In a new session, after the person confirms, call resume_profile with the claim, then read_profile with the NEW profile_ref it returns, and tell the person to store the replacement claim. Each claim works once. Never use a correlation ID, package ID or event ID in place of a claim. Never list or search for other profiles. If a read fails, report the failure with the write's correlation_id; do not hide it.
12.19 Live schema wins. Section 12 is a pre-wired reference and can lag the running Core. Where your client advertises route schemas (profile DISCOVER, or a schema read the person asks for), use the advertised schema over any example here. If they differ, follow the live schema and add an Instruction note (13.1).


---

Previous: [K3-RULES-AND-LIMITS.md](K3-RULES-AND-LIMITS.md). Index: [README](../README.md). Next: [K5-SAFETY-AND-TRACE.md](K5-SAFETY-AND-TRACE.md).
