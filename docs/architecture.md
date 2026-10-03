# Architecture

## Overview

```
                       ┌──────────────────────────── Docker host ────────────────────────────┐
                       │                                                                     │
 Browser / CLI ──443──▶│  caddy (custom build)                 searxng (official image)      │
   HTTPS               │  ─ TLS (Let's Encrypt / local CA)      ─ query parsing              │
          ──80───────▶ │  ─ HTTP → HTTPS redirect      ──8080─▶ ─ parallel engine requests ──┼──▶ DuckDuckGo
          (redirect)   │  ─ rate limits (per client IP)  private ─ merge, dedupe, rank        │    Bing, Brave,
                       │  ─ basic auth                  network ─ HTML / JSON rendering      │    Wikipedia, GitHub,
                       │  ─ security headers                                                 │    arXiv, ...
                       │  ─ About / Privacy pages, branding                                  │
                       │    (reverse-proxy/site, read-only)                                  │
                       │                                                                     │
                       │  ports published: 80, 443 only     ports published: none           │
                       └─────────────────────────────────────────────────────────────────────┘
```

Two containers, one private bridge network, no database. All state that
matters lives in Git (configuration) or in two Docker volumes (TLS
certificates, SearXNG's engine cache).

## Components

| Component | Image | Responsibility |
|---|---|---|
| `caddy` | `quietsearch-caddy` built from `caddy:<version>-alpine` + the caddy-ratelimit module | The only internet-facing process: TLS termination and certificate renewal, redirect, rate limiting, authentication, security headers, static instance pages |
| `searxng` | `searxng/searxng:<pinned tag>` (unmodified) | Metasearch: sends each query to the selected upstream engines, merges and ranks results, renders HTML or JSON |
| `client/search_client.py` | Runs on the user's machine | Command-line access to the JSON API |

## Request lifecycle

1. The browser resolves the hostname and connects to port 443. (Port 80 only
   returns a `308` redirect to HTTPS.)
2. Caddy terminates TLS.
3. **Rate limit** (per client IP): at most 30 searches and 120 requests per
   minute, otherwise `429`. This runs before authentication, so it also
   limits password guessing.
4. **Authentication**: HTTP basic auth checked against a bcrypt hash;
   `401` on failure. `/healthz` is the only unauthenticated path.
5. `/instance/*` (About and Privacy pages) is served by Caddy from
   `reverse-proxy/site`. Everything else is proxied to `searxng:8080`.
6. SearXNG parses the query (`!bangs`, `:language`, `<timeout`), picks the
   engines for the requested category, and sends one request per engine in
   parallel from the server's IP.
7. Engines that don't answer within their timeout (3s, hard cap 8s) are
   reported as unresponsive; engines returning a CAPTCHA or HTTP 429 are
   suspended for a while.
8. Results are merged by URL (ignoring the scheme) and scored:
   `score = weights × number_of_engines × Σ(1 / position)`, so pages that
   several engines rank highly come first.
9. The page or JSON travels back through Caddy, which adds the security
   headers.

Details: [searxng-internals.md](searxng-internals.md).

## Design decisions

### Caddy instead of Nginx

Caddy obtains and renews certificates automatically, and the whole proxy
configuration is a single Caddyfile of about 120 lines, most of it comments. Nginx would need Certbot plus a renewal
timer: more moving parts, and certificate expiry is the most common way
small self-hosted services break.

### Rate limiting at the proxy, not in SearXNG

The first version used SearXNG's built-in limiter with a Valkey store. Testing
the JSON API showed the limiter is designed for anonymous public instances:
it rejects clients that don't look like browsers and hard-codes a quota of 4
API requests per IP per hour. On a private, authenticated instance, rate
limiting belongs at the edge. Moving it into Caddy (caddy-ratelimit module)
made the API usable, added protection against password guessing, and removed
a whole container (Valkey) and a network.

Trade-off: Caddy modules are compiled in, so the proxy image is built locally
(about 90 seconds the first time, cached afterwards) instead of pulled.

### No custom backend

SearXNG already serves JSON from `/search?format=json`. The CLI talks to that
endpoint directly; an extra API service would add code, a container and a
failure point without adding capability.

### Configuration as a diff from upstream

`searxng/settings.yml` starts with `use_default_settings: true` and contains
only what this instance changes (about 130 lines, against more than 3,000 in
the upstream defaults). Upstream
fixes to engine definitions arrive automatically on upgrade.

### Branding without modifying SearXNG

The About/Privacy pages are static files served by Caddy, linked
through SearXNG's supported `brand` and `privacypolicy_url` settings. Changing
SearXNG's own templates would require copying the whole template directory
(`ui.templates_path`) and re-syncing it on every upgrade, so that was avoided.

### Authentication: basic auth

The instance has one user. Basic auth over HTTPS is supported by every
browser and HTTP client, needs no session storage, and is enforced before any
request reaches SearXNG. An identity provider (OAuth/OIDC) would be the next
step for multiple users.

### Single private network

An earlier design used a second, internal-only network for Valkey. With two
containers and a single trust relationship (Caddy → SearXNG), one private
network is enough. SearXNG needs outbound internet access by design.

## Operational characteristics

Measured values are in [measurements.md](measurements.md). In short, on the
development machine: SearXNG uses about 121 MiB of memory at idle and Caddy
about 14 MiB; SearXNG is healthy about 6 seconds after start; warm general
searches took about 1.1s.

The main operational risk is upstream blocking: a single-IP instance can be
rate-limited or served CAPTCHAs by engines (observed with Brave, DuckDuckGo
and Bing from a residential IP during testing). SearXNG degrades gracefully
(the request still succeeds, with the failing engines listed), but result
quality depends on which engines are answering.
