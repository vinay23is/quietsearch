# How SearXNG works

A practical guide to the metasearch engine this project deploys, based on
SearXNG `2026.10.2` (the pinned version). File references point to the
SearXNG source tree (`searx/...`) or to this repository.

## 1. Metasearch, not a search index

Google, Bing and Brave run crawlers and keep their own index of the web.
SearXNG has neither. Every query is answered live:

```
query ──▶ parse ──▶ pick engines ──▶ query engines in parallel ──▶ parse responses
      ──▶ normalize ──▶ merge duplicates ──▶ score + sort ──▶ render HTML / JSON
```

Consequences:

- Result quality depends entirely on the upstream engines that answer.
- Latency is roughly the latency of the slowest engine that answers in time.
- If upstreams block the server IP (CAPTCHAs, HTTP 403/429), results degrade.
  There's nothing to fall back on.
- No storage of the web means a tiny footprint: ~120 MiB RAM at idle
  (see measurements.md).

## 2. Engines

An **engine** is a Python module (`searx/engines/<name>.py`) that knows how to
(1) turn a query into an HTTP request for one upstream site (`request()`) and
(2) turn that site's response into results (`response()`). Most engines
scrape HTML; some use official JSON APIs.

Each engine in `settings.yml` has properties such as:

| Property | Meaning |
|---|---|
| `name` / `shortcut` | Display name and `!bang` shortcut (`duckduckgo` / `!ddg`) |
| `engine` | Which module implements it (several entries can share one module, e.g. `brave` vs `brave.images`) |
| `categories` | Tabs the engine answers for (`general`, `images`, `news`, `it`, ...) |
| `timeout` | Per-engine time budget; defaults to `outgoing.request_timeout` |
| `disabled` | Loaded but off unless the user enables it or uses its `!bang` |
| `inactive` | Not loaded at all (upstream marks broken engines this way) |
| `weight` | Multiplier in result scoring (default 1.0) |

The upstream default list has 350+ engine definitions, about 80 enabled.
This instance keeps 24 with `use_default_settings.engines.keep_only` (see
`searxng/settings.yml`). Fewer engines means fewer outbound requests per
query and less to break.

## 3. Categories

Categories are the tabs in the UI. A search in category `images` queries only
engines whose `categories` include `images`. One engine can serve several
categories; for example `github` is in both `it` and `repos`. The live list
for this instance is printed by `tests/engine_report.py`.

## 4. The request lifecycle in detail

