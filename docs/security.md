# Security

This document describes what Quietsearch protects, the controls in place,
why each exists, and how to verify it. Privacy properties (what upstream
engines, DNS providers and others can still see) are in
[privacy.md](privacy.md).

## Scope

**Assets:** search queries, the basic-auth credential, `SEARXNG_SECRET`,
server access (SSH keys, cloud credentials when hosted), and the server IP's
reputation with upstream engines.

**In scope:** internet scanners and bots, unauthorized users, credential
leaks through Git, exposure of internal services, eavesdropping on the
network path, outdated software.

**Out of scope:** a compromised hosting provider or hypervisor, nation-state
adversaries, malicious pages linked from search results (the browser's
responsibility), and anonymity against the instance operator.

## Threat model

| Threat | Example | Mitigations |
|---|---|---|
| Unauthorized use | Bots find the instance, burn its IP reputation, get it blocked upstream | Basic auth on every route except `/healthz`; per-client rate limits at the proxy; not listed in public instance directories |
| Internal service exposure | SearXNG reachable from the internet without TLS or auth | No published ports except Caddy; private Docker network; firewall/security group allows only 80/443 (+22 restricted) |
| Eavesdropping | Queries read on public Wi-Fi | HTTPS only, HSTS, HTTP→HTTPS redirect |
| Credential/secret leak | `.env`, keys or state committed to GitHub | `.gitignore`, `.env.example` pattern, pre-commit checks, password stored only as a bcrypt hash |
| Brute force | Password guessing against basic auth or SSH | Proxy rate limit (120 requests/min per client, applied before auth); bcrypt (cost 14); 12-character minimum; SSH key-only, restricted by source IP (see Host hardening) |
| Container compromise | Exploit in Caddy or SearXNG | Dropped capabilities, `no-new-privileges`, read-only Caddy filesystem, read-only config mounts |
| Outdated software | CVE in an image | Pinned versions, documented upgrade/rollback, OS security updates |

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
| **What** | Only the `caddy` service has `ports:`. SearXNG (8080) is reachable only over the private Docker network. Locally, ports bind to `127.0.0.1`. |
| **Why** | One hardened, purpose-built component faces the internet. SearXNG's built-in server isn't designed to face the internet directly. |
| **Where** | `docker-compose.yml` |

### Network design

| | |
|---|---|
| **What** | One private bridge network (`quietsearch`) shared by Caddy and SearXNG. SearXNG needs outbound internet access to query upstream engines; nothing else runs on the network. |
| **Why** | Two containers with a single trust relationship (Caddy → SearXNG) don't need further segmentation. An earlier design added an internal-only network for a Valkey rate-limit store; it was removed together with Valkey (see Rate limiting). |
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
| **What** | Caddy's `rate_limit` handler (the caddy-ratelimit module, compiled into a custom image). Per client IP: 30 searches/minute (`/search`, `/autocompleter`) and 120 requests/minute overall. Excess requests get HTTP 429 with `Retry-After`. |
| **Why** | Each search fans out to every enabled upstream engine, so a runaway script or leaked password could get the server IP blocked upstream. The limit runs before `basic_auth`, so it also caps password guessing and the CPU spent on bcrypt checks. |
| **Design decision** | SearXNG ships its own limiter (backed by Valkey), used in the first version of this project. Testing showed it's built for anonymous public instances: it rejects non-browser clients and hard-codes a quota of 4 JSON API requests per IP per hour, which made the API unusable. On an authenticated private instance, rate limiting belongs at the edge, so the SearXNG limiter is off and Valkey was removed. |
| **Where** | `reverse-proxy/Caddyfile`, `reverse-proxy/Dockerfile` |

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
| Cloud credentials (if hosted) | Your machine / the provider's SSO | Never on the server or in the repo |

Rules: `.env.example` documents every variable with empty values; real
values never appear in `docker-compose.yml`, settings files or commit
history. Before committing:

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
- SearXNG has no published port.

## Update strategy

| Component | Pinned as | How to update |
|---|---|---|
| SearXNG | exact build tag in `.env` (`SEARXNG_VERSION`) | Change the tag, `./scripts/start.sh`, run the checks below; roll back by restoring the old tag |
| Caddy | exact release in `.env` (`CADDY_VERSION`, used as the image build argument) | Change the version and run `./scripts/start.sh`, which rebuilds the image |
| caddy-ratelimit module | git commit in `reverse-proxy/Dockerfile` | Check the module's repository; bump the commit and rebuild (`./scripts/start.sh`) |
| Host OS (server) | Ubuntu LTS | `unattended-upgrades` for security patches |

Check upstream releases roughly monthly, and immediately for announced CVEs.

## Host hardening (servers)

For deployment on an internet-facing server (see deployment.md):

- Cloud firewall / security group: 80 and 443 from anywhere (port 80 is needed
  for the Let's Encrypt HTTP challenge and the redirect); 22 only from your
  own IP address.
- Host firewall (`ufw`) mirrors those rules: defense in depth if the cloud
  firewall is ever misconfigured.
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

# Caddy's root filesystem is read-only (expect failure)
docker compose exec caddy touch /etc/test && echo "UNEXPECTED: writable" || echo "caddy rootfs read-only (good)"

# Rate limit: 35 rapid searches, expect some 429s after the first 30
for i in $(seq 1 35); do curl -sk -o /dev/null -w '%{http_code}\n' "https://localhost/search?q=x"; done | sort | uniq -c
```

All of the above is automated in `tests/security_check.sh`.
