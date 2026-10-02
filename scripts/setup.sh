#!/usr/bin/env bash
# First-time setup: checks prerequisites and creates .env with fresh secrets.
#
# Usage:
#   ./scripts/setup.sh                                      # local: https://localhost
#   ./scripts/setup.sh search.example.com you@example.com   # server with a real domain
#
# Safe to re-run: an existing .env is never overwritten.
set -euo pipefail

cd "$(dirname "$0")/.."

HOSTNAME_ARG="${1:-localhost}"
EMAIL_ARG="${2:-you@example.com}"
CADDY_IMAGE="docker.io/library/caddy:2.10.2-alpine"

info()  { printf '\033[1;34m[setup]\033[0m %s\n' "$*"; }
fail()  { printf '\033[1;31m[setup] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# --- 0. Argument validation ------------------------------------------------------
# zsh doesn't treat '#' as a comment in interactive shells by default, so
# pasting "./scripts/setup.sh   # note" passes '#' and 'note' as arguments.
# Reject anything that isn't a plausible hostname or email.
[[ "$HOSTNAME_ARG" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] \
  || fail "Invalid hostname '$HOSTNAME_ARG'. Usage: ./scripts/setup.sh [hostname] [email]"
[[ "$EMAIL_ARG" =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]] \
  || fail "Invalid email '$EMAIL_ARG'. Usage: ./scripts/setup.sh [hostname] [email]"

# --- 1. Prerequisites ---------------------------------------------------------
command -v docker >/dev/null 2>&1 || fail "docker not found. Install Docker first (see docs/deployment.md)."
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 plugin not found ('docker compose')."
docker info >/dev/null 2>&1 || fail "Docker daemon not reachable. Is Docker running? (Linux: is your user in the 'docker' group?)"
command -v openssl >/dev/null 2>&1 || fail "openssl not found."
info "Docker $(docker version --format '{{.Server.Version}}'), $(docker compose version --short | sed 's/^/Compose /')"

# --- 2. .env ------------------------------------------------------------------
if [[ -f .env ]]; then
  info ".env already exists; leaving it unchanged. Delete it to regenerate."
  exit 0
fi

# Replace the value of KEY in .env. Uses awk rather than sed because values
# (bcrypt hashes) contain '$' and '/', and because macOS and GNU sed differ.
set_env() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  awk -v k="$key" -v v="$value" 'BEGIN{FS=OFS="="} $1==k {print k "=" v; next} {print}' .env > "$tmp"
  mv "$tmp" .env
}

cp .env.example .env
chmod 600 .env   # readable only by you

info "Generating SEARXNG_SECRET"
set_env SEARXNG_SECRET "$(openssl rand -hex 32)"

set_env SEARXNG_HOSTNAME "$HOSTNAME_ARG"
set_env ACME_EMAIL "$EMAIL_ARG"
if [[ "$HOSTNAME_ARG" == "localhost" ]]; then
  set_env PUBLISH_ADDR "127.0.0.1"
else
  set_env PUBLISH_ADDR "0.0.0.0"
fi

# --- 3. Basic auth password ----------------------------------------------------
info "Choose a password for the web UI (user: quiet)."
while true; do
  read -r -s -p "Password: " pw1; echo
  read -r -s -p "Repeat:   " pw2; echo
  [[ "$pw1" == "$pw2" ]] || { echo "Passwords differ, try again."; continue; }
  [[ ${#pw1} -ge 12 ]]   || { echo "Use at least 12 characters."; continue; }
  break
done

info "Hashing password with bcrypt (pulls the Caddy image on first run)"
# The password goes to the container over stdin, not as a command-line
# argument, so it doesn't show up in the process list or shell history.
hash="$(printf '%s\n' "$pw1" | docker run --rm -i "$CADDY_IMAGE" caddy hash-password 2>/dev/null | tail -n1)"
unset pw1 pw2
[[ "$hash" == \$2* ]] || fail "Password hashing failed."
set_env BASIC_AUTH_HASH "'$hash'"

info "Done. Created .env (mode 600) for https://$HOSTNAME_ARG"
info "Next: ./scripts/start.sh"
