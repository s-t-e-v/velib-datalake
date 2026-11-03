# Engineering handoff

- Status: Living assessment and improvement backlog
- Last reviewed: 2026-10-30
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

### Resolved: Prevent misleading presentation

The SQL presentation layer now compares the latest processed collection with the current time.
It marks results stale after 15 minutes and suppresses the current KPI for stale, missing, or
future-dated data. The CLI's default `indicator` command now reads that guarded contract and
includes collection age and health status. Explicit `--collection-id` requests remain unguarded
historical calculations. Raw-attempt and end-to-end health signals remain part of the separate
observability gap below.

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

The first SQL health view now exposes processed collection age, status, current KPI, and exact
processed collection/observation totals. It explicitly cannot see raw failures, backlog, Blob
usage, service state, or lease state. A later application health/reconciliation layer must add
those signals before a monitoring product can answer the complete question.

### P1: Close the raw-to-SQL recovery gap

`collect --process` processes only its current collection. If SQL loading fails after raw commit,
later timer runs do not automatically backfill that collection. Manual bounded reconciliation is
available, but automatic backfill remains intentionally disabled until a recurring failure pattern
justifies it.

Possible incremental design:

1. Add a SQL query or CLI command that compares raw eligible IDs with processed IDs.
2. Retry only missing collections in chronological order.
3. Record processing attempts and last errors explicitly.
4. Add a bounded automatic reconciliation job after behavior is tested manually.

Avoid introducing a full orchestration platform before the required recovery semantics are
understood.

### Resolved: Remove the infinite-lease failure mode

The Azure collector now acquires a 60-second renewable Blob lease. It renews every 20 seconds,
retries transient renewal errors every two seconds, and checks ownership before committing raw
data. On 2026-10-27, a controlled 75-second hold proved renewal beyond the original term; killing
the owner left the blob locked initially and Azure reported it expired/unlocked after the term.

### Implemented foundation: Dashboard-neutral presentation contract

Migration `002_presentation_views.sql` now provides PostgreSQL views as the stable
software-agnostic boundary:

- `pipeline_health`: latest processed timestamp, age, status, guarded KPI, and SQL totals;
- `collection_quality`: counts and quality measures per collection;
- `zero_bicycle_indicator_history`: the agreed business rule over time;
- `latest_station_status`: one row per station from the latest processed collection;
- `station_availability_history`: BI-friendly observation history.

It also provides `postgres_storage_usage` for database/table sizes and estimated table rows. These
are normal views; query cost should be measured as history grows. Power BI on Windows and a
Linux-friendly tool such as Metabase, Superset, or Grafana can use the same definitions. Tool
selection should still follow a small connection and visualization spike rather than drive the
data model.

### P1: Add geography before building a map

Coordinates and municipality fields exist in raw responses but are not loaded into PostgreSQL.
The current model therefore cannot support a useful station map without reparsing raw data in the
BI layer.

Decide whether location belongs in a slowly changing station dimension or in every observation.
A station dimension is likely cleaner, but station-code stability and replacement semantics are
not guaranteed by the source and need an explicit policy.

### Resolved: Complete durability and disaster recovery safeguards

PostgreSQL uses the VM OS disk and has no independent backup. Blob is rebuildable source data but
the resource group contains both Blob and the VM, so deleting the group destroys both copies.

The following exercises were completed between 2026-10-27 and 2026-10-30:

- later scheduled execution and collection continuity;
- VM reboot and PostgreSQL container recreation;
- abandoned-lock recovery;
- database deletion followed by migration and targeted/full replay;
- replacement-VM bootstrap using the retained lake;
- a complete local export verified against Azure MD5 values before resource-group teardown.

PostgreSQL itself was not exported. It remains reproducibly rebuildable from the retained raw
collections.

### P2: Control growth and replay cost

There is no partition catalog or incremental replay index. Raw retention is intentional, so
storage and listing costs will grow indefinitely even with older objects in Cool. Full replay is
linear in the number of collections.

The first measurement on 2026-10-27 found 1,145 objects using 378,352,174 bytes. At the current
five-minute cadence and roughly 665 KB per response, raw growth is approximately 190 MB/day. The
selected policy retains every collection, moves collection objects to Cool after 30 days, and
never deletes automatically. This preserves rebuildability while reducing long-term capacity
cost; replay from Cool has higher retrieval and transaction cost and remains linear.

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

## Resolved review findings

The 2026-10-27 cleanup removed the unused Azure Functions entry point, configuration, dependency,
and infrastructure modules. It also removed the empty `azd` configuration, unused Python helpers,
test-only repository method, and duplicate `requirements.txt` dependency declaration. The active
deployment is now only the VM stack referenced by `infra/main.bicep`.

