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

Do not expose PostgreSQL or Grafana publicly. Create a dedicated read-only PostgreSQL login once
from a trusted SSH session on the VM:

```bash
cd /opt/velib
sudo docker compose -f compose.yml exec postgres psql -U velib -d velib
```

In `psql`, choose a new password interactively so it is not placed in shell history:

```sql
CREATE ROLE velib_dashboard LOGIN;
\password velib_dashboard
GRANT CONNECT ON DATABASE velib TO velib_dashboard;
GRANT USAGE ON SCHEMA public TO velib_dashboard;
GRANT SELECT ON pipeline_health, collection_quality,
    zero_bicycle_indicator_history, latest_station_status,
    station_availability_history, postgres_storage_usage
    TO velib_dashboard;
```

The presentation migration must already be applied on the VM. Leave this tunnel running on the
laptop:

```bash
ssh -i ~/.ssh/velib-dev \
  -N \
  -L 15432:127.0.0.1:5432 \
  azureuser@<VM_PUBLIC_IP>
```

In a second terminal, enter the dashboard password without echoing it, configure the container to
use the tunnel, and start only Grafana:

```bash
read -rsp 'Dashboard database password: ' VELIB_GRAFANA_DB_PASSWORD
export VELIB_GRAFANA_DB_PASSWORD
export VELIB_GRAFANA_DB_HOST=host.docker.internal
export VELIB_GRAFANA_DB_PORT=15432
export VELIB_GRAFANA_DB_NAME=velib
export VELIB_GRAFANA_DB_USER=velib_dashboard
export VELIB_GRAFANA_DB_SSL_MODE=disable
make dashboard-remote-up
```

The database connection from Grafana to `host.docker.internal:15432` is carried through the SSH
tunnel. PostgreSQL remains bound to VM loopback, and Grafana remains bound to laptop loopback.
Unset the password after stopping Grafana:

```bash
make dashboard-down
unset VELIB_GRAFANA_DB_PASSWORD
```

The password is passed to the Grafana container as an environment variable and stored encrypted
in Grafana's internal database. It is not written to a tracked file. This is suitable for the
private workstation spike, not yet a cloud-hosting design.

## Boundaries

- `pipeline_health` covers processed SQL data only; it cannot see failed raw attempts, backlog,
  systemd state, Blob capacity, or a dead VM.
- The dashboard provides no alerts and does not run when the laptop is off.
- No public API, reverse proxy, TLS endpoint, or public inbound port is introduced.
- The local Compose defaults use the development database credentials and must not be reused for
  the Azure VM. The VM workflow above uses the dedicated read-only login.
- The dashboard is not a substitute for the later recovery and external-monitoring work.
