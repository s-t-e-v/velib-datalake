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

Copy the tracked parameter template, then replace every placeholder in the local copy:

```bash
cp infra/dev.local.parameters.json.example infra/dev.local.parameters.json
```

`environmentName` is supplied as `dev` by the parameter file, not defaulted by the template. `sshPublicKey`, `deployerPrincipalId`, and `sshAllowedCidr` are required. `location` and `vmSize` default to the values shown and may be omitted or overridden after checking availability/cost.

This file contains deployment values and a **public** key, no secrets. Never include private keys, database passwords, or `.env` contents. The repository ignores `*.local.parameters.json`; verify this before staging anything:

```bash
git check-ignore infra/dev.local.parameters.json
```

Do not commit the local file. Use `infra/dev.local.parameters.json`, not `infra/dev.parameters.json`.

### Switch between trusted SSH locations

`infra/dev.local.parameters.json` contains the default `sshAllowedCidr`, but operators who move
between known locations can keep named CIDRs in a separate untracked file. Create it from the
tracked example and use meaningful variable names if `PRIMARY` and `SECONDARY` are unclear:

```bash
cp infra/ssh-locations.local.env.example infra/ssh-locations.local.env
curl -4 https://api.ipify.org
```

Edit `infra/ssh-locations.local.env`, store each public outbound IPv4 address with a `/32` suffix,
and confirm Git ignores it:

```bash
git check-ignore infra/ssh-locations.local.env
set -a
source infra/ssh-locations.local.env
set +a
```

Select one location for the current shell and update the existing NSG rule. List the resource
group's NSGs if the name is not already known from Azure:

```bash
selected_cidr=$VELIB_SSH_CIDR_PRIMARY  # or VELIB_SSH_CIDR_SECONDARY

az network nsg list --resource-group <RESOURCE_GROUP> --output table
az network nsg rule update \
  --resource-group <RESOURCE_GROUP> \
  --nsg-name <NSG_NAME> \
  --name AllowSshFromCaller \
  --source-address-prefixes "$selected_cidr"
```

This changes the live SSH allowlist immediately; it does not update the default value in
`infra/dev.local.parameters.json`. When running a later what-if or deployment, pass the selected
CIDR as the final parameter so the template does not restore the old default:

```bash
az deployment sub what-if \
  --name velib-dev \
  --location francecentral \
  --template-file infra/main.bicep \
  --parameters @infra/dev.local.parameters.json \
  --parameters sshAllowedCidr="$selected_cidr"
```

Use the same final `--parameters sshAllowedCidr="$selected_cidr"` override with
`az deployment sub create`. Keep only individual `/32` addresses; do not widen SSH access to
`0.0.0.0/0`. Public IP addresses are not credentials, but keeping location-specific values local
avoids publishing operational access details and prevents accidental stale defaults.

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

Initial installation and live collection were verified on 2026-10-25. Continued scheduled
collections, the SSH-tunnel dashboard, and reboot recovery were verified on 2026-10-27.

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

Raw data is saved **before** SQL processing, and each scheduled run processes only its current
eligible collection. SQL failure leaves the raw collection available but does **not** trigger
automatic backfill. Inspect and reconcile only missing collections in bounded chronological
batches:

```bash
sudo velib-admin backlog
sudo velib-admin reconcile --limit 100
sudo velib-admin backlog
```

Reconciliation is manual so an operator can inspect the backlog and service logs before writing
SQL. It is idempotent if another process loads one of the selected collections concurrently.

For a deliberate full database rebuild from every eligible blob:

```bash
sudo velib-admin migrate
sudo velib-admin process
sudo velib-admin indicator
```

Both backlog inspection and replay list raw metadata, but reconciliation downloads and transforms
only missing bodies. Full replay downloads and writes every eligible collection, so its time and
transaction costs grow with the lake. If required, target one collection with
`sudo velib-admin process --collection-id <COLLECTION_ID>`.

### Collector lock recovery

The collector uses a 60-second lease on `locks/collector.lock`, renews it every 20 seconds, and
retries transient renewal errors every two seconds. It checks ownership before committing raw
data. If the process or VM dies, wait at least 60 seconds; the fixed lease should become available
without operator action. Inspect it from your laptop:

```bash
az storage blob show \
  --account-name <RAW_STORAGE_ACCOUNT_NAME> \
  --container-name velib-raw \
  --name locks/collector.lock \
  --auth-mode login \
  --query 'properties.lease'
```

