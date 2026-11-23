# Where to look

> Version 1.0 · Updated 2026-11-21 · Reference, read when you need to find something  
> [Documentation index](README.md) · [Repository README](../README.md)

A map from a question to the place that answers it. Each folder has its own index with the detail. If you are an AI agent scanning the repository, start here, then open the folder index named in the right-hand column.

## By question

| If you want to know | Look in | Start with |
|---|---|---|
| What the platform is and how its layers fit | `docs/` | [Architecture](ARCHITECTURE.md) |
| Why it was built this way, and which use cases drove it | `docs/` | [Use cases](USE-CASES.md) |
| How a person goes from a need to an answer | `docs/` | [Eligibility pattern](ELIGIBILITY-PATTERN.md) |
| Where the program data comes from and how it is governed | `docs/`, `configs/eligibility/` | [Data and sources](DATA-AND-SOURCES.md) |
| How to connect Claude, ChatGPT, Copilot or another client | `docs/`, `agents/` | [Connecting agents and clients](CONNECT.md) |
| How an agent should behave when it uses the server | `agents/` | [Agent instructions](../agents/README.md) |
| How to install and run it | [`QUICKSTART.md`](../QUICKSTART.md), `deploy/` | [Quickstart](../QUICKSTART.md), then the [deployment guide](../deploy/DEPLOYMENT-GUIDE.md) |
| How the Core routes a request | `src/example_server/` | [Example server index](../src/example_server/README.md) |
| How a program search or evaluation works in code | `src/clusters/` | [Clusters index](../src/clusters/README.md) |
| What a given test checks | `tests/` | [Tests index](../tests/README.md) |
| What proves the platform works | `eval/` | [Evidence summary](../eval/EVIDENCE-SUMMARY.md) |
| How the tests and validation are designed | `docs/` | [Evaluation](EVALUATION.md) |
| What a script does and when to run it | `scripts/` | [Scripts index](../scripts/README.md) |
| The database tables | `db/` | [Database index](../db/README.md) |
| The persistence service in front of the database | `deploy/local/vm-data-api/` | [Folder index](../deploy/local/vm-data-api/README.md) |
| Which attribute schemas and contracts exist | `registries/` | [Registries index](../registries/README.md) |
| Which tenants, templates and registry versions are current | `configs/` | [Configuration index](../configs/README.md) |
| How things are named, and how to name your own | `docs/` | [Naming standard](NAMING-STANDARD.md) |
| What is planned but not built | `docs/enhancements/` | [Enhancements](enhancements/README.md) |

## By folder

| Folder | What it holds |
|---|---|
| `agents/` | Instructions for AI agents, one master and a cut per platform |
| `configs/` | Versioned data the platform reads |
| `db/` | Schema migrations and seed data |
| `deploy/` | Deployment guide, compose files and the persistence service |
| `docs/` | The documentation, in reading order |
| `eval/` | The test questions, personas, script engine and recorded runs |
| `registries/` | Published attribute schemas, contracts and the public dataset registry |
| `scripts/` | Command-line tools for building, checking, registering and ingesting |
| `src/` | The platform code |
| `tests/` | The unit and contract tests |

---

[Documentation index](README.md) · [Repository README](../README.md)
