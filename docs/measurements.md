# Measurements

Observed values only. Each entry records where and how it was measured, so
any number quoted elsewhere (README, resume) can be traced back here.

## Local: MacBook Pro (Apple Silicon), Docker Desktop, home network

Date: 2026-10-02. SearXNG `2026.10.2-19ffbcd30`, Caddy `2.10-alpine`, Valkey `9-alpine`.

| Metric | Value | Method |
|---|---|---|
| Containers healthy after `docker compose up` | SearXNG 6.8s, Valkey 1.1s | `./scripts/start.sh` output |
| Outbound DNS lookup from the container | 0.01–0.10s | Python `socket.gethostbyname` inside `quietsearch-searxng` |
| Outbound HTTPS request from the container | 0.18–0.28s | Python `urllib` inside `quietsearch-searxng` |
| JSON search, cold (first query after start) | 4.74s, 0 results (all engines timed out) | `curl -w %{time_total}` via Caddy |
| JSON search, warm (`q=linux kernel`) | 1.46s, 13 results | `curl -w %{time_total}` via Caddy |
| Engines contributing (warm query) | duckduckgo 11, bing 10 (8 URLs merged) | `engines` field in JSON response |
| Idle memory per container | searxng 120.9 MiB, caddy 13.8 MiB, valkey 9.4 MiB (~144 MiB total) | `docker stats --no-stream` after restart |
| Idle CPU | <1% per container | `docker stats --no-stream` |
| Configured engines | 24 loaded (23 enabled, google disabled) | `searxng/settings.yml` |

Notes:
- Brave returned HTTP 429 to this home IP and SearXNG suspended it for 180s
  (`suspended_times.SearxEngineTooManyRequests`).
- The first query after a restart is slow because SearXNG opens new DNS/TLS
  connections to every engine within the 3s per-engine timeout.
