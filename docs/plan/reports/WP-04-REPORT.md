# WP-04 — REPORT

## Hecho
- `services/reliability_monitor/core/` (puro, sin paho ni psycopg):
  - `bands.py`: `BandBuffer`. Cierra la banda con `watermark ≥ fin + allowed_lateness_s`; las llegadas de una banda ya cerrada suman a `late_arrivals` y no recalculan nada.
  - `indicators.py`: c_vh (HF) y OTP (LF) por terna y banda; `blocked`, estado, LOS y `distance_to_range`.
  - `detectors.py`: `Ewma`, `Cusum`, `Shewhart` (este último solo para K8) con `update(z)` y `reset()`.
  - `alerts.py`: `TernAlerts`, ciclo de vida de una terna. `alert_id` = uuid5(línea, sentido, tramo, `opened_at`).
  - `monitor.py`: `Monitor.on_arrival` / `on_watermark` → `BandStatus` y `Alert`.
- `config.py` + `config/reliability.yaml`: todos los umbrales. `emtra_common/bands.py`: `range_limit()` (borde del rango *inside*, sale de `bands.yaml`; no cambia ningún contrato).
- `baseline.py` y `config/baselines/c19.yaml` (25 celdas, **commiteado**). `tune_detectors.py` escribe `L` y `h` en `config/reliability.yaml`.
- `batch.py`: `run_batch(run_dir, baseline)` y `replay(...)`, que usan el mismo core que el servicio. `__main__.py`: servicio en vivo (suscrito a `fog/arrivals/#` y `fog/watermark`; publica `fog/status/...` retenido y `fog/alerts/...`; persiste `band_status` y `alerts`; latido en `service_health`).
- Tests: `tests/acceptance/wp04/` (13 unitarios o de simulador, 1 de integración), con los nombres del spec. Los tests se escribieron antes (commit en rojo).
- **Decisiones de spec posteriores (autorizadas por el humano):**
  1. `test_ewma_detects_step`: escalón de +3σ detectado en ≤ 6 bandas, ≥ 95/100 (antes +2σ en ≤ 4). `arl0_target` sigue en 2000. Actualizado `WP-04-monitor.md`; `KPIS.md` no citaba el valor.
  2. OTP con tolerancia de adelanto: puntual si −`otp_early_tolerance_s` ≤ δ ≤ 300 s, con `otp_early_tolerance_s: 60` en `config/reliability.yaml`. Actualizados `INTERFACES.md` (§7), el glosario de `AGENTS.md`, `WP-04-monitor.md` y `DEVIATIONS.md` (fila 24). Línea base y L/h regenerados con la nueva definición. En `test_otp_matching` el adelanto de la 4.ª llegada pasa de −60 s a −(tolerancia+1) s y se añade una 5.ª en −tolerancia (cuenta); el OTP esperado sigue siendo 3/5.
  3. `faults_it.yaml` (integración): la avería de la línea 19 a las 06:20 ya no produce alertas con los umbrales nuevos (L y h subieron, ver más abajo), y el test exige alertas para comparar. Ahora usa `blocked_segment` alta en el tramo 1 a las 06:05, que sí las produce (3–6 mensajes de alerta en esa hora). No cambia ningún umbral ni aserción.

## No hecho / en rojo (con motivo)
`make lint` verde. `make accept WP=04`: **13 de 14 en verde, 1 en rojo** (`test_breakdown_high_detected`). Los umbrales de los tests no se han tocado.

### `test_breakdown_high_detected` — 1/10 (umbral ≥ 8)
Con la línea base y los umbrales del spec solo la semilla 3000 produce alerta. Causa medida: una avería de un bus deja un hueco de ~2 headways en 8 paradas aguas abajo, pero la dispersión natural del corredor sin retenciones ya es alta aguas abajo (línea 19, tramo 2, am_peak: c_vh = 0,364 ± 0,177 en día limpio). En las semillas 3001 y 3002 el c_vh del tramo 2 tras el fallo (0,16–0,95) da z entre −1,1 y +3,3 y los detectores no saltan. El indicador está bien medido (K4); falta señal frente al ruido. El test no se ha cambiado, a la espera de decisión.

