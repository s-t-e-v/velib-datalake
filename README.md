# Vélib’ datalake — first vertical slice

This repository collects the official Vélib’ real-time availability export, preserves each raw response, and reproducibly loads station observations into PostgreSQL (on an Azure VM or locally). It then calculates one indicator:

> Among stations open for rental with valid and sufficiently fresh observations, what proportion have zero available bicycles?

The project deliberately stays focused. It adds a private workstation Grafana dashboard over a
PostgreSQL presentation layer, but does not yet add station reference data, cycle infrastructure,
spatial analysis, managed PostgreSQL, or a general orchestration framework.

## Architecture

```text
Paris Open Data full JSON export
              |
              | every 5 minutes UTC / one request and exit
              v
Ubuntu VM: systemd timer -> velib collect --process
              |
              | response.json, then metadata.json commit marker
              v
private Azure Blob container (durable raw lake)
              |
              | only the current eligible snapshot, after raw persistence
              v
PostgreSQL 17 in Docker on the VM -> indicator JSON

Local equivalent: API -> ./raw -> transform -> PostgreSQL -> indicator JSON
```

The VM runs both the collector and PostgreSQL. Blob Storage is the durable source of truth; SQL is rebuildable from eligible raw collections. The same raw-store interface supports a local directory and Azure Blob Storage. See the [Azure VM runbook](docs/azure-vm.md) for the Azure CLI deployment, bootstrap, operations, and recovery steps.

Architecture documentation:

- [ADR 0001: initial vertical slice](docs/architecture/0001-initial-vertical-slice.md) preserves the original design and the hypotheses it introduced.
- [Current architecture](docs/architecture/current-architecture.md) describes the implemented runtime, data flow, storage, and failure semantics.
- [Engineering handoff](docs/architecture/engineering-handoff.md) records weaknesses, legacy code, improvement options, and a suggested delivery sequence.
- [PostgreSQL presentation layer](docs/presentation-layer.md) defines the health, quality, indicator, station, and storage-usage views intended for dashboards.
- [Portable Grafana dashboard](docs/dashboard.md) explains the local dashboard and private SSH-tunnel connection to the VM database.

## Source exploration

Live checks were made on 2026-10-25 UTC with a small number of requests.

### Selected endpoint

```text
GET https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/velib-disponibilite-en-temps-reel/exports/json
```

No query parameters are required. The Opendatasoft v2.1 documentation says exports default to `limit=-1` and are not subject to the records endpoint's result limit. The collector therefore uses one export request rather than paginating:

```text
GET https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/velib-disponibilite-en-temps-reel/records?limit=100&offset=0
```

The records endpoint allows at most 100 non-aggregated records per request and requires `offset + limit < 10000`. At the observed count, pagination would take 16 requests. The export is the more reasonable collection mechanism. An export returning all rows does **not** guarantee an atomic snapshot; rows can carry different `duedate` values and may be updated while the export is generated.

### Required fields

The source field names remain unchanged in PostgreSQL.

| Field | Meaning and use |
| --- | --- |
| `stationcode` | Official unique station identifier within Vélib’ Métropole; analytical key within a collection. |
| `name` | Station name; descriptive only. |
| `is_installed` | `OUI` when deployed/operationally installed. |
| `capacity` | Number of docks at the station. |
| `numdocksavailable` | Available docks. |
| `numbikesavailable` | Total available bicycles; required for the indicator. |
| `mechanical`, `ebike` | Available mechanical and electric bicycles. |
| `is_renting` | `OUI` when the station can rent bicycles; required for the indicator population. |
| `is_returning` | Whether bicycles can be returned. |
| `duedate` | Source-provided observation update timestamp; used for per-station freshness. |

`coordonnees_geo`, `nom_arrondissement_communes`, `code_insee_commune`, and `station_opening_hours` remain in raw data but are intentionally outside this analytical slice.

### Observed completeness and access

