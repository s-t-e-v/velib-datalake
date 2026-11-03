# Source API and data observations

The pipeline uses the official Vélib' real-time availability dataset published by Paris Open Data.
This document records the source selection and the observations that informed validation rules.

## Selected endpoint

```text
GET https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/velib-disponibilite-en-temps-reel/exports/json
```

No query parameters are required. Opendatasoft exports default to `limit=-1` and are not subject
to the records endpoint's result limit, so the collector makes one export request instead of
paginating the records endpoint:

```text
GET https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/velib-disponibilite-en-temps-reel/records?limit=100&offset=0
```

The records endpoint allows at most 100 non-aggregated records per request and requires
`offset + limit < 10000`. At the observed station count, pagination would take 16 requests. A
complete export does not guarantee an atomic snapshot: rows can have different `duedate` values
and may be updated while the export is generated.

Official references:

- [Paris Data dataset and schema](https://opendata.paris.fr/explore/dataset/velib-disponibilite-en-temps-reel/information/)
- [Opendatasoft Explore API v2.1](https://help.opendatasoft.com/apis/ods-explore-v2/)

## Fields used by the pipeline

Source field names remain unchanged in PostgreSQL.

| Field | Meaning and use |
| --- | --- |
| `stationcode` | Official station identifier and analytical key within a collection |
| `name` | Descriptive station name |
| `is_installed` | Whether the station is operationally installed |
| `capacity` | Number of docks at the station |
| `numdocksavailable` | Available docks |
| `numbikesavailable` | Total available bicycles; required for the indicator |
| `mechanical`, `ebike` | Available mechanical and electric bicycles |
| `is_renting` | Whether the station can rent bicycles; defines the indicator population |
| `is_returning` | Whether bicycles can be returned |
| `duedate` | Source-labeled data-update timestamp; used for per-station freshness |

`coordonnees_geo`, `nom_arrondissement_communes`, `code_insee_commune`, and
`station_opening_hours` remain in raw data but are outside the current analytical slice.

The publisher labels `duedate` as *Actualisation de la donnée* (data update) but provides no
detailed field description. The pipeline treats it as the best available time at which the
upstream availability row was updated; it is not assumed to be an exact physical sensor event or
the time our collector downloaded the export. Source lag is therefore
`request_ended_at - duedate`, subject to this documented uncertainty.

## Observations

Live checks were made on 2026-10-25 UTC with a small number of requests:

- A `limit=3` records request reported 1,519 records. The response is retained in
  `tests/fixtures/records-page-limit-3.json`.
- A full export contained 1,519 rows and 1,519 distinct, non-empty station identifiers.
- No row in that export lacked `duedate` or `numbikesavailable`.
- Observed `is_renting` values were `OUI` and `NON`.
- `duedate` ranged from 2017-11-27 to 2026-10-25, demonstrating that collection recency cannot
  replace per-station freshness validation.
- The dataset advertised one-minute refreshes, while two live checks found the newest observation
  roughly 30 minutes behind collection time. This informed the initial 45-minute freshness
  threshold.
- The observed anonymous quota was 10,000 calls per day, resetting at midnight UTC. Opendatasoft
  documents quotas as portal-configurable, so this is not a contractual guarantee.
- The export response was gzip encoded. The HTTP client decodes the transport encoding and stores
  the resulting JSON response bytes without business transformation.

These are dated observations, not permanent source guarantees. The provider does not promise an
atomic export, an immutable identifier across station replacement, a fixed anonymous quota, or a
maximum response latency. The meaning of very old station rows is also undocumented. The pipeline
therefore validates or excludes uncertain data rather than inferring values.
