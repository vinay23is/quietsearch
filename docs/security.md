# Security

This document describes what Quietsearch protects, the controls in place,
why each exists, and how to verify it. Privacy properties (what upstream
engines, DNS providers and others can still see) are in
[privacy.md](privacy.md).

## Scope

**Assets:** search queries, the basic-auth credential, `SEARXNG_SECRET`,
server access (SSH key, AWS credentials), the server IP's reputation with
upstream engines, and the AWS bill.

**In scope:** internet scanners and bots, unauthorized users, credential
leaks through Git, exposure of internal services, eavesdropping on the
network path, outdated software.

**Out of scope:** a compromised hosting provider or hypervisor, nation-state
adversaries, malicious pages linked from search results (the browser's
responsibility), and anonymity against the instance operator.

## Threat model

| Threat | Example | Mitigations |
|---|---|---|
| Unauthorized use | Bots find the instance, burn its IP reputation, get it blocked upstream | Basic auth on every route except `/healthz`; SearXNG limiter; not listed in public instance directories |
| Internal service exposure | Valkey or SearXNG reachable from the internet | No published ports except Caddy; `internal: true` backend network; firewall/security group allows only 80/443 (+22 restricted) |
| Eavesdropping | Queries read on public Wi-Fi | HTTPS only, HSTS, HTTP→HTTPS redirect |
| Credential/secret leak | `.env`, keys or state committed to GitHub | `.gitignore`, `.env.example` pattern, pre-commit checks, password stored only as a bcrypt hash |
| Brute force | Password guessing against basic auth or SSH | bcrypt (cost 14) slows each guess; 12-character minimum; SSH key-only, restricted by source IP (see Host hardening) |
| Container compromise | Exploit in Caddy or SearXNG | Dropped capabilities, `no-new-privileges`, read-only Caddy filesystem, Valkey isolated with no internet access |
| Outdated software | CVE in an image | Pinned versions, documented upgrade/rollback, OS security updates |
| Cost abuse | Forgotten or hijacked cloud resources | No long-lived AWS keys on the server, billing alarm, documented teardown (see deployment.md) |

## Controls

### Transport security (HTTPS)

| | |
|---|---|
| **What** | Caddy terminates TLS with automatically issued and renewed certificates (Let's Encrypt for real domains, Caddy's local CA for `localhost`). Port 80 only redirects to HTTPS. HSTS tells browsers to never use HTTP for this host again. |
| **Why** | Queries are sensitive. Without TLS, anyone on the network path can read them. Automatic renewal removes the most common TLS outage: an expired certificate. |
| **Where** | `reverse-proxy/Caddyfile` |

### Reverse proxy as the only entry point

| | |
|---|---|
| **What** | Only the `caddy` service has `ports:`. SearXNG (8080) and Valkey (6379) are reachable only over Docker networks. Locally, ports bind to `127.0.0.1`. |
| **Why** | One hardened, purpose-built component faces the internet. SearXNG's built-in server isn't designed to face the internet directly, and Valkey has no authentication configured. |
| **Where** | `docker-compose.yml` |

### Network segmentation

| | |
|---|---|
| **What** | Two networks. `frontend` (Caddy ↔ SearXNG, internet egress for upstream engines). `backend`, marked `internal: true` (SearXNG ↔ Valkey, no route to the internet). |
| **Why** | Valkey can't be reached by Caddy, and couldn't send data out even if compromised. Each component can talk only to what it needs. |
| **Where** | `docker-compose.yml` → `networks:` |

### Authentication

| | |
|---|---|
| **What** | HTTP basic auth on every route except `/healthz` (which returns only `OK`). The password is stored as a bcrypt hash in `.env`; the plaintext is never written to disk. |
| **Why** | This is a personal instance. Public SearXNG instances attract automated traffic that gets the server IP blocked by upstream engines. Authentication at the proxy stops unknown clients before they cause any upstream request. Basic auth is only safe over HTTPS, which is enforced. |
| **Where** | `reverse-proxy/Caddyfile`, `scripts/setup.sh` |

### Rate limiting and bot detection

| | |
|---|---|
| **What** | SearXNG's limiter, with state in Valkey. Rejects clients without normal HTTP headers and limits request bursts per client (each IPv4 address, or each IPv6 `/48` network). Only Caddy's fixed container IP is trusted to supply the real client IP via `X-Forwarded-For`. |
| **Why** | Defense in depth behind authentication. A leaked password or a runaway script can't flood upstream engines from this IP. Trusting only Caddy's IP stops clients from spoofing `X-Forwarded-For` to dodge limits. |
| **Trade-off** | API clients must send a non-bot `User-Agent` plus `Accept`, `Accept-Language` and `Accept-Encoding` headers (see runbook.md). |
| **Where** | `searxng/settings.yml` (`server.limiter`), `searxng/limiter.toml` |

### Security headers

| Header | Purpose |
|---|---|
| `Strict-Transport-Security` | Browser refuses plain HTTP for one year |
| `Content-Security-Policy` | Scripts, styles and connections only from this origin; no framing; limits impact of any injected content |
| `X-Frame-Options: DENY` | Clickjacking protection for older browsers |
| `X-Content-Type-Options: nosniff` | No MIME-type guessing |
| `Referrer-Policy: no-referrer` | Result pages (whose URLs contain the query) are never sent as `Referer` to clicked sites |
| `Permissions-Policy` | Camera, microphone, geolocation etc. disabled |
| `Server` removed | Doesn't advertise software versions |

