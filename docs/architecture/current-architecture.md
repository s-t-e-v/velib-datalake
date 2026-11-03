# Current architecture

- Status: Implemented first vertical slice; Azure deployment torn down on 2026-10-30
- Last reviewed: 2026-10-30
- Historical decision: [ADR 0001](0001-initial-vertical-slice.md)
- Improvement backlog: [Engineering handoff](engineering-handoff.md)

## Purpose and boundary

The system periodically captures the official Vélib' real-time availability export, retains
each raw collection attempt, loads eligible station observations into PostgreSQL, and calculates
one indicator:

> Among stations open for rental with valid and sufficiently fresh observations, what
> proportion have zero available bicycles?

This slice does not yet ingest station reference data or cycling infrastructure. It has a private
workstation Grafana dashboard, but no spatial model, general-purpose orchestrator, monitoring
workspace, or automatic alerting.

The system is more precisely described as a near-real-time snapshot pipeline than as a streaming
system.

The topology below is the implemented and reproducible deployment design. No Azure runtime is
currently active. The retained raw lake was exported locally before teardown and can be restored
into a newly provisioned storage account.

## Runtime topology

```text
                           Azure subscription

Paris Open Data                 Ubuntu VM                     Azure Blob Storage
full JSON export         +------------------------+       +------------------------+
        |                | systemd timer (5 min) |       | private velib-raw      |
        +--------------->| Python CLI collector   |------>| response.json          |
             HTTPS       |                        | OAuth | metadata.json          |
                         | PostgreSQL 17          |       | lock blob + lease      |
                         | in Docker, loopback    |       +------------------------+
                         +-----------+------------+
                                     |
                                     | local SQL connection
                                     v
                         transformed observations
                         and indicator JSON

Operator access: restricted SSH -> systemd/journald, CLI, and PostgreSQL SSH tunnel
Local mode: API -> local raw directory -> local PostgreSQL -> indicator JSON
```

The infrastructure template creates a resource group, one VM with a system-assigned managed
identity, a private Blob container, a virtual network, a network security group, a static public
IPv4 address, and the required Blob role assignments. Only SSH from a caller-supplied CIDR is
allowed inbound. PostgreSQL binds to `127.0.0.1` and is not exposed publicly.

Blob's endpoint is publicly reachable, but anonymous and shared-key access are disabled. The VM
and deploying user authenticate through Microsoft Entra ID and Azure RBAC.

## Collection sequence

One scheduled execution follows this sequence:

1. `velib-collect.timer` starts `velib-collect.service` at a five-minute UTC boundary, with a
   randomized delay of up to 15 seconds.
2. The service runs `velib collect --process` as the unprivileged `velib` user.
3. The CLI reads environment configuration and creates the Azure raw-store implementation.
4. The collector acquires a 60-second lease on `locks/collector.lock`, renews it every 20 seconds,
   and retries transient renewal failures every two seconds. Local development uses a non-blocking
   file lock instead.
5. The HTTP client requests the complete JSON export with connect/read timeouts and bounded
   retries for selected transient failures.
6. The response is validated for JSON structure, minimum record count, duplicate station IDs,
   and required indicator fields.
7. The raw response is persisted before its metadata. The metadata object is the commit marker
   and records eligibility, timing, validation, locations, HTTP status, and failure information.
8. For a successful collection, `--process` reads that collection from Blob, transforms it, and
   upserts it into PostgreSQL in a transaction.
9. The process exits. A later `velib indicator` command reads the guarded current result from
   `pipeline_health` and returns its age, health status, and nullable indicator as JSON. An explicit
   `--collection-id` instead calculates the historical result without a current-time guard.

systemd prevents overlapping starts of the same one-shot service. The Blob lease provides an
additional cross-process or cross-host exclusion mechanism.

## Storage model

### Raw lake

Azure objects use this layout:

```text
collections/YYYY/MM/DD/<collection-id>/response.json
collections/YYYY/MM/DD/<collection-id>/metadata.json
locks/collector.lock
```

Local storage uses the configured raw directory followed by
`YYYY/MM/DD/<collection-id>/...`.

`response.json` contains the decoded HTTP response bytes without business transformation.
`metadata.json` is written last. A collection is eligible for processing only if the body exists
and validation is `passed` or `passed_with_warnings`.

Blob Storage is the durable source of truth. A lifecycle rule moves collection objects from Hot
to Cool after 30 days. It never deletes them automatically and does not apply to the lock blob.

### PostgreSQL serving layer

The relational model has two tables:

- `raw_collection`: one row for each successfully processed raw collection;
- `station_observation`: one row per `(collection_id, stationcode)`.

Upserts make replay of the same collection idempotent. Distinct collections containing the same
station remain distinct observations. Rows without a station identifier cannot form the key and
are counted but not loaded as observations.

PostgreSQL data is stored under `/var/lib/velib/postgres` on the VM OS disk. It survives container
replacement and ordinary reboots, but it is not independently backed up and is deleted with the
OS disk. The database can be rebuilt by migrating and replaying eligible Blob collections.

Migration `002_presentation_views.sql` adds normal SQL views for pipeline health, collection
quality, indicator history, latest station status, station history, and PostgreSQL storage usage.
The station freshness threshold used during processing is stored with each collection so
historical presentation preserves the rule that was active at that time. See the
[presentation layer](../presentation-layer.md) for the view contract and freshness semantics.

## Validation and indicator semantics

A collection fails as a whole when the top-level structure is wrong, its record count is below
the configured minimum, or station identifiers are duplicated. Missing identifiers and invalid
indicator fields produce collection warnings and per-observation exclusions.

