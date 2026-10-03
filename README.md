# Quietsearch

[![CI](https://github.com/vinay23is/quietsearch/actions/workflows/ci.yml/badge.svg)](https://github.com/vinay23is/quietsearch/actions/workflows/ci.yml)

A self-hosted, privacy-focused search service. Quietsearch runs the
open-source metasearch engine [SearXNG](https://github.com/searxng/searxng)
behind a hardened, rate-limiting HTTPS reverse proxy, with one-command Docker
deployment, automated security checks, operational tooling, and a Python CLI
for the JSON search API.

SearXNG itself is an upstream project; this repository is the system built
around it: architecture, configuration, containerization, proxy and TLS
setup, security hardening, tooling, tests and documentation.

## Contents

- [Problem](#problem)
- [How it works](#how-it-works)
- [Features](#features)
- [Technology](#technology)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Security](#security)
- [Privacy](#privacy)
- [Testing](#testing)
- [Search API and CLI](#search-api-and-cli)
- [Repository layout](#repository-layout)
- [Known limitations](#known-limitations)
- [Future work](#future-work)
- [Credits](#credits)

## Problem

Mainstream search engines tie every query to an identity: IP address,
cookies, account and browser fingerprint. Privacy-focused search services help,
but they still require trusting a third party's policies and infrastructure.

Quietsearch is a search service you operate yourself. Queries go to several
providers at once from your server, so no provider receives your IP,
cookies or account. You control which providers are used, what is logged
(nothing, by default) and where it runs. The project also documents
precisely what this does **not** protect.

## How it works

SearXNG is a **metasearch engine**: it has no crawler and no index of its own.
Each query is answered live:

```
Browser ──HTTPS──▶ Caddy ──────────────────────────▶ SearXNG ──▶ DuckDuckGo, Bing, Brave,
                   TLS · rate limits · auth                       Wikipedia, GitHub, arXiv, ...
                   security headers · branding        ◀── merge, de-duplicate, rank ──┘
```

1. Caddy terminates TLS, applies per-client rate limits, checks the password
   and adds security headers. It is the only container with published ports.
2. SearXNG sends the query to every enabled engine for the chosen category in
   parallel, from the server's IP, with a generic browser identity.
3. Engines that time out (3s) are dropped; engines returning CAPTCHAs or
   HTTP 429 are suspended temporarily.
4. Results are merged by URL and ranked: pages that several engines rank
   highly come first.

More detail: [architecture](docs/architecture.md) ·
[SearXNG internals](docs/searxng-internals.md).

## Features

- **Metasearch** across 23 enabled engines in web, images, videos, news, IT,
  science and maps categories; Google is loaded but disabled by default
  (available per query with `!go`)
- **HTTPS everywhere**: automatic certificates (Let's Encrypt for public
  hostnames, Caddy's local CA for `localhost`), HTTP→HTTPS redirect, HSTS
- **Authentication** at the proxy (bcrypt-hashed basic auth); only `/healthz`
  is public
- **Rate limiting** before authentication: 30 searches and 120 requests per
  minute per client, which also caps password guessing
- **Hardened containers**: all Linux capabilities dropped except the few
  required, `no-new-privileges`, read-only proxy filesystem, read-only config
  mounts, no ports published for SearXNG
- **Privacy defaults**: no access logs, autocomplete off, image previews
  proxied through the server, tracking parameters stripped from result links
- **JSON API + CLI**: `client/search_client.py` with distinct exit codes for
  each failure mode
- **Instance pages and branding** (name, About and Privacy pages, footer
  links, dark mode) without modifying SearXNG
- **Operational tooling**: setup/start/stop scripts, health report, engine
  diagnostics, automated security checks
- **CI**: lint, unit tests, and a full build-start-verify run of the stack on
  every push

## Technology

| Area | Choice |
|---|---|
| Metasearch engine | SearXNG (official image, pinned version, unmodified) |
| Reverse proxy / TLS | Caddy 2.10 with the [caddy-ratelimit](https://github.com/mholt/caddy-ratelimit) module, built in a multi-stage Dockerfile |
| Containers | Docker, Docker Compose v2 |
| Scripting | Bash (ShellCheck-clean) |
| CLI client and tests | Python 3.9+, `requests`, `unittest` |
| CI | GitHub Actions |

## Quick start

Requirements: Docker with the Compose v2 plugin, `openssl`, and about 2 GB of
free memory for the first build.

```bash
git clone https://github.com/vinay23is/quietsearch.git
cd quietsearch
./scripts/setup.sh      # checks prerequisites, creates .env with a fresh secret, asks for a password
./scripts/start.sh      # validates config, builds the proxy image, starts, waits for health
```

Open **https://localhost** and sign in as `quiet` with your password. The
certificate comes from Caddy's local CA, so the browser warns until you trust
it ([deployment guide](docs/deployment.md#local-machine)).

```bash
./scripts/healthcheck.sh        # status report
./scripts/stop.sh               # stop (certificates are kept)
```

Deploying to a LAN or internet-facing server uses the same files with a
different hostname: see [docs/deployment.md](docs/deployment.md).

## Configuration

| File | What it controls |
|---|---|
| `.env` (from `.env.example`, git-ignored) | Hostname, listen address, secret, password hash, pinned image versions |
| `searxng/settings.yml` | Engines, categories, SafeSearch, autocomplete, formats, branding links. Contains only changes from upstream defaults (`use_default_settings`) |
| `reverse-proxy/Caddyfile` | TLS, redirect, rate limits, authentication, headers, static pages |
| `reverse-proxy/site/` | About and Privacy pages |

Enable or disable an engine for everyone in `searxng/settings.yml`, then
`docker compose up -d --force-recreate searxng`. Users can also toggle engines
for their own browser under Preferences, or target one engine per query with
a `!bang`. See [docs/searxng-internals.md](docs/searxng-internals.md#13-enabling-and-disabling-engines-four-levels).

## Security

| Control | Implementation |
|---|---|
| Single entry point | Only Caddy publishes ports (80, 443); SearXNG is reachable only on a private Docker network |
| Encryption | Automatic TLS, HSTS, HTTP→HTTPS redirect |
| Access control | Basic auth checked against a bcrypt hash |
| Abuse protection | Per-client rate limits before authentication |
| Browser hardening | CSP, `X-Frame-Options`, `nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, no `Server` header |
| Container hardening | Capabilities dropped, `no-new-privileges`, read-only filesystems where possible |
| Secrets | `.env` only (mode 600, git-ignored); password stored as a hash; nothing secret in Compose, settings or history |
| No admin surfaces | Caddy admin API off, SearXNG debug and metrics endpoint off |
| Updates | Pinned versions with a documented upgrade and rollback procedure |

`./tests/security_check.sh` verifies these controls against the running stack
(20 checks). Threat model and rationale: [docs/security.md](docs/security.md).

## Privacy

Quietsearch **reduces** what search providers learn about you; it does **not**
make you anonymous.

- Providers see the search terms, sent from the server's IP. They don't see
  your IP, cookies or accounts. On a single-user instance, though, the server's
  IP effectively stands for one person.
- Websites you open from the results see a normal, direct visit.
- The hosting provider, the instance operator and your network each still see
  some metadata.

The full breakdown is in [docs/privacy.md](docs/privacy.md); the instance
serves a user-facing version at `/instance/privacy`.

## Testing

| What | How |
|---|---|
| CLI unit tests (12, network mocked) | `python3 -m unittest discover -s tests -v` |
| Security controls (20 checks) | `./tests/security_check.sh` |
| Engine diagnostics: configured engines, per-category probes, failures | `./tests/engine_report.py --insecure` |
| Health: containers, HTTP, TLS expiry, resources, errors | `./scripts/healthcheck.sh` |
| CI (every push) | ShellCheck, YAML validation, unit tests, Caddyfile validation, then build + start + security checks + health report on a fresh runner |

Measured results (startup time, memory, query latency, engine behaviour) are
recorded in [docs/measurements.md](docs/measurements.md).

## Search API and CLI

Results are available as JSON from the same endpoint as the web UI:

```bash
curl -s --cacert caddy-local-root.crt -u "quiet:$QS_PASS" \
  "https://localhost/search?q=distributed+systems&format=json"
```

The CLI adds argument handling, TLS verification, readable output and
distinct exit codes for connection, authentication and rate-limit errors:

```bash
python3 -m venv .venv && .venv/bin/pip install -r client/requirements.txt
export QS_CACERT="$PWD/caddy-local-root.crt"
.venv/bin/python client/search_client.py -c it -n 5 "python asyncio"
```

Response format and options: [docs/api.md](docs/api.md).

## Repository layout

```
.
├── docker-compose.yml          services, network, health checks, hardening
├── .env.example                documented configuration template
├── searxng/settings.yml        SearXNG instance configuration (diff from upstream)
├── reverse-proxy/
│   ├── Caddyfile               TLS, rate limits, auth, headers, static pages
│   ├── Dockerfile              Caddy build with the rate-limit module
│   └── site/                   About and Privacy pages
├── scripts/                    setup, start, stop, healthcheck
├── client/                     Python CLI for the JSON API
├── tests/                      security checks, engine diagnostics, unit tests
├── docs/                       architecture, security, privacy, deployment,
│                               runbook, API, SearXNG internals, measurements
└── .github/workflows/ci.yml    CI pipeline
```

## Known limitations

- **Upstream blocking.** All upstream requests come from one IP. During
  testing from a residential IP, Brave returned HTTP 429, DuckDuckGo a
  CAPTCHA, and Bing connection errors after repeated queries, leaving only
  some categories working until the suspensions expired. Result quality
  depends on which engines are answering.
- **Not anonymous.** See [Privacy](#privacy).
- **Single user.** Basic auth with one account; no per-user preferences on the
  server (preferences live in browser cookies).
- **Not yet run on a public host.** The stack has been run on macOS (Docker
  Desktop, Apple Silicon), and CI builds and starts it on Linux for every
  push. The internet-facing server path in
  [docs/deployment.md](docs/deployment.md) has not been exercised on a public
  host yet.
- **Custom proxy image.** Rate limiting requires compiling Caddy with a
  module, so the first start includes a ~90-second build.

## Future work

- Deploy to a cloud VM with infrastructure as code (firewall rules, static IP,
  DNS) and measure upstream behaviour from a datacenter IP
- Route outbound engine requests through a proxy pool to reduce upstream blocking
- Replace basic auth with OIDC for multiple users
- Push health-check results to an alerting channel
- Automated dependency update PRs for the pinned image versions

## Credits

- [SearXNG](https://github.com/searxng/searxng): the metasearch engine this
  project deploys (AGPL-3.0). Used unmodified from the official image.
- [Caddy](https://caddyserver.com/) and
  [caddy-ratelimit](https://github.com/mholt/caddy-ratelimit): reverse proxy,
  automatic TLS, rate limiting.
- [sslip.io](https://sslip.io/): referenced as a no-domain DNS option in the
  deployment guide.

## License

The configuration, scripts, client and documentation in this repository are
released under the [MIT License](LICENSE). SearXNG, Caddy and other
components are covered by their own licenses.
