# INTERFACES.md — contratos de EMTRA

Única fuente de verdad de todo lo que cruza un límite entre componentes. WP1 lo convierte en código (`contracts/`, `emtra_common/`). Cambiarlo exige fila en `docs/DEVIATIONS.md` (regla 1 de AGENTS.md).

Convenciones: tiempos en UTC con zona (`AwareDatetime`), serializados ISO 8601. Unidades en el nombre del campo (`_s`, `_m`, `_pax_min`). Ningún dato personal.

## 1. Corredor (`config/corridors/c19.yaml`, modelo en `emtra_common/corridor.py`)

Corredor sintético, un solo sentido (`direction_id = 0`).

```yaml
corridor_id: c19
timezone: Europe/Madrid
service_date: 2026-11-16          # lunes; todos los días simulados son laborables
service_window: ["06:00", "24:00"]
stops:                            # 12 paradas en orden; stop_sequence = posición (1..12)
  - {stop_id: "S01", name: "...", lat: 40.42, lon: -3.69, bays: 2}
segments:                         # segment_id entero, ordenado aguas abajo
  - {segment_id: 1, stop_ids: [S01, S02, S03, S04, S05, S06]}
  - {segment_id: 2, stop_ids: [S07, S08, S09, S10, S11, S12]}
lines:
  - line_id: "19"                 # HF, recorre todo el corredor
    direction_id: 0
    enter_stop: S01
    exit_stop: S12
    capacity: 80
    headway_s: {early: 600, am_peak: 360, midday: 480, pm_peak: 360, evening: 720}
  - line_id: "C1"                 # HF, comparte S03..S10
    ...
  - line_id: "170"                # LF (headway 20 min), comparte S01..S08
    ...
control_points: [S04, S08]        # paradas donde se puede retener
hf_max_headway_s: 600             # headway <= esto -> HF (c_vh); más -> LF (OTP)
synthetic: true
```

- Una terna (línea, sentido, tramo) existe si la línea sirve ≥ 3 paradas del tramo.
- Parámetros del simulador (tiempos de enlace, demanda, dwell, predictor) en `config/sim/c19.yaml` (WP2).
- Nombres y coordenadas de paradas: los de un tramo real de la línea 19 si el GTFS de EMT es accesible al escribir el YAML; si no, inventados y marcados `synthetic: true`. No hay parser de GTFS en el sistema.

## 2. Horario (`emtra_common/timetable.py`)

Sustituye al GTFS. `build_timetable(corridor, sim_params) -> DataFrame` determinista:

`line_id, direction_id, trip_id, stop_id, stop_sequence, sched_arrival` (UTC).

Salidas en cabecera cada `headway_s` del grupo de banda; `sched_arrival` aguas abajo = salida + suma de medias de enlace + dwell medio. Lo usan el simulador (salidas), el monitor (h_sch, OTP, HF/LF) y la tabla `schedule` de FogStore.

## 3. Registro crudo de instantánea (mundo → Ingestor)

Un JSON por parada y sondeo. No lleva sobre. Modelo `contracts/raw.py: RawSnapshot`.

```json
{"stop_id": "S04", "polled_at": "2026-11-16T07:00:30Z", "collector_id": "sim-<run_id>",
 "stopset_version": 1,
 "arrivals": [{"line_id": "19", "direction_id": 0, "bus_id": "B19-007", "destination": "...",
               "time_left_s": 245, "distance_m": 900}]}
```

- Como la API de EMT: por parada, las próximas llegadas de **todas** las líneas, a lo sumo 2 buses por línea, con `time_left_s ≤ 1800`.
- Un sondeo fallido = no hay registro de esa parada en ese instante.
- Batch: `runs/<run_id>/raw/snapshots.jsonl.gz`, ordenado por `polled_at`. En vivo: MQTT `emtra/v1/sim/raw/{stop_id}`.

## 4. Sobre y payloads (`contracts/envelope.py`, `contracts/payloads.py`)

```python
class Envelope(BaseModel, Generic[P]):
    schema_name: str             # p.ej. "emtra.arrival"
    schema_version: int = 1
    msg_id: UUID                 # determinista: uuid5(NAMESPACE, schema_name + clave natural)
    event_time: AwareDatetime    # tiempo de dominio
    produced_at: AwareDatetime   # solo para latencia
    producer: str
    correlation_id: UUID | None = None   # = alert_id en todo el ciclo de una alerta
    payload: P
```

