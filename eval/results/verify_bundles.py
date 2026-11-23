#!/usr/bin/env python3
"""Read-only check of the recorded run bundles. No network, no keys.

For each bundle it validates required fields, counts cases and MCP calls, and
reports how many calls carry a correlation ID and a joined Phoenix trace ID.
Unmatched calls are reported as they are; nothing is filled in.

Usage: python3 eval/results/verify_bundles.py
"""

import hashlib
import json
import pathlib

HERE = pathlib.Path(__file__).parent / "replay-bundles"
rows = []
for path in sorted(HERE.glob("*.json")):
    if path.name.endswith(".schema.json"):
        continue
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == "1.0", path.name
    run, cases = data["run"], data["cases"]
    calls = [c for case in cases for c in case.get("calls", [])]
    corr = sum(1 for c in calls if c.get("correlation_id"))
    traced = sum(1 for c in calls if c.get("trace_id"))
    sha = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    rows.append((path.name, run.get("mode"), len(cases), len(calls), corr, traced, sha))

print(f"{'bundle':58} {'mode':20} cases calls corr traced sha256[:12]")
for name, mode, n, calls, corr, traced, sha in rows:
    print(f"{name:58} {mode:20} {n:5} {calls:5} {corr:4} {traced:6} {sha}")
