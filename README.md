# Vélib’ datalake — first vertical slice

This repository collects the official Vélib’ real-time availability export, preserves each raw response, and reproducibly loads station observations into local PostgreSQL. It then calculates one indicator:

> Among stations open for rental with valid and sufficiently fresh observations, what proportion have zero available bicycles?

The project deliberately stops there. It does not yet add station reference data, cycle infrastructure, spatial analysis, dashboards, managed PostgreSQL, or a general orchestration framework.

## Architecture

```text
Paris Open Data full JSON export
              |
              | every 5 minutes UTC / one request and exit
              v
Azure Function (Python 3.12, Flex Consumption)
              |
              | response.json, then metadata.json commit marker
              v
private Azure Blob container

Local equivalent: API -> ./raw -> transform -> PostgreSQL -> indicator JSON
```

The Azure collector does not connect to PostgreSQL. PostgreSQL remains local in this slice. The same raw-store interface supports a local directory and Azure Blob Storage; the processing command reads whichever backend is configured.

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

Every attempt gets a UUID. Successful files use this layout:

```text
raw/YYYY/MM/DD/<collection-id>/response.json
raw/YYYY/MM/DD/<collection-id>/metadata.json
```

`response.json` is written first. `metadata.json` is written atomically and last, and is the commit marker. Its fields include the collection ID, URL and non-secret parameters, UTC request start/end, HTTP status, content type, record count, validation result, raw locations, and failure details. A collection is eligible only when its body was saved and validation is `passed` or `passed_with_warnings`.

HTTP error bodies are preserved but remain ineligible. A network failure has no response body, so only failure metadata is stored. An interrupted body write cannot create the metadata commit marker. A body orphaned before metadata is therefore invisible to processing.

The HTTP client has connect/read timeouts and bounded retries for 408, 429 and transient 5xx responses. It delegates `Retry-After` handling to urllib3. JSON, top-level schema, minimum-row, duplicate-identifier and per-record validation are explicit. Logs include collection IDs and failure categories, never credentials or response bodies.

The Azure timer monitor prevents a second scheduled invocation while one is outstanding. The collector additionally holds a local file lock or Azure blob lease, covering manual/concurrent invocations. Failed attempts remain visible through `velib collections` and Application Insights. The command compares consecutive attempt timestamps and reports inferred missing five-minute runs. A complete Function App outage can still create a schedule gap without raw metadata; timer `past_due` warnings and Azure monitoring remain the authoritative alerting path.

## Local usage

Prerequisites: Python 3.12–3.14, `uv`, and Docker with Compose.

The common commands are also available through the root `Makefile`; they intentionally rely on normal Docker group access and do not invoke `sudo`.

```bash
cp .env.example .env
set -a
source .env
set +a

uv sync --extra dev
docker compose up -d postgres
uv run velib migrate
uv run velib collect
uv run velib collections
uv run velib process
uv run velib indicator
```

Equivalent end-to-end convenience command:

```bash
make install
make e2e
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

The deployment is based on Microsoft's current Python timer-trigger Flex Consumption pattern:

- Azure Functions runtime v4, Python 3.12, Python v2 decorator model.
- Flex Consumption on Linux; the legacy Linux Consumption plan is not selected.
- `0 */5 * * * *`, which Azure documents as every five minutes. Flex Consumption uses UTC by default; no unsupported Linux `WEBSITE_TIME_ZONE` setting is added.
- `run_on_startup=False` and `use_monitor=True`.
- User-assigned managed identity and identity-based `AzureWebJobsStorage`.
- Shared-key access and anonymous blob access disabled.
- Storage public networking disabled; Blob and Queue private endpoints plus Function VNet integration are provisioned.
- The runtime gets Storage Blob Data Owner because Microsoft documents it as the default role required for identity-based timer host storage, plus Storage Queue Data Contributor and Monitoring Metrics Publisher. The deploying user gets Blob Data Owner for package deployment. No role is granted to PostgreSQL because PostgreSQL is local.
- No lifecycle deletion policy is configured for raw data.

References:

- [Python versions supported by Azure Functions](https://learn.microsoft.com/azure/azure-functions/functions-versions)
- [Python v2 trigger/decorator model](https://learn.microsoft.com/azure/azure-functions/functions-bindings-triggers-python)
- [Timer trigger schedule, UTC, locking and overlap behavior](https://learn.microsoft.com/azure/azure-functions/functions-bindings-timer)
- [Flex Consumption hosting](https://learn.microsoft.com/azure/azure-functions/flex-consumption-plan)
- [Official Python timer/Flex sample](https://github.com/Azure-Samples/functions-quickstart-python-azd-timer)
- [Passwordless Blob access with `DefaultAzureCredential`](https://learn.microsoft.com/azure/storage/blobs/storage-quickstart-blobs-python)

No Azure command has been run by this project. To provision later:

```bash
azd auth login
azd env new interview
azd env set AZURE_LOCATION francecentral
azd up
```

`azd up` creates billable resources and modifies the selected subscription. Review `infra/` and run `az bicep build --file infra/main.bicep` before deployment.

Because storage is network-isolated, a workstation cannot directly process Azure blobs unless it has private network connectivity to the deployed VNet. Local end-to-end work uses the local raw directory. Adding a controlled transfer path or private developer connectivity is a later operational decision; weakening storage networking silently is not part of this slice.

## Verification status and limitations

Locally tested:

- response validation and transformation;
- missing/stale/closed exclusion behavior and explicit `0.5` example;
- zero-denominator behavior;
- network failure metadata;
- incomplete collection invisibility;
- PostgreSQL migration and duplicate-free reprocessing against PostgreSQL 17 in Docker;
- a live 1,519-row collection, raw persistence, processing and indicator calculation;
- lint and unit tests listed above (see the latest task report for exact results).

Live API checked:

- endpoint reachability and current schema sample;
- paginated total count;
- full-export count, duplicate station IDs, missing required fields and timestamp range.

Not yet verified:

- Azure provisioning, package deployment, managed-identity permissions, VNet/private DNS behavior, timer execution, Application Insights alerts, or live Blob writes;
- source behavior during concurrent export updates, prolonged failures, schema changes, quota exhaustion, or anomalous clock skew.

These boundaries are intentional and should be stated plainly in an interview.

