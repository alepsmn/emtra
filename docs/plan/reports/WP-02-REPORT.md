# WP-02 — REPORT
## Hecho
- `sim/`: `params.py` (+ `config/sim/c19.yaml` completo), `engine.py` (SimPy: buses, bahías, pasajeros Poisson pre-generados por (parada, línea), dwell, hold, fallos), `predictor.py`, `faults.py`, `policy.py` (`ControlPolicy`, `NoAction`, `BusView`, `StopView`), `run.py` (batch y `write_run`), `world.py` (mismo motor sobre `simpy.rt.RealtimeEnvironment`, publica en `emtra/v1/sim/raw/{stop_id}`).
- Salidas de §8 que escribe el simulador: `manifest.json`, `raw/snapshots.jsonl.gz`, `timetable.parquet`, `truth_arrivals.parquet`, `passengers_agg.parquet`, `faults.parquet`.
- Aleatoriedad solo por `emtra_common.rng`, indexada por entidad (`run`, `pax`, `dispatch`, `surge`, `pred`, `pollfail`).
- `Engine(..., initial_state=None)`: el parámetro existe y lanza `NotImplementedError` si se usa (lo implementa WP8); los buses se crean desde una lista de trips.
- Arreglo en `emtra_common/bands.py`: `band_start` con `pandas.Timestamp` conservaba nanosegundos.
- `scripts/wp02_report.py` genera la tabla y la figura de abajo.

## No hecho / en rojo (con motivo)
- Nada en rojo: `make lint test` y `make accept WP=02` en verde (10/10).
- `python -m sim.world` escrito pero **no ejecutado contra un broker** (sin test; se ejercita en WP5). Los 60× no están medidos.
- `test_breakdown_creates_gap` es estadístico: el hueco es la suma de dos headways vecinos (media 2h, σ≈100 s con la dispersión de salida del spec). En 20 semillas (3000–3019, `breakdown` alta) el máximo en S05 / 360 s fue ≥ 1,8 en 14 (70 %); rango 1,62–2,71. El test usa la semilla 3000 (primera del rango de validación, elegida a priori) y pasa; con otra semilla puede fallar. No se tocó el umbral.
- `test_no_overtaking_blowup`: máximo headway de la 19 en punta / programado en 20 semillas: 1,8–2,75 (límite 3). Margen escaso.

## Supuestos y decisiones tomadas
- **Dependencia añadida (dev):** `matplotlib` y `tabulate`, solo para la figura y la tabla del REPORT.
- Llegada verdadera (`arrival_time`) = llegada física a la parada, antes de la cola de bahía; así `llegada(k+1) − salida(k)` es exactamente el tiempo de marcha y `test_crn_sync` es exacto. `departure_time` incluye dwell, hold y bloqueo.
- Los buses aparecen en las instantáneas `preroll_s` = 180 s antes de salir de cabecera, para que el Ingestor vea su llegada al primer stop.
- Predictor: suma de medias de enlace + dwell nominal 20 s por parada intermedia, × 0,95 × ruido lognormal (CV 0,05) por (bus, sondeo). Distancia = `time_left × 5 m/s` (no hay geometría).
- Demanda: pesos por parada/línea/grupo en YAML; escala calibrada con un modelo fluido para que la carga máxima de punta sea 65 % de la capacidad. Medido: máx. 45 pasajeros en la 19 (56 %) por redondeos y la media del día.
- El día arranca vacío a las 06:00; el primer grupo tras un cambio de horario incluye el escalón de headway (p.ej. 600 → 360 s), por eso `test_clean_day_is_plausible` excluye la primera banda de 07:00.
- Pasajeros que no caben quedan en la parada para el siguiente bus. Pasajeros sin identidad (solo conteos).
- Parámetros: CV de enlace en punta 0,25 en vez de 0,30 (DEVIATIONS 21).
- Fallos: ver DEVIATIONS 22. El hold ocupa la bahía.
- Los tests de fallos usan semilla 3000 (validación, §10); `collector_id` del test de determinismo se normaliza porque lleva el nombre del directorio.

## Cómo reproducir (un comando) y cuánto tarda
`make accept WP=02` — ~3 s los 10 tests. Un día completo (06–24 h) en batch: ~1,7 s; 3 h: ~1,1 s (incluye arranque de Python y escritura).
Tabla y figura: `PYTHONPATH=. uv run python scripts/wp02_report.py` (~25 s).

## Resultados medidos (tablas/figuras que sirven al documento)
c_vh medio por línea y grupo de banda, día limpio, 10 semillas (1000–1009), tramos 1 y 2 agrupados, sin la primera banda de cada grupo:

| línea | grupo | c_vh medio | σ entre (semilla, tramo, banda) | n |
|---|---|---|---|---|
| 19 | early | 0,123 | 0,089 | 49 |
| 19 | am_peak | 0,298 | 0,155 | 220 |
| 19 | midday | 0,175 | 0,101 | 460 |
| 19 | pm_peak | 0,280 | 0,143 | 300 |
| 19 | evening | 0,092 | 0,069 | 300 |
| C1 | early | 0,077 | 0,052 | 48 |
| C1 | am_peak | 0,177 | 0,110 | 220 |
| C1 | midday | 0,118 | 0,075 | 460 |
| C1 | pm_peak | 0,186 | 0,116 | 300 |
| C1 | evening | 0,071 | 0,052 | 300 |

(La 170 es LF: usa OTP, no c_vh. La 19 en punta queda cerca del tope 0,35 del test; la media del test sobre la semilla 1 está dentro del rango.)

Figura de trayectorias (tiempo × parada) de un `breakdown` alta en la línea 19: `docs/plan/reports/figures/wp02_breakdown.png`.

## Filas añadidas a DEVIATIONS.md
- 20: horario con constantes nominales frente a enlaces por tramo.
- 21: CV de enlace 0,25 en punta.
- 22: semántica de `blocked_segment` y `breakdown`; `faults.parquet` guarda tiempos reales de disparo.
