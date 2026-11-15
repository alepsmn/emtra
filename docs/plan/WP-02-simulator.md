# WP2 — Simulador de corredor

**Hito:** H4 · **Depende de:** WP1 · **Pieza más delicada del proyecto: de ella salen los datos, la verdad y, en H5, el evaluador.**

## Objetivo
Simulador de eventos discretos (SimPy) del corredor `c19` que:
1. genera un día de servicio plausible con varias líneas compartiendo paradas;
2. emite instantáneas en el formato de INTERFACES §3;
3. guarda la verdad (INTERFACES §8);
4. inyecta fallos de un catálogo;
5. admite una política de control y un estado inicial (lo usa WP8).

## Módulos (`sim/`)

### `params.py` + `config/sim/c19.yaml`
Fijados a mano (ADR 0001), documentados en el YAML:
- **Tiempo de enlace:** lognormal por enlace y grupo de banda. Media 60–120 s, CV 0,20 (0,30 en punta).
- **Salida en cabecera:** horario de `emtra_common.timetable` + desviación Normal(μ = 20 s, σ = 45 s), truncada a ≥ −30 s.
- **Demanda:** Poisson λ(parada, línea, grupo de banda), escalada para que la carga máxima en punta ≈ 65 % de la capacidad. Fracción de bajada por parada fija por posición.
- **Dwell:** `d = d0 + b·B + a·A`, `d0 = 8 s`, `b = 2,5 s/pax`, `a = 1,5 s/pax` ([40] del H3).

### `engine.py`
- Procesos: `Bus` (recorre enlaces, pide bahía, dwell, consulta la política al salir) y generador de pasajeros por parada y línea.
- Parada = `simpy.Resource(capacity=bays)`.
- **Aleatoriedad indexada por entidad** (INTERFACES §7): marcha del trip T en el enlace k → `rng(seed, "run", T, k)`; pasajeros de la parada s y línea l → `rng(seed, "pax", s, l)`; salida del trip T → `rng(seed, "dispatch", T)`. Cambiar la política no altera los números de las entidades no afectadas.
- `Engine(corridor, params, seed, policy=NoAction(), faults=[], initial_state=None, start=None, horizon_s=None)`. Con `initial_state=None` arranca vacío al inicio del servicio. La carga desde un estado la implementa WP8; aquí solo existe el parámetro y la estructura no debe impedirlo (los buses se crean a partir de una lista de "trips pendientes + buses en ruta", no dentro de un bucle cerrado).

### `predictor.py`
El mundo emite una **predicción**, no la verdad: `time_left_s` = suma de medias de los enlaces restantes hasta la parada − tiempo ya transcurrido en el enlace actual (mín. 0), multiplicado por un sesgo configurable (default 0,95) y con ruido lognormal pequeño por (bus, sondeo). No mira el futuro. Como mucho 2 buses por línea y parada, con `time_left_s ≤ 1800`.

### `emitter.py`
Cada 30 s de tiempo simulado, un `RawSnapshot` por parada. Fallo de sondeo con probabilidad `p_poll_fail` (default 0,002). Destino: fichero (batch) o MQTT `emtra/v1/sim/raw/{stop_id}` (mundo).

### `faults.py`
| Tipo | Parámetros | Severidad baja / media / alta |
|---|---|---|
| `breakdown` | línea, primer bus que entra tras t₀ | retirado 10 min / 30 min / resto del día |
| `slowdown` | tramo, t₀ | ×1,3 15 min / ×1,6 30 min / ×2,2 45 min |
| `dispatch_delay` | línea, primer trip tras t₀ | +½ / +¾ / +1 headway |
| `demand_surge` | parada, t₀ | ×2 / ×3 / ×5 durante 20 min |
| `blocked_segment` | tramo, t₀ | ningún bus sale del tramo durante 10 / 20 / 30 min |

Cada fallo va a `faults.parquet` con su `segment_id` (el del tramo afectado, o el primero que recorre el bus afectado).

### `policy.py`
```python
class ControlPolicy(Protocol):
    def hold_seconds(self, bus: BusView, stop: StopView, now: float) -> float: ...
class NoAction: ...   # 0
```
`BusView` expone el headway con el bus anterior de su línea en esa parada. La política se puede cambiar por línea con la simulación en marcha (WP9).

### `run.py` y `world.py`
- Batch: `python -m sim.run --seed N --hours H [--faults faults.yaml] --out runs/<run_id>` → todos los ficheros de INTERFACES §8 que escribe el simulador.
- Mundo: `python -m sim.world --seed N --speed 60 [--faults ...]` → mismo modelo sobre `simpy.rt.RealtimeEnvironment(factor=1/speed, strict=False)`, publicando por MQTT y escribiendo también `runs/<run_id>/`.

## Aceptación (`tests/acceptance/wp02/`)
```python
def test_determinism():                 # misma semilla -> mismos parquet (comparación de contenido)
def test_crn_sync():                    # hold en la línea 19: los tiempos de marcha de C1 y 170 son idénticos a NoAction
def test_clean_day_is_plausible():      # día sin fallos, línea 19, am_peak: c_vh por tramo y banda (con truth_arrivals) con media en [0,10; 0,35]
def test_no_overtaking_blowup():        # día sin fallos: ningún headway verdadero de la 19 en punta > 3 x programado
def test_breakdown_creates_gap():       # breakdown alta: headway máximo en la parada siguiente >= 1,8 x programado
def test_dispatch_delay_creates_bunching():  # alta: algún headway de la 19 < 0,3 x programado aguas abajo
def test_snapshot_schema():             # cada registro emitido valida contra RawSnapshot; máx. 2 buses por línea y parada
def test_predictor_does_not_use_truth(): # error (llegada verdadera - anunciada) tiene dispersión > 0 y crece con el horizonte
def test_truth_files_complete():        # existen todos los ficheros de §8 con sus columnas
def test_batch_speed():                 # 3 h de corredor en batch en < 10 s
```

## Checklist
- [ ] Ningún generador aleatorio fuera de `emtra_common.rng`.
- [ ] El REPORT trae: c_vh medio por línea y grupo de banda en un día limpio, y una figura de trayectorias (tiempo × parada) de un `breakdown`.
- [ ] Si `test_clean_day_is_plausible` no sale con los parámetros iniciales, se ajustan los parámetros (no el test) y se documenta.
