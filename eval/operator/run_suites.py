"""Run the automated operator test suites against a live MCP Core.

Suites live in eval/operator/suites/<Snn-objective>.json.
Each suite declares its dependencies, start conditions, stop rule and tests;
each test declares one action and its pass/fail assertions.

Safety: this suite runner is read-only. Its default MCP client allows only
read-only tools and routed operations. The shared client supports an explicit,
route-scoped write allowlist for application callers; the test-suite Runner
never supplies it. Any other tool name or routed operation stops the run. The
operator token is read from a file or environment variable at run time and
never printed or saved.

Usage (from the repo root):
    python eval/operator/run_suites.py --token-file PATH
    python eval/operator/run_suites.py --token-file PATH --suite S01 --suite S03
    python eval/operator/run_suites.py --token-file PATH \
        --personas eval/synthetic-datasets/expected-outcomes.json

Results go to --out-dir (default: ~/mcp-test-runs/<run id>/), outside Git.
Only the sanitized RUN-SUMMARY.md is meant to be shared or committed.
Standard library only; no project dependencies.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITES_DIR = HERE / "suites"
DEFAULT_URL = "https://YOUR-CORE-APP.REGION.codeengine.appdomain.cloud/mcp"
READ_ONLY_TOOLS = {
    "describe_core_capabilities",
    "read_core_workflow",
    "list_core_routes",
    "list_cluster_templates",
    "read_cluster_capability",
    "read_cluster_schema",
    "list_registered_tenants",
    "read_tenant_binding",
    "read_source_metadata",
    "read_source_policy_state",
    "read_program_candidate_publication",
    "read_evaluation_fixture",
    "list_evaluation_fixtures",
    "validate_cluster_template",
    "validate_tenant_onboarding",
    "get_fleet_operations_report",
    "read_profile",
    "route_operation",
}
READ_ONLY_OPERATIONS = {
    "program.inventory.list",
    "program.inventory.read",
    "program.criteria.extract.normalized",
    "program.discover",
    "program.evaluate",
    "program.results",
    "program.requirements.preview",
    "program.inventory.search",
    "program.discovery.external.search",
    "program.candidate.normalize",
    "program.candidate.requirements.list",
    "program.candidate.requirements.read",
}
OUTCOMES = {"met", "unmet", "unknown", "not_applicable"}


# Phase profiles are declarative files beside the suites: <name>.profile.json.
# client-only: the stack runs with operator tools disabled (W2).
def available_profiles() -> list[str]:
    """Profile names: full plus every suites/<name>.profile.json."""
    return ["full"] + sorted(
        p.name.removesuffix(".profile.json") for p in SUITES_DIR.glob("*.profile.json")
    )


def load_profile(name: str) -> dict:
    """Load a phase profile; assertions and not-applicable classes live in the file."""
    path = SUITES_DIR / f"{name}.profile.json"
    profile = json.loads(path.read_text(encoding="utf-8"))
    if profile.get("profile_id") != name:
        raise SystemExit(f"{path.name}: profile_id must be {name!r}")
    return profile


def profile_na_tools(profile: dict) -> set[str]:
    """Tools whose tests are not applicable: asserted absent, plus declared extras."""
    absent: set[str] = set()
    s01 = profile.get("test_assertions", {}).get("S01", {})
    for assertion in s01.get("S01-T01", []):
        if assertion.get("op") == "contains_none":
            absent |= set(assertion["value"])
    return absent | set(profile.get("not_applicable_tools", []))


FAST_SUITES_EXCLUDE_KEY = "persona_tests"  # fast mode skips the persona suites


class SafetyStop(Exception):
    """Raised when a test would call a tool outside the read-only allowlist."""


class McpClient:
    """Minimal MCP Streamable HTTP client (JSON-RPC over POST, JSON or SSE)."""

    def __init__(
        self,
        url: str,
        headers: dict[str, str],
        timeout: int = 90,
        unavailable_retries: int = 1,
        retry_pause_seconds: float = 15.0,
        sleep: Callable[[float], None] = time.sleep,
        client_name: str = "usda-operator-suite",
        client_version: str = "0.1",
        write_operations_by_route: dict[str, set[str] | frozenset[str]] | None = None,
        allow_profile_write: bool = False,
    ):
        self.allow_profile_write = allow_profile_write
        self.url, self.timeout = url, timeout
        self.client_name, self.client_version = client_name, client_version
        self.unavailable_retries = unavailable_retries
        self.retry_pause_seconds = retry_pause_seconds
        self.sleep = sleep
        self.write_operations_by_route = {
            route_id: frozenset(operation_ids)
            for route_id, operation_ids in (write_operations_by_route or {}).items()
        }
        self.last_call_attempts = 0
        self.call_log: list[dict] | None = None
        self.tag: dict = {}
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **headers,
        }
        self.next_id = 1

    def _post(self, payload: dict) -> tuple[dict | None, dict]:
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self.headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = response.read().decode("utf-8")
            meta = {k.lower(): v for k, v in response.headers.items()}
        if not body.strip():
            return None, meta
        data_lines = [
            ln[5:].strip() for ln in body.splitlines() if ln.startswith("data:")
        ]
        text = data_lines[-1] if data_lines else body
        return json.loads(text), meta

    def rpc(self, method: str, params: dict | None = None) -> dict:
        """Send a JSON-RPC request and return its result."""
        payload = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            payload["params"] = params
        self.next_id += 1
        message, _ = self._post(payload)
        if message is None:
            raise RuntimeError(f"empty response to {method}")
        if "error" in message:
            raise RuntimeError(f"MCP returned an error for {method}")
        return message["result"]

    def initialize(self) -> None:
        """Open the MCP session."""
        payload = {
            "jsonrpc": "2.0",
            "id": self.next_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {
                    "name": self.client_name,
                    "version": self.client_version,
                },
            },
        }
        self.next_id += 1
        _, meta = self._post(payload)
        if meta.get("mcp-session-id"):
            self.headers["mcp-session-id"] = meta["mcp-session-id"]
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _log_call(self, name, arguments, response, attempt, started_utc, tick) -> None:
        """Record one call with its correlation ID and any package IDs."""
        if self.call_log is None:
            return
        body = response if isinstance(response, dict) else {}
        self.call_log.append(
            {
                **self.tag,
                "tool": name,
                "route_id": arguments.get("route_id"),
                "operation_id": arguments.get("operation_id"),
                "arguments": arguments,
                "started_at_utc": started_utc,
                "duration_ms": round((time.perf_counter() - tick) * 1000),
                "attempt": attempt,
                "status": body.get("status"),
                "correlation_id": body.get("correlation_id"),
                "package_ids": find_values(body, PACKAGE_ID_KEYS),
                "ids": {
                    key: values
                    for key in TRACE_ID_KEYS
                    if (values := find_values(body, (key,)))
                },
                "trace_id": None,
                "span_id": None,
            }
        )

    def call_tool(self, name: str, arguments: dict) -> dict:
        """Call a tool, refusing any tool that is not on the read-only allowlist."""
        if name not in READ_ONLY_TOOLS and not (
            self.allow_profile_write and name == "write_profile"
        ):
            raise SafetyStop(f"tool '{name}' is not on the read-only allowlist")
        if name == "route_operation":
            route_id = arguments.get("route_id")
            operation_id = arguments.get("operation_id")
            route_writes = self.write_operations_by_route.get(route_id, frozenset())
            if (
                operation_id not in READ_ONLY_OPERATIONS
                and operation_id not in route_writes
            ):
                raise SafetyStop("routed operation is not on the read-only allowlist")
        for attempt in range(1, self.unavailable_retries + 2):
            started_utc = datetime.now(UTC).isoformat()
            tick = time.perf_counter()
            result = self.rpc("tools/call", {"name": name, "arguments": arguments})
            if result.get("structuredContent") is not None:
                content = result["structuredContent"]
                response = (
                    content.get("result", content) if len(content) == 1 else content
                )
            else:
                text = "".join(
                    part.get("text", "")
                    for part in result.get("content", [])
                    if part.get("type") == "text"
                )
                try:
                    response = json.loads(text)
                except json.JSONDecodeError:
                    response = {
                        "_unstructured_error": bool(result.get("isError", False))
                    }
            self.last_call_attempts = attempt
            self._log_call(name, arguments, response, attempt, started_utc, tick)
            if (
                not isinstance(response, dict)
                or response.get("status") != "unavailable"
                or attempt > self.unavailable_retries
            ):
                return response
            self.sleep(self.retry_pause_seconds)
        raise AssertionError("unreachable retry loop")


PACKAGE_ID_KEYS = (
    "package_id",
    "requirements_package_id",
    "criteria_package_id",
    "active_ruleset_package_id",
    "binder_id",
)
# Every identifier worth joining to a trace: the profile, the saved package, the
# program, the source, the tenant and any claim, write or revision the call made.
TRACE_ID_KEYS = (
    "profile_id",
    "profile_ref",
    "claim_id",
    "write_id",
    "revision_id",
    "request_id",
    "session_id",
    "tenant_id",
    "program_id",
    "listing_id",
    "source_id",
    "source_record_id",
    "template_id",
    "fixture_set_id",
    "fixture_id",
) + PACKAGE_ID_KEYS


def find_values(data: object, keys: tuple[str, ...]) -> list[str]:
    """Collect the distinct string values stored under any of the given keys."""
    found: list[str] = []
    stack = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if key in keys and isinstance(value, str) and value not in found:
                    found.append(value)
                elif isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(item, list):
            stack.extend(item)
    return found


def get_path(data: object, path: str) -> object:
    """Resolve a dotted path with [n] and [*] list selectors."""
    current = [data]
    for token in re.findall(r"[^.\[\]]+|\[\*\]|\[\d+\]", path):
        nxt = []
        for item in current:
            if token == "[*]":
                nxt.extend(item if isinstance(item, list) else [])
            elif token.startswith("["):
                index = int(token[1:-1])
                if isinstance(item, list) and index < len(item):
                    nxt.append(item[index])
            elif isinstance(item, dict) and token in item:
                nxt.append(item[token])
        current = nxt
    if "[*]" in path:
        return current
    return current[0] if current else None


def check(observed: object, assertion: dict) -> tuple[bool, str]:
    """Evaluate one assertion; return (passed, short observed description)."""
    op, expected = assertion["op"], assertion.get("value")
    value = get_path(observed, assertion["path"]) if assertion.get("path") else observed
    shown = json.dumps(value)[:160] if not isinstance(value, str) else value[:160]
    if op == "eq":
        return value == expected, shown
    if op == "ne":
        return value != expected, shown
    if op == "one_of":
        return value in expected, shown
    if op == "len_eq":
        ok = isinstance(value, list) and len(value) == expected
        return ok, f"len={len(value) if isinstance(value, list) else 'not-list'}"
    if op == "exists":
        return value not in (None, [], {}), shown
    if op == "is_null":
        return value is None, shown
    if op == "contains":
        return isinstance(value, list) and expected in value, shown
    if op == "contains_all":
        return isinstance(value, list) and all(x in value for x in expected), shown
    if op == "contains_none":
        return isinstance(value, list) and not any(x in value for x in expected), shown
    if op == "len_gte":
        return isinstance(value, list) and len(
            value
        ) >= expected, f"len={len(value or [])}"
    if op == "len_lt":
        ok = isinstance(value, list) and len(value) < expected
        return ok, f"len={len(value) if isinstance(value, list) else 'not-list'}"
    if op == "any_match":
        items = value if isinstance(value, list) else []
        ok = any(all(get_path(i, k) == v for k, v in expected.items()) for i in items)
        return ok, f"{len(items)} item(s)"
    if op == "all_match":
        items = value if isinstance(value, list) else []
        ok = bool(items) and all(
            all(get_path(i, k) == v for k, v in expected.items()) for i in items
        )
        return ok, f"{len(items)} item(s)"
    raise ValueError(f"unknown assertion op {op}")


class Runner:
    """Executes suites in dependency order and records sanitized results."""

    def __init__(self, args: argparse.Namespace, token: str | None):
        self.args, self.token = args, token
        now = datetime.now(UTC)
        self.context: dict = {
            "repo_root": args.repo_root,
            "now_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "run_start_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "today_start_utc": now.strftime("%Y-%m-%dT00:00:00Z"),
        }
        self.records: list[dict] = []
        self.calls: list[dict] = []
        self.current: dict = {}
        self.suite_status: dict[str, str] = {}
        self.deferred: list[dict] = []
        self.last_action_attempts = 1

    def client(
        self, auth: str, calls: list | None = None, tag: dict | None = None
    ) -> McpClient:
        """Return a client with the access level a test asks for."""
        headers = {}
        if auth == "operator":
            if not self.token:
                raise RuntimeError("operator token not available")
            headers["x-mcp-saas-operator-token"] = self.token
        elif auth == "wrong":
            headers["x-mcp-saas-operator-token"] = "invalid-test-credential"
        mcp = McpClient(
            self.args.url,
            headers,
            unavailable_retries=getattr(self.args, "unavailable_retries", 2),
            retry_pause_seconds=getattr(self.args, "retry_pause_seconds", 15.0),
        )
        mcp.call_log = self.calls if calls is None else calls
        mcp.tag = self.current if tag is None else tag
        mcp.initialize()
        return mcp

    def resolve(self, value: object) -> object:
        """Replace ${name} placeholders from the run context, recursively.

        ``${report_end_utc}`` becomes the current UTC timestamp, and a missing
        key becomes an empty string.
        """
        if isinstance(value, str):
            if value == "${report_end_utc}":
                return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            return re.sub(
                r"\$\{([^}]+)\}", lambda m: str(self.context.get(m.group(1), "")), value
            )
        if isinstance(value, dict):
            return {k: self.resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.resolve(v) for v in value]
        return value

    def act(self, test: dict) -> object:
        """Perform one test's action and return what it observed."""
        action = test["action"]
        mcp = self.client(test.get("auth", "none"))
        kind = action["type"]
        if kind == "tools_list":
            return {"tools": [t["name"] for t in mcp.rpc("tools/list")["tools"]]}
        if kind == "call":
            response = mcp.call_tool(
                action["tool"], self.resolve(action.get("args", {}))
            )
            self.last_action_attempts = mcp.last_call_attempts
            return response
        if kind == "call_with_file":
            path = Path(self.resolve(action["file"]))
            if not path.exists():
                return {"_skip": f"input file not found: {path.name}"}
            arg = json.loads(path.read_text(encoding="utf-8"))
            return mcp.call_tool(action["tool"], {action["arg_name"]: arg})
        if kind == "derived":
            response = self.derived(
                action["check"], mcp, self.resolve(action.get("target"))
            )
            self.last_action_attempts = mcp.last_call_attempts
            return response
        raise ValueError(f"unknown action type {kind}")

    def derived(self, name: str, mcp: McpClient, target: str | None = None) -> dict:
        """Cross-checks computed from the scoped read-only registry tools."""
        routes = mcp.call_tool("list_core_routes", {"page_size": 100}).get("routes", [])
        templates = mcp.call_tool("list_cluster_templates", {"page_size": 100}).get(
            "templates", []
        )
        tenants = mcp.call_tool("list_registered_tenants", {"page_size": 100}).get(
            "tenants", []
        )
        if name == "tenant_route":
            tenant_id = target or ""
            if not tenant_id:
                return {"route_id": None, "operations": []}
            tenant_matches = [
                t
                for t in tenants
                if t.get("tenant_id") == tenant_id
                and t.get("lifecycle_state") == "active"
            ]
            if len(tenant_matches) != 1:
                return {
                    "route_id": None,
                    "operations": [],
                }
            match = [
                r
                for r in routes
                if r.get("tenant_id") == tenant_id
                and r.get("lifecycle_state") == "active"
            ]
            first = match[0] if match else {}
            return {
                "route_id": first.get("route_id"),
                "operations": sorted(r["operation_id"] for r in match),
            }
        if name == "active_tenant":
            active = [
                t
                for t in tenants
                if t.get("tenant_id") == (target or "")
                and t.get("lifecycle_state") == "active"
            ]
            first = active[0] if len(active) == 1 else {}
            return {
                "active_count": len(active),
                "template_id": first.get("template_id"),
                "template_version": first.get("template_version"),
            }
        if name == "active_template_versions":
            return {
                "versions": sorted(
                    t["template_version"]
                    for t in templates
                    if t.get("template_id") == (target or "")
                    and t.get("lifecycle_state") == "active"
                )
            }
        if name == "template":
            template_id, _, version = (target or "").partition("@")
            match = next(
                (
                    t
                    for t in templates
                    if t["template_id"] == template_id
                    and t["template_version"] == version
                ),
                {},
            )
            return {
                "lifecycle_state": match.get("lifecycle_state"),
                "descriptor_sha256": match.get("descriptor_sha256"),
                "operations": sorted(
                    o["operation_id"] for o in match.get("operations", [])
                ),
                "input_schema_refs": match.get("input_schema_refs", []),
            }
        if name == "tenant_routes_cover_template_operations":
            gaps = []
            for tenant in tenants:
                if tenant.get("lifecycle_state") != "active":
                    continue
                tenant_id = tenant["tenant_id"]
                template_version = tenant["template_version"]
                binding = mcp.call_tool(
                    "read_tenant_binding",
                    {
                        "template_id": tenant["template_id"],
                        "template_version": template_version,
                        "tenant_id": tenant_id,
                    },
                )
                binding_tenant = binding.get("tenant", {})
                if binding.get("status") != "available" or not binding_tenant:
                    gaps.append(f"{tenant_id}@{template_version}:binding_unavailable")
                    continue
                declared = set(binding_tenant.get("operation_ids", []))
                routed = {
                    r["operation_id"]
                    for r in routes
                    if r.get("tenant_id") == tenant_id
                    and r.get("lifecycle_state") == "active"
                }
                for op in sorted(declared - routed):
                    gaps.append(f"{tenant_id}@{template_version}:{op}")
            return {"gaps": gaps}
        if name == "active_templates_publish_input_schemas":
            missing = [
                f"{t['template_id']}@{t['template_version']}"
                for t in templates
                if t.get("lifecycle_state") == "active"
                and not t.get("input_schema_refs")
            ]
            return {"missing": missing}
        if name == "active_routes_are_tenant_scoped":
            unscoped = [
                f"{r['route_id']}:{r['operation_id']}"
                for r in routes
                if r.get("lifecycle_state") == "active"
                and not r.get("tenant_id")
                and r.get("operation_id") in {"program.discover", "program.evaluate"}
            ]
            return {"unscoped_legacy": unscoped}
        if name == "duplicate_active_routes_for_operation":
            operation_id = target or "program.inventory.search"
            matching = [
                r
                for r in routes
                if r.get("lifecycle_state") == "active"
                and r.get("operation_id") == operation_id
            ]
            return {"routes": [r["route_id"] for r in matching]}
        raise ValueError(f"unknown derived check {name}")

    def persona_tests(self, suite: dict) -> list[dict]:
        """Expand persona-driven tests from expected-outcomes.json."""
        spec = suite["persona_tests"]
        path = Path(self.args.personas) if self.args.personas else None
        if not path or not path.exists():
            return [
                {
                    "id": f"{suite['suite_id']}-P00",
                    "title": "Persona file not provided",
                    "_skip": "pass --personas <expected-outcomes.json>",
                }
            ]
        data = json.loads(path.read_text(encoding="utf-8"))
        tests = []
        for n, persona in enumerate(data["personas"], start=1):
            set_ok = persona["persona_set"].startswith(spec["set_prefix"])
            typed = persona["case_type"] in spec["case_types"]
            tagged = any(tag in persona["scenario"] for tag in spec["include_tags"])
            if not set_ok or not (typed or tagged):
                self.deferred.append(
                    {
                        "id": f"{suite['suite_id']}-P{n:02d}",
                        "scenario": persona.get("scenario", "unknown"),
                        "reason": (
                            "edge fixture has no approved deterministic outcome in "
                            "the current source/rule scope"
                            if set_ok
                            else "fixture is assigned to "
                            f"{persona.get('persona_set', 'an unassigned set')}"
                        ),
                    }
                )
                continue
            tests.append(
                {
                    "id": f"{suite['suite_id']}-P{n:02d}",
                    "persona": persona,
                    "profile_mode": spec.get("profile_mode", "navigator"),
                    "defect_ref": suite.get("defect_ref"),
                }
            )
        return tests

    def run_persona(
        self, test: dict, calls: list | None = None, tag: dict | None = None
    ) -> tuple[str, list[dict]]:
        """Run one persona test and return its status and rows."""
        persona = test["persona"]
        if test.get("profile_mode") == "general":
            profile = persona.get(
                "machine_input_general_profile",
                persona["expected_general_end_state_profile"],
            )
        else:
            profile = persona.get(
                "machine_input_profile", persona["expected_end_state_profile"]
            )
        expected = persona["expected"]
        machine_expected = persona.get("machine_expected", expected)
        mcp = self.client("none", calls, tag)
        route = {
            "route_id": self.context["tenant_route_id"],
        }
        checks = []
        selected = expected["candidates"][0] if expected["candidates"] else None
        evaluations: dict = {}  # one evaluation per program, reused by each criterion
        discover_args = {"profile_payload": profile, "ambiguous_attributes": []}
        if selected:  # discovery of a chosen program needs selected_program_id
            discover_args["selected_program_id"] = selected
        disc = mcp.call_tool(
            "route_operation",
            {
                **route,
                "operation_id": "program.discover",
                "arguments": discover_args,
            },
        )
        max_attempts = getattr(mcp, "last_call_attempts", 1)
        disc_result = disc.get("result") or disc
        found = [c.get("program_id") for c in disc_result.get("candidates", [])]
        if not found:
            found = [c.get("program_id") for c in get_path(disc, "candidates") or []]
        chosen = (disc_result.get("profile_readiness") or {}).get("selected_program_id")
        if chosen and chosen not in found:
            found.append(chosen)
        discovery_observation = f"found={found}; status={disc_result.get('status')}"
        if disc_result.get("unavailable_sources"):
            discovery_observation += (
                f"; unavailable_sources={disc_result['unavailable_sources']}"
            )
        checks.append(
            {
                "check": "discover status ok",
                "passed": disc.get("status") == "ok",
                "observed": disc.get("status"),
            }
        )
        if persona["case_type"] == "positive":
            missing = [p for p in expected["candidates"] if p not in found]
            checks.append(
                {
                    "check": "expected candidates discovered",
                    "passed": not missing,
                    "observed": discovery_observation,
                }
            )
        for criterion_id, raw in machine_expected["criteria"].items():
            outcome = raw.split()[-1]
            if outcome not in OUTCOMES:
                continue
            program = expected["candidates"][0] if expected["candidates"] else None
            if program not in evaluations:
                evaluations[program] = mcp.call_tool(
                    "route_operation",
                    {
                        **route,
                        "operation_id": "program.evaluate",
                        "arguments": {
                            "profile_payload": profile,
                            "program_id": program,
                        },
                    },
                )
            ev = evaluations[program]
            max_attempts = max(max_attempts, getattr(mcp, "last_call_attempts", 1))
            evaluation_result = ev.get("result") or ev
            evidence = (
                evaluation_result.get("criteria_evidence")
                or evaluation_result.get("evidence")
                or ev.get("evidence")
                or []
            )
            criterion_evidence = next(
                (e for e in evidence if e.get("criterion_id") == criterion_id),
                None,
            )
            observed = (
                criterion_evidence.get("outcome")
                if criterion_evidence
                else f"status={evaluation_result.get('status')}"
                + (
                    f"; unavailable_sources={evaluation_result['unavailable_sources']}"
                    if evaluation_result.get("unavailable_sources")
                    else ""
                )
            )
            checks.append(
                {
                    "check": f"{criterion_id} == {outcome}",
                    "passed": observed == outcome,
                    "observed": observed,
                }
            )
            source_ids = (
                criterion_evidence.get("source_ids", []) if criterion_evidence else []
            )
            checks.append(
                {
                    "check": f"{criterion_id} cites source evidence",
                    "passed": bool(source_ids),
                    "observed": f"source_count={len(source_ids)}",
                }
            )
            official = evaluation_result.get("official_eligibility_determination")
            checks.append(
                {
                    "check": "evaluation is informational only",
                    "passed": official is False,
                    "observed": str(official),
                }
            )
        test["_attempts"] = max_attempts
        self.last_action_attempts = max_attempts
        status = "PASS" if all(c["passed"] for c in checks) else "FAIL"
        return status, checks

    def run_suite(self, suite: dict) -> str:
        """Run one suite after its dependencies and return its status."""
        sid = suite["suite_id"]
        profile = self.phase_profile()
        client_only = profile is not None
        na_suites = profile.get("not_applicable_suites", {}) if profile else {}
        registry_dependent = (
            client_only and sid in na_suites and not getattr(self.args, "suite", [])
        )
        if client_only and (suite.get("requires_operator") or registry_dependent):
            self.records.append(
                {
                    "suite": sid,
                    "test": f"{sid} (suite)",
                    "status": "NOT_APPLICABLE",
                    "detail": f"profile {profile['profile_id']}: "
                    + (
                        na_suites[sid]
                        if registry_dependent
                        else "operator surface not enabled"
                    ),
                }
            )
            return "NOT_APPLICABLE"
        for dep in suite.get("depends_on", []):
            if self.suite_status.get(dep) not in (
                "PASS",
                "PASS_WITH_DEFECTS",
                "NOT_APPLICABLE",
            ):
                self.records.append(
                    {
                        "suite": sid,
                        "test": f"{sid} (suite)",
                        "status": "BLOCKED",
                        "detail": f"dependency {dep} = "
                        f"{self.suite_status.get(dep, 'not run')}",
                    }
                )
                return "BLOCKED"
        if suite.get("requires_operator") and not self.token:
            self.records.append(
                {
                    "suite": sid,
                    "test": f"{sid} (suite)",
                    "status": "BLOCKED",
                    "detail": "start condition: operator token not provided",
                }
            )
            return "BLOCKED"
        tests = suite.get("tests", [])
        if "persona_tests" in suite:
            tests = tests + self.persona_tests(suite)
        statuses = []
        if client_only:
            tests = [self.client_only_view(t) for t in tests]
        precomputed = self.run_personas_parallel(sid, tests)
        for test in tests:
            self.current.update(suite=sid, test=test["id"])
            started = time.perf_counter()
            self.last_action_attempts = 1
            if test.get("_na"):
                rec = {"status": "NOT_APPLICABLE", "detail": test["_na"]}
            elif test["id"] in precomputed:
                rec, worker_calls, took = precomputed[test["id"]]
                self.calls.extend(worker_calls)
                started = time.perf_counter() - took
                self.last_action_attempts = test.get("_attempts", 1)
            elif test.get("_skip"):
                rec = {"status": "SKIP", "detail": test["_skip"]}
            elif test.get("_deferred"):
                rec = {"status": "DEFERRED", "detail": test["_deferred"]}
            elif "persona" in test:
                try:
                    status, checks = self.run_persona(test)
                    rec = {
                        "status": status,
                        "checks": checks,
                        "title": f"{test['persona']['profile_id']} "
                        f"({test['persona']['case_type']})",
                    }
                except (urllib.error.URLError, RuntimeError, KeyError) as exc:
                    rec = {"status": "ERROR", "detail": type(exc).__name__}
            else:
                try:
                    observed = self.act(test)
                    if isinstance(observed, dict) and observed.get("_skip"):
                        rec = {"status": "SKIP", "detail": observed["_skip"]}
                    else:
                        for key, path in test.get("capture", {}).items():
                            self.context[key] = get_path(observed, path)
                        checks = []
                        for a in self.resolve(test.get("assertions", [])):
                            ok, shown = check(observed, a)
                            checks.append(
                                {
                                    "check": a.get("label")
                                    or f"{a.get('path')} {a['op']}",
                                    "passed": ok,
                                    "observed": shown,
                                }
                            )
                        rec = {
                            "status": "PASS"
                            if all(c["passed"] for c in checks)
                            else "FAIL",
                            "checks": checks,
                        }
                except SafetyStop:
                    raise
                except (urllib.error.URLError, RuntimeError, KeyError) as exc:
                    rec = {"status": "ERROR", "detail": type(exc).__name__}
            rec.update(
                {
                    "suite": sid,
                    "test": test["id"],
                    "title": rec.get("title") or test.get("title", ""),
                    "defect_ref": test.get("defect_ref"),
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                    "attempts": self.last_action_attempts,
                }
            )
            gate = test.get("gate", suite.get("all_tests_gate", False))
            rec["gate"] = gate
            self.records.append(rec)
            statuses.append((rec["status"], gate))
            if rec["status"] in ("FAIL", "ERROR") and suite.get("stop_on_fail"):
                break
        bad = [(st, gate) for st, gate in statuses if st in ("FAIL", "ERROR")]
        if any(gate for _, gate in bad):
            return "FAIL"
        if bad:
            return "PASS_WITH_DEFECTS"
        if any(st == "PASS" for st, _ in statuses):
            return "PASS"
        if statuses and all(st == "NOT_APPLICABLE" for st, _ in statuses):
            return "NOT_APPLICABLE"
        return "SKIP"

    def phase_profile(self) -> dict | None:
        """The declarative phase profile selected with --profile, or None for full."""
        name = getattr(self.args, "profile", "full")
        if name == "full":
            return None
        if not hasattr(self, "_phase_profile"):
            self._phase_profile = load_profile(name)
        return self._phase_profile

    def client_only_view(self, test: dict) -> dict:
        """Adapt one test to the selected phase profile."""
        profile = self.phase_profile()
        tid = str(test.get("id", ""))
        sid = tid.split("-")[0]
        pid = profile["profile_id"]
        override = profile.get("test_assertions", {}).get(sid, {}).get(tid)
        if override is not None:
            return {
                **test,
                "title": f"{test.get('title', tid)} (profile {pid})",
                "assertions": override,
            }
        reason = profile.get("not_applicable_tests", {}).get(sid, {}).get(tid)
        if reason:
            return {**test, "_na": f"profile {pid}: {reason}"}
        action = test.get("action") or {}
        na_tools = profile_na_tools(profile)
        if test.get("auth") == "operator" or action.get("tool") in na_tools:
            return {**test, "_na": f"profile {pid}: operator surface not enabled"}
        return test

    def run_personas_parallel(self, sid: str, tests: list[dict]) -> dict:
        """Run persona tests in worker threads; each worker has its own client."""
        workers = max(1, getattr(self.args, "workers", 1))
        personas = [t for t in tests if "persona" in t and not t.get("_na")]
        if workers <= 1 or len(personas) < 2:
            return {}

        def work(test: dict):
            calls: list[dict] = []
            tag = {"suite": sid, "test": test["id"]}
            began = time.perf_counter()
            try:
                status, checks = self.run_persona(test, calls, tag)
                rec = {
                    "status": status,
                    "checks": checks,
                    "title": f"{test['persona']['profile_id']} "
                    f"({test['persona']['case_type']})",
                }
            except (urllib.error.URLError, RuntimeError, KeyError) as exc:
                rec = {"status": "ERROR", "detail": type(exc).__name__}
            return test["id"], (rec, calls, time.perf_counter() - began)

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            return dict(pool.map(work, personas))