## Mediciones pedidas (solo informativas)
Fallos de severidad alta a las 08:00, semillas 3000–3009, 4 h de simulación, línea base y tuning de arriba (con el cambio de OTP). Los fallos con línea van sobre la línea 19; `slowdown` y `blocked_segment` no tienen línea y van sobre el tramo 1. "Atribuible" sigue el emparejamiento de KPIS.md: alerta `action_required` (primera emisión con ese nivel) con `|tramo_alerta − tramo_fallo| ≤ 1`, dentro de [t₀, t₀ + 60 min] con t₀ el instante real del fallo (`faults.parquet`) y, si el fallo tiene línea, de esa línea. Retardo = (primer `action_required` − t₀) / 15 min, mediana sobre las ejecuciones detectadas. "Falsas" = alertas `action_required` distintas en 5 días limpios (semillas 3000–3004).

| fallo | `arl0_target` | L / h | ejecuciones con `action_required` atribuible | cualquier alerta atribuible | retardo mediano (bandas) | `action_required` en 5 días limpios |
|---|---|---|---|---|---|---|
| breakdown alta (línea 19) | 2000 | 4,69 / 8,87 | 1/10 | 1/10 | 2,2 | 1 |
| slowdown alta (tramo 1) | 2000 | 4,69 / 8,87 | **9/10** | 9/10 | 3,0 | 1 |
| dispatch_delay alta (línea 19) | 2000 | 4,69 / 8,87 | 0/10 | 0/10 | — | 1 |
| blocked_segment alta (tramo 1) | 2000 | 4,69 / 8,87 | **10/10** | 10/10 | 3,0 | 1 |
| breakdown alta (línea 19) | 500 | 3,70 / 6,67 | 1/10 | 1/10 | 1,2 | 2 |
| slowdown alta (tramo 1) | 500 | 3,70 / 6,67 | 9/10 | 10/10 | 3,0 | 2 |
| dispatch_delay alta (línea 19) | 500 | 3,70 / 6,67 | 2/10 | 2/10 | 1,0 | 2 |
| blocked_segment alta (tramo 1) | 500 | 3,70 / 6,67 | 10/10 | 10/10 | 3,0 | 2 |

Lectura:
- Con `arl0_target = 2000` (el de config), `slowdown` y `blocked_segment` llegan a ≥ 8/10 con ≤ 1 falsa (la falsa es una alerta de la línea 19, tramo 2, en la franja evening del día 3001). **Por eso no se ha hecho la repetición con CV de enlace 0,15 (punto 4 de la petición).**
- Bajar a 500 no mejora el recall de `breakdown` (1/10) y duplica las falsas. Cada ARL₀ se re-tunea con el mismo bootstrap.
- `breakdown` y `dispatch_delay` (un solo bus alterado) no son detectables con este indicador y este ruido: los fallos que sí se detectan afectan a todos los buses del tramo.
- El retardo de 3 bandas es la mediana de la primera alerta; el test `test_breakdown_high_detected` mide solo el `breakdown`, así que sigue en rojo con los umbrales actuales.
- Efecto de la tolerancia de OTP en el tuning: el sesgo de los residuos z subió de 0,85 a 1,92 (el 1−OTP ahora es casi siempre 0 y a veces 0,25–1), así que para el mismo ARL₀ L pasó de 3,73 a 4,69 y h de 7,09 a 8,87. Los detectores de c_vh quedan algo menos sensibles que antes porque el bootstrap mezcla todas las celdas.

## OTP de la línea 170 en día limpio, con la tolerancia de 60 s
- Línea base (20 días, semillas 1000–1019): OTP medio por franja early 0,93; am_peak 0,97; midday 0,96; pm_peak 0,98; evening 0,92. Antes (definición literal) era 0,52–0,76.
- Días limpios 3000–3004 (346 bandas con valor de la línea 170): OTP medio **0,968** (ponderado por llegadas 0,963). LOS: A 316, D 10, E 1, F 19. Estado: 316 *inside*, 30 *outside*.

