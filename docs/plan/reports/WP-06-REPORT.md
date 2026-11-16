# WP-06 — REPORT

## Hecho
- `experiments/`: `matching.py` (emparejamiento alerta↔fallo de KPIS.md), `runner.py` (escenario → ejecuciones; sim → ingestor → monitor; `ProcessPoolExecutor`; caché por hash), `kpis.py` (una función por KPI: `{value, ci, n}`), `report.py` (PASS/FAIL leído de `config/kpis.yaml`, sin lógica por KPI; Markdown + CSV + figura), `__main__.py` (orquesta E1 y E2), `scenarios/e1_normal.yaml`, `e2_detection.yaml`, `config/kpis.yaml`.
- `make experiments` (E1 + E2 completos) y `make experiments SMOKE=1` (2 días limpios + 6 fallos; sale en `experiments/reports/smoke/`, que no se versiona).
- Entregable: `experiments/reports/h4.md` (+ `kpis.csv`, `matrix.csv`, `recall_by_segment.csv`, `detection_delay.png`).
- **Petición extra — línea base, residuos y tuner con un solo comando:** `make baseline` corre `baseline` y `tune_detectors` (regenera `config/baselines/c19.yaml`, `c19_residuals.npy`, `c19_residuals.meta.yaml` y L, h, z_max en `config/reliability.yaml`).
- **Petición extra — el test falla si los residuos no corresponden a la línea base:** el tuner guarda en `c19_residuals.meta.yaml` el SHA-256 de `c19.yaml` y el del `.npy`. `tune_detectors.residuals_are_current()` los compara; `load_residuals()` lanza `StaleResidualsError` si no coinciden (así también falla `test_tuned_arl0` de WP4). Comprobado a mano: añadir una línea a `c19.yaml` pone en rojo `test_residuals_match_baseline`.
- Monitor: parámetro `shewhart=True` (solo para K8), `run_batch(..., shewhart=True)` escribe `*_shewhart.parquet`.

## No hecho / en rojo (con motivo)
Los 4 tests del spec y los 4 extra están en verde. En rojo están **KPIs medidos**, no tests (se informan tal cual, KPIS.md):
- **K3 (FAIL)**: 5 "nunca cumplidas" en 10 días limpios y las 5 tenían llegada verdadera (objetivo ≤ 1 %). Causa observada en el crudo (semilla 3006, S12, bus B19-104): el predictor limita la lista a 2 buses por línea y el ruido reordena las estimaciones; el bus sale de la lista un par de sondeos, el Ingestor lo declara nunca cumplido y el bus reaparece y llega. No se ha tocado el Ingestor (fuera del alcance de WP6; es un hallazgo de WP3).
- **K5 (FAIL)**: recall 0,44 frente a ≥ 0,80. Ver la explicación por tramo abajo.
- K1 se cumple, pero es 100 % por construcción en batch (DEVIATIONS 26).

## Supuestos y decisiones tomadas
- **Rama**: `wp-06-validation-v1` sale de `wp-05-dashboard-demo` porque WP5 aún no está mergeado a `main`.
- **Ubicaciones de los fallos de E2** (el spec solo dice "en am_peak"): 4 ubicaciones, una por semilla (3030–3033), t₀ 07:30/08:15/08:45/09:15; `breakdown`/`dispatch_delay` en líneas 19, C1, 170, 19; `slowdown`/`blocked_segment` en tramos 1, 2, 1, 2; `demand_surge` en S02, S09, S04, S07. Se fijaron antes de ver resultados y no se han cambiado después. **Consecuencia:** un `blocked_segment` en el tramo 2 no tiene efecto observable (DEVIATIONS 22: los buses retenidos están en la cabecera final, el terminal), y sale 0/4; en el tramo 1 sale 4/4. Eso hunde K5 por el diseño del escenario, no solo por el detector. Cambiar la ubicación ahora sería ajustar con las semillas de validación; la decisión de reubicar es del grupo.
- **Poblaciones**: K5, K6 y K8 sobre `slowdown` y `blocked_segment` de severidad media/alta (parámetros en `config/kpis.yaml`); `breakdown`, `dispatch_delay` y `demand_surge` se reportan en la matriz sin objetivo. K6 es la mediana solo de los fallos detectados (n = 7). K7 y K17 se calculan en los 20 días limpios (una semana = 5 días de servicio; 3 línea·sentido; 18 h/día); los FP de los días con fallo van solo en la matriz.
- **K8 a igual tasa de falsas alertas** (DEVIATIONS 25): el `z_max` de Shewhart se fija como L y h, por ARL₀ ≥ 2000 sobre los residuos de la línea base: pasó de 3,0 a 6,78. Los tres detectores quedan en ARL₀ ≈ 2000–2300 en el bootstrap. En el flujo real de E2 las falsas son 0,33 (EWMA+CUSUM) y 0,17 (Shewhart) por línea·sentido·semana: 4 y 2 alertas en 20 días, es decir, igualdad solo en el bootstrap y diferencia dentro del ruido.
- **Caché**: `runs/<run_id>/experiment.json` guarda un hash de semilla, fallos, configuración (sim, ingestor, reliability, línea base) y el código de `sim/`, `services/ingestor`, `services/reliability_monitor`, `emtra_common`, `contracts`. Si el código cambia, se recalcula.
- Procesos en modo `spawn` (el fork tras hilos de pandas/arrow avisa de posibles bloqueos).
- IC: Wilson (K1, K3, K5), bootstrap por ejecución con 1000 remuestreos y semilla fija (K4, K7, K17), bootstrap sobre fallos (K6, una ejecución por fallo). K2 (p95) y K8 sin IC.
- `make baseline` simula los 20 días dos veces (una en cada módulo, ~56 s en total) en vez de compartir el cálculo: se prefirió no reestructurar `baseline.py` y `tune_detectors.py`.
- La regeneración con el comando reproduce byte a byte `c19.yaml` y `c19_residuals.npy` que ya estaban en el repo (la simulación es determinista); solo aparecen el `.meta.yaml` (nuevo) y `z_max`.
- Dependencias nuevas: ninguna (`matplotlib` y `tabulate` ya estaban en el grupo dev).

