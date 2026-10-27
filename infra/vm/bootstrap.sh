#!/usr/bin/env bash
# Run as root from the extracted release root on Ubuntu 24.04.
set -euo pipefail
umask 022
if [[ $(id -u) != 0 || $# != 1 ]]; then
    echo 'Usage: sudo bash infra/vm/bootstrap.sh https://ACCOUNT.blob.core.windows.net/' >&2
    exit 1
fi
storage_url=$1
if [[ ! $storage_url =~ ^https://[a-z0-9]+\.blob\.core\.windows\.net/?$ ]]; then
    echo 'Expected an Azure public-cloud Blob account HTTPS URL.' >&2
    exit 1
fi
if [[ ! -f pyproject.toml || ! -d src/velib_datalake || ! -d infra/vm ]]; then
    echo 'Run this script from the extracted release root.' >&2
    exit 1
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3-venv python3-pip docker.io docker-compose-v2 openssl ca-certificates
systemctl enable --now docker
if ! id velib >/dev/null 2>&1; then
    useradd --system --home-dir /var/lib/velib --create-home --shell /usr/sbin/nologin velib
fi
# Stop scheduled work while replacing code; existing database files are preserved.
systemctl stop velib-collect.timer velib-collect.service 2>/dev/null || true
install -d -m 755 /opt/velib /opt/velib/app /var/lib/velib
install -d -m 700 /etc/velib
cp -R src migrations pyproject.toml requirements.txt README.md /opt/velib/app/
chown -R root:root /opt/velib/app
chmod -R go-w /opt/velib/app
python3 -m venv /opt/velib/venv
/opt/velib/venv/bin/pip install /opt/velib/app
if [[ ! -f /etc/velib/postgres.env ]]; then
    password=$(openssl rand -hex 32)
    umask 077
    printf 'POSTGRES_PASSWORD=%s\n' "$password" > /etc/velib/postgres.env
fi
# Reuse the original password on every bootstrap; changing the env alone cannot rotate PostgreSQL.
source /etc/velib/postgres.env
umask 077
cat > /etc/velib/app.env <<EOF
VELIB_STORAGE_BACKEND=azure
VELIB_STORAGE_ACCOUNT_URL=$storage_url
VELIB_STORAGE_CONTAINER=velib-raw
VELIB_MIN_RECORDS=1000
VELIB_FRESHNESS_MINUTES=45
DATABASE_URL=postgresql://velib:$POSTGRES_PASSWORD@127.0.0.1:5432/velib
EOF
chmod 600 /etc/velib/*.env
install -m 644 infra/vm/compose.yml /opt/velib/compose.yml
install -m 755 infra/vm/velib-admin /usr/local/bin/velib-admin
install -m 644 \
    infra/vm/systemd/velib-db.service \
    infra/vm/systemd/velib-collect.service \
    infra/vm/systemd/velib-collect.timer \
    /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now velib-db.service
velib-admin migrate
# Schedule before the first request, so a temporary source/RBAC error still retries later.
systemctl enable --now velib-collect.timer
systemctl start velib-collect.service
printf '\nCollection is scheduled. Check: systemctl status velib-collect.timer\n'