- A `limit=3` records request reported `total_count=1519`; the exact response is retained in `tests/fixtures/records-page-limit-3.json`.
- A full export contained 1,519 rows and 1,519 distinct, non-empty `stationcode` values: zero duplicate identifiers.
- No row lacked `duedate` or `numbikesavailable` in that export.
- `is_renting` values were `OUI` and `NON`.
- `duedate` ranged from `2017-11-27T13:40:36+00:00` to `2026-10-25T13:16:45+00:00`. This proves that collection-level recency cannot substitute for per-station freshness validation.
- The dataset page says the JSON API is available without a key and is refreshed each minute. The observed response advertised a domain quota of 10,000 calls resetting at midnight UTC. Opendatasoft documents that quotas are portal-configurable, so 10,000/day is an observation, not a contractual guarantee.
- The export response was gzip encoded. The HTTP client decodes content encoding and stores the resulting response JSON bytes without business transformation.

Remaining uncertainties: the provider does not promise an atomic export snapshot, an immutable station identifier across station replacement, a fixed anonymous quota, or a maximum response latency. The source's meaning for very old station rows is also not documented. These are monitored or excluded rather than guessed.

Official references:

- [Paris Data dataset and schema](https://opendata.paris.fr/explore/dataset/velib-disponibilite-en-temps-reel/information/)
- [Opendatasoft Explore API v2.1](https://help.opendatasoft.com/apis/ods-explore-v2/)

## Raw preservation and failure semantics

Every attempt gets a UUID. Azure Blob objects use this layout:

```text
collections/YYYY/MM/DD/<collection-id>/response.json
collections/YYYY/MM/DD/<collection-id>/metadata.json
```

Local development uses the configured raw directory followed by
`YYYY/MM/DD/<collection-id>/...`.

`response.json` is written first. `metadata.json` is written atomically and last, and is the commit marker. Its fields include the collection ID, URL and non-secret parameters, UTC request start/end, HTTP status, content type, record count, validation result, raw locations, and failure details. A collection is eligible only when its body was saved and validation is `passed` or `passed_with_warnings`.

HTTP error bodies are preserved but remain ineligible. A network failure has no response body, so only failure metadata is stored. An interrupted body write cannot create the metadata commit marker. A body orphaned before metadata is therefore invisible to processing.

The HTTP client has connect/read timeouts and bounded retries for 408, 429 and transient 5xx responses. It delegates `Retry-After` handling to urllib3. JSON, top-level schema, minimum-row, duplicate-identifier and per-record validation are explicit. Logs include collection IDs and failure categories, never credentials or response bodies.

The VM's systemd timer schedules collection every five UTC minutes, with `Persistent=true`, `RandomizedDelaySec=15`, and `OnBootSec=30`. systemd does not start another instance of the same service while it is active; the collector also uses a local file lock or Azure blob lease. Failed collection attempts remain visible through `velib collections`; service failures and SQL errors are inspected with `journalctl`. The history command reports inferred gaps between attempt timestamps, but an outage can leave no metadata. Persistent scheduling does not replay every missed snapshot. No monitoring workspace or managed alerting is deployed.

`velib collect --process` saves raw data first and then loads only that collection into SQL. A SQL
failure does not remove the raw collection, and later timer runs do **not** automatically backfill
it. `velib backlog` compares eligible raw commit markers with PostgreSQL, while
`velib reconcile --limit N` processes only missing collections in chronological order. Automatic
reconciliation is not enabled. On the VM, `sudo velib-admin process` remains the full rebuild
command; its listing, reads, and SQL work grow with lake size.

## Local usage

Prerequisites: Python 3.12–3.14, `uv`, and Docker with Compose.

The common commands are also available through the root `Makefile`; they intentionally rely on normal Docker group access and do not invoke `sudo`.
Run `make` without a target to display the safe command summary.

```bash
cp .env.example .env
set -a
source .env
set +a

uv sync --extra dev
docker compose -f dev/compose.yml up -d postgres
uv run velib migrate
uv run velib collect
uv run velib collections
uv run velib backlog
uv run velib reconcile --limit 100
uv run velib process
uv run velib indicator
```

Equivalent end-to-end convenience command:

```bash
make install
make dev-e2e
```

Reprocess one raw collection without duplicating observations:

```bash
uv run velib process --collection-id <uuid>
uv run velib indicator --collection-id <uuid>
```

The primary keys are `raw_collection.collection_id` and `(station_observation.collection_id, stationcode)`. Reprocessing uses PostgreSQL upserts. Two collection IDs containing identical station values remain two distinct observations.

Run checks:

```bash
uv run ruff check .
uv run pytest
```

For the PostgreSQL reprocessing integration test:

```bash
TEST_DATABASE_URL="$DATABASE_URL" uv run pytest -m integration
```

## Indicator rules

The initial freshness threshold is 45 minutes, configured by `VELIB_FRESHNESS_MINUTES`. Although the source advertises one-minute updates, two live checks showed its newest `duedate` roughly 30 minutes behind request time. A 10-minute candidate consequently excluded every open station, while 45 minutes retained 1,350 of 1,353 open stations in the end-to-end check. This remains an operational assumption, not a source guarantee, and should be validated from collected lag distributions.

For one collection, records are classified in this order:

1. Invalid or missing identifier/value/timestamp.
2. Valid but not open for rental (`is_renting != OUI`).
3. Open but stale, or more than five minutes in the future (clock-skew guard).
4. Eligible denominator; zero bicycles contributes to the numerator.

The JSON result includes numerator, denominator, proportion and each exclusion count. A zero denominator produces JSON `null`, never `0`. Missing values, absent stations, failed collections and stale values are never converted to zero bicycles.

The focused test example has six source rows: one missing/invalid row plus one record missing an identifier are excluded as invalid, one closed and one stale station are excluded, and one of the two eligible stations has zero bicycles. The expected indicator is therefore `1 / 2 = 0.5`.

## Azure deployment

Follow the [Azure VM runbook](docs/azure-vm.md). The supported workflow is **Azure CLI**, not `azd up`: compile `infra/main.bicep`, review a subscription-scoped what-if, explicitly deploy, then run `make vm-deploy` on your laptop to upload, bootstrap, and verify the application over SSH.

- Ubuntu VM, default size `Standard_B2ats_v2`, default region `francecentral`; check regional availability, quota, and pricing first. Free-trial credit does not make every resource free.
- PostgreSQL 17 runs in Docker, bound only to `127.0.0.1:5432`, with persistent data on the VM OS disk.
- The NSG allows only SSH from the supplied caller IPv4 `/32`; use an SSH tunnel for SQL access.
- The Blob endpoint is publicly reachable, but `velib-raw` is private: OAuth access only, no anonymous or shared-key access. The VM's system-assigned managed identity and the deploying **user** receive Storage Blob Data Contributor.
- Raw collections move from Hot to Cool after 30 days but are never deleted automatically. No
  monitoring workspace is deployed.

The first cloud deployment was verified on 2026-10-25 using `Standard_D2als_v7` in France Central; the default B-series size was unavailable. See the runbook for verification boundaries.

## Verification status and limitations

Previously reported local checks (not evidence of a working Azure deployment):

- response validation and transformation;
- missing/stale/closed exclusion behavior and explicit `0.5` example;
- zero-denominator behavior;
- network failure metadata;
- incomplete collection invisibility;
- PostgreSQL migration and duplicate-free reprocessing against PostgreSQL 17 in Docker;
- a live 1,519-row collection, raw persistence, processing and indicator calculation;
- lint and unit tests listed above. Rerun these commands for the current checkout; these historical results do not validate the new VM/bootstrap path.

Live API checked:

- endpoint reachability and current schema sample;
- paginated total count;
- full-export count, duplicate station IDs, missing required fields and timestamp range.

Verified on Azure between 2026-10-25 and 2026-10-27: provisioning, SSH bootstrap,
managed-identity Blob writes, authenticated laptop Blob listing, PostgreSQL migration/load,
continued five-minute collections, and the presentation views over more than 500 processed
collections. A local Grafana instance connected through an SSH tunnel with a read-only database
role and executed all 12 dashboard queries. After deploying manual reconciliation, Blob contained
563 eligible raw collections and PostgreSQL contained all 563; a bounded reconcile correctly
performed no writes. A later controlled test proved lease renewal beyond 60 seconds, automatic
lease expiry after a killed process, persistence across VM reboot, a successful first scheduled
collection after reboot, and a full 567-collection replay into a disposable database.

Not yet verified:

- replacement-host recovery, teardown export, or a non-empty live reconciliation;
- source behavior during concurrent export updates, prolonged failures, schema changes, quota exhaustion, or anomalous clock skew.

These boundaries are intentional and should be stated plainly in an interview.