def read_token(path: Path) -> str:
    """Return the operator token from a file.

    Accepts a file holding only the token, or a labelled file where the token is
    the first non-blank line after ``operator_token`` or ``Key:operator_token``.
    """
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()]
    lines = [ln for ln in lines if ln]
    labels = {"operator_token", "key:operator_token"}
    index = next(
        (position for position, line in enumerate(lines) if line.lower() in labels),
        None,
    )
    if index is not None:
        if index + 1 < len(lines):
            value = lines[index + 1]
            token = (
                value.split(":", 1)[1].strip()
                if value.lower().startswith("value:")
                else value
            )
            if token:
                return token
    if len(lines) == 1:
        return lines[0]
    raise SystemExit("token file: operator_token value not found")


def load_suites(selected: list[str]) -> list[dict]:
    """Load the suite definitions, optionally limited to the selected ones."""
    everything = []
    for path in sorted(SUITES_DIR.glob("S*.json")):
        suite = json.loads(path.read_text(encoding="utf-8"))
        suite["_folder"] = path.parent.name
        everything.append(suite)
    if not selected:
        return everything
    wanted = set(selected if isinstance(selected, (list, tuple, set)) else [selected])
    by_id = {s["suite_id"]: s for s in everything}
    queue = list(wanted)
    while queue:  # a selected suite also pulls in the suites it depends on
        for dep in by_id.get(queue.pop(), {}).get("depends_on", []):
            if dep not in wanted:
                wanted.add(dep)
                queue.append(dep)
    return [s for s in everything if s["suite_id"] in wanted]


