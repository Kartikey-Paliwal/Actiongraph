#!/bin/sh
# First-boot setup for an Ubuntu 24.04 Lightsail instance. Lightsail runs this
# once, as root, when the instance is created. Before passing it as user data,
# replace __STATIC_IP__ with the instance's static IPv4 address,
# __REPO_REF__ with the git commit or branch to deploy, and __DOMAIN__ with a
# custom hostname whose A record points at the static IP (or leave it empty).
#
# Plain POSIX sh on purpose: Lightsail puts its own lines in front of the
# launch script, so the shebang is not the first line and /bin/sh (dash) runs it.
#
# Log: /var/log/actiongraph-setup.log

set -eu
exec > /var/log/actiongraph-setup.log 2>&1

STATIC_IP="__STATIC_IP__"
REPO_REF="__REPO_REF__"
DOMAIN="__DOMAIN__"
REPO_URL="https://github.com/Kartikey-Paliwal/Actiongraph.git"
APP_DIR="/opt/actiongraph"
SITE_ADDRESS="$(echo "$STATIC_IP" | tr '.' '-').sslip.io"
case "$DOMAIN" in
  ""|__DOMAIN__) ;;
  *) SITE_ADDRESS="$DOMAIN, $SITE_ADDRESS" ;;
esac

echo "[$(date -u +%FT%TZ)] setup start: site $SITE_ADDRESS, ref $REPO_REF"

# Small swap file so an image build can never run the server out of memory.
if [ ! -f /swapfile ]; then
  fallocate -l 1G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo "/swapfile none swap sw 0 0" >> /etc/fstab
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl git docker.io docker-compose-v2
systemctl enable --now docker

if [ ! -d "$APP_DIR/.git" ]; then
  git clone "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"
git fetch --all --tags
git checkout --force "$REPO_REF"

# Wait until the static IP is attached, so the HTTPS certificate request reaches
# this server. Give up after 15 minutes and start anyway; Caddy keeps retrying.
for attempt in $(seq 1 90); do
  current="$(curl -fsS --max-time 5 https://checkip.amazonaws.com || true)"
  if [ "$current" = "$STATIC_IP" ]; then
    echo "static IP attached after $attempt checks"
    break
  fi
  sleep 10
done

# Compose reads deploy/.env, so the addresses survive restarts and reboots.
echo "SITE_ADDRESS=$SITE_ADDRESS" > deploy/.env
docker compose -f deploy/docker-compose.prod.yml up -d --build

for attempt in $(seq 1 60); do
  if docker compose -f deploy/docker-compose.prod.yml exec -T api python -m app.cli health; then
    break
  fi
  sleep 5
done
docker compose -f deploy/docker-compose.prod.yml exec -T api python -m app.cli reset

echo "[$(date -u +%FT%TZ)] setup done: $SITE_ADDRESS"
