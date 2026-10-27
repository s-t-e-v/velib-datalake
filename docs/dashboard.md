# Portable Grafana dashboard

The repository provisions a local Grafana dashboard from version-controlled files. It is a
workstation visualization client, not part of the Azure VM runtime. Grafana listens only on
`127.0.0.1:3000`, permits anonymous local viewing, and has no login form or initial administrator.

Grafana was selected for this spike because its PostgreSQL data source and dashboard can both be
provisioned from reviewable files. The choice does not prevent a later Metabase spike: both tools
would consume the same PostgreSQL presentation views.

## Dashboard content

The `Velib pipeline and availability` dashboard includes:

- pipeline health and latest processed age;
- a current KPI that becomes unavailable when the SQL-serving data is stale;
- processed collection, persisted observation, and latest source-record counts;
- zero-bicycle indicator history;
- collection exclusion and source-lag trends;
- the first 100 latest stations requiring attention;
- PostgreSQL database and station-table sizes.

The dashboard refreshes every five minutes and initially displays the last 24 hours.

## Run against local PostgreSQL

Prerequisites are Docker Compose, Python 3.12–3.14, and `uv`.

```bash
make dashboard-up
```

This starts local PostgreSQL, applies migrations, and starts the pinned Grafana container. Open:

```text
http://127.0.0.1:3000/d/velib-overview
```

An empty local database correctly shows `NO DATA`. To generate a local collection using the
normal development workflow, run `make dev-e2e`; that command calls the live source API.

Inspect or stop the dashboard with:

```bash
make dashboard-logs
make dashboard-down
make dev-down
```

Grafana keeps its small internal database in a named Docker volume, but the data source and
dashboard are recreated from the repository files. UI edits cannot overwrite the provisioned
dashboard.

## Run locally against the Azure VM through SSH

Do not expose PostgreSQL or Grafana publicly. The presentation migration must already be applied
on the VM. Create the dedicated read-only PostgreSQL login and its local Compose secret once:

```bash
bash scripts/dashboard-password.sh <VM_PUBLIC_IP>
```

The script generates a hexadecimal password, creates or updates `velib_dashboard`, grants only the
presentation-view permissions, and stores the password at
`.secrets/velib-dashboard-password`. It never prints the password. The `.secrets` directory is
ignored by Git and has mode `700`. Standalone Compose bind-mounts the source without remapping it,
so the password file uses mode `604`: its owner can read and write it, and the non-root Grafana
process can read the direct mount. Other workstation users cannot traverse the private parent
directory. Rotate it explicitly when required:

```bash
bash scripts/dashboard-password.sh <VM_PUBLIC_IP> \
  ~/.ssh/velib-dev \
  "$PWD/.secrets/velib-dashboard-password" \
  --rotate
```

Leave the tunnel running on the laptop:

```bash
bash scripts/dashboard-tunnel.sh <VM_PUBLIC_IP>
```

The script binds the forwarded port to Docker's host-gateway address rather than laptop loopback.
On native Linux, `host.docker.internal` resolves to that gateway; a tunnel bound only to
`127.0.0.1` is not reachable from the Grafana container. The gateway address is reachable by
local containers but is not a listener on the laptop's LAN interfaces. The script also requires a
previously verified SSH host key and exits if the port forward cannot be established.

In a second terminal, start only Grafana. The Make target uses the standard secret path and sets
the remote database host, port, name, user, and SSL mode:

```bash
make dashboard-remote-up
```

To use a nonstandard secret location, set `DASHBOARD_REMOTE_SECRET=/absolute/path` on the Make
command line.

The database connection from Grafana to `host.docker.internal:15432` is carried through the SSH
tunnel. PostgreSQL remains bound to VM loopback, and Grafana remains bound to laptop loopback.
Stop Grafana with:

```bash
make dashboard-down
```

Compose mounts the password read-only at `/run/secrets/velib_grafana_db_password`; it is not
present in the container environment. Grafana reads the file while provisioning the data source
and stores the value encrypted in its internal database. Standalone Compose secrets are
permission-scoped file mounts rather than an encrypted secret store, so the protected local source
file remains part of the workstation trust boundary. This is suitable for the private workstation
spike, not yet a cloud-hosting design.

## Boundaries

- `pipeline_health` covers processed SQL data only; it cannot see failed raw attempts, backlog,
  systemd state, Blob capacity, or a dead VM.
- The dashboard provides no alerts and does not run when the laptop is off.
- No public API, reverse proxy, TLS endpoint, or public inbound port is introduced.
- Local development PostgreSQL is bound to laptop loopback only.
- The local Compose defaults use the development database credentials and must not be reused for
  the Azure VM. The VM workflow above uses the dedicated read-only login.
- The dashboard is not a substitute for the later recovery and external-monitoring work.
