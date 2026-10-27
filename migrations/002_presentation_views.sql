ALTER TABLE raw_collection
    ADD COLUMN IF NOT EXISTS freshness_minutes integer NOT NULL DEFAULT 45
        CHECK (freshness_minutes > 0);

COMMENT ON COLUMN raw_collection.freshness_minutes IS
    'Station freshness rule used when this collection was processed.';

CREATE INDEX IF NOT EXISTS raw_collection_request_ended_at_idx
    ON raw_collection (request_ended_at DESC);

CREATE OR REPLACE VIEW station_availability_history AS
SELECT
    observation.collection_id,
    collection.request_started_at AS collection_started_at,
    collection.request_ended_at AS collected_at,
    collection.freshness_minutes,
    observation.stationcode,
    observation.name,
    observation.is_installed,
    observation.capacity,
    observation.numdocksavailable,
    observation.numbikesavailable,
    observation.mechanical,
    observation.ebike,
    observation.is_renting,
    observation.is_returning,
    observation.duedate AS source_observed_at,
    EXTRACT(EPOCH FROM (collection.request_ended_at - observation.duedate))::double precision
        AS source_lag_seconds,
    observation.is_valid,
    observation.validation_errors,
    CASE
        WHEN NOT observation.is_valid THEN 'invalid'
        WHEN observation.is_renting IS NOT TRUE THEN 'not_open_for_rental'
        WHEN observation.duedate IS NULL
            OR observation.duedate < collection.request_ended_at
                - make_interval(mins => collection.freshness_minutes)
            OR observation.duedate > collection.request_ended_at + interval '5 minutes'
            THEN 'stale'
        WHEN observation.numbikesavailable = 0 THEN 'eligible_zero_bicycles'
        ELSE 'eligible_bicycles_available'
    END AS indicator_classification
FROM station_observation AS observation
JOIN raw_collection AS collection USING (collection_id);

CREATE OR REPLACE VIEW collection_quality AS
SELECT
    collection.collection_id,
    collection.request_started_at AS collection_started_at,
    collection.request_ended_at AS collected_at,
    collection.processed_at,
    collection.validation_outcome,
    collection.freshness_minutes,
    EXTRACT(EPOCH FROM (
        collection.request_ended_at - collection.request_started_at
    ))::double precision AS collection_duration_seconds,
    EXTRACT(EPOCH FROM (
        collection.processed_at - collection.request_ended_at
    ))::double precision AS processing_lag_seconds,
    collection.source_record_count,
    collection.missing_stationcode_count,
    count(history.stationcode) AS persisted_observation_count,
    collection.missing_stationcode_count
        + count(*) FILTER (WHERE history.indicator_classification = 'invalid')
        AS excluded_invalid_or_missing,
    count(*) FILTER (WHERE history.indicator_classification = 'not_open_for_rental')
        AS excluded_not_open_for_rental,
    count(*) FILTER (WHERE history.indicator_classification = 'stale') AS excluded_stale,
    count(*) FILTER (
        WHERE history.indicator_classification IN (
            'eligible_zero_bicycles',
            'eligible_bicycles_available'
        )
    ) AS eligible_station_count,
    count(*) FILTER (WHERE history.indicator_classification = 'eligible_zero_bicycles')
        AS zero_bicycle_station_count,
    avg(history.source_lag_seconds) FILTER (WHERE history.source_lag_seconds IS NOT NULL)
        AS average_source_lag_seconds,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY history.source_lag_seconds)
        FILTER (WHERE history.source_lag_seconds IS NOT NULL) AS p95_source_lag_seconds
FROM raw_collection AS collection
LEFT JOIN station_availability_history AS history USING (collection_id)
GROUP BY collection.collection_id;

CREATE OR REPLACE VIEW zero_bicycle_indicator_history AS
SELECT
    collection_id,
    collected_at,
    collection_started_at,
    processed_at,
    freshness_minutes,
    source_record_count,
    excluded_invalid_or_missing,
    excluded_not_open_for_rental,
    excluded_stale,
    zero_bicycle_station_count AS numerator_zero_bicycles,
    eligible_station_count AS denominator_open_valid_fresh,
    CASE
        WHEN eligible_station_count = 0 THEN NULL
        ELSE zero_bicycle_station_count::double precision / eligible_station_count
    END AS proportion_zero_bicycles
