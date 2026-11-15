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

## No hecho / en rojo (con motivo)
`make lint` verde. `make accept WP=04`: **12 de 14 en verde, 2 en rojo**. Los umbrales de los tests no se han tocado.

### 1. `test_ewma_detects_step` — 40/100 (umbral ≥ 95)
No se puede cumplir con `arl0_target = 2000`. Es una propiedad estadística, no de la implementación. Medido con 4000 rachas:

| detector (ARL₀ ≈ 2000) | escalón +2σ: P(alarma ≤ 4 bandas) | P(≤ 8 bandas) | escalón +3σ: P(≤ 4 bandas) |
|---|---|---|---|
| EWMA (λ=0,2; L=3,73) | 0,45 | 0,95 | 0,95 |
| CUSUM (k=0,5; h=7,09) | 0,40 | 0,97 | 0,95 |

El test pide detectar +2σ en ≤ 4 bandas con 95 %. A ARL₀ = 2000 eso se consigue con +3σ en 4 bandas, o con +2σ en 8 bandas. En el test salen 40 de 100 porque su generador `rng(3000, "wp04.step", i)` da algo menos que la P teórica 0,45 (es la misma P, 100 semillas). Para que pasara habría que bajar `arl0_target` a unos 50 (ver tabla de abajo), lo cual destruye `test_clean_days_quiet`.

### 2. `test_breakdown_high_detected` — 1/10 (umbral ≥ 8)
Con la línea base y los umbrales del spec, solo la semilla 3000 produce alerta. Causa medida: una avería de un bus deja un hueco de ~2 headways en 8 paradas aguas abajo, pero la dispersión natural del corredor sin retenciones ya es alta aguas abajo. La línea base del tramo 2 de la línea 19 en am_peak es c_vh = 0,364 ± 0,177. En las semillas 3001 y 3002 el c_vh del tramo 2 tras el fallo (0,16–0,95) cae a z entre −1,1 y +3,3 y los detectores, con ARL₀ = 2000, no saltan. El indicador está bien medido (K4, abajo); lo que falta es señal frente al ruido.

Compromiso recall / falsas alertas (medido: 10 semillas de avería 3000–3009 y 5 días limpios 3000–3004; los umbrales se re-tunearon con el mismo bootstrap para cada ARL₀; **solo informativo, no se ha cambiado `config/reliability.yaml`**):

| `arl0_target` | L | h | averías detectadas | `action_required` en 5 días limpios | alertas totales en 5 días limpios |
|---|---|---|---|---|---|
| **2000 (config)** | 3,74 | 7,06 | **1/10** | **0** | 1 |
| 500 | 3,08 | 5,35 | 1/10 | 5 | 7 |
| 200 | 2,62 | 4,21 | 4/10 | 12 | 16 |
| 100 | 2,22 | 3,39 | 3/10 | 20 | 28 |
| 50 | 1,77 | 2,62 | 6/10 | 28 | 42 |

Ningún ARL₀ da a la vez ≥ 8/10 y ≤ 1 falsa. `test_clean_days_quiet` (verde) y `test_breakdown_high_detected` (rojo) son incompatibles con este indicador en este corredor. Decisión del humano: dejar `arl0_target = 2000` (K7 ≈ 1 falsa/semana, recall de averías bajo; el demo debe usar un fallo más grande, p. ej. `blocked_segment` o `slowdown` alta) o bajarlo con una fila en `DEVIATIONS.md`.

## Supuestos y decisiones tomadas
- **Sin las 18 h**: `Monitor` evalúa bandas desde la de la primera llegada recibida; no cierra bandas anteriores (evita `blocked_segment` falsos al arrancar a mitad de día). Las bandas finales sin cerrar quedan sin cerrar, igual en vivo y en batch.
- **c_vh**: el headway se asigna a la banda de la llegada posterior; para el primero de la banda se usa la última llegada de la banda anterior (estado entre bandas). `h_sch` = headway programado de la banda de la llegada posterior.
- **OTP, emparejamiento**: cada llegada, en orden temporal, toma el horario libre **más cercano** dentro de [−5, +30] min (no el más antiguo: con 20 min de headway y ventana de 35 min el más antiguo emparejaría con el bus anterior). Las llegadas sin horario cuentan en `n` y no son puntuales. El `n_obs` de LF es el número de llegadas.
- **Alertas**: `opened_at`, `updated_at` y `closed_at` son el **fin de la banda** (tiempo de evento). Mientras está abierta se emite un `emtra.alert` por banda. `triggers` acumula los disparadores de toda la vida de la alerta. El nivel solo sube. Una banda `insufficient_data` reinicia la cuenta de bandas *inside* consecutivas. Cerrar no depende de que los detectores hayan dejado de dar alarma (el CUSUM tarda decenas de bandas en bajar); al cerrar, `reset()`.
- `blocked_segment`: 0 llegadas de la línea en todas las paradas del tramo y ≥ 2 programadas en la banda. `distance_to_range` vale 0 si no hay valor.
- `sigma_floor = 0,02` (config): evita dividir por una σ₀ ≈ 0. No se activa en ninguna celda de la línea base actual.
- Orden de eventos en batch: una llegada entra antes de toda marca de agua ≥ su `first_missing_at` (es el orden en que las emite el Ingestor).
- **Estado en memoria**: tras un reinicio el Monitor empieza vacío y, al arrancar, cierra las alertas que el proceso anterior dejó abiertas en `alerts` (el índice único parcial impediría abrir otra en la misma terna). Es la misma limitación que ya tiene el Ingestor.
- El servicio deduplica llegadas por `msg_id` (entrega al menos una vez).
- La línea base y el tuning usan semillas 1000–1019; los tests, 3000 en adelante.
- **Hallazgo sobre el OTP (para el documento):** con la definición literal 0 ≤ δ ≤ 5 min, cualquier adelanto, aunque sea de 2 s, no es puntual. El simulador reparte δ alrededor de 0 (σ de 30–60 s en el tramo 1), así que **la mitad de las llegadas cuentan como no puntuales en un día limpio**: OTP medio 52–76 % (LOS F) en la línea 170 y 52 % de la 19 en la franja evening. Los estados LF de la demo saldrán casi siempre *outside*. Los detectores no se ven afectados (usan μ₀ y σ₀ por celda), pero el semáforo LF no informa. Sugerencia para el humano: una tolerancia de adelanto (p. ej. −1 min) en la definición, con fila en `DEVIATIONS.md`; no se ha hecho porque cambia la definición del glosario.
- El tuning se hace con los residuos z de la línea base (sesgo 0,85, cola derecha pesada), así que L y h son conservadores para una normal: con N(0,1) el ARL₀ medido es 13 010 (EWMA) y 7 534 (CUSUM) frente a 2000. Con el bootstrap salen 2031 y 2056.
- Dependencias nuevas: ninguna.

