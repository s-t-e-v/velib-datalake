# Azure VM + Blob runbook

The supported deployment is an Ubuntu VM running the collector and Docker PostgreSQL 17, with Azure Blob Storage as the durable raw lake. Use **Azure CLI**, not `azd up`. The commands below are operator steps, not evidence that deployment has been tested. No cloud resources were provisioned as part of this documentation update.

## 1. Prepare locally

Prerequisites: Azure CLI with Bicep, SSH/SCP, tar, and an Azure subscription. For laptop application commands, also install Python 3.12–3.14 and `uv`.

```bash
az login
az account show --output table
# If needed, select the intended subscription:
az account set --subscription <SUBSCRIPTION_ID>
az ad signed-in-user show --query id -o tsv
```

Use the returned **user object ID** as `deployerPrincipalId`, not an application/client ID. The template assigns a role with principal type `User`; this workflow is not for a service-principal login. The deploying identity needs subscription deployment/resource creation permissions and permission to create role assignments.

Before deploying, check regional VM availability and quota (including the relevant VM-family vCPU quota):

```bash
az vm list-skus --location francecentral --size Standard_B2ats_v2 --all --output table
az vm list-usage --location francecentral --output table
```

Review SKU restrictions, Azure pricing, and your subscription's trial/credit terms. A free trial does **not** mean everything is free: VM compute, OS disk, public IPv4, Blob capacity/transactions, and data transfer can incur charges. Disk, public IP, and storage charges can continue when compute is stopped/deallocated. Set a budget; quota and successful what-if do not guarantee capacity at deployment time.

Create a dedicated SSH key **on your laptop**, choosing a passphrase. Do not overwrite an existing key:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/velib-dev -C velib-dev
cat ~/.ssh/velib-dev.pub
```

Keep `~/.ssh/velib-dev` private and local. Only the `.pub` contents go into deployment parameters. Obtain your current public outbound **IPv4** address (the address Azure sees, including any VPN/NAT) and use that address followed by `/32`. Never use `0.0.0.0/0` for convenience.

Create `infra/dev.local.parameters.json` locally, replacing the example values:

```json
{
  "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
  "contentVersion": "1.0.0.0",
  "parameters": {
    "environmentName": { "value": "dev" },
    "location": { "value": "francecentral" },
    "sshPublicKey": { "value": "ssh-ed25519 REPLACE_WITH_YOUR_PUBLIC_KEY velib-dev" },
    "deployerPrincipalId": { "value": "REPLACE_WITH_YOUR_USER_OBJECT_ID" },
    "sshAllowedCidr": { "value": "203.0.113.10/32" },
    "vmSize": { "value": "Standard_B2ats_v2" }
  }
}
```

`environmentName` is supplied as `dev` by the parameter file, not defaulted by the template. `sshPublicKey`, `deployerPrincipalId`, and `sshAllowedCidr` are required. `location` and `vmSize` default to the values shown and may be omitted or overridden after checking availability/cost.

This file contains deployment values and a **public** key, no secrets. Never include private keys, database passwords, or `.env` contents. The repository ignores `*.local.parameters.json`; verify this before staging anything:

```bash
git check-ignore infra/dev.local.parameters.json
```

Do not commit the local file. Use `infra/dev.local.parameters.json`, not `infra/dev.parameters.json`.

## 2. Compile, review, then explicitly deploy

Run from the repository root. Compilation does not provision resources; what-if queries Azure; create provisions billable resources. Review the templates and what-if output before running create.

```bash
az bicep build --file infra/main.bicep --outfile /tmp/velib-main.json

az deployment sub what-if \
  --name velib-dev \
  --location francecentral \
  --template-file infra/main.bicep \
  --parameters @infra/dev.local.parameters.json

# Run only after approving the plan and costs:
az deployment sub create \
  --name velib-dev \
  --location francecentral \
  --template-file infra/main.bicep \
  --parameters @infra/dev.local.parameters.json

