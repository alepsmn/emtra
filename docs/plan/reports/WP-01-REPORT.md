# WP-01 — REPORT
## Hecho
- `contracts/`: `raw.py` (RawSnapshot), `envelope.py` (Envelope genérico, `make_msg_id` = uuid5 con instantes normalizados a UTC), `payloads.py` (los 11 schemas de §4 + `passenger_counts` y `config` solo modelo; registro `SCHEMAS` con event_time y clave natural de cada fila de la tabla; `make_envelope`), `topics.yaml` + `topics.py` (12 topics MQTT, 10 Kafka, helpers de topic/filtro/clave).
- `contracts/sql/fogstore/001–004`: todas las tablas de §6, hypertables `estimates` y `arrivals`, índice único parcial de alerta abierta, tres vistas.
- `emtra_common/`: `clock`, `rng`, `bands`, `config`, `corridor`, `timetable`, `db` (migraciones con tabla `schema_migrations`, idempotentes), `reference` (`python -m emtra_common.reference load`), `mqtt`, `health` (`write_health`, `Heartbeat`).
- `config/`: `corridors/c19.yaml` (12 paradas, 2 tramos, líneas 19/C1/170, puntos de control S04/S08), `sim/c19.yaml` provisional (enlace 90 s, dwell 20 s), `data_split.yaml`, `bands.yaml`, `infra.yaml`.
- Tests: los 11 sin Docker + 3 de integración del spec + `test_health_upsert` (extra). Escritos en el primer commit, en rojo.

## No hecho / en rojo (con motivo)
- Nada en rojo. `holding.py` es de WP4. `mqtt.py` y `Heartbeat` no tienen test con broker (solo `write_health` se prueba); se ejercitarán en WP3.
- Checklist: ningún modelo tiene campo `schema` (se usa `schema_name`, también en topics.yaml); `event_time` y claves naturales siguen la tabla de §4; bandas en Europe/Madrid, UTC almacenado.

## Supuestos y decisiones tomadas
- `make test` excluye los tests marcados `integration` (`-m "not integration"`); `make accept WP=01` y `make it` los ejecutan y necesitan `make up`. `make it` ya levanta el compose y corre `pytest -m integration` (era un TODO de WP5). Los tests de integración crean una BD temporal por test.
- Límites de LOS, grupos de banda, bandas de horizonte y el umbral HF (`hf_max_headway_s: 600`) están en YAML (regla 6), no en código.
- `event_time` no derivable del payload (`recommendation_request`, `recommendation`, `recovery` no recuperada) se pasa explícito a `make_envelope`; falla si falta. No se añadieron campos a los payloads.
- Horario: salidas desde el inicio de cada grupo de banda con su headway; llegada en la parada k = salida + k·(enlace + dwell) (110 s con los valores provisionales). Los headways de C1 (450/540 s) y 170 (1200 s) los elegí para que cuadren con los límites de grupo; son sintéticos.
- Paradas inventadas (`synthetic: true`, ADR 0001); no se consultó GTFS.
- `scheduled_headway` y `line_type` aceptan `corridor` opcional (por defecto c19) para cumplir la firma de §7 y del test.
- Interfaces con Docker: TimescaleDB en `localhost:5433` (WP0).

## Cómo reproducir (un comando) y cuánto tarda
`make up && make accept WP=01` — ~3 s los 15 tests con el compose ya levantado. `make lint test` en verde (13 tests sin Docker).
`uv run python -m emtra_common.reference load` carga 12 paradas y 3100 filas de horario.

## Resultados medidos (tablas/figuras que sirven al documento)
Horario sintético del día de servicio: 3100 filas (trayecto×parada) para las 3 líneas.

## Filas añadidas a DEVIATIONS.md
- Fila 19: `UNIQUE (msg_id, arrival_time)` en `arrivals` (requisito de hypertable).
- Huecos corregidos en `INTERFACES.md` (sin fila propia): `hf_max_headway_s` y `synthetic` en §1; fórmula de llegada, `corridor` opcional y `band_end`/`los_status` en §7.