## Cómo reproducir (un comando) y cuánto tarda
`make accept WP=04` — 92 s (unitarios y simulador ~15 s; integración ~78 s, requiere `make up`). `make test` (sin Docker) ~18 s. Regenerar la línea base: `uv run python -m services.reliability_monitor.baseline` (~26 s). Re-tunear: `uv run python -m services.reliability_monitor.tune_detectors` (~30 s).

## Resultados medidos (tablas/figuras que sirven al documento)
**K4 (c_vh del sistema frente a c_vh con llegadas verdaderas)**, un día limpio (semilla 3000), 216 bandas de tramo HF con valor en ambos: MAE = **0,009** (p95 0,024). Objetivo ≤ 0,05.

**Días limpios** (semillas 3000–3004, 355 bandas de terna cada uno, 7–14 `insufficient_data`): 1 alerta en total (línea C1, nivel `warning`), 0 `action_required`.

**Detectores**, ARL₀ por bootstrap de los 6860 residuos z de los 20 días de línea base:

| detector | parámetro | ARL₀ (bandas) | objetivo |
|---|---|---|---|
| EWMA | λ = 0,2; L = 3,73 | 2031 | ≥ 2000 |
| CUSUM | k = 0,5; h = 7,09 | 2056 | ≥ 2000 |

**Línea base μ₀, σ₀** (x = c_vh o 1 − OTP; 20 días, semillas 1000–1019):

| línea | tramo | grupo | indicador | μ₀ | σ₀ | n |
|---|---|---|---|---|---|---|
| 170 | 1 | early | 1−OTP | 0.474 | 0.425 | 73 |
| 170 | 1 | am_peak | 1−OTP | 0.244 | 0.321 | 232 |
| 170 | 1 | midday | 1−OTP | 0.369 | 0.383 | 454 |
| 170 | 1 | pm_peak | 1−OTP | 0.270 | 0.323 | 313 |
| 170 | 1 | evening | 1−OTP | 0.452 | 0.390 | 286 |
| 19 | 1 | early | c_vh | 0.095 | 0.051 | 60 |
| 19 | 1 | am_peak | c_vh | 0.239 | 0.105 | 240 |
| 19 | 1 | midday | c_vh | 0.139 | 0.065 | 480 |
| 19 | 1 | pm_peak | c_vh | 0.216 | 0.090 | 320 |
| 19 | 1 | evening | 1−OTP | 0.480 | 0.321 | 300 |
| 19 | 2 | early | c_vh | 0.117 | 0.093 | 49 |
| 19 | 2 | am_peak | c_vh | 0.364 | 0.177 | 240 |
| 19 | 2 | midday | c_vh | 0.212 | 0.109 | 480 |
| 19 | 2 | pm_peak | c_vh | 0.341 | 0.151 | 320 |
| 19 | 2 | evening | 1−OTP | 0.470 | 0.322 | 300 |
| C1 | 1 | early | c_vh | 0.092 | 0.049 | 37 |
| C1 | 1 | am_peak | c_vh | 0.153 | 0.084 | 239 |
| C1 | 1 | midday | c_vh | 0.108 | 0.058 | 461 |
| C1 | 1 | pm_peak | c_vh | 0.152 | 0.079 | 320 |
| C1 | 1 | evening | 1−OTP | 0.288 | 0.304 | 300 |
| C1 | 2 | early | c_vh | 0.119 | 0.074 | 43 |
| C1 | 2 | am_peak | c_vh | 0.200 | 0.118 | 235 |
| C1 | 2 | midday | c_vh | 0.140 | 0.086 | 458 |
| C1 | 2 | pm_peak | c_vh | 0.219 | 0.125 | 320 |
| C1 | 2 | evening | 1−OTP | 0.349 | 0.341 | 300 |

Nota: en el tramo 2 la línea 19 tiene c_vh medio 0,36 en am_peak (LOS C) sin ningún fallo: la dispersión de headways crece aguas abajo porque el simulador no retiene buses. Es la causa de la baja sensibilidad a una avería (punto 2 de "en rojo").

**Live = batch** (`test_live_equals_batch`, 1 h de mundo a 60× con una avería de la línea 19 a las 06:20): `band_status` idéntico y mismas alertas (id, estado, nivel, aperturas y cierres) en FogStore y en batch.

## Filas añadidas a DEVIATIONS.md
Ninguna. Si el humano decide cambiar `arl0_target` o la tolerancia de adelanto del OTP, hay que añadir fila.
