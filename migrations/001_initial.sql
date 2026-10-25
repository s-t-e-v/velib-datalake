CREATE TABLE IF NOT EXISTS schema_migration (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw_collection (
    collection_id uuid PRIMARY KEY,
    source_url text NOT NULL,
    source_parameters jsonb NOT NULL,
    request_started_at timestamptz NOT NULL,
    request_ended_at timestamptz NOT NULL,
    raw_data_location text NOT NULL,
    raw_metadata_location text NOT NULL,
    source_record_count integer NOT NULL CHECK (source_record_count >= 0),
    missing_stationcode_count integer NOT NULL CHECK (missing_stationcode_count >= 0),
    validation_outcome text NOT NULL,
    processed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS station_observation (
    collection_id uuid NOT NULL REFERENCES raw_collection(collection_id) ON DELETE RESTRICT,
    stationcode text NOT NULL,
    name text,
    is_installed boolean,
    capacity integer CHECK (capacity >= 0),
    numdocksavailable integer CHECK (numdocksavailable >= 0),
    numbikesavailable integer CHECK (numbikesavailable >= 0),
    mechanical integer CHECK (mechanical >= 0),
    ebike integer CHECK (ebike >= 0),
    is_renting boolean,
    is_returning boolean,
    duedate timestamptz,
    is_valid boolean NOT NULL,
    validation_errors text[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (collection_id, stationcode)
);

CREATE INDEX IF NOT EXISTS station_observation_duedate_idx
    ON station_observation (duedate);