Entrega al menos una vez: todos los consumidores deduplican por `msg_id`. Payloads pydantic v2 con `extra="forbid"`.

| schema_name | Campos | event_time | Clave natural (msg_id) | Productor |
|---|---|---|---|---|
| `emtra.snapshot` | campos de `RawSnapshot` | `polled_at` | stop_id, polled_at | Ingestor |
| `emtra.arrival` | `line_id, direction_id, stop_id, stop_sequence, bus_id, arrival_time, uncertainty_s, last_seen_at, first_missing_at` | `arrival_time` | stop_id, line_id, bus_id, last_seen_at | Ingestor |
| `emtra.never_fulfilled` | `line_id, direction_id, stop_id, bus_id, last_seen_at, last_time_left_s` | `last_seen_at` | stop_id, line_id, bus_id, last_seen_at | Ingestor |
| `emtra.watermark` | `watermark` (mínimo, sobre las paradas, del último `polled_at` procesado) | `watermark` | watermark | Ingestor |
| `emtra.band_status` | `line_id, direction_id, segment_id, band_start, indicator: "cvh"\|"otp", value: float\|None, n_obs, status: "inside"\|"warning"\|"outside"\|"insufficient_data", los: "A".."F"\|None` | fin de banda | line, dir, segment, band_start | Monitor |
| `emtra.alert` | `alert_id, line_id, direction_id, segment_id, state: "open"\|"closed", level: "warning"\|"action_required", triggers: list["ewma"\|"cusum"\|"blocked_segment"], distance_to_range, ewma_w, cusum_s, opened_at, updated_at, closed_at\|None` | `updated_at` | alert_id, updated_at | Monitor |
| `emtra.recommendation_request` | `alert_id, line_id, direction_id, segment_id, budget_s, state: CorridorState` | banda que la dispara | alert_id, event_time | Monitor |
| `emtra.recommendation` | `recommendation_id, alert_id, fallback: bool, runtime_s, replications, candidates: list[Candidate]` | el de la petición | recommendation_id | ActionPlanner / Monitor (respaldo) |
| `emtra.decision` | `decision_id, alert_id, recommendation_id, choice: int\|"declined"` (rank), `action, params` (copiados del candidato; `no_action` si declined), `operator_id, decided_at` | `decided_at` | recommendation_id | OperatorAPI |
| `emtra.recovery` | `alert_id, decision_id, status: "recovered"\|"not_recovered_within_horizon", recovered_at\|None, recovery_time_s\|None, peak_deviation, damping_sim_mean\|None, damping_sim_ci95\|None` | `recovered_at` o fin de horizonte | decision_id | ActionPlanner |
| `emtra.calibration` | `version, valid_from, alpha, cells: list[{line_id\|None, horizon_band, band_group\|None, q_lo_s, q_hi_s, n}]` | `valid_from` | version | Analytics |

```python
class CorridorState:      # lo que el fog sabe; la perturbación va implícita en las posiciones
    as_of: AwareDatetime
    corridor_id: str
    buses: list[BusState] # bus_id, line_id, direction_id, last_stop_seq (0 = aún no ha entrado),
                          # last_stop_passed_at | None, eta_next_s | None

class Candidate:
    rank: int
    action: Literal["hold", "no_action"]
    params: dict                       # hold: {"stop_id":..., "line_id":..., "alpha":..., "hold_max_s":...}
    ewt_acc_mean_pax_min: float | None # None en todas las métricas si fallback=true
    ewt_acc_ci95: tuple[float, float] | None
    recovery_time_mean_s: float | None
    peak_deviation_mean: float | None
    damping_mean: float | None
    damping_ci95: tuple[float, float] | None
    onboard_delay_pax_min: float | None
    pros: list[str]
    cons: list[str]
```

Solo diseño (modelo pydantic sí, sin productor ni topic activo): `emtra.passenger_counts`, `emtra.config`.

## 5. Topics

### MQTT (prefijo `emtra/v1/`, QoS 1, sesiones persistentes)

