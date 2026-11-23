"""Select the highest versioned Eligibility template and matching cluster manifest."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import TypedDict


class EligibilityRelease(TypedDict):
    """The template and cluster manifest paths and version of one release."""

    template_path: str
    template_version: str
    cluster_manifest_path: str


VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _version_key(version: str) -> tuple[int, ...]:
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"Invalid Eligibility template version: {version!r}.")
    return tuple(int(part) for part in version.split("."))


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}.")
    return value


def select_eligibility_release(root: Path = REPOSITORY_ROOT) -> EligibilityRelease:
    """Resolve the release from version fields and reject ambiguous registries."""
    templates = []
    for path in (root / "configs" / "templates").glob("eligibility*.json"):
        definition = _read_json(path)
        if definition.get("template_id") != "eligibility" or not isinstance(
            definition.get("operations"), list
        ):
            continue
        version = definition.get("template_version")
        if not isinstance(version, str):
            raise ValueError(f"Eligibility template has no version: {path}.")
        templates.append((_version_key(version), version, path))
    if not templates:
        raise ValueError("No versioned Eligibility template definitions were found.")

    latest_version = max(item[0] for item in templates)
    latest_templates = [item for item in templates if item[0] == latest_version]
    if len(latest_templates) != 1:
        raise ValueError(
            "Expected exactly one latest Eligibility template definition; "
            f"found {len(latest_templates)} for {latest_version}."
        )
    _, template_version, template_path = latest_templates[0]
    manifests = []
    for path in (root / "configs" / "registry").glob("eligibility-cluster*.json"):
        manifest = _read_json(path)
        for pattern in manifest.get("patterns", []):
            if (
                isinstance(pattern, dict)
                and pattern.get("pattern_id") == "eligibility"
                and pattern.get("pattern_version") == template_version
            ):
                manifests.append(path)
    if len(manifests) != 1:
        raise ValueError(
            "Expected exactly one Eligibility cluster manifest for template "
            f"{template_version}; found {len(manifests)}."
        )
    return {
        "template_path": template_path.relative_to(root).as_posix(),
        "template_version": template_version,
        "cluster_manifest_path": manifests[0].relative_to(root).as_posix(),
    }


def main() -> None:
    """Parse command-line arguments and run this tool."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    arguments = parser.parse_args()
    release = select_eligibility_release(arguments.root)
    if arguments.json:
        print(json.dumps(release, sort_keys=True))
    else:
        print(
            f"Eligibility {release['template_version']}: "
            f"{release['template_path']} + {release['cluster_manifest_path']}"
        )


if __name__ == "__main__":
    main()
