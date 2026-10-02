#!/usr/bin/env bash
# Stop and remove the containers. Named volumes are kept, including Caddy's
# certificates, so the next start doesn't need to request new ones.
#
# Usage: ./scripts/stop.sh            # stop, keep data
#        ./scripts/stop.sh --purge    # stop AND delete volumes (certificates, cache)
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ "${1:-}" == "--purge" ]]; then
  echo "[stop] Stopping and deleting volumes"
  docker compose down --volumes --remove-orphans
else
  echo "[stop] Stopping containers (volumes kept)"
  docker compose down --remove-orphans
fi