az deployment sub show --name velib-dev --query properties.outputs --output json
```

Record these output values for the commands below:

| Output | Use |
| --- | --- |
| `VM_PUBLIC_IP` | SSH/SCP destination |
| `VM_NAME` | VM operations |
| `RESOURCE_GROUP` | Resource operations and eventual teardown |
| `RAW_STORAGE_ACCOUNT_NAME` | Azure Storage CLI |
| `RAW_STORAGE_ACCOUNT_URL` | Application/bootstrap Blob endpoint |

Angle-bracket values below are placeholders to replace, not literal shell syntax. The deployment is subscription scoped; the `dev` resource group is `rg-velib-dev`. Role assignments can take time to propagate: if initial Blob operations return authorization errors, verify the identity/role and retry after propagation.

### Security boundary

- Ubuntu uses SSH keys for `azureuser`; password authentication is disabled.
- The NSG permits inbound TCP 22 only from `sshAllowedCidr` and denies other inbound traffic. If your public IP changes, update the local parameters and redeploy after reviewing what-if.
- PostgreSQL is not exposed publicly; it binds to VM loopback only.
- Blob's HTTPS endpoint is publicly reachable, **not** behind a private endpoint. Container `velib-raw` is private; anonymous and shared-key access are disabled. OAuth/RBAC grants Storage Blob Data Contributor to the VM's system-assigned managed identity and deploying user.
- No Function App or monitoring workspace is used.

## 3. Upload and bootstrap

After provisioning, run this **on your laptop from the repository root**, not inside the VM:

```bash
make vm-deploy
# Optional overrides:
make vm-deploy VM_DEPLOYMENT=velib-dev SSH_KEY=~/.ssh/another-key
```

This calls `scripts/deploy-app.sh`, discovers the VM IP and Blob URL from the existing subscription deployment, uploads an allowlisted archive, runs bootstrap, and checks the timer, collection logs, and indicator. It does not provision infrastructure. It preserves the existing database password and data, but rerunning installs the current code, rewrites the app configuration, and triggers collection. Keep the SSH key on your laptop. The key must be usable noninteractively (no passphrase, or unlocked in an SSH agent), and the VM host key must already be verified and trusted. Host-key checking is never disabled. A bootstrap error stops the script and prints recent service logs; inspect the error before retrying.

For the equivalent manual steps:

Build an explicit allowlisted archive from the repository root. Do not archive the whole checkout: that could upload `.env`, private keys, or local deployment files.

```bash
tar -czf /tmp/velib-app.tar.gz src migrations pyproject.toml README.md infra/vm
scp -i ~/.ssh/velib-dev /tmp/velib-app.tar.gz azureuser@<VM_PUBLIC_IP>:~/velib-app.tar.gz
ssh -i ~/.ssh/velib-dev azureuser@<VM_PUBLIC_IP>
```

Verify the SSH host fingerprint using a trusted channel before accepting it; do not disable host-key checking. In the VM shell:

```bash
mkdir -p ~/velib-release
tar -xzf ~/velib-app.tar.gz -C ~/velib-release
cd ~/velib-release
sudo bash infra/vm/bootstrap.sh <RAW_STORAGE_ACCOUNT_URL>
```

The bootstrap contract is:

- Install Ubuntu packages `python3-venv`, `python3-pip`, `docker.io`, `docker-compose-v2`, and `openssl`.
- Install the application in root-owned `/opt/velib/app`, with its package installed into `/opt/velib/venv` using pip.
- Run the application as user `velib`, **not** a member of the Docker group.
- Run `postgres:17-alpine` through `/opt/velib/compose.yml`, with `restart: unless-stopped`, port `127.0.0.1:5432`, and a bind mount at `/var/lib/velib/postgres` on the persistent OS disk.
- Generate a database password and keep `/etc/velib/postgres.env` and `/etc/velib/app.env` root-owned, mode `600`. Do not copy these into the release archive or repository.
- Install `/usr/local/bin/velib-admin`, which loads the application environment and runs the CLI as `velib`.
- Start `velib-db.service` after Docker and wait for database readiness. Run migrations before enabling collection.
- Configure `velib-collect.service` to execute `velib collect --process`, and its timer for every five UTC minutes with `Persistent=true`, `RandomizedDelaySec=15`, and `OnBootSec=30`.
- Enable the timer and start collection immediately after migration.

Initial installation and live collection were verified on 2026-10-25. Reboot recovery and subsequent scheduled invocations still require verification.

## 4. Operate and inspect

On the VM:

```bash
sudo systemctl status velib-db.service velib-collect.timer
sudo systemctl list-timers velib-collect.timer
sudo journalctl -u velib-db.service -u velib-collect.service -n 100 --no-pager
sudo velib-admin collections
sudo velib-admin indicator
```

Inspect collector service failures in the journal; there is no deployed monitoring workspace or automatic alerting. If a request fails during bootstrap (for example while RBAC propagates), the timer is already enabled and retries later. Use `sudo systemctl start velib-collect.service` for an immediate retry. A missed timer window cannot recreate the historical API state. Persistent scheduling can trigger a catch-up invocation, not replay every missed five-minute snapshot.

Raw data is saved **before** SQL processing, and each scheduled run processes only its current eligible collection. SQL failure leaves the raw collection available but does **not** trigger automatic backfill. To retry/rebuild from all eligible blobs:

```bash
sudo velib-admin migrate
sudo velib-admin process
sudo velib-admin indicator
```

Processing is idempotent through SQL upserts, but full replay lists/reads historical blobs and writes SQL again; time and transaction costs grow with the lake. If required, target one collection with `sudo velib-admin process --collection-id <COLLECTION_ID>`.

### Recover an abandoned collection lock

The existing Blob collector uses an infinite lease on `locks/collector.lock`. If the VM or process is killed mid-collection, that lease can remain held. Only after confirming no collector is active (stop the timer and collector service on every host using this lake), break the abandoned lease from your laptop:

```bash
az storage blob lease break --account-name <RAW_STORAGE_ACCOUNT_NAME> --container-name velib-raw --blob-name locks/collector.lock --auth-mode login
```

Wait for the lease break to complete, then restart the VM's timer. Do not break a live collector's lease.

### Inspect Blob from your laptop

Use your deploying user login and the deployment outputs; no account keys are needed:

```bash
az login
VELIB_STORAGE_BACKEND=azure VELIB_STORAGE_ACCOUNT_URL=<RAW_STORAGE_ACCOUNT_URL> uv run velib collections