| Topic | Contenido | Productor | Consumidores | Retenido | → Kafka |
|---|---|---|---|---|---|
| `sim/raw/{stop_id}` | RawSnapshot (sin sobre) | World | Ingestor | no | — |
| `fog/snapshots/{stop_id}` | snapshot | Ingestor | Bridge | no | `emtra.snapshots.v1` |
| `fog/arrivals/{line_id}/{direction_id}` | arrival | Ingestor | Monitor, Bridge | no | `emtra.arrivals.v1` |
| `fog/never_fulfilled/{line_id}/{direction_id}` | never_fulfilled | Ingestor | Bridge | no | `emtra.never_fulfilled.v1` |
| `fog/watermark` | watermark | Ingestor | Monitor | no | — |
| `fog/status/{line_id}/{direction_id}/{segment_id}` | band_status | Monitor | Bridge, PassengerInfo | **sí** | `emtra.status.v1` |
| `fog/alerts/{line_id}/{direction_id}` | alert | Monitor | Bridge | no | `emtra.alerts.v1` |
| `fog/rec_requests/{alert_id}` | recommendation_request | Monitor | Bridge | no | `emtra.rec_requests.v1` |
| `fog/recommendations/{alert_id}` | recommendation | Bridge (desde Kafka) / Monitor (respaldo) | Monitor | no | ← `emtra.recommendations.v1` |
| `fog/decisions/{alert_id}` | decision | OperatorAPI | Monitor, Bridge, World | no | `emtra.decisions.v1` |
| `fog/recoveries/{alert_id}` | recovery | Bridge (desde Kafka) | Monitor | no | ← `emtra.recoveries.v1` |
| `calibration/current` | calibration | Bridge (desde Kafka) | PassengerInfo | **sí** | ← `emtra.calibration.v1` |

### Kafka (1 partición por topic; clave entre paréntesis)

fog→cloud: `emtra.snapshots.v1` (stop_id), `emtra.arrivals.v1`, `emtra.never_fulfilled.v1`, `emtra.status.v1`, `emtra.alerts.v1` (line_id:direction_id), `emtra.rec_requests.v1`, `emtra.decisions.v1` (alert_id).
cloud→fog: `emtra.recommendations.v1`, `emtra.recoveries.v1` (alert_id), `emtra.calibration.v1` ("current").

`contracts/topics.yaml` contiene estas dos tablas; el puente genera sus rutas de ahí.

## 6. FogStore (`contracts/sql/fogstore/NNN_*.sql`, TimescaleDB)

| Tabla | Contenido | Escribe |
|---|---|---|
| `stops` | stop_id, name, lat, lon, stop_sequence, segment_id | carga inicial |
| `schedule` | horario de §2 | carga inicial |
| `estimates` (hypertable `polled_at`) | polled_at, stop_id, line_id, direction_id, bus_id, time_left_s, distance_m; `UNIQUE (stop_id, line_id, bus_id, polled_at)` (clave natural; incluye la columna de partición, y `direction_id` es función de `line_id`). El Ingestor inserta con `ON CONFLICT DO NOTHING` | Ingestor |
| `arrivals` (hypertable `arrival_time`) | payload + `msg_id`; `UNIQUE (msg_id, arrival_time)` (una hypertable exige la columna de partición en el índice único; `arrival_time` es función de la clave natural, así que deduplica igual) | Ingestor |
| `never_fulfilled` | payload + msg_id | Ingestor |
| `band_status` | payload; PK (line_id, direction_id, segment_id, band_start) | Monitor |
| `alerts` | payload; PK alert_id; índice único parcial `WHERE state='open'` sobre (line_id, direction_id, segment_id) | Monitor |
| `recommendations` (candidates JSONB), `decisions`, `recoveries` | payloads | Monitor |
| `calibration_cells` | celdas de la calibración vigente | PassengerInfo |
| `service_health` | service PK, status, updated_at, metrics JSONB | cada servicio (latido cada 10 s, reloj de pared permitido) |

Vistas para Grafana: `v_line_status_latest`, `v_open_alerts`, `v_alert_timeline` (alerta → recomendación → decisión → recuperación por `alert_id`).

Carga inicial: `python -m emtra_common.reference load` (stops + schedule), la ejecuta `make up`.

Rol `grafana_ro` (migración `006_grafana_readonly.sql`): solo `SELECT` sobre todas las tablas y vistas; lo usa el datasource de Grafana.

