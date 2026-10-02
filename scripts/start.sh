#!/usr/bin/env bash
# Validate the configuration, start the stack and wait until it's healthy.
# Usage: ./scripts/start.sh
set -euo pipefail

cd "$(dirname "$0")/.."

[[ -f .env ]] || { echo "No .env found. Run ./scripts/setup.sh first." >&2; exit 1; }

echo "[start] Validating docker-compose.yml + .env"
docker compose config --quiet

echo "[start] Pulling images (no-op if already present)"
docker compose pull --quiet

echo "[start] Starting containers"
docker compose up -d --remove-orphans

echo "[start] Waiting for SearXNG to become healthy"
for _ in $(seq 1 30); do
  status="$(docker inspect --format '{{.State.Health.Status}}' quietsearch-searxng 2>/dev/null || echo missing)"
  if [[ "$status" == "healthy" ]]; then
    break
  fi
  sleep 2
done

docker compose ps

if [[ "${status:-}" != "healthy" ]]; then
  echo "[start] SearXNG is '${status:-unknown}', not healthy. Check: docker compose logs searxng" >&2
  exit 1
fi

host="$(awk -F= '$1=="SEARXNG_HOSTNAME"{print $2}' .env)"
echo "[start] Ready: https://${host}/"