For a processed collection, observations are classified in this order:

1. invalid or missing identifier, bicycle count, rental state, or timestamp;
2. valid but not open for rental;
3. open but stale relative to collection time, or more than five minutes in the future;
4. eligible for the denominator, with zero bicycles contributing to the numerator.

The configured freshness threshold is currently 45 minutes. A zero denominator produces JSON
`null`. Missing, stale, closed, or absent stations are never treated as zero bicycles.

Freshness is relative to collection time. Consumers must separately check the age of the latest
collection before presenting it as current information.

## Failure and recovery behavior

| Failure point | Retained evidence | Automatic recovery |
| --- | --- | --- |
| Network request | Failure metadata, no response body | Next timer run tries a new snapshot |
| Non-success HTTP response | Response body and failure metadata | Next timer run tries a new snapshot |
| Validation | Response and failure metadata | Next timer run tries a new snapshot |
| Interrupted body upload | Possible orphan response, no commit marker | No automatic orphan cleanup |
| Metadata upload | Response may be orphaned | Next timer run uses a new collection ID |
| SQL transformation/load | Eligible raw collection remains in Blob | Manual bounded reconciliation, targeted replay, or full rebuild |
| Process death while lease held | Journal and possibly partial raw state | Lease expires within 60 seconds; manual break remains an exceptional fallback |
| Missed schedule | Possibly inferable from adjacent metadata | Historical snapshot cannot be recreated |
| PostgreSQL loss | Raw lake remains if Blob survives | Migrate and replay all eligible collections |

`velib backlog` compares eligible raw metadata with processed SQL collection IDs. `velib reconcile
--limit N` loads only missing collections, oldest first. Both operations currently list all raw
metadata, but reconciliation downloads and transforms only the bounded missing bodies. Full replay
is `velib process`; targeted replay uses `--collection-id`. All writes are idempotent through SQL
upserts. Reconciliation remains a manual operator action rather than an automatic timer behavior.
On 2026-10-27, the first live backlog inspection found 563 eligible raw collections and all 563 in
PostgreSQL. A bounded live reconciliation therefore selected and wrote zero collections. On
2026-10-28, a replacement Azure VM started with an empty database, rebuilt 833 retained
collections, and then reconciled the one collection that arrived during replay. It reached 834 of
834 eligible collections with zero backlog.

## Operational views available today

There is no cloud-hosted dashboard or automatic alerting. A version-controlled Grafana instance
can run on the operator workstation, and PostgreSQL exposes a SQL-serving view of health through
`pipeline_health`, but operators still combine it with:

- `systemctl status` and `systemctl list-timers` for scheduling state;
- `journalctl` for collector and database service logs;
- `velib collections` for raw attempt metadata and inferred schedule gaps;
- `velib indicator` for the health-guarded current business result;
- Azure CLI for Blob contents and infrastructure state;
- an SSH tunnel and SQL client for PostgreSQL inspection.

`postgres_storage_usage` reports database/table sizes, while `pipeline_health` reports exact
processed collection and persisted observation totals. These surfaces still answer different
questions. In particular, PostgreSQL contains successfully processed collections, not every raw
attempt or failure. Accordingly, the health view labels its scope `processed_sql_only`. The CLI
can calculate the raw-to-SQL backlog by joining Blob metadata with SQL IDs, but the SQL-only
dashboard still cannot report that backlog or Blob capacity.

A version-controlled Grafana dashboard can run on the operator's Linux workstation and consume
these views from local PostgreSQL or through the existing SSH tunnel. It binds only to workstation
loopback and is not deployed on the VM. See the [dashboard runbook](../dashboard.md).

## Deployment and verification boundary

Infrastructure is deployed explicitly with Azure CLI and Bicep. Application code is uploaded
over SSH and bootstrapped with `make vm-deploy`; Azure Developer CLI and Azure Functions are not
part of the active architecture.

Repository paths reflect their execution boundary:

- `dev/compose.yml` runs development PostgreSQL and is not uploaded;
- `scripts/deploy-app.sh` runs from the operator workstation and is not uploaded;
- `infra/main.bicep`, its modules, and local parameter files are submitted to Azure Resource
  Manager but are not installed on the VM;
- `infra/vm/` contains the bootstrap and runtime assets intentionally included in the release
  archive.

The release archive temporarily contains the Python sources, migrations, package metadata,
README, and `infra/vm/`. Bootstrap installs the Python package and virtual environment under
`/opt/velib`, configuration under `/etc/velib`, administrative command under `/usr/local/bin`,
systemd units under `/etc/systemd/system`, and PostgreSQL files under `/var/lib/velib`. The
temporary uploaded release is deleted after bootstrap.

The live deployment verified provisioning, bootstrap, managed-identity Blob writes, PostgreSQL
migration and loading, continued timer-driven collections, authenticated Blob listing, indicator
calculation, and the presentation dashboard through an SSH tunnel. On 2026-10-27, controlled
tests also verified lease renewal and crash expiry, PostgreSQL persistence and collection restart
across a VM reboot, and a full 567-collection replay into a disposable database. On 2026-10-28, a
separate replacement VM was bootstrapped against the retained lake and converged to zero backlog.
On 2026-10-30, the timer was stopped at 1,305 eligible collections with zero backlog. All 2,638
collection objects were exported and verified against Azure MD5 values before both project
resource groups were deleted. Prolonged source failures remain unverified. See the [VM
runbook](../azure-vm.md) for commands and the exact verification record.