## 7. Módulos compartidos (`emtra_common/`)

| Módulo | API |
|---|---|
| `clock.py` | `Clock` (protocolo `now()`), `SystemClock`, `ManualClock` |
| `rng.py` | `rng(root_seed: int, stream: str, *key) -> np.random.Generator`. Implementación: `SeedSequence([root_seed, *words])`, `words` = 4 enteros de 32 bits de `blake2b(repr((stream, *key)))`. Mismo resultado en cualquier proceso |
| `bands.py` | (límites y LOS en `config/bands.yaml`) `band_start(ts)`, `band_end(ts)`, `los_status(los)` → inside/warning/outside, `band_group(ts)`, `horizon_band(tau_s)` → `"0-3"\|"3-6"\|"6-12"\|"12-30"\|None`, `los_cvh(x)`, `los_otp(x)` |
| `corridor.py` | modelo y carga de §1; `segment_of(stop_id)`, `lines_in_segment(segment_id)` |
| `timetable.py` | §2; `scheduled_headway(line, stop, band_start)`, `line_type(line, band_group)` → `"HF"\|"LF"` (ambas con `corridor=None` opcional = c19). Llegada en la parada k-ésima servida = salida + k·(enlace + dwell) |
| `config.py` | carga YAML → pydantic |
| `holding.py` | regla de retención (WP4): `hold_s = clip(alpha * (H - h_fwd), 0, hold_max_s)` |
| `mqtt.py`, `db.py`, `health.py` | ayudas finas de conexión y latido |

Niveles de servicio (Tabla 12 del H3), límites cerrados por arriba:

| LOS | c_vh | OTP | Estado |
|---|---|---|---|
| A | ≤ 0,21 | ≥ 95 % | inside |
| B | ≤ 0,30 | ≥ 90 % | inside |
| C | < 0,40 | ≥ 85 % | warning |
| D | < 0,53 | ≥ 80 % | outside |
| E | < 0,75 | ≥ 75 % | outside |
| F | ≥ 0,75 | < 75 % | outside |

OTP: una llegada es puntual si −`otp_early_tolerance_s` ≤ δ ≤ 300 s, con δ = llegada − horario (`config/reliability.yaml`, tolerancia 60 s; DEVIATIONS 24). El resto de llegadas, incluidas las emparejadas con un adelanto mayor, no son puntuales.

## 8. Salidas de una ejecución (`runs/<run_id>/`)

| Fichero | Columnas | Escribe |
|---|---|---|
| `manifest.json` | root_seed, hash de config, git sha, modo, fallos | sim |
| `raw/snapshots.jsonl.gz` | §3 | sim |
| `timetable.parquet` | §2 | sim |
| `truth_arrivals.parquet` | line_id, direction_id, trip_id, bus_id, stop_id, stop_sequence, arrival_time, departure_time, load_after, hold_s | sim |
| `passengers_agg.parquet` | stop_id, line_id, band_start, n, wait_sum_s, sched_wait_sum_s | sim |
| `faults.parquet` | fault_id, type, severity, line_id, direction_id, segment_id, t0, t_end | sim |
| `estimates.parquet`, `arrivals.parquet`, `never_fulfilled.parquet`, `watermarks.parquet` | payloads de §4 | Ingestor batch |
| `band_status.parquet`, `alerts.parquet` | payloads de §4 | Monitor batch |

## 9. Modo batch

```python
# services/ingestor/batch.py
def run_batch(run_dir: Path) -> None            # lee raw/, escribe estimates/arrivals/never_fulfilled/watermarks
# services/reliability_monitor/batch.py
def run_batch(run_dir: Path, baseline: Path) -> None   # lee arrivals + watermarks + timetable, escribe band_status/alerts
```

Misma lógica (`core`) que el servicio en vivo. Un test de integración por servicio comprueba que vivo y batch dan el mismo resultado sobre la misma ejecución.

## 10. Semillas (`config/data_split.yaml`)

| Uso | root_seed |
|---|---|
| Línea base de detectores (20 días limpios) | 1000–1019 |
| Calibración del intervalo F12 | 2000–2019 |
| Validación / experimentos | 3000 en adelante |
| Demo | 42 |

Ningún parámetro del sistema se ajusta con semillas de validación.
