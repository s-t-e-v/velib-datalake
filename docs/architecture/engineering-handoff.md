# Engineering handoff

- Status: Living assessment and improvement backlog
- Last reviewed: 2026-10-27
- System description: [Current architecture](current-architecture.md)

## Purpose

This document preserves the findings and follow-up ideas from the first architecture review. It
is a handoff, not a promise that every item should be implemented. Reassess priorities after each
step and convert selected items into narrowly scoped work.

The core pipeline is functional. Its most important next stage is to make correctness and current
state visible before expanding the number of data sources or infrastructure components.

## What is already strong

- Raw data is committed before transformation, so SQL failure does not destroy source evidence.
- Separate metadata preserves the source response while recording provenance and validation.
- Blob is treated as the durable source of truth; PostgreSQL is rebuildable serving state.
- Local and Azure storage implement the same interface.
- Collection and station keys make replay idempotent.
- Invalid, missing, closed, and stale observations are not silently converted to zero bicycles.
- HTTP requests have explicit timeouts and bounded transient retries.
- Managed identity avoids storage credentials on the VM.
- PostgreSQL is not publicly exposed, and SSH ingress is restricted.
- The architecture is intentionally small enough to explain in an interview.

## Confirmed weaknesses and risks

### P0: Prevent misleading presentation

The indicator checks source timestamps relative to the collection timestamp, but the default
command selects the latest processed collection without checking how old that collection is
relative to now. If collection stops, an old result can remain internally valid and appear
current.

Suggested response:

- expose the latest attempt, raw success, SQL success, and their ages;
- define an explicit maximum acceptable collection age;
- make dashboards show a visible stale-data state instead of only the last value;
- include the collection timestamp and age in every presentation query.

### P0: Make pipeline state observable

Operational evidence is split among systemd, journald, Blob metadata, PostgreSQL, and Azure
resource state. There is no joined answer to “is the pipeline healthy?” and no alerting.

Minimum useful health signals:

- last attempt time and status;
- last successful raw commit and its age;
- last successful SQL load and its age;
- raw collections awaiting SQL processing;
- collection duration, HTTP status, retry/failure category, and record count;
- invalid, closed, stale, and eligible observation counts;
- distribution of source timestamp lag;
- detected scheduling gaps;
- PostgreSQL observation count and storage size;
- Blob object count and storage size;
- service/timer failures and current lease state.

Prefer producing these as application/SQL health views first. A monitoring product can consume
them later.

### P1: Close the raw-to-SQL recovery gap

`collect --process` processes only its current collection. If SQL loading fails after raw commit,
later timer runs do not automatically backfill that collection. Manual full replay is possible,
but it rereads every eligible historical collection.

Possible incremental design:

1. Add a SQL query or CLI command that compares raw eligible IDs with processed IDs.
2. Retry only missing collections in chronological order.
3. Record processing attempts and last errors explicitly.
4. Add a bounded automatic reconciliation job after behavior is tested manually.

Avoid introducing a full orchestration platform before the required recovery semantics are
understood.

### P1: Remove the infinite-lease failure mode

The Azure collector acquires an infinite Blob lease. A hard process or VM failure can leave the
lock held indefinitely, requiring a careful manual lease break.

Investigate a finite renewable lease or another lock with expiry. Any change must still prevent
two collectors from committing overlapping runs and must be tested for process termination,
slow requests, and temporary Azure errors.

### P1: Create a dashboard-neutral presentation contract

There is no application layer yet. The CLI returns one indicator as JSON, while BI clients would
otherwise need to understand raw tables and validation rules independently.

Use PostgreSQL views as the stable software-agnostic boundary. Candidate views are:

- `pipeline_health`: stage timestamps, ages, status, and backlog;
- `collection_quality`: counts and quality measures per collection;
- `zero_bicycle_indicator_history`: the agreed business rule over time;
- `latest_station_status`: one current row per station, including data-age status;
- `station_availability_history`: BI-friendly observation history.

Power BI on Windows and a Linux-friendly tool such as Metabase, Superset, or Grafana can then use
the same definitions. Tool selection should follow a small connection and visualization spike,
not drive the data model.

### P1: Add geography before building a map

Coordinates and municipality fields exist in raw responses but are not loaded into PostgreSQL.
The current model therefore cannot support a useful station map without reparsing raw data in the
BI layer.

Decide whether location belongs in a slowly changing station dimension or in every observation.
A station dimension is likely cleaner, but station-code stability and replacement semantics are
not guaranteed by the source and need an explicit policy.

### P2: Test durability and disaster recovery

PostgreSQL uses the VM OS disk and has no independent backup. Blob is rebuildable source data but
the resource group contains both Blob and the VM, so deleting the group destroys both copies.

Run and record these exercises:

- later scheduled execution and collection continuity;
- VM reboot and PostgreSQL container recreation;
- abandoned-lock recovery;
- database deletion followed by migration and targeted/full replay;
- replacement-VM bootstrap using the retained lake;
- separate export/retention before resource-group teardown.

### P2: Control growth and replay cost

There is no Blob lifecycle policy, partition catalog, or incremental replay index. Raw retention
is intentional, but storage and listing costs will grow indefinitely. Full replay is linear in
the number of collections.

Measure actual growth before adding complexity. Then document a retention policy, archive tier,
or manifest strategy based on the desired demonstration horizon and recovery requirements.

