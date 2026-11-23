"""Run a real Streamable HTTP smoke test across core and two tenant processes."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

from fastmcp import Client

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_TENANTS = [
    {
        "tenant_id": "federal-agency",
        "cluster_id": "sample-directory",
        "cluster_config": {
            "entries": [{"id": "sample-a", "label": "Synthetic Directory Alpha"}],
        },
    },
    {
        "tenant_id": "state-kansas",
        "cluster_id": "sample-directory",
        "cluster_config": {
            "entries": [{"id": "sample-b", "label": "Synthetic Directory Beta"}],
        },
    },
]


def available_port() -> int:
    """Find a TCP port that is free now (the socket closes before it returns)."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def captured_output(log_file: Any, limit: int = 4000) -> str:
    """Return the tail of a child process's captured output for error messages."""
    log_file.flush()
    log_file.seek(0)
    text = log_file.read().decode("utf-8", errors="replace").strip()
    return text[-limit:] if text else "(no output captured)"


def wait_until_healthy(
    process: subprocess.Popen[Any], port: int, log_file: Any | None = None
) -> None:
    """Wait for a server health route or fail with its captured process output."""
    deadline = time.monotonic() + 20
    health_url = f"http://127.0.0.1:{port}/health"
    detail = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            if log_file is not None:
                detail = f"\n{captured_output(log_file)}"
            raise RuntimeError(
                f"MCP server exited during startup (exit={process.returncode}).{detail}"
            )
        try:
            if not health_url.startswith("http://127.0.0.1"):
                raise ValueError(f"health URL must be local http: {health_url}")
            with urllib.request.urlopen(  # nosec B310 - local http URL, checked above
                health_url, timeout=1
            ) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    if log_file is not None:
        detail = f"\n{captured_output(log_file)}"
    raise TimeoutError(f"MCP server did not become healthy: {health_url}{detail}")


async def verify_core_routes_to_both_tenants(core_port: int) -> dict[str, Any]:
    """Call the core MCP endpoint and verify both separate tenant responses."""
    async with Client(f"http://127.0.0.1:{core_port}/mcp", timeout=10.0) as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools} == {"route_use_case"}
        result = await client.call_tool(
            name="route_use_case",
            arguments={
                "use_case_id": "directory.list",
                "use_case_version": "1.0.0",
            },
        )
    assert not result.is_error
    data = result.data
    assert data["status"] == "ok"
    assert [row["tenant_id"] for row in data["results"]] == [
        "federal-agency",
        "state-kansas",
    ]
    return data


def main() -> None:
    """Start three real HTTP MCP apps, verify routed results, then stop them."""
    processes: list[subprocess.Popen[Any]] = []
    log_files = [tempfile.TemporaryFile() for _ in range(3)]
    ports = [available_port() for _ in range(3)]
    endpoint_config = json.dumps(
        {
            "registry_tenants": [
                {
                    "tenant_id": tenant["tenant_id"],
                    "endpoint": f"http://127.0.0.1:{ports[index + 1]}/mcp",
                }
                for index, tenant in enumerate(SYNTHETIC_TENANTS)
            ],
        }
    )

    try:
        for index in range(3):
            environment = os.environ.copy()
            environment.pop("PORT", None)
            role = "core" if index == 0 else "tenant"
            tenant_args = (
                ["--tenant-id", SYNTHETIC_TENANTS[index - 1]["tenant_id"]]
                if role == "tenant"
                else []
            )
            command = [
                sys.executable,
                str(ROOT / "scripts/smoke_mcp_process.py"),
                "--role",
                role,
                "--port",
                str(ports[index]),
                "--endpoints",
                endpoint_config,
                *tenant_args,
            ]
            if role == "tenant":
                tenant = SYNTHETIC_TENANTS[index - 1]
                command.extend(
                    [
                        "--cluster-id",
                        tenant["cluster_id"],
                        "--pattern-version",
                        "1.0.0",
                        "--cluster-config",
                        json.dumps(tenant["cluster_config"]),
                    ]
                )
            processes.append(
                subprocess.Popen(
                    command,
                    cwd=ROOT,
                    env=environment,
                    stdout=log_files[index],
                    stderr=subprocess.STDOUT,
                )
            )

        for process, port, log_file in zip(processes, ports, log_files, strict=True):
            wait_until_healthy(process, port, log_file)

        result = asyncio.run(verify_core_routes_to_both_tenants(ports[0]))
        print(
            json.dumps(
                {"result": "PASS", "tenant_results": result["results"]}, indent=2
            )
        )
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for log_file in log_files:
            log_file.close()


if __name__ == "__main__":
    main()
