# Quickstart

> Version 1.0 · Updated 2026-11-21 · Reference  
> [Documentation index](docs/README.md)

There are two paths.

| Path | What you get | What it needs |
|---|---|---|
| **A. Quick try** (this page, steps 1 to 3) | A running Core routing requests to two synthetic tenants, and the test suite | Python and uv. No cloud account, no database, no credentials |
| **B. Full Eligibility journey** (last section) | Discovery, screening and the application binder, as demonstrated | A persistence layer. The demonstration runs PostgreSQL behind a Persistence Access API as separate containers; the design allows any persistence method |

Path A is the fastest way to see the platform work. Path B is the shape of the demonstration environment.

## 1. Prerequisites

- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/) (`pip install uv`, or `brew install uv`)

New to this tool chain, or setting up Windows, containers or a pipeline? See [Setup and recommended tools](docs/SETUP.md).

## 2. Install and test

```bash
# from the unzipped package folder
uv sync --group dev
uv run pytest tests/ deploy/local/vm-data-api/tests/ -q
```

Troubleshooting:

- **Windows long paths.** Clone near the drive root, or use `git clone -c core.longpaths=true ...`, if Git reports that it cannot create some paths.
- **Proxy environments.** If your shell sets a SOCKS proxy (`ALL_PROXY`), the HTTP client needs SOCKS support. Run the tests with `uv run --with socksio pytest ...`, or unset the proxy variables.
- **Windows console encoding.** If a script fails on a character-encoding error, set `PYTHONUTF8=1` for that command.

## 3. Run the local platform smoke test

```bash
uv run python scripts/smoke_local_mcp_stack.py
```

This starts one Core and two synthetic tenants as separate processes over Streamable HTTP, then routes requests through the Core to each tenant. The output shows each tenant answering from its own configuration (`Synthetic Directory Alpha` and `Synthetic Directory Beta`), which is the multi-tenant pattern in miniature.

## Connect your own MCP client to a local server

The simplest server runs over stdio:

```bash
cp .env.example .env
uv run python main.py
```

A client launches it as a subprocess. For Claude Code, add this to `.mcp.json` using the absolute path to your clone:

```json
{
  "mcpServers": {
    "mcp-saas": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/this-package", "python", "main.py"]
    }
  }
}
```

Other clients: see [Connecting agents and clients](docs/CONNECT.md).

## Path B: the full Eligibility journey

The discovery, screening and binder journey for the Eligibility cluster needs a persistence layer (the demonstration runs PostgreSQL behind a Persistence Access API; the design allows any persistence method) in addition to Core and tenants. The local container profile is described in [deploy/local/LOCAL-RUNBOOK.md](deploy/local/LOCAL-RUNBOOK.md). The evaluation harness and synthetic personas that exercise it are in [eval/](eval/README.md).

## Next

- Add your own cluster: copy `configs/templates/sample-directory.v1.json`, and change its cluster and operations. [Architecture](docs/ARCHITECTURE.md) explains how Core registers templates
- [Architecture](docs/ARCHITECTURE.md)

---

[Documentation index](docs/README.md)
