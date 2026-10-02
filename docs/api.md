# Search API

SearXNG serves results as JSON from the same endpoint as the web UI. This
instance enables it in `searxng/settings.yml` (`search.formats: [html, json]`);
no extra backend service is involved.

## Request

```
GET /search?q=<query>&format=json[&categories=..][&engines=..][&language=..][&pageno=..][&time_range=..][&safesearch=..]
Authorization: Basic <base64(user:password)>
```

| Aspect | Behaviour on this instance |
|---|---|
| Transport | HTTPS only (HTTP is redirected) |
| Authentication | HTTP basic auth at the reverse proxy; `401` without valid credentials |
| Rate limit | 30 searches/minute and 120 requests/minute per client IP; `429` with `Retry-After` when exceeded |
| Method | `GET` (`POST` with form fields also works) |
| Parameters | Same as the web UI; see [searxng-internals.md](searxng-internals.md#8-query-parameters-html-and-json) |

```bash
curl -s --cacert caddy-local-root.crt -u "quiet:$QS_PASS" \
  "https://localhost/search?q=distributed+systems&format=json" | python3 -m json.tool
```

## Response

Top-level object:

| Field | Type | Meaning |
|---|---|---|
| `query` | string | The query as received (including any `!bang` syntax) |
| `results` | array | Main results, already merged and sorted by score |
| `answers` | array | Direct answers (unit conversion, plugins), each with an `answer` string |
| `infoboxes` | array | Summary boxes (e.g. Wikipedia/Wikidata), with `infobox` title, `content`, `urls` |
| `suggestions` | array of strings | Related queries suggested by engines |
| `corrections` | array of strings | Spelling corrections suggested by engines |
| `unresponsive_engines` | array of `[engine, reason]` | Engines that failed or timed out for this query |

Fields of each entry in `results` (not every engine fills every field):

| Field | Meaning |
|---|---|
| `title`, `url`, `content` | Title, link and snippet |
| `engine` | Engine that produced the first copy of this result |
| `engines` | All engines that returned this URL (merged) |
| `positions` | Rank of this URL in each contributing engine's list |
| `score` | Ranking score: `weight × len(positions) × Σ 1/position` |
| `category` | Category the result belongs to (`general`, `images`, ...) |
| `template` | Result type: `default.html`, `images.html`, `videos.html`, ... |
| `publishedDate` | Date, for news and some other results |
| `thumbnail`, `img_src` | Image URLs (image and video results) |

Example: a real response from local testing on 2026-10-02 (abridged). It
shows a common situation for a home IP: the three general web engines were
blocked (CAPTCHA, HTTP 429, connection error), so the request still
succeeded with HTTP 200 but only the Wikipedia/Wikidata infoboxes came back.

```json
{
  "query": "distributed systems",
  "results": [],
  "answers": [],
  "corrections": [],
  "infoboxes": [
    {
      "infobox": "Distributed computing",
      "id": "https://en.wikipedia.org/wiki/Distributed_computing",
      "content": "Distributed computing is a field of computer science that studies distributed systems, defined a…",
      "engine": "wikipedia",
      "engines": [
        "wikipedia"
      ],
      "urls": […]
    }
  ],
  "suggestions": [],
  "unresponsive_engines": [
    [
      "bing",
      "Suspended: HTTP connection error"
    ],
    [
      "brave",
      "Suspended: too many requests"
    ],
    [
      "duckduckgo",
      "CAPTCHA"
    ]
  ]
}
```

A direct answer from a SearXNG plugin (no upstream engine involved) looks
like this inside `answers`:

```json
{ "engine": "plugin: unit_converter", "template": "answer/legacy.html", "answer": "6.2137119224 mi" }
```

## Error responses

| Status | Cause |
|---|---|
| `401` | Missing or wrong credentials (from Caddy) |
| `403` | Requested `format` is not enabled in `search.formats` |
| `429` | Proxy rate limit exceeded |
| `502` | SearXNG container down or restarting |

Note that **engine failures are not HTTP errors**: the request still returns
`200`, with the failed engines listed in `unresponsive_engines`.

## CLI client

`client/search_client.py` wraps this API:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r client/requirements.txt

export QS_CACERT="$PWD/caddy-local-root.crt"   # local instance only
read -rs 'QS_PASS?Password: '; export QS_PASS   # zsh; bash: read -rsp 'Password: ' QS_PASS

python3 client/search_client.py "distributed systems"
python3 client/search_client.py -c it -n 5 "python asyncio"
python3 client/search_client.py --engines duckduckgo,bing "tcp handshake"
python3 client/search_client.py --json "linux kernel" > result.json
```

| Exit code | Meaning |
|---|---|
| 0 | Success (possibly with some engines unavailable, reported on stderr) |
| 1 | Usage or configuration error |
| 2 | Connection, timeout or TLS error |
| 3 | Authentication failed |
| 4 | Rate limited |
| 5 | Other HTTP or response error |

Unit tests (no network needed): `python3 -m unittest discover -s tests -v`

Requires Python 3.9+. macOS's built-in Python 3.9 is linked against LibreSSL,
so `urllib3` prints a `NotOpenSSLWarning` on every run. It's harmless for
TLS 1.2/1.3 connections; a Homebrew or python.org Python (3.10+) avoids it.
