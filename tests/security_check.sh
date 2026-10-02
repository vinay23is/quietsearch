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
for c in quietsearch-searxng quietsearch-valkey; do
  ports="$(docker port "$c" 2>/dev/null)"
  if [[ -z "$ports" ]]; then pass "$c publishes no host ports"; else fail "$c published: $ports"; fi
done
caddy_ports="$(docker compose ps caddy --format '{{.Ports}}')"
echo "    caddy: $caddy_ports"

echo "== Network isolation"
if docker compose exec -T valkey wget -q -T 3 -O /dev/null https://example.com >/dev/null 2>&1; then
  fail "valkey can reach the internet"; else pass "valkey has no internet access"; fi
if docker compose exec -T caddy nc -z -w 3 valkey 6379 >/dev/null 2>&1; then
  fail "caddy can reach valkey"; else pass "caddy cannot reach valkey"; fi
if docker compose exec -T caddy nc -z -w 3 searxng 8080 >/dev/null 2>&1; then
  pass "caddy can reach searxng (required)"; else fail "caddy cannot reach searxng"; fi

echo "== Container hardening"
if docker compose exec -T caddy touch /etc/qs-test >/dev/null 2>&1; then
  fail "caddy root filesystem is writable"; else pass "caddy root filesystem is read-only"; fi
if docker compose exec -T searxng touch /etc/searxng/qs-test >/dev/null 2>&1; then
  fail "searxng config mount is writable"; else pass "searxng config mount is read-only"; fi
for c in quietsearch-caddy quietsearch-searxng quietsearch-valkey; do
  drop="$(docker inspect -f '{{.HostConfig.CapDrop}}' "$c")"
  nnp="$(docker inspect -f '{{.HostConfig.SecurityOpt}}' "$c")"
  if [[ "$drop" == *ALL* && "$nnp" == *no-new-privileges* ]]; then
    pass "$c: cap_drop ALL + no-new-privileges"; else fail "$c: drop=$drop opts=$nnp"; fi
done

echo "== Rate limiter (request from inside the caddy container, bypassing auth)"
code="$(docker compose exec -T caddy wget -S -q -O /dev/null -U 'Wget' \
        'http://searxng:8080/search?q=test&format=json' 2>&1 \
        | grep -oE 'HTTP/[0-9.]+ [0-9]{3}' | awk '{print $2}' | tail -1)"
check "Bot User-Agent rejected by limiter" "429" "${code:-none}"

echo "== Versions"
docker compose ps --format '    {{.Image}}'

echo
if (( fails == 0 )); then echo "All checks passed."; else echo "$fails check(s) failed."; fi
exit "$fails"