1. **Parsing** (`searx/query.py`): the raw query is scanned for special
   prefixes:

   | Syntax | Effect | Example |
   |---|---|---|
   | `!name` / `!shortcut` | Use only that engine (works even if it's disabled) | `!wp linux` |
   | `!category` | Use only that category | `!images aurora` |
   | `:lang` | Search language | `:de kernel` |
   | `<N` | Timeout limit: seconds if N < 100, milliseconds otherwise | `<2 linux` |
   | `!!bang` | Redirect to an external site's search (DuckDuckGo-style bang); no metasearch | `!!w linux` |

2. **Engine selection**: explicit `engines=` parameter or `!bangs` win.
   Otherwise, the enabled engines of the selected categories (from the
   request or the user's preferences) are used. Suspended engines are skipped.

3. **Fan-out** (`searx/search/__init__.py`): one thread per engine. Each
   engine module builds its request with the query, page number, language,
   SafeSearch level and time range mapped to that site's own URL parameters.

4. **Timeouts**: the overall deadline is the largest timeout among the
   selected engines, capped by `outgoing.max_request_timeout` (8s here) and by
   any `<N` the user typed. Engines that miss the deadline are reported in
   `unresponsive_engines` with reason `timeout`, and their late results are
   discarded.

5. **Errors and suspension**: an engine that fails is suspended for a while
   so it doesn't slow down every following query (`search.suspended_times`):
   180s after HTTP 403/429, one hour after a CAPTCHA. The Brave 429 seen
   during local testing was handled exactly this way.

6. **Aggregation and ranking**: see section 5.

7. **Rendering**: HTML via Jinja templates, or JSON/CSV/RSS when
   `format=` is set and the format is enabled in `search.formats`.

## 5. Aggregation and ranking

**Merging** (`searx/results.py`, `searx/result_types/_base.py`): two results
are the same if their URL matches **ignoring the scheme** (host, path, query
and fragment must match), along with the result template and image source.
Merged results keep one entry with the union of `engines` and every position
at which each engine ranked it.

**Scoring** (`calculate_score` in `searx/results.py`):

```
weight = product of the engines' weights × number of positions (engines) that found it
score  = Σ over positions of  weight / position
```

So agreement between engines dominates. With default weights:

| Result | Positions | Score |
|---|---|---|
| Found by 1 engine at #1 | [1] | 1 × 1/1 = **1.0** |
| Found by 2 engines at #1 and #3 | [1, 3] | 2 × (1/1 + 1/3) = **2.67** |
| Found by 2 engines at #5 and #6 | [5, 6] | 2 × (1/5 + 1/6) = **0.73** |

A page that two independent engines both rank highly beats any single
engine's top result. In the first warm local test, DuckDuckGo returned 11
and Bing 10 results, which merged into 13: 8 URLs were found by both engines
and received the agreement boost.

Per-engine `weight` lets you trust one source more. This instance leaves all
weights at 1.0: there's no measurement yet that justifies favoring one.

## 6. Outgoing HTTP

- **Client**: requests go through `curl_cffi`, a libcurl binding that can
  present browser-like TLS fingerprints. Some upstreams block clients whose
  TLS handshake doesn't look like a browser.
- **Connection reuse**: connections are pooled per engine
  (`outgoing.pool_connections`) with HTTP/2 enabled. That's why the first
  query after a restart was slow (all-new DNS + TLS) and later ones weren't.
- **User-Agent**: generated from `searx/data/useragents.json`: a current
  Firefox UA with a random OS and version, so requests don't advertise
  "SearXNG". `outgoing.useragent_suffix` can append a contact address (left
  empty here).
- **Your identity**: upstream engines see the **server's** IP and this
  generated User-Agent, never your browser's IP, cookies or headers.

## 7. Cookies and preferences

SearXNG keeps no user accounts and no server-side user data. Preferences
(theme, enabled engines, SafeSearch, language) live in **cookies in your
browser**. The Preferences page can also export them as a URL, so the same
settings can be loaded on another device without an account.

Cookies set by upstream engines are not passed to your browser. Some
engines need short-lived tokens (for example DuckDuckGo's `vqd` token); those
are cached server-side in `/var/cache/searxng` (the `searxng-cache` volume),
not tied to any user.

## 8. Query parameters (HTML and JSON)

| Parameter | Example | Notes |
|---|---|---|
| `q` | `q=linux+kernel` | The query, including any `!bang`/`:lang` syntax |
| `format` | `format=json` | `html` (default) or any format enabled in `search.formats` |
| `categories` | `categories=news,it` | Comma-separated |
| `engines` | `engines=duckduckgo,bing` | Overrides categories |
| `language` | `language=en-US` | `auto` (default) detects from the browser |
| `pageno` | `pageno=2` | For engines that support paging |
| `time_range` | `time_range=week` | `day`, `week`, `month`, `year`. Engines without time-range support are **skipped**, not just unfiltered (e.g. DuckDuckGo News) |
| `safesearch` | `safesearch=2` | 0 off, 1 moderate, 2 strict |

## 9. SafeSearch

`search.safe_search: 1` (moderate) is the default here; users can change it
in Preferences. SearXNG passes the level to each engine module, which maps it to that
site's own parameter (for example Bing's `adlt=off|moderate|strict`).
Engines without SafeSearch support ignore the setting; the Preferences →
Engines page shows which engines support it. Filtering quality is whatever
the upstream engine provides.

## 10. Images, videos and news

- **Images**: results use the `images.html` template with thumbnail and
  full-size URLs. Because `server.image_proxy: true`, both the thumbnails and
  the full-size preview are fetched by the server through `/image_proxy`, so
  your browser doesn't contact image hosts while you browse results. It
  connects to the source only when you follow a link to the original page.
- **Videos**: results carry duration and thumbnail; YouTube results can
  play in an embedded `youtube-nocookie.com` player, which the
  Content-Security-Policy's `frame-src` explicitly allows.
- **News**: the same as general search with recency. Setting `time_range`
  silently drops engines that can't filter by date: in local testing,
  `time_range=week` removed DuckDuckGo News from the query entirely.

## 11. Autocomplete

When enabled, the browser calls `/autocompleter?q=...` on every keystroke
(after `autocomplete_min` characters) and SearXNG forwards the partial query
to a backend such as DuckDuckGo. It's **off by default** here: it would
send fragments of everything you type upstream, including searches you never
submit. Users can opt in per browser under Preferences.

## 12. Plugins

Plugins post-process queries or results inside SearXNG. Defaults kept:

| Plugin | Effect |
|---|---|
| `calculator` | `2+2*3` answered locally |
| `unit_converter` | `10 km in miles` |
| `hash_plugin` | `sha256 hello` |
| `self_info` | `ip` / `user agent` shows what the server sees |
| `time_zone` | `time in Tokyo` |
| `tracker_url_remover` | Strips tracking parameters (`utm_*` etc.) from result URLs, using ClearURLs rules |
| `hostnames` | Rewrite/remove/prioritise domains (configured with the `hostnames:` setting; none set) |
| `ahmia_filter` | Filters known-abusive .onion results (only relevant with Tor engines) |

## 13. Enabling and disabling engines: four levels

| Level | How | Scope |
|---|---|---|
| Instance: not loaded | Remove from `keep_only` in `searxng/settings.yml` | Engine doesn't exist on this instance |
| Instance: loaded but off | `engines: - name: google  disabled: true` | Off by default; users can turn it on; `!go` still works |
| User | Preferences → Engines | Stored in that browser's cookie |
| Single query | `!bang` in the query, or `engines=` parameter | That request only |

To change the instance level:

```bash
# 1. edit searxng/settings.yml
# 2. apply it
docker compose up -d --force-recreate searxng
# 3. confirm what's enabled
./tests/engine_report.py --insecure
```

## 14. Instance configuration in this project

`searxng/settings.yml` starts with `use_default_settings: true`, so it
contains only differences from upstream defaults. Upgrades pick up upstream
fixes (new engines, changed URLs) automatically, and the file stays short
enough to review. The settings changed and why:

| Setting | Value | Why |
|---|---|---|
| `engines.keep_only` | 24 engines | Smaller outbound footprint, fewer failures |
| `bing.disabled` | `false` | Third general web source next to DuckDuckGo and Brave |
| `google.disabled` | `true` | Blocks datacenter IPs aggressively; available via `!go` |
| `search.formats` | `html`, `json` | JSON for the CLI client; CSV/RSS not needed |
| `search.autocomplete` | off | Privacy (section 11) |
| `search.safe_search` | 1 | Sensible default, user-overridable |
| `server.limiter` | `false` | Rate limiting is done by Caddy (see security.md) |
| `server.image_proxy` | `true` | Thumbnails don't expose your IP to image hosts |
| `ui.query_in_title` | `false` | Queries stay out of window titles / history titles |
| `outgoing.request_timeout` | 3s | Upstream default; slow engines are dropped, not waited for |
| `outgoing.max_request_timeout` | 8s | Hard ceiling for any query |
