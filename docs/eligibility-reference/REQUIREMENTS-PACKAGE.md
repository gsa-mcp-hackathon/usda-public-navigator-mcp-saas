# The requirements package

> Version 1.1 · Updated 2026-11-21 · Reference, read after Eligibility pattern  
> [Documentation index](../README.md) · [Related: Eligibility pattern](../ELIGIBILITY-PATTERN.md) · [Criteria mapping](CRITERIA-MAPPING.md)

The requirements package is the last thing a person receives from the Eligibility pattern: a cited, saved summary of what a program asks of an applicant, together with how the person's profile measured up. This page explains what it contains, how it is produced and saved, and what it deliberately is not.

## What it is, and what it is not

The package is a preparation aid. It lists the steps, forms, documents, deadlines and contacts the program's own public pages publish, adds the screening result for the person's profile, and cites the source for every statement. It is not an agency decision, a completed application, or a submission to any government system, and the demonstration says so wherever it shows one: the wording is "requirements package saved in the sandbox", never "application filed".

A package carries the program and its source references, the freshness of those sources, the requirements with their citations, the profile snapshot and evaluation evidence used to render the report, the limits of what was checked, and the tenant and catalog it came from. It never carries profile access references, credentials, chat transcripts or copies of public datasets. Profiles in the demonstration are synthetic, and program information always stays tied to its public source.

## How a package is produced

A person, or an agent acting for them, selects one program and asks for the package. The platform first previews it, which assembles and returns the package without saving anything. When the person wants to keep it, the platform assembles the same package again on the server and saves it. The client never sends a package body of its own, so what is saved is always what the platform built from current sources. The active screening request is the authorization for this synthetic write, so there is no extra confirmation step.

Assembly needs exactly one clear program in the tenant's approved source catalogs. If the sources are stale, unavailable, conflicting or out of scope, the request fails instead of producing a partial answer.

Three situations are worth knowing about:

- **A listing with no mapped criteria.** If the program is current and unambiguous but no criteria have been mapped, the platform still produces a limited package. It carries the listing and its citation, marks each detail as unavailable, and states that no eligibility scoring was done. That means the shared catalog has no reviewed details yet. It does not mean the program has no requirements.
- **A listing linked to a saved rule set.** When the listing is explicitly linked to a stored criteria rule set, the result shows the met, unmet and unknown outcomes with their reasons, alongside the package. A missing or conflicting rule set stays a reasoned result and nothing is written.
- **A program that was not in the catalog.** For a program found outside the catalog, the client carries the program's identity, cited details, mapped criteria and profile facts through the mapping steps, and then calls the candidate form of the preview and save. The result is saved with the tenant's identity attached, read back exactly, and returned with a rendered Markdown copy. This path was exercised live with a real program that was not in the catalog.

Public pages are read carefully. The reader follows at most three redirects, checks that every destination is a public HTTPS address before the next read, and rejects redirects that carry credential-like parameters. A CAPTCHA or anti-automation page is reported as an access challenge and is never passed to mapping as evidence; it does not prove the publisher is offline.

## Saving, identity and retrieval

A package is saved through the Persistence Access API, with no direct database connection and no file on the tenant's disk. Each saved package is immutable and stored with the tenant's identity and a digest of its content. The credential used for writing is dedicated to this one table and can only read and insert there.

A package's identity is computed from the tenant, the pattern, the program, the catalog version and the content. Saving the same package again returns the existing record and receipt. Saving different content under the same identity is rejected as a conflict. Each save returns a retrieval path that only the same dedicated credential can read.

People find their packages later through their saved profile. Listing returns short summaries only, and reading returns the saved package with its rendered Markdown. The store never lists profiles across tenants. Reconnecting a person who returns in a new session to the same saved profile uses a resume claim, which exchanges for a scoped profile reference in the new session; linking that resumed profile to its package history is a known follow-up.

The saved package is a sandbox artifact. It is not a permanent document store and not an official channel for submitting anything to an agency.

## Running it in an environment

A tenant needs its dedicated package-writer secret in place before the platform will deploy it, and the data service must hold the matching scoped credential and table grant first. The secret being present only proves it is configured, so a connected write and read-back must be run to prove it works. If you are applying the database migrations to an existing database, apply the latest before testing package writes.

Acceptance covers the whole path: a preview, server-side assembly, the authorized save, an exact replay, a conflict on changed content, retrieval by receipt, source provenance and freshness, and retention of only the synthetic report inputs.

---

[Documentation index](../README.md) · [Related: Eligibility pattern](../ELIGIBILITY-PATTERN.md) · [Criteria mapping](CRITERIA-MAPPING.md)
