# Components

> Version 1.0 · Updated 2026-11-21 · Reference  
> [Documentation index](docs/README.md)

This prototype was built and run in the environment the hackathon assigned (IBM Cloud
and IBM watsonx Orchestrate). Participants could also use their own environment and
tools. The design is vendor- and environment-agnostic: the IBM pieces are listed here
as replaceable components, and none is required for the code to run.

| Component | Role | Used for | Replaceable with |
|---|---|---|---|
| Container runtime (IBM Code Engine) | Hosts the Core and tenant containers | Deployment | Any container host; local Docker Compose files are in `deploy/local/` |
| Container registry (IBM Container Registry) | Stores images | Deployment | Any OCI registry |
| LLM criteria mapper (IBM watsonx Orchestrate agent) | Drafts structured criteria from a public source page | Optional mapping step (`src/clusters/eligibility_mapper.py`) | Any LLM or agent that returns the same criteria schema, or the calling AI agent |
| Source control and CI/CD (GitHub) | Hosts the repository | Submission, required by the event | Any Git host and any CI/CD pipeline; the repository is plain Git |
| Persistence | Catalog, rulesets, audit | Persistence | Any method that persists the data: a managed database, direct role-scoped connections, or an API. The demonstration uses PostgreSQL behind a Persistence Access API, a compensating component for the assigned environment. PostgreSQL was chosen for familiarity, not as a requirement |

Example registry descriptors: `configs/registry/examples/` (placeholder values only).

See [docs/PROVIDER-NEUTRALITY.md](docs/PROVIDER-NEUTRALITY.md) for the mapper toggle.

---

[Documentation index](docs/README.md)
