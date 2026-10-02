#!/usr/bin/env python3
"""Engine diagnostics for a Quietsearch / SearXNG instance.

Shows which engines are configured, then runs one query per category and
reports response time, result counts, which engines contributed, how many
results were merged across engines, and which engines failed.

Uses only the Python standard library. The password is read from the
QS_PASS environment variable or prompted for; it is never accepted as an
argument (arguments end up in shell history and process lists).

Examples:
    ./tests/engine_report.py --insecure                 # local, Caddy's private CA
    ./tests/engine_report.py --cacert caddy-local-root.crt
    ./tests/engine_report.py --base-url https://search.example.com
"""

from __future__ import annotations

import argparse
import base64
import collections
import getpass
import gzip
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Identify the tool honestly and accept compressed responses.
HEADERS = {
    "User-Agent": "quietsearch-diagnostics/0.1",
    "Accept": "application/json, text/html;q=0.5",
    "Accept-Language": "en-US,en;q=0.8",
    "Accept-Encoding": "gzip",
}

# (label, query parameters). Each exercises a different part of SearXNG.
PROBES = [
    ("general", {"q": "linux kernel", "categories": "general"}),
    ("images", {"q": "aurora borealis", "categories": "images"}),
    ("videos", {"q": "docker compose tutorial", "categories": "videos"}),
    ("news", {"q": "open source", "categories": "news"}),
    # Engines without time-range support are skipped when time_range is set.
    ("news+week", {"q": "open source", "categories": "news", "time_range": "week"}),
    ("it", {"q": "python asyncio", "categories": "it"}),
    ("science", {"q": "distributed consensus", "categories": "science"}),
    ("bang !wp", {"q": "!wp metasearch engine"}),
    ("engines=ddg", {"q": "tcp handshake", "engines": "duckduckgo"}),
    ("disabled !go", {"q": "!go tcp handshake"}),
]


def build_opener(args: argparse.Namespace, password: str):
    if args.insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    else:
        ctx = ssl.create_default_context(cafile=args.cacert)
    token = base64.b64encode(f"{args.user}:{password}".encode()).decode()
    headers = dict(HEADERS, Authorization=f"Basic {token}")

    def get(path: str, params: dict | None = None) -> tuple[int, dict | None, float]:
        url = args.base_url.rstrip("/") + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=headers)
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=args.timeout) as resp:
                body = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                elapsed = time.perf_counter() - start
                return resp.status, json.loads(body), elapsed
        except urllib.error.HTTPError as err:
            return err.code, None, time.perf_counter() - start

    return get


def report_config(get) -> None:
    status, cfg, _ = get("/config")
    if status != 200 or cfg is None:
        sys.exit(f"/config returned HTTP {status}. Check the password and URL.")
    engines = cfg["engines"]
    enabled = [e for e in engines if e["enabled"]]
    disabled = [e for e in engines if not e["enabled"]]
    by_cat: dict[str, list[str]] = collections.defaultdict(list)
    for e in enabled:
        for c in e["categories"]:
            by_cat[c].append(e["name"])

    print(f"Instance: {cfg.get('instance_name')}  (SearXNG {cfg.get('version')})")
    print(f"Engines: {len(engines)} loaded, {len(enabled)} enabled, {len(disabled)} disabled")
    for e in disabled:
        print(f"  disabled: {e['name']} (shortcut !{e['shortcut']})")
    print("Enabled engines per category:")
    for cat in sorted(by_cat):
        print(f"  {cat:12} {', '.join(sorted(by_cat[cat]))}")
    print(f"Autocomplete backend: {cfg.get('autocomplete') or 'off'}")
    print(f"Default SafeSearch: {cfg.get('safe_search')}")
    print()


def report_probes(get) -> None:
    header = f"{'probe':14} {'http':>4} {'time':>6} {'res':>4} {'merged':>6} {'box':>3}  contributing engines / failures"
    print(header)
    print("-" * len(header))
    for label, params in PROBES:
        status, data, elapsed = get("/search", dict(params, format="json"))
        if status != 200 or data is None:
            print(f"{label:14} {status:>4} {elapsed:5.2f}s")
            continue
        results = data["results"]
        counts = collections.Counter(e for r in results for e in r["engines"])
        # A result found by N engines is listed once; merged = extra copies removed.
        merged = sum(len(r["engines"]) - 1 for r in results)
        contrib = ", ".join(f"{k} {v}" for k, v in counts.most_common()) or "-"
        # Infoboxes (e.g. Wikipedia summaries) and direct answers aren't in "results".
        boxes = len(data.get("infoboxes", [])) + len(data.get("answers", []))
        if boxes and not results:
            contrib = "(infobox/answer only)"
        print(f"{label:14} {status:>4} {elapsed:5.2f}s {len(results):>4} {merged:>6} {boxes:>3}  {contrib}")
        for name, reason in data["unresponsive_engines"]:
            print(f"{'':40}  ! {name}: {reason}")
        # Stay well inside the proxy's 30 searches/minute limit.
        time.sleep(1.0)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default="https://localhost")
    p.add_argument("--user", default="quiet")
    p.add_argument("--cacert", help="CA bundle for a private CA (e.g. Caddy's local root)")
    p.add_argument("--insecure", action="store_true", help="skip TLS verification (local testing only)")
    p.add_argument("--timeout", type=float, default=15.0)
    args = p.parse_args()

    password = os.environ.get("QS_PASS") or getpass.getpass(f"Password for {args.user}: ")
    get = build_opener(args, password)
    try:
        report_config(get)
        report_probes(get)
    except urllib.error.URLError as err:
        sys.exit(f"Cannot reach {args.base_url}: {err.reason}")


if __name__ == "__main__":
    main()
