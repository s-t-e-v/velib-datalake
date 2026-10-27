#!/usr/bin/env bash
# Forward the VM PostgreSQL port to the Docker host gateway without exposing it on the LAN.
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
    echo 'Usage: bash scripts/dashboard-tunnel.sh VM_PUBLIC_IP [SSH_PRIVATE_KEY]' >&2
    exit 1
fi

vm_host=$1
ssh_key=${2:-$HOME/.ssh/velib-dev}

for tool in docker ssh; do
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

docker_gateway=$(
    docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}'
)
if [[ ! $docker_gateway =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
    echo "Could not determine the Docker host gateway: $docker_gateway" >&2
    exit 1
fi

echo "Forwarding Docker gateway $docker_gateway:15432 to PostgreSQL on $vm_host."
echo 'Leave this process running while Grafana uses the remote database.'
exec ssh \
    -F /dev/null \
    -i "$ssh_key" \
    -N \
    -o BatchMode=yes \
    -o ExitOnForwardFailure=yes \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=yes \
    -o UpdateHostKeys=no \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -L "$docker_gateway:15432:127.0.0.1:5432" \
    "azureuser@$vm_host"
