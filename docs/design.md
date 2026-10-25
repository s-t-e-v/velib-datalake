# Datalake practice

## Business need

> better recognize renting pattern of users
> investigate the impact of the environment (bicycle ways, etc.) on renting pattern
> Study unvaillibilty of bicycles

## Datasources

- **velib real time availability**: https://opendata.paris.fr/explore/dataset/velib-disponibilite-en-temps-reel/api/?disjunctive.is_renting&disjunctive.is_installed&disjunctive.is_returning&disjunctive.name&disjunctive.nom_arrondissement_communes
- **velib station location**: https://opendata.paris.fr/explore/dataset/velib-emplacement-des-stations/information/
- **amenagements cyclables**: https://opendata.paris.fr/explore/dataset/amenagements-cyclables/information/?disjunctive.amenagement&disjunctive.arrondissement&disjunctive.position_amenagement&disjunctive.vitesse_maximale_autorisee&disjunctive.source


## Architecture

### 1st vertical slice

**Success criteria**:
- Periodic ingestion
- ELT setup
- 1 bussiness insight

**Periodic ingestion**:
- The 2 velib data sources have continuous update frequency, 1 min resolution. We choose 5 min of time resultion to avoid overloading the server.
- We don't know rate limitation yet
- We don't know what is the maximum limit in term of batch
- I am not sure if just straight up using export could give me the full picture for all stations, and do so periodically
- The "amenagement cyclable" is updated every day
- in term of metadata:
  - either putting the timestamp on the filename
  - or creating a JSON wrapper that contains the JSON data in a fied and metadata of that JSON like the timestamp on the smae level, or under a metadata field. Not sure if this violate the ETL, since we kinda transform it, but there is no alteration of the raw sources, so I guess it is ok
- python would be used
- I guess creating an object dedicating to datacolection
- All the frequency parameters or other data necessary for the datacolection, like maybe the API url, etc. should live into a configuration file or using a library for that.

**Storage**
- Since data are of various sources and are mainly json, I believe MangoDb is the good choice in term of database, we can add meta data to documents and retrive them. The documets here would be json files. It would maybe solves the metadata issue

**Business Insight**

- A insight could be:
  - How many stations have 0 bike available
  - A distribution of the duration of 0 bike availabity
  - The number of stations with 0 bike by period of time

## Vocabulary to master

- "besoin metier"

## Interesting ressources

- **Courses**: https://datatalksclub.github.io/docs/courses/data-engineering-zoomcamp/?utm_source=chatgpt.com
- **Data Engineer roadmap**: https://roadmap.sh/data-engineer
- **ETL vs ELT**: https://www.ibm.com/fr-fr/think/topics/elt-vs-etl

**Videos**
- DataLake vs DataLakehouse vs DataWarehouse: https://youtu.be/PQFWQmL3fLY
- DataLake: https://youtu.be/LxcH6z8TFpI