Only if Azure still reports a lease after the expiry window, confirm no collector is active on
any host, stop every timer and collector service using this lake, and break the lease:

```bash
az storage blob lease break --account-name <RAW_STORAGE_ACCOUNT_NAME> --container-name velib-raw --blob-name locks/collector.lock --auth-mode login
```

Wait for the lease break to complete, then restart the VM's timer. Do not break a live collector's
lease.

### Test a rebuild without deleting the live database

For a recovery exercise, stop collection so the expected raw count is stable, create a disposable
database, override only the database name in `DATABASE_URL`, migrate, and run the full replay.
Validate `raw_collection`, `station_observation`, and `pipeline_health` before dropping the test
database and restarting the timer. Never print the URL because it contains the database password.
The 2026-10-27 exercise used `velib_recovery_test`; it replayed 567 collections in about 3 minutes
16 seconds while leaving the live `velib` database unchanged.

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

Blob is the durable source of truth. The lifecycle policy moves only
`velib-raw/collections/` block blobs from Hot to Cool after 30 days and never deletes them. The
lock blob remains Hot. On 2026-10-27 the lake used 378,352,174 bytes across 1,145 objects;
five-minute responses of roughly 665 KB imply about 190 MB/day of raw growth. Monitor actual
capacity and retrieval cost. Replaying Cool data remains supported but costs more in retrieval
and transactions.

PostgreSQL data survives container replacement/reboots via `/var/lib/velib/postgres`, but this is
on the VM OS disk, not a separate database backup. The template deletes the OS disk with the VM:
deleting that disk loses SQL. Rebuild SQL by migrating and replaying retained eligible blobs on a
replacement host.

**Deleting the resource group destroys the Blob lake as well as the VM and database.** Export/retain anything required elsewhere first. Only when intentional, using the actual output value:

Before teardown:

1. Stop the collection timer and wait for any active collection to finish.
2. Record the source object count and total bytes with authenticated `az storage blob list`.
3. Copy `velib-raw` to storage outside this resource group or to durable local storage.
4. Compare destination object count and bytes with the source and test-read sample metadata and
   response pairs.
5. Record the last SQL collection count and, if SQL itself must be retained, export it separately.
6. Delete the group only after the retained copy is independently accessible.

```bash
# Destructive; Azure prompts for confirmation. This deletes the lake too.
az group delete --name <RESOURCE_GROUP>
```

## Validation boundary

On 2026-10-25, Azure provisioning succeeded in France Central with the explicit override `vmSize=Standard_D2als_v7` (the original B-series candidates were unavailable). Application deployment through `make vm-deploy` then succeeded: PostgreSQL migration, managed-identity Blob writes, raw-first SQL loading, and indicator output were observed. The first collection had 1,519 source records; 48 of 1,349 eligible stations had zero bicycles. Authenticated Blob listing from the laptop confirmed both response and metadata files. The timer was active with its next invocation scheduled. At that time, no reboot, later timer invocation, database recovery, or full replay had been tested. The current local parameters/template default may still name a B-series size: retain the `vmSize=Standard_D2als_v7` override when reviewing or repeating infrastructure deployment, and review its non-free compute price first.

On 2026-10-27, the database contained more than 500 timer-produced collections. Migration
`002_presentation_views.sql` was applied, a dedicated read-only dashboard role was created, and
Grafana connected through the Docker-gateway SSH tunnel. All 12 provisioned panel queries
succeeded. Manual reconciliation was then deployed: `velib-admin backlog` reported 563 eligible
raw collections and 563 processed collections, and `reconcile --limit 1` correctly performed no
writes. This verifies continued scheduling, the private dashboard path, and zero-backlog
inspection, but not a non-empty live reconciliation. Later that day, a 75-second controlled lease
hold proved renewal, a killed owner proved automatic expiry, and a VM reboot preserved all 566
pre-reboot collections; the first scheduled run after boot increased SQL to 567. A full replay of
those 567 collections into a disposable database completed in about 3 minutes 16 seconds without
altering the live database. Replacement-host recovery and teardown export remain unverified.

Local validation: Bicep compilation passed previously; shell syntax, lint, and 15 tests passed after adding deployment automation. One PostgreSQL integration test was skipped because `TEST_DATABASE_URL` was unset. Mocked script tests cover endpoint discovery, allowlisted upload, strict host-key checks, rejection of unexpected storage URLs, and stopping on SSH failure; they do not replace the live/recovery checks above.
