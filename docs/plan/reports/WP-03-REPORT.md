# WP-03 — REPORT
## Hecho
- `services/ingestor/core.py`: `ArrivalDeriver.process(snap)` puro (sin paho/psycopg). Llegada por punto medio, nunca cumplida con periodo de gracia, marca de agua monótona con `stale_stop_s`.
- `services/ingestor/config.py` + `config/ingestor.yaml`: `pass_threshold_s`, `grace_s`, `stale_stop_s`.
- `services/ingestor/sources.py`: `DataSource`, `FileSource`, `MqttSource` (sesión persistente).
- `services/ingestor/batch.py`: `run_batch(run_dir)` escribe `estimates`, `arrivals`, `never_fulfilled`, `watermarks` (.parquet).
- `services/ingestor/__main__.py`: servicio. Inserta `estimates`, publica snapshot/arrival/never_fulfilled/watermark, `ON CONFLICT DO NOTHING` en `arrivals` y `never_fulfilled`, latido con `snapshots_per_min`.
- Tests: `tests/acceptance/wp03/` (9 unitarios + 5 de integración).
- `test_breakdown_emits_never_fulfilled`: escenario `breakdown` alta en la línea 19; K3 medido con una retirada real.
- DDL: migración `005_estimates_unique.sql` (índice único `(stop_id, line_id, bus_id, polled_at)`); el servicio inserta `estimates` con `ON CONFLICT DO NOTHING`. `INTERFACES.md` §6 y `DEVIATIONS.md` (fila 23) actualizados. Autorizado en la petición (regla 1).
- `sim/world.py` y `services/ingestor/__main__.py`: manejador de `SIGINT` y `SIGTERM` (un trabajo en segundo plano arranca con `SIGINT` ignorado). Cierra escribiendo `runs/` y con código 0. Test `test_world_closes_clean_on_signal` (ambas señales, con `SIGINT` ignorado como lo deja el shell); falla sin el manejador.

## No hecho / en rojo (con motivo)
- Nada en rojo: `make lint test` y `make accept WP=03` (14/14; los 5 de integración necesitan el compose levantado).
- **Limitación declarada (para el documento):** *The Ingestor keeps its per-bus tracking state in memory only, so after a restart the arrivals of buses already being tracked are not derived (a gap, never a duplicate).*
- **Una llegada falsa inevitable por breakdown:** en la parada donde se avería el bus se deriva 1 llegada que no existe en la verdad (en las 6 semillas medidas). Su última estimación fue ≤ 26 s, igual que cualquier llegada normal (las llegadas verdaderas se ven por última vez con `time_left_s` hasta 75 s, medido con la semilla de línea base 1000), así que ningún umbral causal la separa sin perder llegadas verdaderas. El test exige "ninguna llegada falsa aguas abajo" y que la única falsa sea la de la parada del fallo; no se afirma "ninguna falsa" en todo el corredor.

## Supuestos y decisiones tomadas
- **El spec indicado en la petición (`WP-03-repo.md`) no existe**; se usó `docs/plan/WP-03-ingestor.md`, que es el WP3 de `PLAN.md`.
- `pass_threshold_s = 90`, `grace_s = 120`, `stale_stop_s = 300` (valores por defecto del spec, sin ajustar).
- Una parada nunca sondeada cuenta como sondeada en el primer `polled_at` del flujo, y deja de frenar la marca tras `stale_stop_s`.
- La confirmación de nunca cumplidas usa el máximo `polled_at` visto (cualquier parada), no solo el de su parada.
- Los tests de datos excluyen las llegadas verdaderas en el último minuto antes de la última marca de agua: un método causal no puede derivarlas al cortar el flujo. Los umbrales no se tocan.
- Las semillas de los tests son de validación (3000).
- Se probó `pass_threshold_s` solo en su valor por defecto; no se ajustó con semillas de validación.
- `msg_id` no va en los parquet: se calcula desde el payload con `make_envelope`; no cambia INTERFACES §8.
- `produced_at` del servicio usa reloj de pared (permitido: latencia).

## Cómo reproducir (un comando) y cuánto tarda
`make accept WP=03` — ~100 s (unitarios ~2 s; integración ~95 s: dos mundos de 30 min a 60× y dos de señales). Requiere `make up`. Si la BD de desarrollo ya tiene `estimates` duplicadas de antes de la migración 005, hay que hacer `TRUNCATE estimates` antes de aplicarla.

## Resultados medidos (tablas/figuras que sirven al documento)
3 h de corredor, sin fallos, semillas de validación:

| semilla | llegadas verdaderas | emparejadas | mediana error (s) | p95 error (s) | máx (s) | nunca cumplidas |
|---|---|---|---|---|---|---|
| 3000 | 535 | 99,6 % | 7,6 | 14,6 | 15,0 | 0 |
| 3001 | 535 | 99,6 % | 7,3 | 14,5 | 27,4 | 0 |
| 3002 | 537 | 100 % | 7,9 | 14,5 | 15,0 | 0 |

- K2 (p95 ≤ 15 s): cumple con margen de 0,4–0,5 s. Es el límite teórico de sondear cada 30 s (error máx. 15 s con incertidumbre 15); los 27 s son un sondeo fallido.
- K3 (≤ 1 %): en día limpio hay 0 nunca cumplidas (test vacío); por eso se mide también con retirada real:

| semillas 3000–3005, `breakdown` alta línea 19, 08:00, 4 h | resultado |
|---|---|
| nunca cumplidas emitidas | 4–6 por semilla, todas del bus averiado, una por cada parada aguas abajo con estimación pendiente |
| nunca cumplidas con llegada verdadera (K3) | 0 de 30 (0 %) |
| llegadas derivadas aguas abajo del fallo para ese bus | 0 |
| llegadas derivadas falsas en todo el corredor | 1 por semilla (parada del fallo, ver arriba) |
- Live = batch: mismos `msg_id` en `arrivals` (30 min a 60×); reinicio a mitad: 0 duplicados.

## Filas añadidas a DEVIATIONS.md
- 23: `estimates` única por `(stop_id, line_id, bus_id, polled_at)` con inserción idempotente (mismo patrón que la fila 19).
