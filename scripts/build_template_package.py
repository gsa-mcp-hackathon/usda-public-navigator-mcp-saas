"""Build a validated immutable pattern package descriptor from a registry digest."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "deploy" / "local" / "vm-data-api"))

from select_eligibility_release import select_eligibility_release  # noqa: E402
from template_packages import validate_template_package  # noqa: E402

BASELINE_DEFINITION_PATH = (
    REPOSITORY_ROOT / select_eligibility_release()["template_path"]
)
SHA256_DIGEST = re.compile(r"sha256:([0-9a-f]{64})\Z")


def build_template_package(
    definition: Mapping[str, object], *, artifact_ref: str, image_digest: str
) -> dict[str, object]:
    """Add the immutable published image reference and validate its descriptor."""
    match = SHA256_DIGEST.fullmatch(image_digest)
    if match is None or not artifact_ref.endswith(f"@{image_digest}"):
        raise ValueError("Artifact reference must contain the matching sha256 digest.")
    package = {
        **definition,
        "artifact_ref": artifact_ref,
        "artifact_sha256": match.group(1),
    }
    return validate_template_package(package)


def main() -> None:
    """Parse command-line arguments and run this tool."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-ref", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--definition", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()

    definition = json.loads(arguments.definition.read_text(encoding="utf-8"))
    if not isinstance(definition, dict):
        raise ValueError("Template definition must contain a JSON object.")
    package = build_template_package(
        definition,
        artifact_ref=arguments.artifact_ref,
        image_digest=arguments.image_digest,
    )
    arguments.output.write_text(
        json.dumps(package, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