def summary_markdown(run_id: str, args, suites, runner: Runner) -> str:
    """Build the markdown summary of a run."""
    counts: dict[str, int] = {}
    for r in runner.records:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    started_utc = datetime.strptime(run_id[1:], "%Y%m%d-%H%M%S").strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    persona_records = [
        record
        for record in runner.records
        if record.get("suite") == "S05" and "-P" in record.get("test", "")
    ]
    lines = [
        f"# Operator automated test run {run_id}",
        "",
        f"- Started (UTC): {started_utc}",
        f"- Core endpoint: {args.url.split('//')[1].split('.')[0]} (host shortened)",
        "- Personas file: "
        + (Path(args.personas).name if args.personas else "not provided"),
        f"- Totals: {', '.join(f'{k} {v}' for k, v in sorted(counts.items()))}",
        f"- Run settings: mode {getattr(args, 'mode', 'full')}, "
        f"profile {getattr(args, 'profile', 'full')}, "
        f"workers {getattr(args, 'workers', 1)}; "
        f"{len(runner.calls)} Core calls, "
        f"wall time {getattr(runner, 'wall_seconds', 0):.1f} s",
        f"- Persona coverage: {len(persona_records)} exercised, "
        f"{len(runner.deferred)} deferred, "
        f"{len(persona_records) + len(runner.deferred)} total",
        "",
        "| Suite | Objective | Result |",
        "| --- | --- | --- |",
    ]
    for s in suites:
        lines.append(
            f"| {s['suite_id']} {s['name']} | {s['objective']} | "
            f"{runner.suite_status.get(s['suite_id'], 'not run')} |"
        )
    lines += [
        "",
        "## Test results",
        "",
        "| Test | Title | Result | Defect | Attempts | ms |",
        "| --- | --- | --- | --- | ---: | ---: |",
    ]
    for r in runner.records:
        lines.append(
            f"| {r['test']} | {r.get('title', '')} | {r['status']} | "
            f"{r.get('defect_ref') or ''} | {r.get('attempts', '')} | "
            f"{r.get('latency_ms', '')} |"
        )
    failed = [r for r in runner.records if r["status"] in ("FAIL", "ERROR", "BLOCKED")]
    if failed:
        lines += ["", "## Failures and blocks", ""]
        for r in failed:
            lines.append(
                f"- **{r['test']}** {r.get('title', '')}: {r['status']}"
                + (f" ({r['detail']})" if r.get("detail") else "")
            )
            for c in r.get("checks", []):
                if not c["passed"]:
                    lines.append(
                        f"  - failed check: {c['check']} (observed: {c['observed']})"
                    )
    if runner.deferred:
        lines += ["", "## Scenario coverage not exercised", ""]
        for item in runner.deferred:
            lines.append(f"- **{item['scenario']}** ({item['id']}): {item['reason']}")
    if args.provenance_file:
        provenance_path = Path(args.provenance_file)
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        lines += [
            "",
            "## Preflight provenance",
            "",
            "| Component | Readback |",
            "| --- | --- |",
        ]
        for key, value in provenance.items():
            lines.append(f"| {key} | {value} |")
    lines += [
        "",
        "Read-only run. No credentials, endpoints or profile values are recorded.",
    ]
    return "\n".join(lines) + "\n"


