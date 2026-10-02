# Runbook

Day-to-day operation of a Quietsearch deployment. Run all commands from the
repository root.

## Start / stop

| Task | Command |
|---|---|
| First-time setup (creates `.env`) | `./scripts/setup.sh` (local) or `./scripts/setup.sh search.example.com you@example.com` |
| Start and wait for health | `./scripts/start.sh` |
| Stop, keep certificates/cache | `./scripts/stop.sh` |
| Stop and delete all volumes | `./scripts/stop.sh --purge` |
| Restart one service | `docker compose restart searxng` |
| Apply config changes | `docker compose up -d --force-recreate searxng caddy` |

Config files are bind-mounted read-only, so changes to `searxng/settings.yml`,
or `reverse-proxy/Caddyfile` take effect after the
container restarts. Caddy's admin API is disabled, so `caddy reload` is not
available by design.

## Status and logs

```bash
docker compose ps                         # state + health of each container
docker compose logs -f                    # follow all logs
docker compose logs --since 10m searxng   # recent SearXNG logs
docker compose logs caddy | grep -i -E "error|certificate"
docker stats --no-stream                  # CPU / memory per container
```

Logs use Docker's `json-file` driver, rotated at 10 MB × 3 files per container
(configured in `docker-compose.yml`), so they can't fill the disk.

What you won't find in the logs: Caddy has no access log, so search queries
in URLs are never written there. SearXNG logs warnings and errors only.

## Validating configuration

```bash
docker compose config --quiet && echo "compose OK"

# Caddyfile syntax. Uses the project's own Caddy image (stock Caddy doesn't
# know the rate_limit directive). A throwaway hash is generated on the fly so
# no hash-like string lives in the repo.
docker compose build caddy
IMG="$(docker compose config --images | grep quietsearch-caddy)"
TEST_HASH="$(docker run --rm "$IMG" caddy hash-password --plaintext throwaway)"
docker run --rm \
  -e SEARXNG_HOSTNAME=localhost -e ACME_EMAIL=a@example.org \
  -e BASIC_AUTH_USER=u -e BASIC_AUTH_HASH="$TEST_HASH" \
  -v "$PWD/reverse-proxy/Caddyfile:/etc/caddy/Caddyfile:ro" \
  "$IMG" caddy validate --config /etc/caddy/Caddyfile

# YAML syntax
python3 -c "import yaml,sys; yaml.safe_load(open('searxng/settings.yml')); print('settings.yml OK')"
```

## Quick functional checks

Read the password into a variable first, so it stays out of shell history:
`read -rs 'QS_PASS?Password: '` (zsh) or `read -rsp 'Password: ' QS_PASS` (bash).

```bash
# Unauthenticated health endpoint
curl -sk https://localhost/healthz                         # -> OK

# Auth is enforced
curl -sk -o /dev/null -w "%{http_code}\n" https://localhost/          # -> 401

# JSON search
curl -sk -u "quiet:$QS_PASS" "https://localhost/search?q=distributed+systems&format=json" \
  | python3 -m json.tool | head -40

# Engine diagnostics: configured engines, per-category probes, failures
QS_PASS="$QS_PASS" ./tests/engine_report.py --insecure

# Security controls
./tests/security_check.sh
```

`-k` skips certificate verification, which is needed locally because the cert
comes from Caddy's private CA. To trust that CA instead:

```bash
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./caddy-local-root.crt
curl --cacert ./caddy-local-root.crt https://localhost/healthz
```

On macOS you can add `caddy-local-root.crt` to Keychain Access → System →
Certificates and mark it "Always Trust" to remove browser warnings. Remove it
again when you no longer need it. `*.crt` is git-ignored.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `required variable SEARXNG_SECRET is missing` | `.env` missing or incomplete | `./scripts/setup.sh`, or fill in `.env` |
| `Bind for 127.0.0.1:443 failed: port is already allocated` | Something else is using 80/443 | `lsof -nP -iTCP:443 -sTCP:LISTEN` and stop it |
| Browser keeps asking for a password | Wrong password, or a bad hash in `.env` | The hash must be single-quoted in `.env`. Rerun `setup.sh` after deleting `.env`. |
| `429 Too Many Requests` | Caddy rate limit: more than 30 searches or 120 requests per minute from one client | Wait for the `Retry-After` period (up to 1 minute); expected behaviour |
| Results page shows "Engines cannot retrieve results" | Upstream engine blocked or timed out (CAPTCHA, 403, 429) | `docker compose logs searxng \| grep -i -E "captcha\|denied\|timeout"`. Engines are suspended for a while automatically, then retried. |
| First search after a restart returns 0 results (all engines "timeout") | Cold start: new DNS + TLS connections to every engine must fit in the 3s per-engine timeout | Search again. Connections are reused after the first query. |
| `brave: Suspended: too many requests` | Brave rate-limited the server IP (HTTP 429) | Nothing; SearXNG retries Brave after 180s. Persistent? Disable it in `settings.yml`. |
| `call to ResultContainer.add_unresponsive_engine after ResultContainer.close` | An engine timed out after the response was already sent | Harmless upstream log noise |
| searxng `unhealthy` | Usually a settings.yml error | `docker compose logs searxng` shows the YAML/key error |
| Log warning `"/etc/searxng" directory is not owned by "searxng:searxng"` | Expected: config is mounted read-only and `FORCE_OWNERSHIP=false` | None. Files only need to be readable. |
| `TRACKER_PATTERNS: failed fetching ClearURL rule lists` | Outbound access to the tracker-rule CDN blocked | Only the tracker-URL-remover plugin is affected. Check outbound HTTPS. |

## Upgrading

1. Find the current image version: `docker image inspect searxng/searxng:latest --format '{{index .Config.Labels "org.opencontainers.image.version"}}'`
2. Pin it in `.env` (`SEARXNG_VERSION=<tag>`) so every deployment runs the same build.
3. To upgrade: change the tag, then `./scripts/start.sh` (pulls and recreates), then run the checks above.
4. To roll back: restore the previous tag and run `./scripts/start.sh`.