The same cleanup separated workstation deployment tooling, development Compose configuration,
and VM runtime assets into explicit repository paths. Mocked deployment tests continue to verify
the release allowlist and remote bootstrap flow.

## Documentation maintenance

- Deployment documentation repeats parts of the architecture and verification boundary. Keep the
  current-architecture document authoritative and let the runbook focus on commands.
- Historical validation numbers are useful evidence but should be clearly dated and separated
  from current health.

## Suggested delivery sequence

### Step 1: Clarify and simplify

Status: completed and verified on 2026-10-27. Unit, PostgreSQL integration, lint, shell syntax,
mocked deployment, and active Bicep compilation checks passed.

Outcome: one obvious architecture with less cognitive overhead.

### Step 2: Add a presentation and health SQL layer

Status: implemented locally and applied to the live PostgreSQL 17 database on 2026-10-27. The
live views reported more than 500 processed collections and a healthy latest collection.

- Specify collection-age and health semantics.
- Add tested SQL views for pipeline health, collection quality, historical indicator, and station
  observations.
- Ensure every presentation result carries collection time and freshness state.

Outcome: stable contracts shared by dashboards and operational inspection.

### Step 3: Build a portable dashboard

Status: implemented and verified locally on 2026-10-27. Grafana 13.2.4 started successfully,
provisioned the PostgreSQL data source and dashboard, returned a healthy database connection, and
executed all 12 panel queries successfully. A headless-browser check verified the rendered empty
state. The SSH-tunnel path was then verified against the live VM with a dedicated read-only role;
all 12 queries succeeded against the accumulated dataset. The credential is delivered through a
local Compose secret rather than a container environment variable.

- Spike one Linux-compatible client against the SQL views.
- Reuse the same views from Power BI on Windows.
- Start with pipeline health, latest KPI, KPI history, and quality/exclusion trends.
- Add a map only after the station/location model is settled.

Outcome: a showcase that is demonstrable on Linux without coupling the data model to one BI tool.

### Step 4: Reconcile missed processing

Status: manual reconciliation implemented and deployed on 2026-10-27. Live inspection found 563
eligible raw collections and all 563 already processed; bounded reconciliation correctly selected
zero. On 2026-10-28, a replacement Azure VM rebuilt 833 collections and then used bounded
reconciliation to process the one collection that arrived during replay, reaching 834 of 834 with
zero backlog. Automatic backfill is not justified yet because there is no observed recurring
production backlog or measured failure pattern.

- Expose raw-versus-processed backlog. Implemented by `velib backlog`.
- Add targeted chronological reconciliation. Implemented by `velib reconcile --limit N`.
- Test failure between Blob commit and SQL commit. Covered by a raw-retention and subsequent
  reconciliation test.
- Decide whether bounded automatic backfill is justified. Deferred until a recurring backlog or
  operational failure pattern is observed.

Outcome: SQL reliably converges toward the durable raw lake.

### Step 5: Prove operations and recovery

Status: completed and live-verified between 2026-10-27 and 2026-10-30. Finite lease
renewal, killed-process lease expiry, VM reboot, PostgreSQL persistence, the first scheduled
post-reboot collection, and a full 567-collection replay into a disposable database were
exercised. A separate replacement Azure VM was then bootstrapped against the retained lake and
rebuilt 833 collections into fresh PostgreSQL in 6 minutes 25 seconds. Bounded reconciliation
processed one collection that arrived during replay, reaching 834 of 834 with zero backlog and a
valid indicator. A 30-day Hot-to-Cool/no-deletion lifecycle rule and teardown checklist were
added. The final lake contained 1,305 eligible collections with zero backlog. Its 2,638 collection
objects were exported locally and verified before both Azure resource groups were deleted.

- Replace or harden the infinite lease. Completed.
- Exercise reboot, database rebuild, replacement host, and lock recovery. Completed.
- Add alerts only after health semantics are stable. Health semantics are stable; external alert
  delivery remains deferred because no monitoring destination or workspace is in scope.
- Define retention and teardown safeguards. Completed and exercised.

Outcome: operational claims are demonstrated rather than inferred.

### Step 6: Expand analytical scope

- Add station reference/location data.
- Model station identity and history explicitly.
- Add cycling infrastructure only when a concrete business question and spatial method are
  defined.
- Consider zero-bike duration and period-of-day analysis after collection continuity is measured.

Outcome: richer analysis built on an observable and recoverable foundation.

## Decisions still needed

- Should the initial 15-minute maximum collection age change after observing real scheduling
  behavior?
- Which operational metrics must trigger an alert versus only appear on a dashboard?
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
