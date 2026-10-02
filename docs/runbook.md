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
`searxng/limiter.toml` or `reverse-proxy/Caddyfile` take effect after the
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

# Caddyfile syntax (uses the same image as production).
# A throwaway hash is generated on the fly so no hash-like string lives in the repo.
TEST_HASH="$(docker run --rm caddy:2.10-alpine caddy hash-password --plaintext throwaway)"
docker run --rm \
  -e SEARXNG_HOSTNAME=localhost -e ACME_EMAIL=a@example.org \
  -e BASIC_AUTH_USER=u -e BASIC_AUTH_HASH="$TEST_HASH" \
  -v "$PWD/reverse-proxy/Caddyfile:/etc/caddy/Caddyfile:ro" \
  caddy:2.10-alpine caddy validate --config /etc/caddy/Caddyfile

# YAML / TOML syntax
python3 -c "import yaml,sys; yaml.safe_load(open('searxng/settings.yml')); print('settings.yml OK')"
python3 -c "import tomllib; tomllib.load(open('searxng/limiter.toml','rb')); print('limiter.toml OK')"
```

## Quick functional checks

The SearXNG limiter rejects requests that don't look like a browser or a
well-behaved API client. When testing with curl, send a non-default
User-Agent plus `Accept`, `Accept-Language` and `Accept-Encoding`:

```bash
# Unauthenticated health endpoint
curl -sk https://localhost/healthz                         # -> OK

# Auth is enforced
curl -sk -o /dev/null -w "%{http_code}\n" https://localhost/          # -> 401

# JSON search (replace PASSWORD)
curl -sk --compressed -u quiet:PASSWORD \
  -H "User-Agent: quietsearch-cli/0.1" \
  -H "Accept: application/json, text/html;q=0.5" \
  -H "Accept-Language: en-US,en;q=0.8" \
  "https://localhost/search?q=distributed+systems&format=json" \
  | python3 -m json.tool | head -40
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
| `429 Too Many Requests` from curl | Limiter: default curl User-Agent or missing headers | Send the headers shown above |
| `429` after many fast searches | Limiter `ip_limit` burst protection | Wait a minute; this is expected behaviour |
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