def write_three_files(out: Path, prefix: str, run_id: str, suites, runner) -> None:
    """Write the Input, Response and Trace JSON files for one run.

    Input is what was asked (the suites). Response is what came back (one record per
    test). Trace is every call with its correlation ID and package IDs; trace_id and
    span_id stay null until the Phoenix pull fills them by correlation ID.
    """
    calls = runner.calls
    files = {
        "Input": {"run_id": run_id, "suites": suites},
        "Response": {
            "run_id": run_id,
            "suite_status": runner.suite_status,
            "records": runner.records,
            "deferred": runner.deferred,
        },
        "Trace": {
            "run_id": run_id,
            "correlation_ids": sorted(
                {c["correlation_id"] for c in calls if c["correlation_id"]}
            ),
            "package_ids": sorted({p for c in calls for p in c["package_ids"]}),
            "ids_by_kind": {
                key: sorted({v for c in calls for v in c.get("ids", {}).get(key, [])})
                for key in TRACE_ID_KEYS
                if any(c.get("ids", {}).get(key) for c in calls)
            },
            "calls": calls,
        },
    }
    for kind, body in files.items():
        (out / f"{prefix}-{kind}.json").write_text(
            json.dumps(body, indent=2), encoding="utf-8"
        )


def main() -> int:
    """Parse arguments and run the suites."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=os.environ.get("CORE_URL", DEFAULT_URL))
    parser.add_argument(
        "--token-file", help="file whose last non-blank line is the token"
    )
    parser.add_argument("--personas", help="path to expected-outcomes.json")
    parser.add_argument("--suite", action="append", default=[], help="e.g. S01")
    parser.add_argument("--out-dir", default=str(Path.home() / "mcp-test-runs"))
    parser.add_argument(
        "--unavailable-retries",
        type=int,
        default=2,
        help="additional attempts after a read-only call returns unavailable",
    )
    parser.add_argument(
        "--retry-pause-seconds",
        type=float,
        default=15.0,
        help="pause between bounded unavailable retries (default: 15)",
    )
    parser.add_argument(
        "--prefix",
        help="file name prefix, e.g. UC1-S1-E2E-Run1-Positive (default OPS-<run id>)",
    )
    parser.add_argument("--repo-root", default=str(HERE.parents[1]))
    parser.add_argument(
        "--provenance-file", help="sanitized preflight JSON outside Git"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="parallel persona workers (default 1 = serial; 4 is a good start)",
    )
    parser.add_argument(
        "--mode",
        choices=["full", "fast"],
        default="full",
        help="fast skips the persona suites (sub-second reads only)",
    )
    parser.add_argument(
        "--profile",
        choices=available_profiles(),
        default="full",
        help="phase profile from suites/<name>.profile.json; client-only: "
        "stack runs with operator tools disabled (W2)",
    )
    args = parser.parse_args()
    if args.unavailable_retries < 0 or args.retry_pause_seconds < 0:
        parser.error("retry count and pause must be non-negative")
    if args.workers < 1:
        parser.error("workers must be at least 1")

    token = os.environ.get("CORE_OPERATOR_TOKEN")
    if args.token_file:
        token = read_token(Path(args.token_file))

    run_id = "R" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    suites = load_suites(args.suite)
    if args.mode == "fast" and not args.suite:
        suites = [s for s in suites if FAST_SUITES_EXCLUDE_KEY not in s]
    runner = Runner(args, token)
    wall_start = time.perf_counter()
    try:
        for suite in suites:
            runner.suite_status[suite["suite_id"]] = runner.run_suite(suite)
            if suite.get("stop_run_on_fail") and runner.suite_status[
                suite["suite_id"]
            ] not in ("PASS", "PASS_WITH_DEFECTS"):
                break
    except SafetyStop as exc:
        runner.records.append(
            {"suite": "-", "test": "-", "status": "SAFETY-STOP", "detail": str(exc)}
        )

    runner.wall_seconds = time.perf_counter() - wall_start
    out = Path(args.out_dir) / run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "suites": runner.suite_status,
                "records": runner.records,
                "deferred": runner.deferred,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    prefix = args.prefix or f"OPS-{run_id}"
    write_three_files(out, prefix, run_id, suites, runner)
    summary = summary_markdown(run_id, args, suites, runner)
    (out / "RUN-SUMMARY.md").write_text(summary, encoding="utf-8")
    print(summary)
    print(f"Results written to {out}")
    ok = ("PASS", "NOT_APPLICABLE")
    return 0 if all(v in ok for v in runner.suite_status.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
