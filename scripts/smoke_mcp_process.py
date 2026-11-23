"""Start one synthetic MCP process for smoke_local_mcp_stack.py only."""

from __future__ import annotations

import argparse
import json

from example_server.platform import create_server
from example_server.registry import (
    OperationDefinition,
    PatternDefinition,
    RegisteredTenant,
    ResolvedUseCase,
    UseCaseDefinition,
)


class SmokeRegistry:
    """In-memory synthetic registry injected by the dedicated smoke harness."""

    def __init__(self, tenants: list[dict[str, str]]) -> None:
        self.tenants = tenants

    def resolve_use_case(
        self, use_case_id: str, version: str
    ) -> ResolvedUseCase | None:
        """Resolve the one smoke-test use case, or None for any other."""
        if (use_case_id, version) != ("directory.list", "1.0.0"):
            return None
        return ResolvedUseCase(
            use_case=UseCaseDefinition(
                use_case_id=use_case_id,
                use_case_version=version,
                pattern_id="sample-directory",
                pattern_version="1.0.0",
                operation_id="list_entries",
                lifecycle_state="active",
                tenant_ids=[item["tenant_id"] for item in self.tenants],
            ),
            pattern=PatternDefinition(
                pattern_id="sample-directory",
                pattern_version="1.0.0",
                lifecycle_state="active",
                title="Synthetic generic directory fixture",
                capabilities=["directory.list"],
                catalog_names=["programs"],
                operations=[
                    OperationDefinition(
                        operation_id="list_entries",
                        mcp_tool_name="list_entries",
                        request_schema_id="mcp.no-arguments.v1",
                        response_schema_id="mcp.pattern-result.v1",
                        access_mode="read_only",
                    )
                ],
            ),
            tenants=[
                RegisteredTenant(
                    tenant_id=item["tenant_id"],
                    pattern_id="sample-directory",
                    pattern_version="1.0.0",
                    endpoint=item["endpoint"],
                )
                for item in self.tenants
            ],
        )


def main() -> None:
    """Parse command-line arguments and run this tool."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=("core", "tenant"), required=True)
    parser.add_argument("--tenant-id", default="")
    parser.add_argument("--cluster-id", default="sample-directory")
    parser.add_argument("--pattern-version", default="1.0.0")
    parser.add_argument("--cluster-config", default="{}")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--endpoints", default="{}")
    args = parser.parse_args()

    tenant_id = args.tenant_id
    endpoint_config = json.loads(args.endpoints)
    if args.role == "core":
        server = create_server(
            role="core",
            registry_repository=SmokeRegistry(endpoint_config["registry_tenants"]),
        )
    else:
        server = create_server(
            role="tenant",
            tenant_id=tenant_id,
            cluster_id=args.cluster_id,
            pattern_version=args.pattern_version,
            cluster_config=json.loads(args.cluster_config),
        )

    server.run(transport="streamable-http", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
