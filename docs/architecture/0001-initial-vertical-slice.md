# ADR 0001: Define the initial Vélib' data-lake vertical slice

- Status: Superseded by the [current architecture](current-architecture.md)
- Date: 2026-10-25
- Scope: Initial exploration and project direction

## Context

The project started as a data-engineering exercise around Vélib' data. The broader business
questions were:

- How can rental patterns be characterized?
- How does the surrounding environment, such as cycling infrastructure, relate to those
  patterns?
- When and where are bicycles unavailable?

Three Paris Open Data sources were identified:

- [Vélib' real-time availability](https://opendata.paris.fr/explore/dataset/velib-disponibilite-en-temps-reel/information/)
- [Vélib' station locations](https://opendata.paris.fr/explore/dataset/velib-emplacement-des-stations/information/)
- [Cycling infrastructure](https://opendata.paris.fr/explore/dataset/amenagements-cyclables/information/)

Implementing all three sources and all possible analyses at once would not establish whether
the basic ingestion and processing path worked. The first milestone therefore needed to be a
small end-to-end slice.

At this point several facts were still unknown:

- the anonymous API quota;
- the maximum useful response size;
- whether the export endpoint reliably returned every station;
- how best to retain collection metadata without altering the raw response;
- whether document storage, initially considered as MongoDB, was appropriate.

## Decision

Build a first vertical slice with three success criteria:

1. Periodically ingest Vélib' availability data.
2. Demonstrate an ELT-style path in which source data is retained before analytical
   transformation.
3. Produce one business indicator about bicycle unavailability.

Use Python for collection and keep source URL, cadence, and other collection parameters in
configuration. Target a five-minute cadence: the source advertises minute-level updates, but a
five-minute snapshot is sufficient for the exercise and avoids unnecessary requests.

Preserve collection metadata separately from the source response so the raw JSON remains
unaltered. The precise storage technology and metadata layout are left for implementation
research.

Use the proportion or count of open stations with zero available bicycles as the first insight.
Potential later measures include the duration of zero-bike periods and their distribution over
time.

Defer station reference data, cycling infrastructure, and environmental analysis until the
availability pipeline works end to end.

## Alternatives considered

### Implement all sources immediately

This would support richer analysis sooner, but would mix source discovery, spatial modelling,
collection scheduling, storage, and presentation in the first milestone. It was rejected in
favor of a narrow vertical slice.

### Store raw JSON documents in MongoDB

MongoDB was initially considered because the sources are JSON and collection metadata could be
stored beside each document. This was a hypothesis, not a finalized choice. It was later
rejected: object storage is a simpler durable home for immutable raw responses, while
PostgreSQL is a better fit for the selected analytical queries and BI clients.

### Add metadata inside a wrapper around each response

A wrapper would keep data and metadata together but would mean the stored object was no longer
the exact decoded source response. Separate response and metadata objects were preferred during
implementation.

## Consequences anticipated at the time

Positive consequences:

- the project can prove an end-to-end data flow quickly;
- raw retention leaves room for later reprocessing;
- one explicit indicator gives validation a concrete target;
- postponing other sources limits early modelling complexity.

Trade-offs:

- the first slice cannot answer the broader rental-pattern or infrastructure questions;
- five-minute polling loses changes between snapshots;
- a current-availability source cannot recreate snapshots missed during an outage;
- an initial storage hypothesis may need to change after source exploration.

## Outcome and supersession

Implementation research resolved the open questions and changed parts of this proposal:

- the full export endpoint was selected instead of paginated record requests;
- raw responses and separate metadata commit markers are stored in Azure Blob Storage, or in a
  local directory during development;
- PostgreSQL stores transformed station observations;
- the deployed scheduler is a systemd timer on an Azure VM;
- the first indicator became the proportion of open, valid, sufficiently fresh stations with
  zero bicycles;
- MongoDB was not adopted;
- the other two sources remain intentionally out of scope.

The implemented design and its operational boundaries are recorded in
[Current architecture](current-architecture.md). Follow-up work is tracked in the
[Engineering handoff](engineering-handoff.md).

## Historical learning resources

The initial exploration used these general references:

- [Data Engineering Zoomcamp](https://datatalks.club/blog/data-engineering-zoomcamp.html)
- [Data Engineer roadmap](https://roadmap.sh/data-engineer)
- [ETL vs. ELT](https://www.ibm.com/think/topics/elt-vs-etl)
