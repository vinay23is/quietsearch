# Deployment

The same `docker-compose.yml` runs everywhere. What changes between
environments is `.env`: the hostname (which decides how TLS works) and
which address the proxy listens on.

| | Local machine | Home server / LAN | Internet-facing Linux server |
|---|---|---|---|
| `SEARXNG_HOSTNAME` | `localhost` | `localhost` or a LAN name | A public DNS name |
| `PUBLISH_ADDR` | `127.0.0.1` | LAN address or `0.0.0.0` | `0.0.0.0` |
| Certificate | Caddy's local CA | Caddy's local CA | Let's Encrypt (automatic) |
| Status | **Tested** (macOS, Docker Desktop, Apple Silicon) | Same as local | Documented below, **not yet exercised** on a public host |

## Prerequisites

- Docker Engine with the Compose v2 plugin (`docker compose version`)
- `openssl`, `git`, `curl`
- Python 3.9+ for the CLI client and tests (optional)
- 2 GB RAM recommended: the stack idles at about 135 MiB, but the proxy image
  compiles Caddy with Go on first build

## Local machine

```bash
git clone https://github.com/<you>/quietsearch.git
cd quietsearch
./scripts/setup.sh          # checks Docker, creates .env (mode 600), asks for a password
./scripts/start.sh          # validates config, builds the proxy image, starts, waits for health
```

Open `https://localhost` and sign in as `quiet`. The browser warns about the
certificate because it comes from Caddy's private CA; to trust it:

```bash
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./caddy-local-root.crt
# macOS: open it in Keychain Access → System → set "Always Trust"
```

Verify:

```bash
./tests/security_check.sh       # 20 automated security checks
./scripts/healthcheck.sh        # container, HTTP, TLS and resource report
```

Stop with `./scripts/stop.sh` (keeps certificates) or
`./scripts/stop.sh --purge` (removes all volumes).

## Home server on a LAN

Same as local, but set `PUBLISH_ADDR` to the server's LAN address in `.env`
so other devices can connect. Keep it off the public internet: for remote
access, use a VPN (for example WireGuard or Tailscale) instead of forwarding
ports on the router.

## Internet-facing Linux server (e.g. a cloud VM)

> This path follows from the local setup (the only differences are the
> hostname and listen address), but it has not yet been run on a public host.

**1. Server.** Ubuntu 24.04 LTS, 2 GB RAM, about 10 GB disk. ARM64 and x86-64
both work (all images are multi-architecture).

**2. Firewall.** Allow inbound TCP 80 and 443 from anywhere, and 22 only from
your own IP. Port 80 must be reachable for the Let's Encrypt HTTP challenge
and the redirect. Mirror the rules on the host:

```bash
sudo ufw default deny incoming
sudo ufw allow from <your-ip> to any port 22 proto tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

Note that ports published by Docker bypass `ufw`'s INPUT rules, so the cloud
firewall is the authoritative control; only ports 80 and 443 are published.

**3. SSH hardening** (`/etc/ssh/sshd_config`): `PasswordAuthentication no`,
`PermitRootLogin no`, then `sudo systemctl restart ssh`.

**4. Automatic security updates:** `sudo apt install unattended-upgrades`.

**5. Docker** from Docker's official apt repository (see
docs.docker.com/engine/install/ubuntu), then add your user to the `docker`
group and log in again.

**6. DNS.** Point a hostname at the server's public IP. Options:

- An `A` record on a domain you own, e.g. `search.example.com`
- No domain: a wildcard DNS service such as sslip.io, where
  `203-0-113-10.sslip.io` resolves to `203.0.113.10`. This only works while
  the IP stays the same, so use a static/reserved IP.

Check before continuing: `dig +short <hostname>` must print the server IP.

**7. Deploy:**

```bash
git clone https://github.com/<you>/quietsearch.git && cd quietsearch
./scripts/setup.sh <hostname> <your-email>   # sets PUBLISH_ADDR=0.0.0.0 for non-localhost names
./scripts/start.sh
docker compose logs caddy | grep -i certificate   # expect "certificate obtained successfully"
./tests/security_check.sh <hostname>
```

Use a **new, strong password** on any internet-facing instance.

**8. Reboots.** Both services use `restart: unless-stopped`, and Docker's
systemd unit is enabled by default on Ubuntu, so the stack comes back
after a reboot. Confirm with `sudo reboot`, then `./scripts/healthcheck.sh`.

**9. Monitoring.** Add the health check to cron (see runbook.md).

## Upgrades

See [runbook.md](runbook.md#upgrading): change the pinned version in `.env`,
run `./scripts/start.sh`, re-run the checks; roll back by restoring the old
version.
