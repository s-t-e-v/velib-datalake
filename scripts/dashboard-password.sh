#!/usr/bin/env bash
# Create and rotate the read-only dashboard credential without printing it.
set -euo pipefail

if [[ $# -lt 1 || $# -gt 4 || ( $# == 4 && $4 != --rotate ) ]]; then
    echo 'Usage: bash scripts/dashboard-password.sh VM_PUBLIC_IP [SSH_PRIVATE_KEY] [SECRET_FILE] [--rotate]' >&2
    exit 1
fi

vm_host=$1
ssh_key=${2:-$HOME/.ssh/velib-dev}
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
secret_file=${3:-$root/.secrets/velib-dashboard-password}
rotate=${4:-}

for tool in docker openssl ssh; do
    command -v "$tool" >/dev/null || { echo "Missing required tool: $tool" >&2; exit 1; }
done
if [[ ! -f $ssh_key ]]; then
    echo "SSH private key not found: $ssh_key" >&2
    exit 1
fi
if [[ ! $vm_host =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
    echo "Expected an IPv4 VM address, got: $vm_host" >&2
    exit 1
fi
if [[ -e $secret_file && $rotate != --rotate ]]; then
    echo "Secret file already exists; refusing to overwrite it: $secret_file" >&2
    echo 'Pass --rotate explicitly to replace the database credential and file.' >&2
    exit 1
fi

secret_dir=$(dirname -- "$secret_file")
secret_name=$(basename -- "$secret_file")
mkdir -p -- "$secret_dir"
chmod 700 -- "$secret_dir"
secret_dir=$(cd -- "$secret_dir" && pwd)
secret_file=$secret_dir/$secret_name
secret_temp=$(mktemp "$secret_dir/.velib-dashboard-password.XXXXXX")
trap 'rm -f -- "$secret_temp"' EXIT
# Compose bind-mounts file-backed secrets without remapping ownership. The private 0700 parent
# blocks other host users; the final read bit lets the non-root Grafana process read the mount.
chmod 604 -- "$secret_temp"
openssl rand -hex 32 > "$secret_temp"
dashboard_password=$(<"$secret_temp")

ssh_options=(
    -F /dev/null
    -i "$ssh_key"
    -o BatchMode=yes
    -o IdentitiesOnly=yes
    -o StrictHostKeyChecking=yes
    -o UpdateHostKeys=no
    -o ConnectTimeout=15
)
target=azureuser@$vm_host
role_exists=$(
    ssh "${ssh_options[@]}" "$target" \
        "sudo -n docker compose -f /opt/velib/compose.yml exec -T postgres psql -U velib -d velib -Atqc \"SELECT 1 FROM pg_roles WHERE rolname = 'velib_dashboard'\""
)
role_sql=
if [[ $role_exists != 1 ]]; then
    role_sql='CREATE ROLE velib_dashboard LOGIN;'
fi

printf "BEGIN; %s ALTER ROLE velib_dashboard PASSWORD '%s'; GRANT CONNECT ON DATABASE velib TO velib_dashboard; GRANT USAGE ON SCHEMA public TO velib_dashboard; GRANT SELECT ON pipeline_health, collection_quality, zero_bicycle_indicator_history, latest_station_status, station_availability_history, postgres_storage_usage TO velib_dashboard; COMMIT;\n" \
    "$role_sql" "$dashboard_password" \
    | ssh "${ssh_options[@]}" "$target" \
        "sudo -n docker compose -f /opt/velib/compose.yml exec -T postgres psql -v ON_ERROR_STOP=1 -U velib -d velib"

mv -- "$secret_temp" "$secret_file"
trap - EXIT
echo "Dashboard credential rotated in a private directory: $secret_file"
echo 'Run make dashboard-remote-up while the SSH tunnel is active.'
