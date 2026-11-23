# Local container runbook

> Version 1.1 · Updated 2026-11-21 · Guide, part of the Operator path  
> [Documentation index](../../docs/README.md) · [Deployment guide](../DEPLOYMENT-GUIDE.md)

This runbook runs the Core and tenants as containers on one machine with configuration-backed tenants. It works on any machine with Docker or Podman.

## Run the Core and tenants locally

From the repository root:

```bash
docker compose -f deploy/local/docker-compose.yml up -d --build
```

(Use `podman-compose` in place of `docker compose` with Podman.) This builds the platform image and starts one Core and four synthetic tenants that answer from their own configuration. The Core listens on `127.0.0.1:8080`, and each container has a health check. No database or credentials are needed.

## Persistence

The journeys that save data (profiles, saved programs, the binder) need a persistence layer behind the Core. The design is a persistence contract with interchangeable implementations; the demonstration used PostgreSQL behind a Persistence Access API. See the [Persistence Access API design overview](vm-data-api/PERSISTENCE-ACCESS-API.md). The environment-specific setup used for the demonstration is not part of this package.
