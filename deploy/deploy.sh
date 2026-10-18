#!/bin/sh
# Deploy a commit on the server, with automatic rollback.
#
# Run as root from /opt/actiongraph after `git checkout <new commit>`:
#
#   sh deploy/deploy.sh <previous commit>
#
# It rebuilds and restarts the stack, waits for the API to reach FalkorDB,
# reseeds the synthetic graph, and checks the page through Caddy. If any of
# that fails, it checks out <previous commit>, rebuilds it, and exits non-zero.
#
# Log: /var/log/actiongraph-deploy.log (also printed).

set -eu

PREVIOUS="${1:?usage: sh deploy/deploy.sh <previous commit>}"
COMPOSE="docker compose -f deploy/docker-compose.prod.yml"
LOG=/var/log/actiongraph-deploy.log

log() {
  echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG"
}

start_and_check() {
  $COMPOSE up -d --build --remove-orphans >>"$LOG" 2>&1 || return 1
  attempt=0
  until $COMPOSE exec -T api python -m app.cli health >>"$LOG" 2>&1; do
    attempt=$((attempt + 1))
    [ "$attempt" -ge 40 ] && return 1
    sleep 3
  done
  $COMPOSE exec -T api python -m app.cli reset >>"$LOG" 2>&1 || return 1
  # Through Caddy, the way a visitor reaches it.
  host="$(sed -n 's/^SITE_ADDRESS=\([^,]*\).*/\1/p' deploy/.env)"
  curl -fsS --max-time 10 --resolve "$host:443:127.0.0.1" "https://$host/health" >>"$LOG" 2>&1 || return 1
  curl -fsS --max-time 10 --resolve "$host:443:127.0.0.1" -o /dev/null "https://$host/" || return 1
}

CURRENT="$(git rev-parse HEAD)"
log "deploy start: $PREVIOUS -> $CURRENT"

if start_and_check; then
  log "deploy ok: $CURRENT"
  docker image prune -f >>"$LOG" 2>&1 || true
  exit 0
fi

log "deploy FAILED for $CURRENT, rolling back to $PREVIOUS"
git checkout --force "$PREVIOUS" >>"$LOG" 2>&1
if start_and_check; then
  log "rollback ok: running $PREVIOUS"
else
  log "ROLLBACK FAILED: check $LOG and docker compose logs"
fi
exit 1
