# WP4 — ReliabilityMonitor (F3 + F9)

**Hito:** H4 · **Depende de:** WP1, WP2, WP3

## Objetivo
Calcular el indicador de fiabilidad por banda de 15 min, clasificarlo, detectar la salida de rango con EWMA + CUSUM y gestionar el ciclo de vida de las alertas. La petición de recomendación y el respaldo (D5) se añaden en WP8.

## Diseño (`services/reliability_monitor/`)

### `core/bands.py` — cierre de banda por tiempo de evento
- Banda `[b, b + 15 min)`. Se cierra cuando llega un `Watermark ≥ b + 15 min + allowed_lateness_s` (default 150; el Monitor no lee las nunca cumplidas, así que no depende de `grace_s`).
- Llegadas posteriores al cierre de su banda: se cuentan en `late_arrivals` y no recalculan nada.

### `core/indicators.py` — F3
Por cada terna (línea, sentido, tramo) existente (INTERFACES §1) y banda cerrada:

**HF → c_vh.** En cada parada del tramo, headways entre llegadas consecutivas de la misma línea; el headway se asigna a la banda de la llegada posterior. Se juntan los headways de **todas las paradas del tramo**. `d_i = h_obs,i − h_sch,i` con `h_sch` de `emtra_common.timetable.scheduled_headway`. `c_vh = std(d, ddof=1) / mean(h_sch)`. Si `n < min_headways` (default 6) → `insufficient_data`.

**LF → OTP.** Emparejar cada llegada con un horario de la parada (voraz en orden temporal, cada horario una vez, ventana [−5, +30] min). OTP = cuota con −`otp_early_tolerance_s` ≤ δ ≤ 300 s (default de la tolerancia: 60 s; DEVIATIONS 24). Si `n < min_arrivals` (default 3) → `insufficient_data`.

Estado y LOS con `emtra_common.bands.los_*` (INTERFACES §7). `distance_to_range` = valor − límite del rango (c_vh) o límite − valor (OTP); positivo = fuera.

### `core/detectors.py` — F9
Serie por terna: `x_t` = c_vh o `1 − OTP`. `z_t = (x_t − μ₀) / σ₀`, con `μ₀, σ₀` por (línea, tramo, grupo de banda) de la línea base.

- `Detector` (protocolo): `update(z) -> bool`, `reset()`.
- `Ewma`: `w_t = λ z_t + (1 − λ) w_{t−1}`; alarma si `w_t > L·sqrt(λ / (2 − λ))`. Default λ = 0,2.
- `Cusum`: `S_t = max(0, S_{t−1} + z_t − k)`; alarma si `S_t > h`. Default k = 0,5.
- `Shewhart`: alarma si `z_t > 3`. Solo como comparación (K8).
- `insufficient_data` no actualiza los estadísticos.

**Tramo bloqueado:** banda cerrada con 0 llegadas de la línea en el tramo y ≥ 2 programadas → disparador `blocked_segment`.

### `baseline.py` y `tune_detectors.py`
- `python -m services.reliability_monitor.baseline` → corre los 20 días limpios de línea base (semillas de INTERFACES §10) en batch (sim → ingestor → indicadores) y escribe `config/baselines/c19.yaml` (μ₀, σ₀ por celda). **Se commitea**: la demo no lo recalcula.
- `tune_detectors.py`: elige `L` y `h` por Monte Carlo sobre los residuos `z` de la línea base (bootstrap) para que cada detector tenga ARL₀ ≥ `arl0_target` (default 2000 bandas: con 72 bandas/día, 2 tramos y 2 detectores, da del orden de una falsa alarma por línea y semana, que es K7). Escribe los valores en `config/reliability.yaml` y el ARL₀ obtenido en el REPORT.

### `core/alerts.py` — ciclo de vida
- Abrir si hay disparador y no hay alerta abierta en la terna; si la hay, actualizarla.
- Nivel `action_required` si (estado `outside` y alarma de detector) o `blocked_segment`; si no, `warning`.
- Cerrar tras `close_after_bands` (default 2) bandas consecutivas `inside`; al cerrar, `reset()` de los detectores de la terna.
- `alert_id` = uuid5 de (línea, sentido, tramo, `opened_at`).

### Adaptadores
- `batch.py`: `run_batch(run_dir, baseline)` (INTERFACES §9).
- `__main__.py`: suscrito a `fog/arrivals/#` y `fog/watermark`; publica `fog/status/...` (retenido) y `fog/alerts/...`; persiste `band_status` y `alerts`. Latido en `service_health`.

## Aceptación (`tests/acceptance/wp04/`)
```python
def test_cvh_known_values():            # headways iguales al programado -> 0 ; caso a mano con resultado exacto (ddof=1)
def test_cvh_pools_segment_stops():     # los headways de las 6 paradas entran en el mismo cálculo
def test_otp_matching():                # 5 programados, 5 observados con δ conocidos -> OTP exacto; llegada adelantada más allá de la tolerancia no cuenta
def test_band_closes_on_watermark():    # sin watermark no hay band_status; con él, exactamente uno por terna y banda
def test_insufficient_data():           # n < mínimo -> status insufficient_data, detectores sin actualizar
def test_ewma_detects_step():           # z ~ N(0,1) 200 bandas y luego N(3,1): alarma en >= 95 de 100 semillas, en <= 6 bandas (celdas c_vh) o <= 8 bandas (celdas OTP; L y h se calibran por indicador y el OTP, de cola pesada, tiene límites más altos)
def test_tuned_arl0():                  # con L y h de config, 2000 rachas bootstrap de los residuos z de la línea base (`config/baselines/c19_residuals.npy`, lo que calibra el tuner): ARL0 medio >= 0,85 x arl0_target (cada detector)
def test_single_open_alert():
def test_alert_closes_after_two_inside_bands():
def test_blocked_segment_trigger():
# con el simulador (batch, semillas de validación):
def test_cvh_vs_truth():                # día limpio: MAE(c_vh sistema, c_vh con truth_arrivals) <= 0,05            (K4)
def test_slowdown_high_detected():      # slowdown alta en el tramo 1 a las 08:00, semillas 3000-3009: action_required atribuible (tramo o adyacente, opened_at en [t0, t0+60 min]) en >= 8
def test_clean_days_quiet():            # 5 días limpios: <= 1 alerta action_required en total
# integración (make it):
def test_live_equals_batch():           # 1 h de mundo a 60x con un breakdown -> band_status y alerts en FogStore == batch
```

## Checklist
- [ ] Ningún umbral fuera de `config/reliability.yaml`.
- [ ] El core no importa paho ni psycopg.
- [ ] El REPORT trae μ₀/σ₀ por celda, L, h y ARL₀.
- [ ] Si `test_slowdown_high_detected` o `test_clean_days_quiet` no salen, se documenta el compromiso recall / falsas alertas; no se tocan los umbrales del test.
