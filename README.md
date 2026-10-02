# Quietsearch

A self-hosted, privacy-focused search platform. It deploys the open-source
[SearXNG](https://github.com/searxng/searxng) metasearch engine behind a
hardened, TLS-terminating reverse proxy, with reproducible Docker deployment
for a laptop, a home server or AWS EC2.

> Status: in active development. Local deployment works; cloud deployment,
> Terraform, monitoring and full documentation are in progress.

## How it works

SearXNG is a **metasearch engine**. It doesn't crawl or index the web. For
each query it sends requests to several upstream engines (DuckDuckGo, Brave,
Bing, Wikipedia, ...) from the server, then merges, deduplicates and ranks
the results. Upstream providers see the server's IP and a generic browser
identity, never your IP, cookies or accounts, but they do still see the search
terms. This is **not anonymity**; see [docs/privacy.md](docs/privacy.md).

```
Browser ──HTTPS──▶ Caddy (TLS, rate limits, auth, headers) ──▶ SearXNG ──▶ upstream engines
```

## Quick start (local)

Requirements: Docker with the Compose v2 plugin, and `openssl`.

```bash
./scripts/setup.sh      # checks prerequisites, creates .env with fresh secrets
./scripts/start.sh      # validates config, starts the stack, waits for health
# then browse to https://localhost (user "quiet" + the password you chose)
./scripts/stop.sh
```

Operations, logs and troubleshooting are covered in [docs/runbook.md](docs/runbook.md).

## Repository layout

| Path | Purpose |
|---|---|
| `docker-compose.yml` | Services, networks, health checks, hardening |
| `searxng/settings.yml` | Instance configuration (overrides upstream defaults) |
| `reverse-proxy/Caddyfile` | TLS, HTTP→HTTPS redirect, rate limits, basic auth, security headers |
| `reverse-proxy/Dockerfile` | Caddy build with the rate-limit module |
| `reverse-proxy/site/` | Instance branding: About and Privacy pages, logo, favicon (served by Caddy; SearXNG stays unmodified) |
| `scripts/` | Setup, start/stop, health checks, backups |
| `client/` | Python CLI for the JSON search API |
| `terraform/` | Optional AWS provisioning |
| `docs/` | Architecture, security, privacy, deployment, runbook |

## Credits

Search is powered by [SearXNG](https://github.com/searxng/searxng)
(AGPL-3.0). This repository contains the deployment, configuration and
tooling around it, not SearXNG itself.