### P2: Tighten data-quality and schema-change handling

The minimum record threshold, freshness threshold, and accepted source values are operational
assumptions. The full export is not guaranteed to be atomic, and the provider does not promise
station-code immutability or a maximum publication delay.

Useful additions include:

- trend alerts rather than only fixed thresholds;
- explicit schema/version compatibility checks;
- source-lag percentiles by collection;
- consistency checks such as bicycle subtype totals and capacity relationships, with documented
  treatment of source anomalies;
- tests covering new or unknown source fields and value variants.

## Dead and legacy code inventory

The active deployment is the VM stack referenced by `infra/main.bicep`. The following tracked
artifacts belong to the earlier Azure Functions architecture and are not referenced by the active
deployment:

- `function_app.py`
- `host.json`
- `local.settings.example.json`
- `.funcignore`
- `infra/modules/function-app.bicep`
- `infra/modules/network.bicep`
- `infra/modules/rbac.bicep`
- `infra/modules/storage-private-endpoints.bicep`
- the `azure-functions` project dependency and lock entry

`azure.yaml` defines no services and only contains comments for an unsupported `azd` workflow. It
is a removal candidate unless it provides a deliberate editor or discovery benefit.

Confirmed unused Python functions:

- `collector.utc_now()`;
- `storage.metadata_as_json()`.

`PostgresRepository.observation_count()` is used only by the database integration test. It is
not dead, but test-only convenience code should either be replaced with a test query or clearly
kept as a supported repository operation.

`requirements.txt` duplicates dependencies from `pyproject.toml`. Deployment copies it, but
bootstrap installs the project from `pyproject.toml`; choose one dependency authority and update
the archive/bootstrap accordingly.

Remove legacy artifacts in a dedicated, tested cleanup change. First verify that no external
deployment process still depends on them, then regenerate `uv.lock`, run unit/integration checks,
compile the active Bicep templates, and test the deployment archive allowlist.

## Documentation inconsistencies to clean up

- `.env.example` still refers to a deployed Function App and says PostgreSQL is local-only,
  although the VM collector also processes into PostgreSQL.
- README examples use `raw/YYYY/...` as a general path, while Azure uses the `collections/`
  prefix and local storage uses its configured root.
- Deployment documentation repeats parts of the architecture and verification boundary. Keep the
  current-architecture document authoritative and let the runbook focus on commands.
- Historical validation numbers are useful evidence but should be clearly dated and separated
  from current health.

## Suggested delivery sequence

### Step 1: Clarify and simplify

- Remove the confirmed Azure Functions path and unused helpers.
- Correct stale configuration comments and dependency duplication.
- Keep the unit suite green and run the PostgreSQL integration test.
- Compile the active Bicep templates and retain the mocked deployment tests.

Outcome: one obvious architecture with less cognitive overhead.

### Step 2: Add a presentation and health SQL layer

- Specify collection-age and health semantics.
- Add tested SQL views for pipeline health, collection quality, historical indicator, and station
  observations.
- Ensure every presentation result carries collection time and freshness state.

Outcome: stable contracts shared by dashboards and operational inspection.

### Step 3: Build a portable dashboard

- Spike one Linux-compatible client against the SQL views.
- Reuse the same views from Power BI on Windows.
- Start with pipeline health, latest KPI, KPI history, and quality/exclusion trends.
- Add a map only after the station/location model is settled.

Outcome: a showcase that is demonstrable on Linux without coupling the data model to one BI tool.

### Step 4: Reconcile missed processing

- Expose raw-versus-processed backlog.
- Add targeted chronological reconciliation.
- Test failure between Blob commit and SQL commit.
- Decide whether bounded automatic backfill is justified.

Outcome: SQL reliably converges toward the durable raw lake.

### Step 5: Prove operations and recovery

- Replace or harden the infinite lease.
- Exercise reboot, database rebuild, replacement host, and lock recovery.
- Add alerts only after health semantics are stable.
- Define retention and teardown safeguards.

Outcome: operational claims are demonstrated rather than inferred.

### Step 6: Expand analytical scope

- Add station reference/location data.
- Model station identity and history explicitly.
- Add cycling infrastructure only when a concrete business question and spatial method are
  defined.
- Consider zero-bike duration and period-of-day analysis after collection continuity is measured.

Outcome: richer analysis built on an observable and recoverable foundation.

## Decisions still needed

- What age makes a collection too stale for presentation?
- Which operational metrics must trigger an alert versus only appear on a dashboard?
- Should indicator history be a normal view, materialized view, or stored aggregate?
- How should station replacement or station-code reuse be represented?
- How much raw history should be retained hot, cool, or outside the resource group?
- Is PostgreSQL on the VM sufficient for the final showcase, or is independent durability a goal?
- Which dashboard tool will be used for the Linux demonstration?
- Will Windows Power BI connect through an SSH tunnel for a private demonstration, or consume an
  exported dataset?

## Review checklist for future changes

When completing an improvement, update this handoff by:

1. moving verified facts into the current architecture document;
2. removing resolved weaknesses rather than leaving stale backlog text;
3. recording material architectural choices as new ADRs;
4. adding concrete verification evidence and its date;
5. preserving unresolved trade-offs and explicitly rejected alternatives.