## Supuestos y decisiones tomadas
- **Sin las 18 h**: `Monitor` evalúa bandas desde la de la primera llegada recibida; no cierra bandas anteriores (evita `blocked_segment` falsos al arrancar a mitad de día). Las bandas finales sin cerrar quedan sin cerrar, igual en vivo y en batch.
- **c_vh**: el headway se asigna a la banda de la llegada posterior; para el primero de la banda se usa la última llegada de la banda anterior (estado entre bandas). `h_sch` = headway programado de la banda de la llegada posterior.
- **OTP, emparejamiento**: cada llegada, en orden temporal, toma el horario libre **más cercano** dentro de [−5, +30] min (no el más antiguo: con 20 min de headway y ventana de 35 min el más antiguo emparejaría con el bus anterior). Las llegadas sin horario cuentan en `n` y no son puntuales. El `n_obs` de LF es el número de llegadas. La ventana de emparejamiento no cambia con la tolerancia.
- **Alertas**: `opened_at`, `updated_at` y `closed_at` son el **fin de la banda** (tiempo de evento). Mientras está abierta se emite un `emtra.alert` por banda. `triggers` acumula los disparadores de toda la vida de la alerta. El nivel solo sube. Una banda `insufficient_data` reinicia la cuenta de bandas *inside* consecutivas. Cerrar no depende de que los detectores hayan dejado de dar alarma (el CUSUM tarda decenas de bandas en bajar); al cerrar, `reset()`.
- `blocked_segment`: 0 llegadas de la línea en todas las paradas del tramo y ≥ 2 programadas en la banda. `distance_to_range` vale 0 si no hay valor.
- `sigma_floor = 0,02` (config): evita dividir por una σ₀ ≈ 0. No se activa en ninguna celda de la línea base actual (mínimo σ₀ = 0,049 en c_vh; 0,070 en 1−OTP).
- Orden de eventos en batch: una llegada entra antes de toda marca de agua ≥ su `first_missing_at` (es el orden en que las emite el Ingestor).
- **Estado en memoria**: tras un reinicio el Monitor empieza vacío y, al arrancar, cierra las alertas que el proceso anterior dejó abiertas en `alerts` (el índice único parcial impediría abrir otra en la misma terna). Es la misma limitación que ya tiene el Ingestor.
- El servicio deduplica llegadas por `msg_id` (entrega al menos una vez).
- La línea base y el tuning usan semillas 1000–1019; los tests y las mediciones de arriba, 3000 en adelante. Ningún parámetro se ha ajustado con ellas: las tablas de ARL₀ distintos de 2000 son solo informativas y `config/reliability.yaml` queda con `arl0_target = 2000`.
- El tuning usa residuos z con cola derecha pesada, así que L y h son conservadores para una normal: con N(0,1) el ARL₀ medido es 822 133 (EWMA) y 44 750 (CUSUM) frente a 2000. Con el bootstrap salen 2047 y 2038. Con un escalón de +3σ sobre N(0,1): P(alarma ≤ 6 bandas) = 0,98 (EWMA) y 0,996 (CUSUM) con 4000 rachas.
- Dependencias nuevas: ninguna.

## Cómo reproducir (un comando) y cuánto tarda
`make accept WP=04` — ~4 min (unitarios y simulador ~15 s; `test_tuned_arl0` ~150 s; integración ~78 s, requiere `make up`). `make test` (sin Docker) ~165 s. **`test_tuned_arl0` se volvió lento** porque con L = 4,69 el ARL₀ sobre N(0,1) es 822 133 bandas y el test simula 2000 rachas de esa longitud (antes, con L = 3,73, ~13 000 y 5 s). Regenerar la línea base: `uv run python -m services.reliability_monitor.baseline` (~26 s). Re-tunear: `uv run python -m services.reliability_monitor.tune_detectors` (~30 s).

