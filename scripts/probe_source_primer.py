#!/usr/bin/env python3
"""Bounded verification probe for source-primer records (v0.2).

Writes ONLY verification.* and verified retrieval fields; never touches
classification, admission, or *_hint fields. Stdlib only. Never activates a
policy or ingests anything.

Outcome classes: live | redirected | blocked | server_error | unreachable |
probe_env_blocked | refused_unsafe_target

Usage:
  python3 probe_sources.py batch-1.json -o batch-1.probed.json [--only id1,id2]
"""

import argparse
import ipaddress
import json
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit

UA = "MCP-SaaS-source-probe/0.2 "
TIMEOUT = 15
MAX_REDIRECTS = 5
# Canary: if this fails, the runner can't reach the internet; abort instead of
# writing misleading per-record results.
CANARY = "https://www.usa.gov/"
# Catalog probes run ONLY for records classified as catalogs.
CATALOG_KINDS = {"open_data_catalog"}
CATALOG_PROBES = [
    (
        "socrata",
        "/api/catalog/v1?limit=1",
        lambda j: isinstance(j, dict) and "results" in j,
    ),
    (
        "ckan",
        "/api/3/action/package_search?rows=1",
        lambda j: isinstance(j, dict) and j.get("success") is True,
    ),
    (
        "arcgis_hub",
        "/api/feed/dcat-us/1.1.json",
        lambda j: isinstance(j, dict) and "dataset" in j,
    ),
    (
        "opendatasoft",
        "/api/explore/v2.1/catalog/datasets?limit=1",
        lambda j: isinstance(j, dict) and "results" in j,
    ),
    (
        "dcat_us",
        "/data.json",
        lambda j: isinstance(j, dict) and isinstance(j.get("dataset"), list),
    ),
]
QUERY_TEMPLATES = {
    "socrata": "{origin}/api/catalog/v1?q={{query}}&limit={{n}}",
    "ckan": "{origin}/api/3/action/package_search?q={{query}}&rows={{n}}",
    "arcgis_hub": (
        "{origin}/api/search/v1/collections/dataset/items?q={{query}}&limit={{n}}"
    ),
    "opendatasoft": (
        "{origin}/api/explore/v2.1/catalog/datasets?"
        "where=search(%22{{query}}%22)&limit={{n}}"
    ),
    "dcat_us": "{origin}/data.json",
}


class UnsafeTarget(Exception):
    """Raised when a URL resolves to a private or otherwise unsafe address."""

    pass


def assert_public(url):
    """Refuse URLs whose host resolves to private/loopback/link-local/reserved space."""
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise UnsafeTarget(f"bad scheme/host: {url}")
    try:
        infos = socket.getaddrinfo(
            p.hostname, p.port or (443 if p.scheme == "https" else 80)
        )
    except socket.gaierror:
        return  # DNS failure is classified later as unreachable, not unsafe
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise UnsafeTarget(f"{p.hostname} -> {ip}")


