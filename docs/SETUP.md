# Setup and recommended tools

> Version 1.0 (draft) · Updated 2026-11-21 · Operator path 1 of 4  
> [Documentation index](README.md) · [Next: Order of operations](OPERATING-ORDER.md)

Setting up a workstation for this platform is not click-and-go. A new operator can lose hours to Git, containers, Python, a Windows Linux layer, runners and test tools before doing any real work. This page is a reconnaissance guide and a set of best practices. It is not a step-by-step install, because the steps change with each environment.

## The stack at a glance

| Layer | What the repository uses | Where it matters |
|---|---|---|
| Language and packages | Python 3.11 or newer (the container images use 3.12), managed with `uv` | Everything |
| Server framework | FastMCP, Pydantic, httpx, OpenTelemetry | Core, tenants, clusters |
| Persistence | SQL migrations and seeds in `db/`; PostgreSQL in the demonstration | Data layer |
| Containers | Dockerfiles for Core, tenants and the data service; compose files for local profiles | Build and run |
| Source control and CI/CD | Git, and a pipeline that runs lint, format and tests | Build and publish |
| Quality tools | `ruff` (lint and format), `pytest` | Before every change |
| Evaluation | Machine suites, test questions and synthetic personas in `eval/` | Testing outside the deploy path |
| Environment capabilities | Hosting, registry, secure access, access control and secrets, from whatever your environment provides | See the next section |

## Capabilities your environment needs

The platform does not require any particular vendor or product. It needs these capabilities from whatever your environment provides. If your environment already has one, use it. If it lacks one, plan for it.

| Capability | Why the platform needs it |
|---|---|
| Compute to run containers | Core, tenants and the persistence layer run as containers |
| Container registry | Holds the images, which are pinned by digest |
| Persistence | A place to durably store the catalog, rule sets and audit data |
| Private network access | Keeps the data layer off the public internet. A private network, VPC, firewall rules or a secure tunnel all do this |
| Client access control | Decides which clients may call Core, with scoped tokens or identities |
| Secrets management | Stores credentials outside Git and hands them to services at run time |
| CI/CD runners | Run lint, tests and builds on a clean machine, and promote releases |
| Automation harnesses | Scripts and workflows that run checks, deploys and evaluations the same way every time |
| AI agent tools and plugins | Whatever an AI coding agent or a client needs to connect, such as MCP connections, skills or IDE integrations |
| Accounts, licenses and sign-in security | An account for every service, a paid plan where one is needed, and multi-factor authentication on all of them |

The demonstration ran in an environment assigned by the event, and it filled these capabilities with that environment's tools. A secure tunnel stood in for the private network a VPC would normally provide. These choices were driven by the assignment. [Components](../COMPONENTS.md) lists what was used and what can replace it.

## Accounts, access and security come first

Tools do not work until the access around them does. Treat access as a setup task of its own, and start it first, because approvals can take days.

- **An account for each service.** Source control, the AI tools, the registry, the hosting environment and any evaluation service each need one.
- **A paid plan or license where the service needs it.** AI tools, usage-based services and some connectors are paid. Know who pays, what the usage limits are, and who is told when a limit is reached.
- **Multi-factor authentication on every account.** Set up the authenticator and save the recovery codes before you need them. A lost second factor can block a release.
- **Plugin and connector permissions.** An AI tool reaches your systems through plugins, connectors and MCP connections. Each one needs approval, and your organization may restrict which are allowed.
- **Scoped tokens.** Give each token the smallest scope and the shortest life that works, and record who owns it and when it expires.
- **Network and policy limits.** Firewalls, allowlists and organization policy can block a tool that works on another machine. Ask early what is allowed.
- **Keep secrets out of prompts and Git.** Use the secrets manager, and let the tool prompt you for the value.

## Recommended workstation tools

- **Git**, and an account on the host you use. Set your name and email first.
- **Python 3.11 or newer and `uv`.** Let `uv` create the environment: `uv sync --group dev`.
- **A container runtime with compose**, Docker or Podman. You need it only for the full persistence path.
- **On Windows, WSL 2** with a Linux distribution, and the repository cloned inside the Linux file system, not under `/mnt/c`. It is faster and avoids path-length and line-ending problems.
- **An editor or IDE** that can open the repository and run a terminal.
- **A password manager.** Tokens and keys go there, never in Git or in a prompt.
- **Your environment's command-line tools** for hosting, registry and secrets, only if it needs them. Install them last.

## Check each tool before the next one

Run one check per tool and move on only when it works: `git --version`, `python --version`, `uv --version`, `docker --version` (or `podman --version`), and `uv run pytest tests/ -q`. The last one proves the whole chain. If it fails, fix that before touching a container or a pipeline.

## Let an AI coding agent do the setup

An AI coding agent in your terminal or IDE can install tools, create files, run the checks and fix configuration errors in minutes. It reads error messages and tries the next fix, which is most of what manual setup is. You stay in the loop for approvals and secrets only.

1. **Give it the goal and the limits.** Say what you want working, the operating system, and that it must check what is installed before changing anything.
2. **Ask for the plan first.** Read it, then let it run.
3. **Approve each install.** It should stop and ask before it installs software or changes system settings.
4. **Type passwords, tokens and logins yourself.** Do not paste a secret into a prompt. Let the agent tell you where it needs one.
5. **Ask for the check output.** Each tool's check command should pass before the next step.
6. **Have it write down what it did.** A short setup note in your own working notes saves the next person the same hours.

A starting prompt:

> Set up this repository for development on my machine. First report which of Git, Python 3.11 or newer, uv and a container runtime are installed. Then propose a plan, and wait for my approval before you install anything. After each step run its check command and show me the result. Finish by running the test suite. Ask me when you need a password, token or login, and do not store secrets in files that are tracked by Git.

## Best practices

- **Follow the order in [Order of operations](OPERATING-ORDER.md).** Data and its access path first, Core last.
- **Work on a branch.** Keep configuration in Git, secrets out of it.
- **Change one thing at a time**, and rerun the tests after each change.
- **Keep tests before deploy, not in it.** A short deploy path is easier to understand and to repair.
- **Match the environment, not this page.** If your environment differs, such as a different registry, runner or cloud, keep the dependencies in the order document and change the tools.

---

[Documentation index](README.md) · [Next: Order of operations](OPERATING-ORDER.md)
