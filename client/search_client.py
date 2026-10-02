#!/usr/bin/env python3
"""Command-line client for a Quietsearch / SearXNG instance.

Calls the instance's JSON search API (GET /search?format=json) and prints
each result's title, URL, contributing engines and snippet.

Configuration comes from flags or environment variables:

    QS_URL      base URL of the instance        (default: https://localhost)
    QS_USER     basic-auth user                 (default: quiet)
    QS_PASS     basic-auth password             (prompted for if unset)
    QS_CACERT   CA bundle for a private CA, e.g. Caddy's local root

The password is never accepted as a command-line flag: arguments are
visible in shell history and to other users via the process list.

Examples:
    python3 client/search_client.py "distributed systems"
    python3 client/search_client.py -c it -n 5 "python asyncio"
    python3 client/search_client.py --engines duckduckgo,bing --json "tcp handshake"

Exit codes: 0 success, 1 usage/config error, 2 connection/TLS error,
3 authentication failed, 4 rate limited, 5 other HTTP or response error.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import sys
import textwrap
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

USER_AGENT = "quietsearch-cli/1.0"

EXIT_OK, EXIT_USAGE, EXIT_CONNECTION, EXIT_AUTH, EXIT_RATE_LIMIT, EXIT_HTTP = range(6)


class ClientError(Exception):
    """An error with a user-facing message and a process exit code."""

    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class Result:
    title: str
    url: str
    engines: list[str]
    snippet: str
    category: str = ""


@dataclass
class SearchResponse:
    query: str
    results: list[Result]
    answers: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    infobox_titles: list[str] = field(default_factory=list)
    unresponsive: list[tuple[str, str]] = field(default_factory=list)
    elapsed: float = 0.0


def parse_response(data: dict[str, Any], elapsed: float = 0.0) -> SearchResponse:
    """Convert SearXNG's JSON payload into a SearchResponse.

    Tolerates missing optional fields: engines return different subsets
    (images have no snippet, some results lack "engines", etc.).
    """
    if not isinstance(data, dict) or "results" not in data:
        raise ClientError("Unexpected response: no 'results' field. Is format=json enabled?", EXIT_HTTP)

    results = []
    for item in data.get("results", []):
        engines = item.get("engines") or ([item["engine"]] if item.get("engine") else [])
        results.append(
            Result(
                title=(item.get("title") or "").strip() or "(no title)",
                url=item.get("url") or "",
                engines=sorted(engines),
                snippet=" ".join((item.get("content") or "").split()),
                category=item.get("category") or "",
            )
        )

    answers = []
    for a in data.get("answers", []):
        text = a.get("answer") if isinstance(a, dict) else a
        if text:
            answers.append(str(text))

    return SearchResponse(
        query=data.get("query", ""),
        results=results,
        answers=answers,
        suggestions=[str(s) for s in data.get("suggestions", [])],
        infobox_titles=[ib.get("infobox", "") for ib in data.get("infoboxes", []) if isinstance(ib, dict)],
        unresponsive=[(str(e[0]), str(e[1])) for e in data.get("unresponsive_engines", []) if len(e) >= 2],
        elapsed=elapsed,
    )


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def build_params(args: argparse.Namespace) -> dict[str, str]:
    params = {"q": args.query, "format": "json", "pageno": str(args.page)}
    if args.category:
        params["categories"] = args.category
    if args.engines:
        params["engines"] = args.engines
    if args.lang:
        params["language"] = args.lang
    if args.time_range:
        params["time_range"] = args.time_range
    if args.safesearch is not None:
        params["safesearch"] = str(args.safesearch)
    return params


def fetch_json(
    session: requests.Session,
    base_url: str,
    params: dict[str, str],
    timeout: float,
    verify: bool | str,
) -> tuple[dict[str, Any], float]:
    """GET /search and return (decoded JSON, elapsed seconds).

    Every failure mode is translated into a ClientError with a specific
    message and exit code.
    """
    url = base_url.rstrip("/") + "/search"
    start = time.perf_counter()
    try:
        resp = session.get(url, params=params, timeout=timeout, verify=verify)
    except requests.exceptions.SSLError as err:
        raise ClientError(
            f"TLS verification failed for {base_url}.\n"
            "For a local instance with Caddy's private CA, pass --cacert (or set QS_CACERT) "
            "to the exported root certificate, or use --insecure for local testing only.\n"
            f"Detail: {err}",
            EXIT_CONNECTION,
        ) from err
    except requests.exceptions.ConnectTimeout as err:
        raise ClientError(f"Timed out connecting to {base_url}.", EXIT_CONNECTION) from err
    except requests.exceptions.ReadTimeout as err:
        raise ClientError(f"No response from {base_url} within {timeout:.0f}s.", EXIT_CONNECTION) from err
    except requests.exceptions.ConnectionError as err:
        raise ClientError(
            f"Cannot connect to {base_url}. Is the stack running (./scripts/start.sh) "
            "and is the URL correct?",
            EXIT_CONNECTION,
        ) from err
    elapsed = time.perf_counter() - start

    if resp.status_code == 401:
        raise ClientError("Authentication failed (HTTP 401). Check QS_USER / QS_PASS.", EXIT_AUTH)
    if resp.status_code == 429:
        retry = resp.headers.get("Retry-After", "a minute")
        raise ClientError(f"Rate limited (HTTP 429). Retry after {retry} seconds.", EXIT_RATE_LIMIT)
    if resp.status_code == 403:
        raise ClientError("Forbidden (HTTP 403). The JSON format may be disabled in settings.yml.", EXIT_HTTP)
    if resp.status_code >= 400:
        raise ClientError(f"Server returned HTTP {resp.status_code}.", EXIT_HTTP)

    try:
        data = resp.json()
    except ValueError as err:
        ctype = resp.headers.get("Content-Type", "unknown")
        raise ClientError(f"Response was not JSON (Content-Type: {ctype}).", EXIT_HTTP) from err

    return data, elapsed


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

class Style:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def title(self, t: str) -> str:
        return self._wrap("1", t)

    def url(self, t: str) -> str:
        return self._wrap("32", t)

    def dim(self, t: str) -> str:
        return self._wrap("2", t)

    def warn(self, t: str) -> str:
        return self._wrap("33", t)


def render(resp: SearchResponse, limit: int, style: Style, width: int) -> str:
    lines: list[str] = []
    for answer in resp.answers:
        lines.append(style.title(f"Answer: {answer}"))
    for title in resp.infobox_titles:
        lines.append(style.dim(f"Infobox: {title}"))
    if lines:
        lines.append("")

    shown = resp.results[:limit]
    indent = "    "
    for i, r in enumerate(shown, 1):
        lines.append(f"{i:>2}. {style.title(r.title)}")
        lines.append(f"{indent}{style.url(r.url)}")
        lines.append(f"{indent}{style.dim('engines: ' + (', '.join(r.engines) or 'unknown'))}")
        if r.snippet:
            lines.extend(textwrap.wrap(r.snippet, width=max(40, width - len(indent)),
                                       initial_indent=indent, subsequent_indent=indent,
                                       max_lines=3, placeholder=" …"))
        lines.append("")

    if not shown:
        lines.append("No results.")
        lines.append("")

    summary = f"{len(shown)} of {len(resp.results)} results in {resp.elapsed:.2f}s"
    if resp.suggestions:
        summary += " · suggestions: " + ", ".join(resp.suggestions[:5])
    lines.append(style.dim(summary))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="search_client.py",
        description="Search a Quietsearch / SearXNG instance from the terminal.",
        epilog="Environment: QS_URL, QS_USER, QS_PASS, QS_CACERT. See the module docstring for details.",
    )
    p.add_argument("query", help="search terms (SearXNG syntax such as !wp or :de works too)")
    p.add_argument("-n", "--limit", type=int, default=10, help="maximum results to print (default 10)")
    p.add_argument("-c", "--category", help="category, e.g. general, images, news, it, science")
    p.add_argument("-e", "--engines", help="comma-separated engine names, e.g. duckduckgo,bing")
    p.add_argument("-l", "--lang", help="search language, e.g. en-US (default: instance setting)")
    p.add_argument("-p", "--page", type=int, default=1, help="result page (default 1)")
    p.add_argument("-t", "--time-range", choices=["day", "week", "month", "year"],
                   help="only recent results (engines without date filtering are skipped)")
    p.add_argument("-s", "--safesearch", type=int, choices=[0, 1, 2], help="0 off, 1 moderate, 2 strict")
    p.add_argument("--json", action="store_true", help="print the raw JSON response")
    p.add_argument("--url", default=os.environ.get("QS_URL", "https://localhost"), help="instance base URL")
    p.add_argument("--user", default=os.environ.get("QS_USER", "quiet"), help="basic-auth user")
    p.add_argument("--cacert", default=os.environ.get("QS_CACERT"), help="CA bundle for a private CA")
    p.add_argument("--insecure", action="store_true", help="skip TLS verification (local testing only)")
    p.add_argument("--timeout", type=float, default=15.0, help="request timeout in seconds (default 15)")
    p.add_argument("--no-color", action="store_true", help="disable colored output")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.limit < 1 or args.page < 1:
        print("error: --limit and --page must be positive", file=sys.stderr)
        return EXIT_USAGE
    if not args.url.startswith("https://") and not args.insecure:
        print("error: refusing to send credentials over plain HTTP; use an https:// URL", file=sys.stderr)
        return EXIT_USAGE

    password = os.environ.get("QS_PASS")
    if password is None:
        if not sys.stdin.isatty():
            print("error: QS_PASS is not set and no terminal is available to prompt", file=sys.stderr)
            return EXIT_USAGE
        password = getpass.getpass(f"Password for {args.user}: ")

    verify: bool | str = False if args.insecure else (args.cacert or True)
    if args.insecure:
        import urllib3  # bundled with requests

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    session = requests.Session()
    session.auth = (args.user, password)
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    try:
        data, elapsed = fetch_json(session, args.url, build_params(args), args.timeout, verify)
        if args.json:
            print(json.dumps(data, indent=2, ensure_ascii=False))
            return EXIT_OK
        result = parse_response(data, elapsed)
    except ClientError as err:
        print(f"error: {err}", file=sys.stderr)
        return err.exit_code

    use_color = sys.stdout.isatty() and not args.no_color and "NO_COLOR" not in os.environ
    style = Style(use_color)
    width = min(shutil.get_terminal_size((100, 24)).columns, 110)
    print(render(result, args.limit, style, width))

    for name, reason in result.unresponsive:
        print(style.warn(f"engine unavailable: {name} ({reason})"), file=sys.stderr)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