class GuardedRedirects(urllib.request.HTTPRedirectHandler):
    """Redirect handler that checks every hop is public and bounds the chain."""

    max_redirections = MAX_REDIRECTS

    def __init__(self):
        self.chain = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Check the redirect target is public before following it."""
        assert_public(newurl)
        self.chain.append(code)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _is_proxy_refusal(exc):
    # urllib surfaces proxy CONNECT refusals as URLError wrapping OSError.
    reason = getattr(exc, "reason", None)
    return isinstance(reason, OSError) and "Tunnel connection failed" in str(reason)


def fetch(url, accept="*/*", use_proxy=True):
    """Fetch a URL with guarded redirects and return the outcome and evidence."""
    out = {
        "url": url,
        "chain": None,
        "final_url": None,
        "status": None,
        "body": b"",
        "ms": None,
        "outcome": None,
        "error": None,
    }
    t0 = time.monotonic()
    rd = GuardedRedirects()
    handlers = [rd] if use_proxy else [rd, urllib.request.ProxyHandler({})]
    opener = urllib.request.build_opener(*handlers)
    try:
        assert_public(url)
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
        with opener.open(req, timeout=TIMEOUT) as r:
            out.update(
                status=r.status,
                chain=rd.chain + [r.status],
                final_url=r.geturl(),
                body=r.read(2_000_000),
            )
            moved = out["final_url"].rstrip("/") != url.rstrip("/")
            out["outcome"] = "redirected" if moved else "live"
    except UnsafeTarget as e:
        out.update(
            outcome="refused_unsafe_target", error=str(e), chain=rd.chain or None
        )
    except urllib.error.HTTPError as e:
        out.update(
            status=e.code,
            chain=rd.chain + [e.code],
            final_url=e.geturl(),
            error=f"HTTP {e.code}",
        )
        out["outcome"] = (
            "blocked"
            if e.code in (401, 403, 429)
            else "server_error"
            if e.code >= 500
            else "unreachable"
        )
    except urllib.error.URLError as e:
        if _is_proxy_refusal(e):
            out.update(outcome="probe_env_blocked", error=str(e.reason)[:200])
        else:
            out.update(
                outcome="unreachable",
                error=f"{type(e.reason).__name__}: {str(e.reason)[:200]}",
            )
    except (socket.timeout, TimeoutError, ssl.SSLError, ConnectionError, OSError) as e:
        out.update(outcome="unreachable", error=f"{type(e).__name__}: {str(e)[:200]}")
    out["ms"] = int((time.monotonic() - t0) * 1000)
    return out


def origin_of(url):
    """Return the scheme and host of a URL."""
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def probe_record(r, now):
    """Probe one primer record: its landing page and source, and record the result."""
    v = r.setdefault("verification", {})
    ret = r.setdefault("retrieval", {})
    land = fetch(r["url_original"], accept="text/html,*/*")
    v.update(
        status=land["outcome"],
        http_status_chain=land["chain"],
        final_url=land["final_url"],
        last_checked=now,
        checked_by="probe_sources.py/0.2",
        latency_ms=land["ms"],
        probe_error=land["error"],
    )
    for k in ("platform", "catalog_url", "query_template"):
        ret[k] = None
    if r.get("source_kind") in CATALOG_KINDS and land["outcome"] in (
        "live",
        "redirected",
    ):
        log = []
        access = ret.get("api_access", {})
        hint = ret.get("catalog_url_hint")
        if access.get("authentication") == "api_key_header":
            log.append(
                {
                    "platform": ret.get("platform_hint"),
                    "url": hint,
                    "outcome": "not_probed",
                    "reason": "api_key_required",
                    "registration_url": access.get("registration_url"),
                }
            )
        elif ret.get("platform_hint") == "socrata" and hint:
            probe_url = hint.rstrip("/") + ("&" if "?" in hint else "?") + "limit=1"
            res = fetch(probe_url, accept="application/json")
            entry = {
                "platform": "socrata",
                "url": probe_url,
                "outcome": res["outcome"],
                "status": res["status"],
            }
            if res["outcome"] in ("live", "redirected"):
                try:
                    payload = json.loads(res["body"].decode("utf-8", "replace"))
                    if isinstance(payload, dict) and "results" in payload:
                        ret.update(
                            platform="socrata",
                            catalog_url=hint,
                            query_template=hint.rstrip("/") + "?q={query}&limit={n}",
                        )
                        entry["match"] = True
                except ValueError:
                    entry["note"] = "non-JSON"
            log.append(entry)
        else:
            bases = list(
                dict.fromkeys(
                    (origin_of(r["url_original"]), origin_of(land["final_url"]))
                )
            )
            for base in bases:
                for name, path, ok in CATALOG_PROBES:
                    res = fetch(base + path, accept="application/json")
                    entry = {
                        "platform": name,
                        "url": base + path,
                        "outcome": res["outcome"],
                        "status": res["status"],
                    }
                    if res["outcome"] in ("live", "redirected"):
                        try:
                            if ok(json.loads(res["body"].decode("utf-8", "replace"))):
                                ret.update(
                                    platform=name,
                                    catalog_url=base + path,
                                    query_template=QUERY_TEMPLATES[name].format(
                                        origin=base
                                    ),
                                )
                                entry["match"] = True
                                log.append(entry)
                                break
                        except ValueError:
                            entry["note"] = "non-JSON"
                    log.append(entry)
                if ret["platform"] is not None:
                    break
        ret["catalog_probe_log"] = log
    if (
        ret["platform"] is None
        and land["outcome"] in ("live", "redirected")
        and not ret.get("catalog_url_hint")
    ):
        ret["platform"] = "html_only"
    hint = ret.get("platform_hint")
    if hint and ret["platform"] and hint != ret["platform"]:
        v["hint_mismatch"] = f"platform_hint={hint} verified={ret['platform']}"
    else:
        v.pop("hint_mismatch", None)


def main():
    """Parse command-line arguments and run this tool."""
    ap = argparse.ArgumentParser()
    ap.add_argument("primer")
    ap.add_argument("-o", "--out")
    ap.add_argument("--only", help="comma-separated source_ids")
    ap.add_argument("--skip-canary", action="store_true")
    a = ap.parse_args()

    if not a.skip_canary:
        c = fetch(CANARY)
        if c["outcome"] not in ("live", "redirected"):
            message = (
                f"ABORT: canary {CANARY} -> {c['outcome']} ({c['error']}). "
                "Runner lacks egress; no records written."
            )
            print(
                message,
                file=sys.stderr,
            )
            sys.exit(2)

    doc = json.load(open(a.primer))
    only = set(a.only.split(",")) if a.only else None
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for r in doc["records"]:
        if only and r["source_id"] not in only:
            continue
        probe_record(r, now)
        v, ret = r["verification"], r["retrieval"]
        detail = v.get("final_url") or v.get("probe_error")
        summary = (
            f"{r['source_id']:40} {v['status']:24} {str(ret['platform']):12} {detail}"
        )
        print(summary, file=sys.stderr)
    doc["batch"]["probed_at"] = now
    out = a.out or a.primer.replace(".json", ".probed.json")
    json.dump(doc, open(out, "w"), indent=2)
    print(out)


if __name__ == "__main__":
    main()
