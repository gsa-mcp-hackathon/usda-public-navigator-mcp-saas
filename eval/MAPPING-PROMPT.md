# Mapper prompt: source clause â†’ typed rule

This is the instruction the mapping step follows. In the prototype it ran in an
interactive Claude session (plan usage, no API key). A background LLM job can run
the same prompt unchanged later; nothing in it depends on a human.

## Input

One file from `fetch_sources.py`: the inventory listing plus the
`program.criteria.source.read` result (`source_text`, `source_id`,
`source_version`, `source_content_sha256`, `source_record_id`, `retrieved_at`).

## Output: `mappings/<sample_id>.json`

```json
{
  "sample_id": "P01",
  "program_id": "...",
  "navigator": "low_income_assistance | senior_caregiver | farmer_producer | general",
  "source_quality": "usable | thin | navigation_only | off_topic",
  "requirements": [
    {
      "requirement_id": "short-slug",
      "statement": "plain restatement",
      "requirement_type": "eligibility | application | availability | context | unclassified",
      "mapping_status": "mapped | partial | not_mapped | context_only",
      "primitive": "threshold_number | threshold_money | count | boolean | enum | geography | date_window | none",
      "concept_id": "household.annual_income",
      "profile_attribute": "annual_household_income_usd",
      "operator": "eq | ne | gt | gte | lt | lte | lte_by | one_of | exists",
      "value": 39750,
      "unresolved_reason": "required when partial; why it is not fully mapped",
      "context_attribute": "household_size (lte_by only: value is a table keyed by this attribute)",
      "logic_group_id": "optional; clauses that are alternatives share an id",
      "logic_group_operator": "all_of | any_of",
      "test_values": {"pass": 30000, "fail": 50000},
      "excerpt": "verbatim substring of source_text"
    }
  ],
  "details": [
    {"detail_type": "application_step | form | required_document | deadline", "text": "...", "excerpt": "verbatim substring"}
  ]
}
```

## Rules

1. Every `excerpt` is copied verbatim from `source_text`. The validator rejects any
   excerpt that is not an exact substring.
2. Map a clause only when it names a concrete test the profile can answer: an
   amount, a count, a yes/no condition, a category list, or a place. Bind it only to a name listed in `profile_fields`. That list holds the navigator's schema fields
   plus the Core's registered extra-attribute vocabulary (dotted names such as
   `core.benefits.ssi_recipient`). An unlisted dotted name is not allowed: mark the clause `partial` with
   `unresolved_reason: "no registered profile attribute"` and name the attribute you would need..
3. Never invent a threshold. "Income limits vary by area" is `not_mapped` with
   `unresolved_reason: "threshold not stated in source"`, not a guessed number.
4. Procedure, forms, documents and deadlines go in `details`, not `requirements`.
   The Core detail kinds have no `contact` kind yet; agency contact text is kept as an
   `application_step` ("Contact ... at ...") and counted as a gap in the report.
5. If the page text is navigation chrome or too thin to support any clause, set
   `source_quality` and return no requirements. That is a finding, not a failure
   of the mapper.
6. Alternatives ("you qualify automatically if you get A, B or C") share a
   `logic_group_id` with `logic_group_operator: any_of`.
7. Each mapped rule carries `test_values` (one profile value that meets it, one that
   does not) so the validator can prove the rule scores both ways.
8. A `partial` rule may name the concept and profile field, but leaves out `operator`
   unless it also has a `value`; the Core rejects a numeric operator with no number.

11. **Copy excerpts character for character**, including ligatures (`ﬁ`, `ﬂ`), curly quotes and
    apostrophes (`’ “ ”`), dashes (`–`, `—`) and non-breaking spaces. Never "clean up" an excerpt.
12. **`household_size` means only the number of household members.** Never infer marital status,
    tax filing status, or an individual/married-couple eligibility unit from that count. A source
    table labeled "individual" and "married couple" is not a household-size table, even if those
    units happen to contain one and two people.
13. **Filing-unit thresholds need an explicit matching profile fact.** Map them only when
    `profile_fields` includes a semantically matching filing-unit fact and supported operators can
    represent the distinction. Otherwise keep the criterion `partial`, omit an executable
    operator/value, and explain the missing or unsupported fact in `unresolved_reason`. Never
    select a married-couple threshold using `household_size` alone. A married threshold may score
    only when the profile explicitly records a verified married-couple unit and the contract
    supports that mapping. The current profiles expose `household_size`, not filing unit, and the
    current `lte_by` contract accepts positive-integer keys only, so individual/married-couple
    limits remain partial/unknown until the profile and evaluator contract support that fact.
14. **Use `lte_by` only for true member-count thresholds**, with
    `context_attribute: household_size` and a table keyed by count (`{"1": 1350, "2": 1824}`). Never
    write one rule per table row. Counts outside the table evaluate to unknown.
15. **Keep each excerpt to 500 characters or fewer**: quote the shortest exact span that supports
    the item. The Core rejects longer citations.
