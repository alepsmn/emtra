# ADR 0002 — Nube local: ficheros + DuckDB + dbt-duckdb

- Estado: aceptada
- Fecha: 2026-11-15
- Autor (humano que aprueba): Milton Paredes

## Contexto
La decisión D6 del H3 pone el almacén frío en GCS y BigQuery con dbt, y Airflow para los jobs. Montarlo exige cuenta, credenciales, coste y un dataset de CI para un prototipo que corre en un portátil.

## Decisión
El nivel cloud corre en la misma máquina: Kafka (KRaft, un nodo) en Docker; el almacén crudo es un directorio `data/lake/raw/<topic>/dt=YYYY-MM-DD/` con NDJSON comprimido (misma estructura que tendría el bucket); el almacén analítico es un fichero DuckDB modelado con dbt-duckdb. Los jobs diarios se lanzan con `make`, sin Airflow.

## Consecuencias
- El SQL de dbt y la estructura de particiones son los del diseño; cambiar a GCS + BigQuery es cambiar el adapter y la ruta.
- La separación fog/cloud se mantiene en los límites que importan: el fog solo habla con la nube por el puente MQTT↔Kafka, y sigue funcionando con Kafka caído.
- Todo el stack corre en WSL/portátil con `make up`. No se usa la VM de los colectores.
- En el documento: la Tabla 43 y la 44 se mantienen como diseño de despliegue real; se añade la configuración del prototipo.
