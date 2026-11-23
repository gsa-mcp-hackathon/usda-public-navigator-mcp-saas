"""Phoenix preflight: four checks to run before a test batch.

Checks (each reports pass, fail or not_probed):
  1. phoenix_reachable         the server answers its health endpoint
  2. project_present           the traces project exists
  3. span_found_by_correlation a span carrying the correlation ID can be found
  4. annotation_write          an annotation can be written (only with --write-probe)

Writes a small sanitized JSON file (no host name, no key) that run_suites.py can
show in its "Preflight provenance" section via --provenance-file.

Usage:
    PHOENIX_API_KEY=... python eval/operator/phoenix_preflight.py \
        --base-url https://PHOENIX-HOST --project usda-core-native \
        --correlation-id <id from a recent call> --out phoenix-preflight.json
Standard library only. Read-only unless --write-probe is given.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

CORRELATION_KEY = "mcp.correlation.id"


def call(base: str, path: str, key: str | None, body: dict | None = None):
    """Make one HTTP call and return (status, parsed JSON or None)."""
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode().strip()
            return response.status, json.loads(raw) if raw[:1] in "{[" else None
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, None


def attribute(span: dict, name: str):
    """Read a span attribute whether Phoenix returns it flat or nested."""
    attrs = span.get("attributes") or {}
    if name in attrs:
        return attrs[name]
    node: object = attrs
    for part in name.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return node


def run(args: argparse.Namespace) -> dict:
    """Run the checks and return the sanitized result."""
    key = os.environ.get("PHOENIX_API_KEY")
    base = args.base_url.rstrip("/")
    result = {
        "phoenix_preflight_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "phoenix_reachable": "fail",
        "project_present": "not_probed",
        "span_found_by_correlation": "not_probed",
        "annotation_write": "not_probed",
    }
    status, _ = call(base, "/healthz", None)
    if status not in (200, 401, 403):  # any answer from the server counts as reachable
        status, _ = call(base, "/v1/projects", None)
        if status not in (200, 401, 403):
            return result
    result["phoenix_reachable"] = "pass"
    status, projects = call(base, "/v1/projects", key)
    names = [p.get("name") for p in (projects or {}).get("data", [])]
    result["project_present"] = "pass" if args.project in names else "fail"
    if result["project_present"] != "pass":
        return result
    status, spans = call(base, f"/v1/projects/{args.project}/spans?limit=200", key)
    found = None
    for span in (spans or {}).get("data", []):
        value = attribute(span, CORRELATION_KEY)
        if value and (not args.correlation_id or value == args.correlation_id):
            found = span
            break
    result["span_found_by_correlation"] = "pass" if found else "fail"
    if args.write_probe and found:
        note = {
            "data": [
                {
                    "span_id": found["context"]["span_id"],
                    "name": "preflight_probe",
                    "annotator_kind": "CODE",
                    "result": {"label": "probe", "score": 1.0},
                    "identifier": "preflight",
                }
            ]
        }
        status, _ = call(base, "/v1/span_annotations?sync=true", key, note)
        result["annotation_write"] = "pass" if status in (200, 201, 202) else "fail"
    return result


def main() -> int:
    """Parse arguments, run the checks and write the sanitized result."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--project", default="usda-core-native")
    parser.add_argument("--correlation-id", help="a correlation ID from a recent call")
    parser.add_argument(
        "--write-probe", action="store_true", help="write one probe annotation"
    )
    parser.add_argument("--out", default="phoenix-preflight.json")
    args = parser.parse_args()
    result = run(args)
    Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    required = ("phoenix_reachable", "project_present", "span_found_by_correlation")
    return 0 if all(result[k] == "pass" for k in required) else 1


if __name__ == "__main__":
    sys.exit(main())
