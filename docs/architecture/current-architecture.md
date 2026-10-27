# Current architecture

- Status: Implemented first vertical slice
- Last reviewed: 2026-10-27
- Historical decision: [ADR 0001](0001-initial-vertical-slice.md)
- Improvement backlog: [Engineering handoff](engineering-handoff.md)

## Purpose and boundary

The system periodically captures the official Vélib' real-time availability export, retains
each raw collection attempt, loads eligible station observations into PostgreSQL, and calculates
one indicator:

> Among stations open for rental with valid and sufficiently fresh observations, what
> proportion have zero available bicycles?

This slice does not yet ingest station reference data or cycling infrastructure. It has no
dashboard, spatial model, general-purpose orchestrator, monitoring workspace, or automatic
alerting.

The system is more precisely described as a near-real-time snapshot pipeline than as a streaming
system.

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
4. The collector acquires an infinite lease on `locks/collector.lock`. Local development uses a
   non-blocking file lock instead.
5. The HTTP client requests the complete JSON export with connect/read timeouts and bounded
   retries for selected transient failures.
6. The response is validated for JSON structure, minimum record count, duplicate station IDs,
   and required indicator fields.
7. The raw response is persisted before its metadata. The metadata object is the commit marker
   and records eligibility, timing, validation, locations, HTTP status, and failure information.
8. For a successful collection, `--process` reads that collection from Blob, transforms it, and
   upserts it into PostgreSQL in a transaction.
9. The process exits. A later `velib indicator` command reads the latest processed collection by
   default and returns the indicator as JSON.

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

Blob Storage is the durable source of truth. It has no configured lifecycle deletion policy.

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
| SQL transformation/load | Eligible raw collection remains in Blob | Manual targeted or full replay |
| Process death while lease held | Journal and possibly partial raw state | Manual lease break after safety checks |
| Missed schedule | Possibly inferable from adjacent metadata | Historical snapshot cannot be recreated |
| PostgreSQL loss | Raw lake remains if Blob survives | Migrate and replay all eligible collections |

Full replay is `velib process`; targeted replay uses `--collection-id`. Replay is idempotent but
currently lists and rereads historical objects, so its time and transaction cost grow with the
lake.

## Operational views available today

There is no unified operational dashboard. Operators currently combine:

- `systemctl status` and `systemctl list-timers` for scheduling state;
- `journalctl` for collector and database service logs;
- `velib collections` for raw attempt metadata and inferred schedule gaps;
- `velib indicator` for the latest processed business result;
- Azure CLI for Blob contents and infrastructure state;
- an SSH tunnel and SQL client for PostgreSQL inspection.

These surfaces answer different questions. In particular, PostgreSQL contains successfully
processed collections, not every raw attempt or failure.

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

The initial live deployment verified provisioning, bootstrap, managed-identity Blob writes,
PostgreSQL migration and loading, an active timer, authenticated Blob listing, and indicator
calculation. Reboot recovery, later scheduled invocations, prolonged source failures, and a full
cloud rebuild have not yet been demonstrated. See the [VM runbook](../azure-vm.md) for commands
and the exact verification record.
