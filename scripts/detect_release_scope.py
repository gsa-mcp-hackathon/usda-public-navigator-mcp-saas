"""Determine which immutable runtime images a main-branch release must publish."""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

CORE_ONLY_INPUTS = frozenset(
    {
        "Dockerfile.core",
        "src/example_server/core_app.py",
        "src/example_server/core_routes.py",
        "src/example_server/core_templates.py",
        "src/example_server/core_tenants.py",
        "src/example_server/core_telemetry.py",
        "src/example_server/core_tracing.py",
        "src/example_server/core_profiles.py",
        "src/example_server/core_source_policy.py",
        "src/example_server/profile_refinement.py",
        "db/migrations/010_tenant_onboarding_lifecycle.sql",
        "db/migrations/011_privacy_safe_route_telemetry.sql",
        "db/migrations/012_synthetic_profile_service.sql",
        "db/migrations/040_core_route_error_outcomes.sql",
    }
)

PLATFORM_INPUT_PATTERNS = (
    "Dockerfile",
    "README.md",
    "pyproject.toml",
    "uv.lock",
    "src/clusters/**",
    "src/example_server/**",
    "configs/registry/demo-v1.json",
    "configs/eligibility/**",
    "registries/schemas/**",
    "scripts/reconcile_registry.py",
    "scripts/select_eligibility_release.py",
)

# Versioned Eligibility descriptors are registry inputs, not image contents
# required to run the shared cluster. The deploy/operator lifecycle publishes
# them against an existing immutable image and updates stable route bindings.


def _matches(path: str, pattern: str) -> bool:
    """Match exact paths and recursive directory patterns without shell tools."""
    return fnmatch.fnmatchcase(path, pattern) or (
        pattern.endswith("/**") and path.startswith(pattern[:-3] + "/")
    )


def detect_release_scope(
    changed_paths: Iterable[str],
    *,
    force_full_release: bool = False,
    publish_scope: str | None = None,
) -> dict[str, bool]:
    """Return the Core and platform image publication requirements."""
    explicit_scopes = {
        "core-only": {"core_changed": True, "platform_changed": False},
        "platform-only": {"core_changed": False, "platform_changed": True},
        "both": {"core_changed": True, "platform_changed": True},
    }
    if publish_scope is not None:
        try:
            return explicit_scopes[publish_scope]
        except KeyError:
            raise ValueError("Unsupported manual release image scope.") from None
    if force_full_release:
        return {"core_changed": True, "platform_changed": True}

    paths = {Path(path).as_posix() for path in changed_paths}
    # Core modules are bundled in the platform build context, but they are not
    # imported by the platform's app entrypoint or Eligibility cluster runtime.
    core_changed = any(
        path in CORE_ONLY_INPUTS
        or path
        in {
            "pyproject.toml",
            "uv.lock",
            "src/example_server/__init__.py",
            "src/example_server/persistence_api_client.py",
        }
        for path in paths
    )
    platform_changed = any(
        path not in CORE_ONLY_INPUTS
        and any(_matches(path, pattern) for pattern in PLATFORM_INPUT_PATTERNS)
        for path in paths
    )
    if "pyproject.toml" in paths or "uv.lock" in paths:
        core_changed = True
        platform_changed = True
    return {"core_changed": core_changed, "platform_changed": platform_changed}


def changed_paths(before: str, head: str) -> list[str]:
    """Read changed paths from the checked-out Git history."""
    result = subprocess.run(
        ["git", "diff", "--name-only", before, head],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


def main() -> None:
    """Parse command-line arguments and run this tool."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", default="")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--event", default="push")
    parser.add_argument(
        "--publish-scope",
        choices=("core-only", "platform-only", "both"),
        help="Explicit image selection for a confirmed manual main-branch publish",
    )
    args = parser.parse_args()
    before = args.before
    if args.event == "workflow_dispatch":
        scope = detect_release_scope(
            (),
            force_full_release=args.publish_scope is None,
            publish_scope=args.publish_scope,
        )
    else:
        force = not re.fullmatch(r"[0-9a-f]{40}", before) or set(before) == {"0"}
        scope = detect_release_scope(
            [] if force else changed_paths(before, args.head),
            force_full_release=force,
        )
    lines = [
        f"{key}={'true' if enabled else 'false'}" for key, enabled in scope.items()
    ]
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as output:
            output.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
