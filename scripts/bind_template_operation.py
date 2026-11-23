"""Activate one reviewed template operation after its tenant passes checks."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from example_server.registry import RegistryManifest


def main() -> int:
    """Parse command-line arguments and run this tool."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument(
        "--api-url",
        default=os.environ.get("PERSISTENCE_API_URL", "http://127.0.0.1:18100"),
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=Path.home() / ".config/mcp-saas/template-registry-write-token",
    )
    args = parser.parse_args()
    api = urlsplit(args.api_url)
    endpoint = urlsplit(args.endpoint)
    if not api.hostname or api.path not in {"", "/"}:
        parser.error("--api-url must be an API origin without a path.")
    if api.scheme != "https" and not (
        api.scheme == "http" and api.hostname in {"127.0.0.1", "localhost"}
    ):
        parser.error("Use HTTPS or the VM loopback API origin.")
    try:
        manifest = RegistryManifest.model_validate_json(
            args.manifest.read_text(encoding="utf-8")
        )
        if len(manifest.tenants) != 1:
            raise ValueError("Route activation requires a one-tenant manifest.")
        tenant = manifest.tenants[0]
        if tenant.tenant_id != args.tenant_id or tenant.lifecycle_state != "active":
            raise ValueError(
                "Tenant identity or lifecycle does not match the manifest."
            )
        expected_app = f"your-namespace-tenant-{tenant.tenant_id}."
        if (
            endpoint.scheme != "http"
            or not endpoint.hostname
            or not endpoint.hostname.startswith(expected_app)
            or not endpoint.hostname.endswith(".svc.cluster.local")
            or endpoint.path != "/mcp"
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("Endpoint must be this tenant's private Code Engine URL.")
        pattern = next(
            item
            for item in manifest.patterns
            if item.pattern_id == tenant.pattern_id
            and item.pattern_version == tenant.pattern_version
            and item.lifecycle_state == "active"
        )
        operation = next(
            item
            for item in pattern.operations
            if item.operation_id == args.operation_id
        )
        token = args.token_file.read_text(encoding="utf-8").strip()
        if not token:
            raise ValueError("Template registry writer token file is empty.")
        path = (
            f"/v1/registry/pattern-templates/{tenant.pattern_id}/"
            f"{tenant.pattern_version}/operations/{args.operation_id}/route"
        )
        with httpx.Client(timeout=8.0) as client:
            response = client.post(
                args.api_url.rstrip("/") + path,
                json={"endpoint": args.endpoint},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            result = response.json()
        if not isinstance(result, dict) or result != {
            "route_id": tenant.pattern_id,
            "route_version": tenant.pattern_version,
            "operation_id": args.operation_id,
            "endpoint": args.endpoint,
            "mcp_tool_name": operation.mcp_tool_name,
            "protocol": "mcp",
        }:
            raise ValueError(
                "Persistence API route readback did not match the request."
            )
    except (
        OSError,
        ValueError,
        StopIteration,
        ValidationError,
        httpx.HTTPError,
    ) as error:
        print(
            f"Template operation binding failed ({type(error).__name__}).",
            file=sys.stderr,
        )
        return 1
    print(
        f"Activated {tenant.pattern_id}@{tenant.pattern_version}/"
        f"{args.operation_id} for tenant {tenant.tenant_id}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