### Container hardening

| Setting | Applied to | Why |
|---|---|---|
| `cap_drop: [ALL]` + minimal `cap_add` | all | Removes Linux privileges the processes don't need. Caddy keeps only `NET_BIND_SERVICE` (ports 80/443). |
| `no-new-privileges` | all | Blocks privilege escalation through setuid binaries |
| `read_only: true` + `tmpfs /tmp` | caddy | Container filesystem can't be modified; writes only to its data volumes |
| Config mounted `:ro` | caddy, searxng | A compromised process can't rewrite its own configuration |
| `request_body max_size 1MB` | caddy | Rejects oversized request bodies early |
| Log rotation (10 MB × 3) | all | Logs can't fill the disk |

### Secret management

| Secret | Where it lives | Notes |
|---|---|---|
| `SEARXNG_SECRET` | `.env` (mode 600, git-ignored) | Generated by `setup.sh` with `openssl rand -hex 32` |
| Basic-auth password | Your password manager only | `.env` holds just the bcrypt hash |
| SSH private key | Your machine (`~/.ssh`) | Never copied to the server or repo |
| AWS credentials | Your machine / AWS SSO | Never on the server; the EC2 instance uses an IAM role if it needs AWS APIs |

Rules: `.env.example` documents every variable with empty values; real
values never appear in `docker-compose.yml`, settings files, Terraform
variables or commit history. Before committing:

```bash
git diff --cached --name-only | grep -E '\.env$|tfstate|\.pem$|\.key$' || echo 'no secret files staged'
git grep --cached -n -E 'SEARXNG_SECRET=[0-9a-f]{16}' || echo 'no secret values staged'
git grep --cached -n -F '$2a$14$' || echo 'no password hashes staged'
```

If a secret is ever committed: rotate it first (new `SEARXNG_SECRET`,
new password via `setup.sh`), then remove it from history. Treat anything
pushed to GitHub as permanently exposed.

### Privacy-preserving operations

- No access log at the proxy, so queries in URLs are never written to disk.
- SearXNG logs warnings and errors only.
- Autocomplete off by default (it sends partial queries upstream as you type).
- Result thumbnails proxied through the server (`image_proxy: true`).

### No exposed admin or debug interfaces

- Caddy admin API disabled (`admin off`).
- SearXNG `debug: false`; `open_metrics` left empty, so `/metrics` is disabled.
- `/stats` and `/config` are behind authentication.
- Valkey has no published port and no internet route.

## Update strategy

| Component | Pinned as | How to update |
|---|---|---|
| SearXNG | exact build tag in `.env` (`SEARXNG_VERSION`) | Change the tag, `./scripts/start.sh`, run the checks below; roll back by restoring the old tag |
| Caddy | minor version (`2.10-alpine`) | Patch releases arrive on `docker compose pull`; bump the minor version deliberately |
| Valkey | major version (`9-alpine`) | Same as Caddy. Holds no persistent data, so upgrades are risk-free |
| Host OS (EC2) | Ubuntu LTS | `unattended-upgrades` for security patches |

Check upstream releases roughly monthly, and immediately for announced CVEs.

## Host hardening (servers)

Applied during the AWS deployment (see deployment.md):

- Security group: 443 and 80 from anywhere; 22 only from your current IP
  (or closed entirely by using SSM Session Manager).
- Host firewall (`ufw`) mirrors the security group: defense in depth if the
  security group is ever misconfigured.
- SSH: key-only, `PasswordAuthentication no`, `PermitRootLogin no`.
- `unattended-upgrades` for automatic security patches.
- Docker's daemon socket is never exposed over TCP.
- No unnecessary services: the instance runs Docker and sshd only.

## Verifying the controls

Run from the repository root with the stack running.

```bash
# HTTPS redirect (expect 308 -> https://localhost/)
curl -s -o /dev/null -w "%{http_code} -> %{redirect_url}\n" http://localhost/

# Auth required (expect 401) and health endpoint open (expect OK)
curl -sk -o /dev/null -w "%{http_code}\n" https://localhost/
curl -sk https://localhost/healthz; echo

# Security headers present
curl -sk -u "quiet:$QS_PASS" -D - -o /dev/null https://localhost/ \
  | grep -i -E 'strict-transport|content-security|x-frame|x-content-type|referrer-policy|permissions-policy|^server:'

# Only Caddy publishes ports, and only on 127.0.0.1 locally
docker compose ps --format '{{.Name}}\t{{.Ports}}'

# Valkey has no internet access (expect failure)
docker compose exec valkey wget -q -T 3 -O /dev/null https://example.com && echo "UNEXPECTED: valkey has internet" || echo "valkey: no internet (good)"

# Caddy cannot reach Valkey (expect failure: different network)
docker compose exec caddy nc -z -w 3 valkey 6379 && echo "UNEXPECTED: caddy reached valkey" || echo "caddy cannot reach valkey (good)"

# Caddy's root filesystem is read-only (expect failure)
docker compose exec caddy touch /etc/test && echo "UNEXPECTED: writable" || echo "caddy rootfs read-only (good)"

# Default curl User-Agent is rejected by the limiter (expect 429)
curl -sk -u "quiet:$QS_PASS" -o /dev/null -w "%{http_code}\n" "https://localhost/search?q=test&format=json"
```
