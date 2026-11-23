# Documentation

> Version 1.0 · Updated 2026-11-21 · Index  
> [Start: Architecture](ARCHITECTURE.md)

There are three tracks. Every document carries its place and Previous and Next links at the top and bottom, so you can follow a track without coming back here.

## 1. Reading path (1 to 7)

Start here to understand what was built and why.

| # | Document | What it covers |
|---|---|---|
| 1 | [Architecture](ARCHITECTURE.md) | Vision and design intent, the enterprise design, the demonstration, and the pathway to scale |
| 2 | [How the use cases led to the design](USE-CASES.md) | The 49 use cases, the hackathon rules, the pattern groups, and why eligibility was chosen |
| 3 | [Eligibility pattern](ELIGIBILITY-PATTERN.md) | The pattern, the use cases, how requirements were translated, the user journey, and how testing follows it |
| 4 | [Data and sources](DATA-AND-SOURCES.md) | What is stored, how a public source becomes searchable, freshness, privacy, and rebuilding |
| 5 | [Connecting agents and clients](CONNECT.md) | How any client reaches the Core, access levels, and how an agent works with the platform |
| 6 | [Evaluation](EVALUATION.md) | The test and validation framework, what is tested, how to run |
| 7 | [How it was built](DEVELOPMENT.md) | The AI team, the human team, the method, and a setback and rebuild |

## 2. Operator path (1 to 4)

Read this to set up, build and run the platform. Follow it in order.

| # | Document | What it covers |
|---|---|---|
| 1 | [Setup and recommended tools](SETUP.md) | The tech stack, the tools to install, and how to use an AI coding agent to set up quickly |
| 2 | [Order of operations](OPERATING-ORDER.md) | What depends on what when building, deploying and operating, and what breaks if the order changes |
| 3 | [Deployment guide](../deploy/DEPLOYMENT-GUIDE.md) | Running locally, the demonstration environment, other providers, and scale |
| 4 | [Naming standard](NAMING-STANDARD.md) | How every deployed part is named, neutral to any organization |

## 3. Reference (read when the topic comes up)

Each reference page names the document to read it after.

| Document | Read after | What it covers |
|---|---|---|
| [Criteria mapping](eligibility-reference/CRITERIA-MAPPING.md) | 3 Eligibility pattern | One mapping contract, interchangeable runners and a server-side verifier |
| [Requirements package](eligibility-reference/REQUIREMENTS-PACKAGE.md) | 3 Eligibility pattern | The cited application-readiness package contract |
| [Provider neutrality](PROVIDER-NEUTRALITY.md) | 4 Data and sources | How the one language-model task stays provider-neutral |
| [Navigator test fixture](NAVIGATOR.md) | 6 Evaluation | The test client used during development; not part of this submission |
| [Enhancements](enhancements/README.md) | 7 How it was built | Features designed but not built, why, and the path to scale |
| [Persistence schema diagram](images/persistence-schema-erd.svg) | 4 Data and sources | The database schema |
| [Repository standard](REPOSITORY-STANDARD.md) | Before adding or moving files | The rules that keep the repository readable, and the check that enforces them |
| [Phoenix folder](../phoenix/README.md) | 3 Deployment guide | Optional: run Phoenix, trace calls from internal and external agents, and record chats |
| [Where to look](WHERE-TO-LOOK.md) | Any time | A map from a question to the folder and file that answers it |

To reuse this repository with your own cluster and tenants, start with the [README](../README.md) and the [quickstart](../QUICKSTART.md).

---

[Start: Architecture](ARCHITECTURE.md)
