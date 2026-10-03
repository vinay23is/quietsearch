#!/usr/bin/env bash
# Health report for the running Quietsearch stack: container state and
# restarts, HTTP and TLS from the outside, CPU/memory/disk, recent errors.
#
# Usage:
#   ./scripts/healthcheck.sh            full report
#   ./scripts/healthcheck.sh --quiet    print failures only (for cron)
#
# Exit code: 0 healthy, 1 one or more checks failed.
# Cron example (every 5 minutes, log failures):
#   */5 * * * * cd /path/to/quietsearch && ./scripts/healthcheck.sh --quiet >> healthcheck.log 2>&1
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

QUIET=false
[[ "${1:-}" == "--quiet" ]] && QUIET=true
HOST="$(awk -F= '$1=="SEARXNG_HOSTNAME"{print $2}' .env 2>/dev/null)"
HOST="${HOST:-localhost}"
DISK_LIMIT_PCT=85
problems=0

say()  { $QUIET || printf '%s\n' "$*"; }
ok()   { say "  OK    $*"; }
bad()  { printf '  FAIL  %s\n' "$*"; problems=$((problems + 1)); }
info() { say "        $*"; }

$QUIET || echo "Quietsearch health report: $(date '+%Y-%m-%d %H:%M:%S %Z')"

say "== Containers"
for c in quietsearch-caddy quietsearch-searxng; do
  fmt='{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} {{.RestartCount}} {{.State.StartedAt}}'
  if ! read -r state health restarts started < <(docker inspect -f "$fmt" "$c" 2>/dev/null); then
    bad "$c: container not found"
    continue
  fi
  detail="$state, health=$health, restarts=$restarts, up since ${started:0:19}Z"
  if [[ "$state" == "running" && "$health" != "unhealthy" ]]; then ok "$c: $detail"; else bad "$c: $detail"; fi
done

say "== HTTP"
read -r code secs < <(curl -sk -o /dev/null -w '%{http_code} %{time_total}' --max-time 10 "https://$HOST/healthz" 2>/dev/null || echo "000 0")
if [[ "$code" == "200" ]]; then ok "https://$HOST/healthz -> 200 in ${secs}s"; else bad "https://$HOST/healthz -> HTTP $code"; fi
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://$HOST/" 2>/dev/null)"
if [[ "$code" == "308" ]]; then ok "http://$HOST/ redirects to HTTPS (308)"; else bad "http://$HOST/ -> HTTP $code (expected 308)"; fi

say "== TLS certificate"
pem="$(echo | openssl s_client -connect "$HOST:443" -servername "$HOST" 2>/dev/null | openssl x509 2>/dev/null)"
if [[ -z "$pem" ]]; then
  bad "no certificate presented on $HOST:443"
else
  issuer="$(openssl x509 -noout -issuer <<<"$pem" | sed 's/^issuer= *//')"
  expiry="$(openssl x509 -noout -enddate <<<"$pem" | sed 's/^notAfter=//')"
  # Caddy's local CA issues 12-hour certificates and renews them continuously;
  # public certificates (Let's Encrypt, 90 days) should never be within 14 days.
  if grep -q "Caddy Local Authority" <<<"$issuer"; then margin=3600; else margin=$((14 * 86400)); fi
  if openssl x509 -noout -checkend "$margin" <<<"$pem" >/dev/null; then
    ok "valid until $expiry (issuer: $issuer)"
  else
    bad "expires soon: $expiry (issuer: $issuer)"
  fi
fi

say "== Resources"
if ! $QUIET; then
  docker stats --no-stream --format '        {{.Name}}  CPU {{.CPUPerc}}  MEM {{.MemUsage}} ({{.MemPerc}})' \
    quietsearch-caddy quietsearch-searxng 2>/dev/null
fi
pct="$(df -P . | awk 'NR==2 { gsub("%", "", $5); print $5 }')"
if (( pct < DISK_LIMIT_PCT )); then ok "disk usage ${pct}% (limit ${DISK_LIMIT_PCT}%)"; else bad "disk usage ${pct}% (limit ${DISK_LIMIT_PCT}%)"; fi
if ! $QUIET; then
  docker system df --format '        docker {{.Type}}: {{.Size}} ({{.Reclaimable}} reclaimable)' 2>/dev/null
fi

say "== Recent log errors (last 15 minutes)"
# Informational: upstream engine errors (CAPTCHA, 429, timeouts) are normal
# for a metasearch engine and are not counted as failures.
for c in quietsearch-searxng quietsearch-caddy; do
  n="$(docker logs --since 15m "$c" 2>&1 | grep -c -E 'ERROR|"level":"error"')"
  info "$c: $n error line(s)"
done
if ! $QUIET; then
  docker logs --since 15m quietsearch-searxng 2>&1 | grep 'ERROR' \
    | sed -E 's/^.*ERROR:([^:]+):[[:space:]]*/\1: /' | cut -c1-70 | sort | uniq -c | sort -rn | head -5 \
    | sed 's/^/        /'
fi

say ""
if (( problems == 0 )); then say "Healthy."; exit 0; fi
echo "$problems problem(s) found."
exit 1