az storage blob list \
  --account-name <RAW_STORAGE_ACCOUNT_NAME> \
  --container-name velib-raw \
  --auth-mode login \
  --output table
```

### Connect to PostgreSQL securely

Open a tunnel from your laptop and leave the session running:

```bash
ssh -i ~/.ssh/velib-dev -N -L 15432:127.0.0.1:5432 azureuser@<VM_PUBLIC_IP>
```

In a separate trusted SSH session, retrieve the configuration only when needed:

```bash
sudo cat /etc/velib/postgres.env
```

Use the configured database/user and generated password with a PostgreSQL client connecting to laptop host `127.0.0.1`, port `15432`. Enter the password interactively or through your client's secure credential mechanism, not in a command-line URL. Avoid recorded/shared terminals; never put configuration output or passwords in the repository, shell history, logs, screenshots, or support messages. Keep port 5432 closed externally.

## 5. Durability and teardown

Blob is the durable source of truth and has **no automatic raw-data deletion lifecycle**. Monitor storage growth and cost. PostgreSQL data survives container replacement/reboots via `/var/lib/velib/postgres`, but this is on the VM OS disk, not a separate database backup. The template deletes the OS disk with the VM: deleting that disk loses SQL. Rebuild SQL by migrating and replaying retained eligible blobs on a replacement host.

**Deleting the resource group destroys the Blob lake as well as the VM and database.** Export/retain anything required elsewhere first. Only when intentional, using the actual output value:

```bash
# Destructive; Azure prompts for confirmation. This deletes the lake too.
az group delete --name <RESOURCE_GROUP>
```

## Validation boundary

On 2026-10-25, Azure provisioning succeeded in France Central with the explicit override `vmSize=Standard_D2als_v7` (the original B-series candidates were unavailable). Application deployment through `make vm-deploy` then succeeded: PostgreSQL migration, managed-identity Blob writes, raw-first SQL loading, and indicator output were observed. The first collection had 1,519 source records; 48 of 1,349 eligible stations had zero bicycles. Authenticated Blob listing from the laptop confirmed both response and metadata files. The timer was active with its next invocation scheduled. No reboot, later timer invocation, database recovery, or full replay was tested. The current local parameters/template default may still name a B-series size: retain the `vmSize=Standard_D2als_v7` override when reviewing or repeating infrastructure deployment, and review its non-free compute price first.

Local validation: Bicep compilation passed previously; shell syntax, lint, and 15 tests passed after adding deployment automation. One PostgreSQL integration test was skipped because `TEST_DATABASE_URL` was unset. Mocked script tests cover endpoint discovery, allowlisted upload, strict host-key checks, rejection of unexpected storage URLs, and stopping on SSH failure; they do not replace the live/recovery checks above.
