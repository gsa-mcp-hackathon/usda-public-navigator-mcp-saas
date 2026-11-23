"""Check a stack manifest against docs/NAMING-STANDARD.md, and detect drift.

Usage:
  python scripts/check_naming.py configs/stack.example.json
  python scripts/check_naming.py stack.json --observed observed.json

observed.json is a list of {"name", "type", "labels"} taken from the running system.
Exit code 0 means pass, 1 means findings. Standard library only.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PLATFORM_RE = re.compile(r"^[a-z][a-z0-9]{2,11}$")
ORG_RE = re.compile(r"^[a-z][a-z0-9-]*$")
ENV_RE = re.compile(r"^[a-z]+$")
ROLES = {"core", "elig", "db", "ingest"}
VERSION_IN_NAME_RE = re.compile(r"(^|[-_])v?\d+(\.\d+)+($|[-_])")
MAX_LABELS = 10


def expected(manifest: dict) -> dict:
    """Return the expected infrastructure names for a manifest."""
    n = manifest["naming"]
    p, e = n["platform"], n["env"]
    names = {
        "stack": f"{p}-{e}",
        "database": f"{p}_{e}",
        "network": f"{p}-{e}-net",
        "volume": f"{p}-{e}-dbdata",
    }
    for part in manifest.get("parts", []):
        names[f"container:{part['role']}"] = f"{p}-{e}-{part['role']}"
    return names


def labels_for(manifest: dict, role: str) -> dict:
    """Return the required labels for a part with this role."""
    n, r = manifest["naming"], manifest["release"]
    return {
        "app.kubernetes.io/name": n["platform"],
        "app.kubernetes.io/instance": f"{n['platform']}-{n['env']}",
        "app.kubernetes.io/component": role,
        "app.kubernetes.io/version": r.get("version", ""),
        "app.kubernetes.io/part-of": n["platform"],
        "app.kubernetes.io/managed-by": "stack-manifest",
        "org": n["org"],
        "env": n["env"],
        "owner": r.get("owner", ""),
    }


def check_manifest(manifest: dict) -> list[str]:
    """Validate the manifest against the naming standard; return findings."""
    out: list[str] = []
    n = manifest.get("naming", {})
    if not PLATFORM_RE.match(n.get("platform", "")):
        out.append(
            "naming.platform: 3 to 12 lowercase letters or digits, start with a letter"
        )
    if not ORG_RE.match(n.get("org", "")):
        out.append("naming.org must be lowercase letters, digits or hyphens")
    if not ENV_RE.match(n.get("env", "")):
        out.append("naming.env must be one lowercase word")
    if out:
        return out
    for key in ("version", "commit", "owner"):
        if not manifest.get("release", {}).get(key):
            out.append(f"release.{key} is required")
    names = expected(manifest)
    for what, name in names.items():
        if n["org"] in re.split(r"[-_]", name):
            out.append(
                f"{what}: organization '{n['org']}' must not be in name '{name}'"
            )
        if VERSION_IN_NAME_RE.search(name):
            out.append(f"{what}: version must not appear in name '{name}'")
        if len(name) > 63:
            out.append(f"{what}: name '{name}' is longer than 63 characters")
    for part in manifest.get("parts", []):
        if part.get("role") not in ROLES:
            out.append(f"part role '{part.get('role')}' is not one of {sorted(ROLES)}")
        if not str(part.get("digest", "")).startswith("sha256:"):
            out.append(f"part {part.get('role')}: image digest must start with sha256:")
        if len(labels_for(manifest, part.get("role", ""))) > MAX_LABELS:
            out.append("more than ten labels")
    return out


def check_drift(manifest: dict, observed: list[dict]) -> list[str]:
    """Compare a running stack with the manifest; return drift findings."""
    out: list[str] = []
    names = expected(manifest)
    stack = names["stack"]
    mine = [
        o
        for o in observed
        if o.get("labels", {}).get("app.kubernetes.io/instance") == stack
    ]
    wanted = {names[k] for k in names if k.startswith("container:")}
    for part in manifest.get("parts", []):
        want = names[f"container:{part['role']}"]
        found = [
            o
            for o in mine
            if o.get("labels", {}).get("app.kubernetes.io/component") == part["role"]
        ]
        if not found:
            out.append(f"missing: no running '{part['role']}' (expected name '{want}')")
            continue
        for o in found:
            if o["name"] != want:
                out.append(
                    f"name drift: '{part['role']}' is '{o['name']}', expected '{want}'"
                )
            for k, v in labels_for(manifest, part["role"]).items():
                got = o.get("labels", {}).get(k)
                if got != v:
                    out.append(
                        f"label drift on '{o['name']}': {k} is '{got}', expected '{v}'"
                    )
    extra = {o["name"] for o in mine if o.get("type") == "container"} - wanted
    for name in sorted(extra):
        out.append(
            f"unexpected container '{name}' labeled for {stack} but not in the manifest"
        )
    per_role: dict[str, int] = {}
    for o in observed:
        if o.get("type") == "image":
            role = o.get("labels", {}).get("app.kubernetes.io/component", "?")
            per_role[role] = per_role.get(role, 0) + 1
    for role, count in per_role.items():
        if count > 3:
            out.append(
                f"role '{role}' has {count} images; max 3 (current, rollback, next)"
            )
    return out


def main(argv: list[str]) -> int:
    """Run the checks from the command line; return the exit code."""
    if not argv:
        print(__doc__)
        return 1
    manifest = json.loads(Path(argv[0]).read_text())
    findings = check_manifest(manifest)
    if "--observed" in argv and not findings:
        observed = json.loads(Path(argv[argv.index("--observed") + 1]).read_text())
        findings += check_drift(manifest, observed)
    for f in findings:
        print("FINDING:", f)
    print("PASS" if not findings else f"{len(findings)} finding(s)")
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
