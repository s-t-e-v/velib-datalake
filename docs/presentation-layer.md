# PostgreSQL presentation and health layer

The migration `002_presentation_views.sql` provides a dashboard-neutral contract over the
serving database. Metabase, Grafana, a SQL client, or a future read-only API should query these
views instead of reproducing indicator rules independently.

## Freshness semantics

Two different ages must not be confused:

- `freshness_minutes` is stored with every processed collection. It classifies each station
  observation relative to that collection's end time. New collections currently use the
  application's `VELIB_FRESHNESS_MINUTES` value; rows that predate this migration use 45 minutes.
- `pipeline_health` compares the latest processed collection with the current time. A collection
  is `healthy` for 15 minutes, `stale` after that, and `clock_skew` when its timestamp is more than
  five minutes in the future. The 15-minute limit allows three expected five-minute collection
  intervals before the SQL result is considered stale.

`pipeline_health.current_proportion_zero_bicycles` is `NULL` for `no_data`, `stale`, and
`clock_skew`. A dashboard should use this guarded value for a current KPI, not the last historical
proportion without its health status.

The default `velib indicator` command reads this guarded contract and exposes the same health
status, age, and nullable current proportion. `velib indicator --collection-id <uuid>` remains an
unguarded historical calculation for investigation and replay verification.

The health scope is deliberately labeled `processed_sql_only`. PostgreSQL cannot see failed raw
attempts that exist only in Blob metadata or journald, nor can it calculate the Blob-to-SQL
backlog. Those signals still require a later reconciliation/observability step.

## Views

| View | Grain and purpose |
| --- | --- |
| `pipeline_health` | One row: latest processed age/status, guarded current KPI, collection and observation totals |
| `collection_quality` | One row per processed collection: duration, processing lag, exclusions, eligible/zero counts, and source-lag statistics |
| `zero_bicycle_indicator_history` | One row per processed collection with the agreed historical indicator |
| `latest_station_status` | One row per station from the latest processed collection |
| `station_availability_history` | One row per collection and station with its indicator classification |
| `postgres_storage_usage` | One row with database/table sizes and PostgreSQL row estimates |

All presentation views are normal views, not materialized views. They always reflect committed
table data and require no refresh job. Historical aggregation cost will grow with
`station_observation`; measure query performance before adding materialization or summary tables.

## Useful inspection queries

```sql
SELECT * FROM pipeline_health;

SELECT * FROM postgres_storage_usage;

SELECT *
FROM zero_bicycle_indicator_history
ORDER BY collected_at DESC
LIMIT 100;

SELECT *
FROM collection_quality
ORDER BY collected_at DESC
LIMIT 100;
```

The size columns ending in `_bytes` are exact PostgreSQL sizes. The human-readable columns are
the same values formatted by PostgreSQL. Row counts in `postgres_storage_usage` are planner
estimates and can lag until statistics are updated; exact processed totals are available from
`pipeline_health`.

These sizes do not include the Azure Blob lake. Blob count and capacity must be measured through
Azure Storage metrics or APIs.

## Deployment boundary

Running `velib migrate` applies the views to a database. The repository change alone does not
modify the live VM. The normal application deployment runs migrations during bootstrap, but the
live migration and its results should still be verified explicitly after deployment.