## Cómo reproducir (un comando) y cuánto tarda
`make experiments` — E1 + E2 completos (90 días simulados): 22 s en frío en esta máquina (20 núcleos), ~10 s con caché. `make experiments SMOKE=1`: ~9 s (el test exige < 5 min). `make baseline`: ~56 s. `make lint test`: verde, 59 tests en 27 s. `make accept WP=06`: 8 tests, ~13 s.

## Resultados medidos (tablas/figuras que sirven al documento)
Semillas de validación: E1 3000–3009; E2 limpios 3010–3029, fallos 3030–3033. Git: ver el pie de `h4.md`.

| KPI | Valor | IC 95 % | n | Objetivo | |
|---|---|---|---|---|---|
| K1 entregadas al registro | 1,00 | [1, 1] | 632 216 | ≥ 0,99 | PASS (trivial en batch) |
| K2 p95 error de llegada derivada | 14,5 s | — | 30 902 | ≤ 15 s | PASS (margen 0,5 s) |
| K3 falsas "nunca cumplidas" | 5/5 | [0,57; 1] | 5 | ≤ 0,01 | **FAIL** |
| K4 MAE de c_vh | 0,0090 | [0,0089; 0,0092] | 2158 | ≤ 0,05 | PASS |
| K5 recall (slowdown, blocked; media/alta) | 0,44 | [0,23; 0,67] | 16 | ≥ 0,80 | **FAIL** |
| K6 retardo mediano | 30 min | [30; 45] | 7 | ≤ 30 min | PASS (solo detectados) |
| K7 falsas por línea·sentido·semana | 0,33 | [0,08; 0,67] | 20 días | ≤ 1 | PASS |
| K8 recall EWMA+CUSUM − Shewhart | +0,06 (0,44 vs 0,38) | — | 16 | ≥ 0 | PASS (una detección de diferencia) |
| K17 `action_required` por hora | 0,011 | [0,003; 0,022] | 20 días | ≤ 2 | PASS |

Matriz TP/FN/FP por tipo y severidad: `experiments/reports/h4.md` (4 fallos por celda). Resumen: `slowdown` alta 3/4, media 0/4, baja 0/4; `blocked_segment` alta 2/4, media 2/4, baja 0/4; `breakdown` 1/12; `dispatch_delay` 3/12; `demand_surge` 0/12; días limpios, 4 alertas.

Recall por tramo (población de K5): `blocked_segment` tramo 1 → 4/4, tramo 2 → 0/4; `slowdown` tramo 1 → 2/4, tramo 2 → 1/4.

> **For the document (EN):** On 20 clean days the detectors raised 4 alerts (0.33 per line·direction·week, 0.011 action-required alerts per service hour). Of the 16 medium/high segment-wide faults, 7 were matched (recall 0.44, 95 % CI 0.23–0.67; median delay 30 min). All 4 blocked-segment faults in segment 1 were detected and none in segment 2, where the simulator's blocking holds buses at the terminal and has no observable effect on arrivals. EWMA+CUSUM and a Shewhart chart tuned to the same in-control ARL0 (2000 bands) differ by one detection out of 16.

## Filas añadidas a DEVIATIONS.md
- 25: `z_max` de Shewhart ajustado por ARL₀ (3,0 → 6,78); la pasada Shewhart reutiliza el trigger `ewma` y escribe `*_shewhart.parquet` (INTERFACES §8).
- 26: K1 en batch es 100 % por construcción; la pérdida extremo a extremo se mide en E5.
