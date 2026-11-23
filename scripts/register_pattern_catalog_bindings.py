"""Register reviewed tenant catalog bindings through the Persistence API."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from example_server.registry import CatalogBindingDefinition, RegistryManifest


def catalog_binding_payload(
    binding: CatalogBindingDefinition,
) -> dict[str, object]:
    """Map the manifest pattern identity to the Persistence API cluster ID."""
    payload = binding.model_dump(mode="json")
    payload["cluster_id"] = payload.pop("pattern_id")
    return payload


def main() -> int:
    """Parse command-line arguments and run this tool."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--api-url",
        default=os.environ.get("PERSISTENCE_API_URL", "http://127.0.0.1:18100"),
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=Path.home() / ".config/mcp-saas/registry-write-token",
    )
    args = parser.parse_args()
    parsed = urlsplit(args.api_url)
    if not parsed.hostname or parsed.path not in {"", "/"}:
        parser.error("--api-url must be an API origin without a path.")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
    ):
        parser.error("Use HTTPS or the VM loopback API origin.")
    try:
        manifest = RegistryManifest.model_validate_json(
            args.manifest.read_text(encoding="utf-8")
        )
        if manifest.catalog_bindings is None or not manifest.catalog_bindings:
            raise ValueError("Manifest must declare at least one catalog binding.")
        token = args.token_file.read_text(encoding="utf-8").strip()
        if not token:
            raise ValueError("Registry writer token file is empty.")
        with httpx.Client(timeout=8.0) as client:
            for binding in manifest.catalog_bindings:
                payload = catalog_binding_payload(binding)
                response = client.post(
                    args.api_url.rstrip("/") + "/v1/registry/pattern-catalog-bindings",
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict) or any(
                    result.get(key) != payload[key]
                    for key in (
                        "tenant_id",
                        "cluster_id",
                        "pattern_version",
                        "dataset_id",
                        "binding_context",
                    )
                ):
                    raise ValueError("Persistence API binding readback differed.")
    except (
        OSError,
        ValueError,
        ValidationError,
        httpx.HTTPError,
    ) as error:
        print(
            f"Catalog binding registration failed ({type(error).__name__}).",
            file=sys.stderr,
        )
        return 1
    print(f"Registered and read back {len(manifest.catalog_bindings)} tenant bindings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
