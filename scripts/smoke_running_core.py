"""Verify the running core across Eligibility and a generic cluster tenant."""

from __future__ import annotations

import asyncio
import json

from fastmcp import Client


async def main() -> None:
    """Route one request to each registered cluster and verify tenant results."""
    async with Client("http://127.0.0.1:8080/mcp", timeout=10.0) as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools} == {"route_use_case"}
        response = await client.call_tool(
            "route_use_case",
            {"use_case_id": "program.discover", "use_case_version": "1.0.0"},
        )
        directory = await client.call_tool(
            "route_use_case",
            {"use_case_id": "directory.list", "use_case_version": "1.0.0"},
        )
    assert not response.is_error
    result = response.data
    assert result["status"] == "ok"
    assert [tenant["tenant_id"] for tenant in result["results"]] == [
        "federal-agency",
        "state-kansas",
    ]
    assert not directory.is_error
    directory_result = directory.data
    assert directory_result["status"] == "ok"
    directory_results = {
        tenant["tenant_id"]: tenant["result"] for tenant in directory_result["results"]
    }
    assert set(directory_results) == {"directory-demo", "directory-partner"}
    assert [
        entry["id"] for entry in directory_results["directory-demo"]["entries"]
    ] == [
        "sample-a",
        "sample-b",
    ]
    assert [
        entry["id"] for entry in directory_results["directory-partner"]["entries"]
    ] == ["partner-a", "partner-b"]
    print(
        json.dumps(
            {
                "result": "PASS",
                "eligibility_results": result["results"],
                "directory_results": directory_result["results"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
