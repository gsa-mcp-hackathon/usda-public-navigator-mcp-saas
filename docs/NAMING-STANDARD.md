# Naming standard

> Version 2.1 (draft) · Updated 2026-11-20 · Operator path 4 of 4  
> [Previous: Deployment guide](../deploy/DEPLOYMENT-GUIDE.md) · [Documentation index](README.md)

This standard is designed for release at scale. Every deployed part (container, database, role, volume, network, image) is declared in one manifest, named by one pattern, described by labels, and checked by a script. The organization and the version are never in a name, so a rename or a new organization changes values in the manifest and nothing else.

It follows common cloud practice: metadata lives in labels (as in AWS tags, Google Cloud labels and Kubernetes recommended labels), desired state lives in Git as a declared, versioned manifest, images are pinned by digest, and a policy check compares the running system with the manifest.

## 1. The three layers

| Layer | What it is | Changes? |
|---|---|---|
| Structure | The slots (platform, env, role), the patterns in section 3, the label keys in section 4 | Rarely, by revising this standard |
| Values | Platform, org, environment, release, owner, image digests, in the stack manifest | Often, by editing the manifest |
| Names | Built from structure and values by the checker and generator | Never edited by hand |

## 2. The manifest

One file per environment describes the stack: [configs/stack.example.json](../configs/stack.example.json). It holds the naming values, the release (version, commit, owner) and the parts with their image digests. Keep it in Git; a change to it is a reviewed change.

To adapt for your organization, copy the example, set `platform`, `org` and `env`, and keep everything else.

| Field | Rule |
|---|---|
| `naming.platform` | 3 to 12 lowercase letters or digits, starts with a letter. Neutral, never an organization name. |
| `naming.org` | Lowercase letters, digits, hyphen. Appears only in the `org` label and in tenant IDs. |
| `naming.env` | One lowercase word, such as `demo`, `test`, `stage`. |
| `release.version`, `release.commit`, `release.owner` | Required. Version appears only in the `app.kubernetes.io/version` label and here. |
| `parts[].digest` | Required, `sha256:...`. Tags are moving pointers; the digest is the identity. |

## 3. Names

| Thing | Pattern | Example (`mcpsaas`, `test`) |
|---|---|---|
| Stack and compose project | `<platform>-<env>` | `mcpsaas-test` |
| Container | `<platform>-<env>-<role>` | `mcpsaas-test-core` |
| Network | `<platform>-<env>-net` | `mcpsaas-test-net` |
| Volume | `<platform>-<env>-dbdata` | `mcpsaas-test-dbdata` |
| Database | `<platform>_<env>` | `mcpsaas_test` |
| Database role | `<platform>_<role>_<access>` | `mcpsaas_core_rw`, `mcpsaas_core_ro` |
| Image | `<platform>-<role>`, pinned by digest | `mcpsaas-core@sha256:...` |
| Environment variable | `<PLATFORM>_<NAME>` | `MCPSAAS_DATABASE_URL` |
| Tenant ID | `<scope>-<env>`; organization may appear here | `eligibility-demo` |

Roles: `core`, `elig`, `db`, `ingest`. Names are 63 characters or fewer, lowercase, with no version and no organization.

## 4. Labels

Every container, volume and network carries these labels, ten at most. Tools find parts by label, never by name.

| Label | Value |
|---|---|
| `app.kubernetes.io/name` | platform |
| `app.kubernetes.io/instance` | stack name, for example `mcpsaas-test` |
| `app.kubernetes.io/component` | role |
| `app.kubernetes.io/version` | release version |
| `app.kubernetes.io/part-of` | platform |
| `app.kubernetes.io/managed-by` | `stack-manifest` |
| `org` | organization |
| `env` | environment |
| `owner` | responsible team or person |

## 5. Rules

1. The manifest is the source of truth. Nothing is named or labeled by hand.
2. No organization and no version in any name.
3. One environment per stack, with its own compose project, network, volume, database and ports, so a test cannot touch another environment.
4. Names never change in place on a running system. Build the new stack, move the data (dump and load), cut over, retire the old one.
5. At most three images per role: current, rollback, next. Configuration lives in Git.
6. Images are pinned by digest in the manifest. Tags are moving pointers only.
7. Secrets are never in a name, label, image or manifest.

## 6. The check

[scripts/check_naming.py](../scripts/check_naming.py) validates the manifest and, given a list of what is running, reports drift.

```bash
python scripts/check_naming.py configs/stack.example.json
python scripts/check_naming.py stack.json --observed observed.json
```

It fails on: an organization or version in a name, a name over 63 characters, a missing release field, a digest that is not `sha256:`, a missing, renamed or unexpected container, a label that differs from the manifest, and more than three images for a role. Run it as a phase-gate test before deploy, not inside the deploy path.

## 7. Applying the standard to a running system

**Where this stands.** The standard, the example manifest and the checker are built and tested. The demonstration system was named before the standard existed, so it has not been migrated, and the deployment guides do not yet generate names from the manifest. The standard is therefore a design to adopt, not a description of what is running today. Adopt it the way rule 4 says: build a new, correctly named stack beside the old one rather than renaming in place. The natural moment is the next new environment, for example a fresh test stack on a new host.

**How to apply it**

1. **Write the manifest.** Copy [configs/stack.example.json](../configs/stack.example.json), set `platform`, `org`, `env` and the release fields, and replace the placeholder digests with the digests of the images you built. Run `python scripts/check_naming.py stack.json` until it passes.
2. **Inventory what exists.** List every container, volume, network, database, role and image of the current system. On a Docker or Podman host, this produces the observed list the checker reads (adjust the command to your runtime):

   ```bash
   docker inspect $(docker ps -aq) \
     | jq '[.[] | {name: (.Name | ltrimstr("/")), type: "container", labels: .Config.Labels}]' > observed.json
   ```

3. **Map each item.** Fill in the table below, one row per item, using the patterns in section 3. The first row is an illustration only.

   | Current name | Type | New name | Move plan | Status |
   |---|---|---|---|---|
   | `core-app-prod-v3` (illustration) | container | `mcpsaas-test-core` | Rebuild from the pinned image; no data to move | Planned |
   | (from your inventory) | | | | |

4. **Build the new stack from the manifest.** Use a new compose project, network, volume, database and ports so nothing in the old stack is touched.
5. **Move the data.** Dump the old database and load it into the new one, then compare row counts.
6. **Check it.** Export the new stack's observed list the same way and run `python scripts/check_naming.py stack.json --observed observed.json`. The goal is zero findings.
7. **Cut over and retire.** Point clients at the new stack, run the smoke check, keep the old stack until it passes, then retire it. Keep at most three images per role (current, rollback, next).
8. **Keep it checked.** Run the checker as a phase-gate test before each deploy, so drift is caught when it appears.

Do this for one environment first, a test stack, and keep the mapping table in Git next to the manifest as the record of what moved.

---

[Previous: Deployment guide](../deploy/DEPLOYMENT-GUIDE.md) · [Documentation index](README.md)
