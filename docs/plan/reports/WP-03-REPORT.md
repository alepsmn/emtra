# WP-03 — REPORT
## Hecho
- `services/ingestor/core.py`: `ArrivalDeriver.process(snap)` puro (sin paho/psycopg). Llegada por punto medio, nunca cumplida con periodo de gracia, marca de agua monótona con `stale_stop_s`.
- `services/ingestor/config.py` + `config/ingestor.yaml`: `pass_threshold_s`, `grace_s`, `stale_stop_s`.
- `services/ingestor/sources.py`: `DataSource`, `FileSource`, `MqttSource` (sesión persistente).
- `services/ingestor/batch.py`: `run_batch(run_dir)` escribe `estimates`, `arrivals`, `never_fulfilled`, `watermarks` (.parquet).
- `services/ingestor/__main__.py`: servicio. Inserta `estimates`, publica snapshot/arrival/never_fulfilled/watermark, `ON CONFLICT DO NOTHING` en `arrivals` y `never_fulfilled`, latido con `snapshots_per_min`.
- Tests: `tests/acceptance/wp03/` (8 unitarios + 2 de integración).

## No hecho / en rojo (con motivo)
- Nada en rojo: `make lint test` (33 pasan), `make accept WP=03` (10/10; necesita el compose levantado por los 2 de integración).
- `estimates` no tiene clave única en el DDL: una reentrega QoS 1 puede duplicar filas de `estimates` (no de `arrivals`). No lo pide el spec ni la aceptación; arreglarlo exige tocar el DDL (regla 1).
- El `core` no persiste estado: tras un reinicio se pierden los buses en seguimiento y sus llegadas no se derivan (no hay duplicados, sí huecos).

## Supuestos y decisiones tomadas
- **El spec indicado en la petición (`WP-03-repo.md`) no existe**; se usó `docs/plan/WP-03-ingestor.md`, que es el WP3 de `PLAN.md`.
- `pass_threshold_s = 90`, `grace_s = 120`, `stale_stop_s = 300` (valores por defecto del spec, sin ajustar).
- Una parada nunca sondeada cuenta como sondeada en el primer `polled_at` del flujo, y deja de frenar la marca tras `stale_stop_s`.
- La confirmación de nunca cumplidas usa el máximo `polled_at` visto (cualquier parada), no solo el de su parada.
- Los tests de datos excluyen las llegadas verdaderas en el último minuto antes de la última marca de agua: un método causal no puede derivarlas al cortar el flujo. Los umbrales no se tocan.
- Las semillas de los tests son de validación (3000).
- `msg_id` no va en los parquet: se calcula desde el payload con `make_envelope`; no cambia INTERFACES §8.
- `produced_at` del servicio usa reloj de pared (permitido: latencia).

## Cómo reproducir (un comando) y cuánto tarda
`make accept WP=03` — ~80 s (unitarios ~2 s; integración ~76 s por dos mundos de 30 min a 60×). Requiere `make up`.

## Resultados medidos (tablas/figuras que sirven al documento)
3 h de corredor, sin fallos, semillas de validación:

| semilla | llegadas verdaderas | emparejadas | mediana error (s) | p95 error (s) | máx (s) | nunca cumplidas |
|---|---|---|---|---|---|---|
| 3000 | 535 | 99,6 % | 7,6 | 14,6 | 15,0 | 0 |
| 3001 | 535 | 99,6 % | 7,3 | 14,5 | 27,4 | 0 |
| 3002 | 537 | 100 % | 7,9 | 14,5 | 15,0 | 0 |

- K2 (p95 ≤ 15 s): cumple con margen de 0,4–0,5 s. Es el límite teórico de sondear cada 30 s (error máx. 15 s con incertidumbre 15); los 27 s son un sondeo fallido.
- K3 (≤ 1 %): 0 nunca cumplidas en un día limpio, así que el test pasa de forma vacía. No se ha medido con fallos activos.
- Live = batch: mismos `msg_id` en `arrivals` (30 min a 60×); reinicio a mitad: 0 duplicados.

## Filas añadidas a DEVIATIONS.md
Ninguna.
