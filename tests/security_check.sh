#!/usr/bin/env bash
# Verifies the security controls described in docs/security.md against the
# running stack. Needs no credentials. Exit code = number of failed checks.
#
# Usage: ./tests/security_check.sh [hostname]    (default: localhost)
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
HOST="${1:-localhost}"
fails=0

pass() { printf '  \033[32mPASS\033[0m %s\n' "$*"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$*"; fails=$((fails + 1)); }
check() { # check "description" expected actual
  if [[ "$3" == "$2" ]]; then pass "$1 ($3)"; else fail "$1 (expected $2, got $3)"; fi
}

# Every check below assumes both containers are running. Without this guard,
# "command failed" checks (read-only filesystem etc.) would pass vacuously.
for c in quietsearch-caddy quietsearch-searxng; do
  if [[ "$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)" != "true" ]]; then
    echo "ERROR: container $c is not running. Start the stack first: ./scripts/start.sh" >&2
    exit 1
  fi
done

echo "== Transport"
check "HTTP redirects to HTTPS" "308" "$(curl -s -o /dev/null -w '%{http_code}' "http://$HOST/")"
check "Redirect target is HTTPS" "https://$HOST/" "$(curl -s -o /dev/null -w '%{redirect_url}' "http://$HOST/")"

echo "== Authentication"
check "Homepage requires auth" "401" "$(curl -sk -o /dev/null -w '%{http_code}' "https://$HOST/")"
check "Search requires auth" "401" "$(curl -sk -o /dev/null -w '%{http_code}' "https://$HOST/search?q=test&format=json")"
check "Health endpoint open" "OK" "$(curl -sk "https://$HOST/healthz")"

echo "== Security headers"
headers="$(curl -sk -D - -o /dev/null "https://$HOST/healthz" | tr -d '\r')"
for h in strict-transport-security content-security-policy x-frame-options \
         x-content-type-options referrer-policy permissions-policy; do
  if grep -qi "^$h:" <<<"$headers"; then pass "$h present"; else fail "$h missing"; fi
done
if grep -qi '^server:' <<<"$headers"; then fail "Server header exposed"; else pass "Server header removed"; fi

echo "== Exposed ports"
# "docker port" lists only host-published mappings, so it must print nothing.
ports="$(docker port quietsearch-searxng 2>/dev/null)"
if [[ -z "$ports" ]]; then pass "searxng publishes no host ports"; else fail "searxng published: $ports"; fi
caddy_ports="$(docker compose ps caddy --format '{{.Ports}}')"
echo "    caddy: $caddy_ports"

echo "== Network isolation"
if docker compose exec -T caddy nc -z -w 3 searxng 8080 >/dev/null 2>&1; then
  pass "caddy can reach searxng (required)"; else fail "caddy cannot reach searxng"; fi

echo "== Container hardening"
# Match the specific error, so a failed "exec" can't count as a pass.
out="$(docker compose exec -T caddy touch /etc/qs-test 2>&1)"
if grep -qi 'read-only file system' <<<"$out"; then pass "caddy root filesystem is read-only"
else fail "caddy root filesystem: $out"; fi
out="$(docker compose exec -T searxng touch /etc/searxng/qs-test 2>&1)"
if grep -qi 'read-only file system' <<<"$out"; then pass "searxng config mount is read-only"
else fail "searxng config mount: $out"; fi
for c in quietsearch-caddy quietsearch-searxng; do
  drop="$(docker inspect -f '{{.HostConfig.CapDrop}}' "$c")"
  nnp="$(docker inspect -f '{{.HostConfig.SecurityOpt}}' "$c")"
  if [[ "$drop" == *ALL* && "$nnp" == *no-new-privileges* ]]; then
    pass "$c: cap_drop ALL + no-new-privileges"; else fail "$c: drop=$drop opts=$nnp"; fi
done

echo "== Rate limiting (Caddy, before authentication)"
if docker compose exec -T caddy caddy list-modules 2>/dev/null | grep -q '^http.handlers.rate_limit$'; then
  pass "rate_limit module compiled into caddy"; else fail "rate_limit module missing"; fi
# The search zone allows 30 requests/minute per client. Unauthenticated
# requests are counted too, so 35 rapid ones must hit the limit. Codes are
# 401 (auth) until the limit, then 429.
codes="$(for _ in $(seq 1 35); do
  curl -sk -o /dev/null -w '%{http_code}\n' "https://$HOST/search?q=ratelimit-test"
done | sort | uniq -c | tr -s ' ' | tr '\n' ';')"
if grep -q ' 429;' <<<"$codes"; then pass "search burst limited ($codes)"; else fail "no 429 in 35 rapid searches ($codes)"; fi
echo "    note: searches from this machine stay limited for up to 1 minute"

echo "== Versions"
docker compose ps --format '    {{.Image}}'

echo
if (( fails == 0 )); then echo "All checks passed."; else echo "$fails check(s) failed."; fi
exit "$fails"