## Resultados medidos (tablas/figuras que sirven al documento)
**K4 (c_vh del sistema frente a c_vh con llegadas verdaderas)**, un día limpio (semilla 3000), 216 bandas de tramo HF con valor en ambos: MAE = **0,009** (p95 0,024). Objetivo ≤ 0,05. No depende de la tolerancia de OTP.

**Días limpios** (semillas 3000–3004, 355 bandas de terna cada uno, 7–14 `insufficient_data`): 1 alerta en total, `action_required`, línea 19 tramo 2 en la franja evening del día 3001 (OTP). Test: ≤ 1.

**Detectores**, ARL₀ por bootstrap de los 6860 residuos z de los 20 días de línea base:

| detector | parámetro | ARL₀ (bandas) | objetivo |
|---|---|---|---|
| EWMA | λ = 0,2; L = 4,69 | 2047 | ≥ 2000 |
| CUSUM | k = 0,5; h = 8,87 | 2038 | ≥ 2000 |

**Línea base μ₀, σ₀** (x = c_vh o 1 − OTP; 20 días, semillas 1000–1019):

| línea | tramo | grupo | indicador | μ₀ | σ₀ | n |
|---|---|---|---|---|---|---|
| 170 | 1 | early | 1−OTP | 0.072 | 0.174 | 73 |
| 170 | 1 | am_peak | 1−OTP | 0.026 | 0.107 | 232 |
| 170 | 1 | midday | 1−OTP | 0.036 | 0.120 | 454 |
| 170 | 1 | pm_peak | 1−OTP | 0.024 | 0.089 | 313 |
| 170 | 1 | evening | 1−OTP | 0.078 | 0.203 | 286 |
| 19 | 1 | early | c_vh | 0.095 | 0.051 | 60 |
| 19 | 1 | am_peak | c_vh | 0.239 | 0.105 | 240 |
| 19 | 1 | midday | c_vh | 0.139 | 0.065 | 480 |
| 19 | 1 | pm_peak | c_vh | 0.216 | 0.090 | 320 |
| 19 | 1 | evening | 1−OTP | 0.095 | 0.183 | 300 |
| 19 | 2 | early | c_vh | 0.117 | 0.093 | 49 |
| 19 | 2 | am_peak | c_vh | 0.364 | 0.177 | 240 |
| 19 | 2 | midday | c_vh | 0.212 | 0.109 | 480 |
| 19 | 2 | pm_peak | c_vh | 0.341 | 0.151 | 320 |
| 19 | 2 | evening | 1−OTP | 0.175 | 0.245 | 300 |
| C1 | 1 | early | c_vh | 0.092 | 0.049 | 37 |
| C1 | 1 | am_peak | c_vh | 0.153 | 0.084 | 239 |
| C1 | 1 | midday | c_vh | 0.108 | 0.058 | 461 |
| C1 | 1 | pm_peak | c_vh | 0.152 | 0.079 | 320 |
| C1 | 1 | evening | 1−OTP | 0.018 | 0.070 | 300 |
| C1 | 2 | early | c_vh | 0.119 | 0.074 | 43 |
| C1 | 2 | am_peak | c_vh | 0.200 | 0.118 | 235 |
| C1 | 2 | midday | c_vh | 0.140 | 0.086 | 458 |
| C1 | 2 | pm_peak | c_vh | 0.219 | 0.125 | 320 |
| C1 | 2 | evening | 1−OTP | 0.080 | 0.177 | 300 |

Nota: en el tramo 2 la línea 19 tiene c_vh medio 0,36 en am_peak (LOS C) sin ningún fallo: la dispersión de headways crece aguas abajo porque el simulador no retiene buses.

**Live = batch** (`test_live_equals_batch`, 1 h de mundo a 60× con un `blocked_segment` alto en el tramo 1): `band_status` idéntico y mismas alertas (id, estado, nivel, aperturas y cierres) en FogStore y en batch.

## Filas añadidas a DEVIATIONS.md
- 24: OTP puntual si −60 s ≤ retraso ≤ 300 s (antes 0 ≤ retraso ≤ 300 s).
