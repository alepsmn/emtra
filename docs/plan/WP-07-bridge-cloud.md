# WP7 — Kafka, puente MQTT↔Kafka con outbox y nube local

**Hito:** H5 · **Depende de:** WP1 (y el stack de H4 para probar) · Decisiones D3, D4 y ADR 0002.

## 7.1 Infra
Añadir `kafka` (apache/kafka, KRaft, un nodo, heap 512 MB) al compose. `infra/kafka/create_topics.py` idempotente, generado desde `contracts/topics.yaml`.

## 7.2 Puente (`services/bridge/`)
- Rutas generadas desde `contracts/topics.yaml`.
- **fog→cloud:** cada mensaje MQTT entra primero en un **outbox SQLite** (`outbox(msg_id PK, topic, key, value, sent INTEGER)`). Un bucle vacía el outbox en orden de inserción hacia Kafka (`enable.idempotence=true`, `acks=all`) y marca `sent` al confirmarse la entrega. `INSERT OR IGNORE` por `msg_id` absorbe los duplicados de QoS 1.
- Kafka caído → el outbox crece; `outbox_depth` va en el latido de `service_health` (panel de salud). Al volver, se vacía en orden.
- **cloud→fog:** consumidor Kafka (commit manual tras publicar) que publica en MQTT con QoS 1 y retenido donde diga `topics.yaml`.
- Núcleo puro: `core.py` con la lógica del outbox sobre una conexión SQLite; el adaptador pone MQTT y Kafka.

## 7.3 Sink (`services/cloud_sink/`)
Consumidor de todos los topics fog→cloud. Escribe NDJSON comprimido en `data/lake/raw/<topic>/dt=YYYY-MM-DD/part-<n>.ndjson.gz` (fecha de `event_time`); commit de offset tras cerrar el fichero. Rotación por nº de mensajes o tiempo.

## 7.4 dbt (`cloud/dbt/`, adapter duckdb)
- `staging/`: `stg_snapshots` (una fila por estimación, desanidada), `stg_arrivals`, `stg_status`, `stg_alerts`, leyendo los NDJSON con `read_json_auto`. Tests `not_null` y `unique(msg_id)`.
- `make dbt` = `dbt build`.

## Aceptación (`tests/acceptance/wp07/`)
```python
def test_outbox_core_orders_and_dedups():   # unitario sobre SQLite en memoria: orden de inserción, duplicado ignorado, reenvío de no confirmados
# integración (make it):
def test_outbox_survives_kafka_outage():    # 2 000 mensajes a 50 msg/s; parar kafka 30 s a mitad -> en Kafka exactamente 2 000 msg_id distintos, en orden por clave   (K16)
def test_bridge_restart_no_loss():          # matar el puente a mitad -> 0 pérdidas
def test_cloud_to_fog_retained():           # producir en emtra.calibration.v1 -> retenido en calibration/current
def test_sink_writes_partitions():          # NDJSON en dt= correcto; nº de líneas == mensajes
def test_dbt_staging_builds():              # dbt build --select staging en verde sobre 30 min de mundo
```

## Checklist
- [ ] El fog (Ingestor, Monitor, Grafana) sigue funcionando con `docker compose stop kafka`.
- [ ] `make up` arranca también puente y sink; `make demo-v1` sigue en verde.
