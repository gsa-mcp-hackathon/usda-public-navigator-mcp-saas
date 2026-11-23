# Deployment guide

> Version 1.1 · Updated 2026-11-21 · Operator path 3 of 4  
> [Previous: Order of operations](../docs/OPERATING-ORDER.md) · [Documentation index](../docs/README.md) · [Next: Naming standard](../docs/NAMING-STANDARD.md)

The platform ships as containers. This guide says what is in this directory, how deployment works in any environment, and what the demonstration environment looked like. The order of the steps, and what breaks if they change, is on [Order of operations](../docs/OPERATING-ORDER.md).

## What is in this directory

| Path | What it is | Do you need it? |
|---|---|---|
| `local/` | Compose files that run the Core and tenants as containers on one machine, plus a runbook ([local runbook](local/LOCAL-RUNBOOK.md)) | Yes, to run the containers locally |
| `local/vm-data-api/` | The Persistence Access API, the component that sat in front of the database in the demonstration ([folder index](local/vm-data-api/README.md), [design overview](local/vm-data-api/PERSISTENCE-ACCESS-API.md)) | Only if you use the same persistence design. Any other persistence method replaces it |
| `component-identities.json` | The declared identity catalog: the release artifacts and the exact name forms for the Core, tenants and clusters. The deployer and the tests read it | Yes, when you deploy; change the placeholder names to yours |

## How the platform is deployed

The Core and each tenant are separate applications. A pattern cluster is reusable code and configuration. Tenant registrations are logical boundaries, so the design allows tenants to share a runtime or run separately. The current deployer starts one tenant application per registration, which is an implementation limit, not a platform requirement.

In any environment the steps are the same, and only the tools change:

1. **Declare identities.** Before wiring a new component, tenant or service, decide its stable ID, name form and version fields in the identity catalog. The deployer and tests check the build manifest against it. An unresolved name is a gate to deployment, not a reason to reuse an old test name.
2. **Provide persistence.** Stand up the persistence layer the design calls for, and give the Core and each tenant their own scoped read access. No application receives a database credential if you use the Persistence Access API.
3. **Build and test.** Build the platform and Core images, and run the tests. Tests belong before deploy, not inside it.
4. **Publish by digest.** Push the images to a registry and record each digest. A tag is only a moving pointer.
5. **Deploy tenants privately, then register them.** Deploy each tenant application, then register its ready endpoint through the restricted writer operation.
6. **Deploy the Core last**, publicly, and run a short smoke check on its MCP endpoint.
7. **Clean up only after the smoke check passes.** Keep the current image and one rollback image, and remove only images that nothing references.

To promote a new Core image alone, verify the exact published digest, update only the Core by digest, and check the tool list and the read-only capability map. That does not change tenants, registrations or routes.

## Check before you promote to a shared environment

- The registry namespace or repository you publish to is set explicitly and has no default to a personal or connectivity-test namespace.
- The persistence endpoint, the writer capability and each reader secret are configured and a connected read and write has been run. A secret being present only proves it is configured.
- The Core and each tenant use their own read capability, and neither receives a database connection string. The API limits catalog reads to a registered tenant, pattern and version.
- Registry storage headroom and the application inventory are refreshed before you publish or deploy.
- Source configuration is not proof of a healthy deployment. Record observed deployment and test evidence separately, with dates.

## The demonstration environment

In the demonstration, a Linux VM was the build and local-test environment and the shared demonstration ran in an IBM cloud sandbox, the environment the hackathon assigned. The VM hosted the database and the Persistence Access API, and the cloud applications reached the API through an outbound-only tunnel. The VM was not the shared runtime destination, so a passing local image or VM smoke check did not by itself prove a cloud release.

The pipelines that built, scanned, published and promoted the images ran in the team's private repository and are not part of this package. This repository holds the deployment bindings, the compose files and the tests. See [Components](../COMPONENTS.md) for which demonstration pieces are replaceable.

---

[Previous: Order of operations](../docs/OPERATING-ORDER.md) · [Documentation index](../docs/README.md) · [Next: Naming standard](../docs/NAMING-STANDARD.md)
