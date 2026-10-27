#!/usr/bin/env bash
# Run from the laptop. Discover endpoints from the existing Azure deployment.
set -euo pipefail
if [[ $# != 2 ]]; then
    echo 'Usage: bash scripts/deploy-app.sh DEPLOYMENT_NAME SSH_PRIVATE_KEY' >&2
    exit 1
fi
deployment=$1
key=$2
for tool in az python3 ssh scp tar; do
    command -v "$tool" >/dev/null || { echo "Missing required tool: $tool" >&2; exit 1; }
done
if [[ ! -f $key ]]; then
    echo "SSH private key not found: $key" >&2
    exit 1
fi
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
az deployment sub show --name "$deployment" --query properties.outputs --output json > "$work/outputs.json"
# Azure CLI may change the case of Bicep output names. Validate before using values in SSH commands.
python3 - "$work/outputs.json" > "$work/endpoints" <<'PY'
import ipaddress
import json
import re
import sys

with open(sys.argv[1]) as handle:
    outputs = {name.lower(): item["value"] for name, item in json.load(handle).items()}
host = str(ipaddress.IPv4Address(outputs["vm_public_ip"]))
url = outputs["raw_storage_account_url"]
if not re.fullmatch(r"https://[a-z0-9]+\.blob\.core\.windows\.net/?", url):
    raise SystemExit("Unexpected Blob endpoint in deployment outputs")
print(host)
print(url)
PY
mapfile -t endpoints < "$work/endpoints"
host=${endpoints[0]}
url=${endpoints[1]}
target=azureuser@$host
# Use explicit connection settings, not workstation-specific SSH proxy/config includes.
ssh_options=(-F /dev/null -i "$key" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UpdateHostKeys=no -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=3)
printf 'Deploying application to %s; raw storage: %s\n' "$target" "$url"
echo 'SSH host must already be verified and trusted. No Azure resources will be provisioned.'
# An allowlist prevents uploading local secrets, keys, or deployment parameter files.
tar --exclude=__pycache__ --exclude='*.pyc' -czf "$work/app.tar.gz" -C "$root" \
    src migrations pyproject.toml requirements.txt README.md infra/vm
release=$(ssh "${ssh_options[@]}" "$target" 'mktemp -d /home/azureuser/velib-release.XXXXXXXX')
if [[ ! $release =~ ^/home/azureuser/velib-release\.[a-zA-Z0-9]+$ ]]; then
    echo 'Unexpected release directory returned by VM.' >&2
    exit 1
fi
scp "${ssh_options[@]}" "$work/app.tar.gz" "$target:$release/app.tar.gz"
ssh "${ssh_options[@]}" "$target" bash -s -- "$release" "$url" <<'REMOTE'
set -euo pipefail
release=$1
storage_url=$2
trap 'rm -rf -- "$release"' EXIT
cd "$release"
tar -xzf app.tar.gz
if ! sudo -n bash infra/vm/bootstrap.sh "$storage_url"; then
    echo 'Bootstrap failed. Recent service logs:' >&2
    sudo -n journalctl -u velib-db.service -u velib-collect.service -n 60 --no-pager
    exit 1
fi
sudo -n systemctl is-active velib-collect.timer
sudo -n systemctl list-timers velib-collect.timer --no-pager
sudo -n journalctl -u velib-collect.service -n 30 --no-pager
sudo -n velib-admin indicator
REMOTE
echo 'Deployment and initial collection checks completed. The VM timer continues without your laptop.'