FROM collection_quality;

CREATE OR REPLACE VIEW latest_station_status AS
SELECT history.*
FROM station_availability_history AS history
WHERE history.collection_id = (
    SELECT collection_id
    FROM raw_collection
    ORDER BY request_ended_at DESC
    LIMIT 1
);

CREATE OR REPLACE VIEW pipeline_health AS
WITH latest_collection AS (
    SELECT collection_id
    FROM raw_collection
    ORDER BY request_ended_at DESC
    LIMIT 1
), latest AS (
    SELECT indicator.*
    FROM zero_bicycle_indicator_history AS indicator
    JOIN latest_collection USING (collection_id)
), totals AS (
    SELECT
        count(*) AS processed_collection_count,
        coalesce(sum(source_record_count - missing_stationcode_count), 0)
            ::numeric AS persisted_observation_count
    FROM raw_collection
)
SELECT
    latest.collection_id AS latest_processed_collection_id,
    latest.collected_at AS latest_processed_collection_at,
    EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - latest.collected_at))::double precision
        AS latest_processed_collection_age_seconds,
    interval '15 minutes' AS maximum_acceptable_collection_age,
    CASE
        WHEN latest.collection_id IS NULL THEN 'no_data'
        WHEN latest.collected_at > CURRENT_TIMESTAMP + interval '5 minutes' THEN 'clock_skew'
        WHEN CURRENT_TIMESTAMP - latest.collected_at > interval '15 minutes'
            THEN 'stale'
        ELSE 'healthy'
    END AS health_status,
    CASE
        WHEN latest.collection_id IS NULL THEN false
        ELSE latest.collected_at <= CURRENT_TIMESTAMP + interval '5 minutes'
            AND CURRENT_TIMESTAMP - latest.collected_at <= interval '15 minutes'
    END AS is_healthy,
    totals.processed_collection_count,
    totals.persisted_observation_count,
    latest.source_record_count AS latest_source_record_count,
    latest.excluded_invalid_or_missing AS latest_excluded_invalid_or_missing,
    latest.excluded_not_open_for_rental AS latest_excluded_not_open_for_rental,
    latest.excluded_stale AS latest_excluded_stale,
    latest.numerator_zero_bicycles,
    latest.denominator_open_valid_fresh,
    CASE
        WHEN latest.collection_id IS NULL
            OR latest.collected_at > CURRENT_TIMESTAMP + interval '5 minutes'
            OR CURRENT_TIMESTAMP - latest.collected_at > interval '15 minutes'
            THEN NULL
        ELSE latest.proportion_zero_bicycles
    END AS current_proportion_zero_bicycles,
    'processed_sql_only'::text AS health_scope
FROM totals
LEFT JOIN latest ON true;

CREATE OR REPLACE VIEW postgres_storage_usage AS
SELECT
    current_database() AS database_name,
    pg_database_size(current_database()) AS database_size_bytes,
    pg_size_pretty(pg_database_size(current_database())) AS database_size_pretty,
    pg_total_relation_size('raw_collection') AS raw_collection_total_bytes,
    pg_size_pretty(pg_total_relation_size('raw_collection')) AS raw_collection_total_pretty,
    coalesce(
        (SELECT n_live_tup FROM pg_stat_user_tables WHERE relname = 'raw_collection'),
        0
    ) AS raw_collection_estimated_rows,
    pg_total_relation_size('station_observation') AS station_observation_total_bytes,
    pg_size_pretty(pg_total_relation_size('station_observation'))
        AS station_observation_total_pretty,
    coalesce(
        (SELECT n_live_tup FROM pg_stat_user_tables WHERE relname = 'station_observation'),
        0
    ) AS station_observation_estimated_rows;

COMMENT ON VIEW pipeline_health IS
    'SQL serving health only. Raw attempts and Blob-to-SQL backlog are not visible in PostgreSQL.';

COMMENT ON VIEW postgres_storage_usage IS
    'PostgreSQL sizes and planner row estimates; does not include Azure Blob Storage usage.';
