# WP-06 — REPORT

## Hecho
- `experiments/`: `matching.py` (emparejamiento alerta↔fallo de KPIS.md), `runner.py` (escenario → ejecuciones; sim → ingestor → monitor; `ProcessPoolExecutor`; caché por hash), `kpis.py` (una función por KPI: `{value, ci, n}`), `report.py` (PASS/FAIL leído de `config/kpis.yaml`, sin lógica por KPI; Markdown + CSV + figura), `__main__.py` (orquesta E1 y E2), `scenarios/e1_normal.yaml`, `e2_detection.yaml`, `config/kpis.yaml`.
- `make experiments` (E1 + E2 completos) y `make experiments SMOKE=1` (2 días limpios + 6 fallos; sale en `experiments/reports/smoke/`, que no se versiona).
- Entregable: `experiments/reports/h4.md` (+ `kpis.csv`, `matrix.csv`, `recall_by_segment.csv`, `detection_delay.png`).
- **Petición extra — línea base, residuos y tuner con un solo comando:** `make baseline` corre `baseline` y `tune_detectors` (regenera `config/baselines/c19.yaml`, `c19_residuals.npy`, `c19_residuals.meta.yaml` y L, h, z_max en `config/reliability.yaml`).
- **Petición extra — el test falla si los residuos no corresponden a la línea base:** el tuner guarda en `c19_residuals.meta.yaml` el SHA-256 de `c19.yaml` y el del `.npy`. `tune_detectors.residuals_are_current()` los compara; `load_residuals()` lanza `StaleResidualsError` si no coinciden (así también falla `test_tuned_arl0` de WP4). Comprobado a mano: añadir una línea a `c19.yaml` pone en rojo `test_residuals_match_baseline`.
- Monitor: parámetro `shewhart=True` (solo para K8), `run_batch(..., shewhart=True)` escribe `*_shewhart.parquet`.

- **Revisión posterior (dos peticiones):**
  1. **Gracia de `never_fulfilled` por sondeos.** `config/ingestor.yaml: never_fulfilled_grace_polls: 3`. Una estimación solo se confirma como nunca cumplida si el bus ha faltado en ese número de sondeos **consecutivos de esa parada** (un sondeo fallido no cuenta) **y** se cumple la gracia por tiempo que ya existía (`grace_s: 120`). Si reaparece, el seguimiento continúa. La regla del punto medio no se ha tocado. Tests nuevos en `tests/acceptance/wp03/test_wp03.py` (4, escritos antes en rojo): valor por defecto, reaparece y sigue, se confirma en el N-ésimo sondeo, un sondeo fallido no cuenta. K3 se mide ahora sobre E1 y E2 juntos (`experiments: [E1, E2]` en `config/kpis.yaml`; KPIS.md actualizado).
  2. **E2 desglosado sin reubicar nada**: tabla tipo × severidad × tramo × hora con detectadas/n, retardo mediano y efecto en la verdad (`experiments/reports/h4.md` y `breakdown.csv`). Para el efecto se añadió `experiments/scenarios/e2_controls.yaml`: las mismas semillas 3030–3033 sin fallo (4 ejecuciones; ningún KPI las lee, así que K7 y K17 siguen sobre 20 días).

## No hecho / en rojo (con motivo)
Los 4 tests del spec y los 4 extra están en verde. En rojo están **KPIs medidos**, no tests (se informan tal cual, KPIS.md):
- **K3 (FAIL, sin cambio tras la gracia)**: 250 de 270 nunca cumplidas (0,926; IC 0,89–0,95) en E1+E2 tenían llegada verdadera; antes y después de añadir `never_fulfilled_grace_polls` salen exactamente las mismas 270 y 250, y solo E1: 5 de 5. **La gracia de 3 sondeos no es vinculante**: `grace_s = 120` s con sondeos de 30 s ya exigía ≥ 4 sondeos ausentes, así que el contador solo actuaría con sondeos fallidos. Las ausencias reales son mucho más largas (`scripts/wp06_k3_gaps.py`): en días limpios, 11 nunca cumplidas, todas con llegada verdadera; 9 reaparecen en la lista de la parada tras una mediana de 5 sondeos (máximo 10) y 2 no reaparecen. Con fallos: `blocked_segment` 139 (120 reaparecen, mediana 6, máx. 17 sondeos; 19 no reaparecen), `breakdown` 87 (67 reaparecen, mediana 20, máx. 63; 20 no reaparecen y esas sí no tienen llegada verdadera), `dispatch_delay` 18, `slowdown` 9, `demand_surge` 6. Causas observadas: (a) en días limpios, el límite de 2 buses por línea en la lista saca de ella a un bus mientras otros dos están más cerca según la estimación, y vuelve cuando uno pasa (semilla 3006, S12, B19-104: 4 sondeos fuera); (b) con fallos, el bus de verdad desaparece de las listas durante la retención o la avería y luego llega. En (b) la definición del KPI (nunca cumplida con llegada verdadera) cuenta como falsa una emisión que el sistema acierta mientras dura el fallo. Valor de N con el que desaparecería (a): ≥ 11 sondeos (≈ 5,5 min), lo que retrasaría la detección de un bus realmente retirado; no se ha ajustado N con estos datos (semillas de validación) y se queda en 3, como se pidió.
- **Prueba de integración `test_live_equals_batch` (WP3), en rojo en este entorno**: falla igual con y sin mi cambio del Ingestor (comprobado con `git stash`). Causa observada: hay un Ingestor y un Monitor de `make demo-v1` vivos desde hace 11 h (PIDs 1716551 y 1716552) escribiendo en la misma BD y el mismo broker; el conjunto de `msg_id` en vivo trae elementos de más. No los he parado (son del demo). `make accept WP=03`: 17 pasan, 1 falla por esto.
- **K5 (FAIL)**: recall 0,44 frente a ≥ 0,80. Ver la explicación por tramo abajo.
- K1 se cumple, pero es 100 % por construcción en batch (DEVIATIONS 26).

## Supuestos y decisiones tomadas
- **Rama**: `wp-06-validation-v1` sale de `wp-05-dashboard-demo` porque WP5 aún no está mergeado a `main`.
- **Ubicaciones de los fallos de E2** (el spec solo dice "en am_peak"): 4 ubicaciones, una por semilla (3030–3033), t₀ 07:30/08:15/08:45/09:15; `breakdown`/`dispatch_delay` en líneas 19, C1, 170, 19; `slowdown`/`blocked_segment` en tramos 1, 2, 1, 2; `demand_surge` en S02, S09, S04, S07. Se fijaron antes de ver resultados y no se han cambiado después. **Consecuencia:** `blocked_segment` sale 0/4 en el tramo 2 y 4/4 en el tramo 1. El tramo 2 sí tiene efecto verdadero (Δc_vh +0,09 a +0,14 en alta, ver el desglose) y no se detecta; que el tramo 1 sea 4/4 y el 2 sea 0/4 pesa mucho en K5 y depende de la ubicación elegida. Cambiar la ubicación ahora sería ajustar con las semillas de validación; la decisión de reubicar es del grupo.
- **Tramo afectado, definición pedida**: el efecto en la verdad se mide en el `segment_id` del fallo (en `faults.parquet`); para `breakdown`/`dispatch_delay` es el tramo donde entra la línea. Cuenta solo bandas completas dentro de [t₀, t₀ + 1 h) (4 si t₀ está alineado; 3 si no, p. ej. `breakdown`), y las parejas (terna, banda) con valor en ambas ejecuciones.
- **Poblaciones**: K5, K6 y K8 sobre `slowdown` y `blocked_segment` de severidad media/alta (parámetros en `config/kpis.yaml`); `breakdown`, `dispatch_delay` y `demand_surge` se reportan en la matriz sin objetivo. K6 es la mediana solo de los fallos detectados (n = 7). K7 y K17 se calculan en los 20 días limpios (una semana = 5 días de servicio; 3 línea·sentido; 18 h/día); los FP de los días con fallo van solo en la matriz.
- **K8 a igual tasa de falsas alertas** (DEVIATIONS 25): el `z_max` de Shewhart se fija como L y h, por ARL₀ ≥ 2000 sobre los residuos de la línea base: pasó de 3,0 a 6,78. Los tres detectores quedan en ARL₀ ≈ 2000–2300 en el bootstrap. En el flujo real de E2 las falsas son 0,33 (EWMA+CUSUM) y 0,17 (Shewhart) por línea·sentido·semana: 4 y 2 alertas en 20 días, es decir, igualdad solo en el bootstrap y diferencia dentro del ruido.
- **Caché**: `runs/<run_id>/experiment.json` guarda un hash de semilla, fallos, configuración (sim, ingestor, reliability, línea base) y el código de `sim/`, `services/ingestor`, `services/reliability_monitor`, `emtra_common`, `contracts`. Si el código cambia, se recalcula.
- Procesos en modo `spawn` (el fork tras hilos de pandas/arrow avisa de posibles bloqueos).
- IC: Wilson (K1, K3, K5), bootstrap por ejecución con 1000 remuestreos y semilla fija (K4, K7, K17), bootstrap sobre fallos (K6, una ejecución por fallo). K2 (p95) y K8 sin IC.
- `make baseline` simula los 20 días dos veces (una en cada módulo, ~56 s en total) en vez de compartir el cálculo: se prefirió no reestructurar `baseline.py` y `tune_detectors.py`.
- La regeneración con el comando reproduce byte a byte `c19.yaml` y `c19_residuals.npy` que ya estaban en el repo (la simulación es determinista); solo aparecen el `.meta.yaml` (nuevo) y `z_max`.
- Dependencias nuevas: ninguna (`matplotlib` y `tabulate` ya estaban en el grupo dev).

## Cómo reproducir (un comando) y cuánto tarda
`make experiments` — E1 + E2 completos (90 días simulados): 22 s en frío en esta máquina (20 núcleos), ~10 s con caché. `make experiments SMOKE=1`: ~9 s (el test exige < 5 min). `make baseline`: ~56 s. `make lint test`: verde, 63 tests en 31 s. `make accept WP=06`: 8 tests, ~15 s. `make experiments` ahora incluye 4 ejecuciones de control y el desglose: 18–28 s. `make accept WP=03`: 17 de 18 (ver arriba).

## Resultados medidos (tablas/figuras que sirven al documento)
Semillas de validación: E1 3000–3009; E2 limpios 3010–3029, fallos 3030–3033. Git: ver el pie de `h4.md`.

| KPI | Valor | IC 95 % | n | Objetivo | |
|---|---|---|---|---|---|
| K1 entregadas al registro | 1,00 | [1, 1] | 632 216 | ≥ 0,99 | PASS (trivial en batch) |
| K2 p95 error de llegada derivada | 14,5 s | — | 30 902 | ≤ 15 s | PASS (margen 0,5 s) |
| K3 falsas "nunca cumplidas" (E1+E2) | 250/270 = 0,93 | [0,89; 0,95] | 270 | ≤ 0,01 | **FAIL** (solo E1: 5/5) |
| K4 MAE de c_vh | 0,0090 | [0,0089; 0,0092] | 2158 | ≤ 0,05 | PASS |
| K5 recall (slowdown, blocked; media/alta) | 0,44 | [0,23; 0,67] | 16 | ≥ 0,80 | **FAIL** |
| K6 retardo mediano | 30 min | [30; 45] | 7 | ≤ 30 min | PASS (solo detectados) |
| K7 falsas por línea·sentido·semana | 0,33 | [0,08; 0,67] | 20 días | ≤ 1 | PASS |
| K8 recall EWMA+CUSUM − Shewhart | +0,06 (0,44 vs 0,38) | — | 16 | ≥ 0 | PASS (una detección de diferencia) |
| K17 `action_required` por hora | 0,011 | [0,003; 0,022] | 20 días | ≤ 2 | PASS |

Matriz TP/FN/FP por tipo y severidad: `experiments/reports/h4.md` (4 fallos por celda). Resumen: `slowdown` alta 3/4, media 0/4, baja 0/4; `blocked_segment` alta 2/4, media 2/4, baja 0/4; `breakdown` 1/12; `dispatch_delay` 3/12; `demand_surge` 0/12; días limpios, 4 alertas.

Recall por tramo (población de K5): `blocked_segment` tramo 1 → 4/4, tramo 2 → 0/4; `slowdown` tramo 1 → 2/4, tramo 2 → 1/4.

### E2 desglosado (tipo × severidad × tramo × hora)
Tabla completa de 54 filas en `experiments/reports/h4.md` (n = 1 en casi todas las celdas: una ubicación por semilla). Resumen por tipo y severidad: detectadas/4 y Δc_vh verdadero medio (HF, tramo del fallo, hora siguiente a t₀; positivo = peor):

| tipo | baja | media | alta |
|---|---|---|---|
| slowdown | 0/4 · +0,011 | 0/4 · +0,033 | 3/4 · +0,108 |
| blocked_segment | 0/4 · +0,001 | 2/4 · +0,006 | 2/4 · +0,055 |
| breakdown | 1/4 · +0,134 | 0/4 · +0,139 | 0/4 · +0,083 |
| dispatch_delay | 0/4 · +0,045 | 1/4 · +0,106 | 2/4 · +0,172 |
| demand_surge | 0/4 · +0,001 | 0/4 · +0,002 | 0/4 · +0,007 |

Lectura (solo lo medido):
- `slowdown`: el efecto verdadero crece con la severidad (Δc_vh +0,01 → +0,03 → +0,11) y la detección solo aparece en alta. La línea 170 (LF) cae en OTP: −0,43 y −0,52 en alta (tramo 1, 07 h y 08 h), −0,14 a −0,17 en media. En media hay efecto verdadero (OTP −0,14/−0,17 en el tramo 1) y 0/2 detectadas ahí.
- `breakdown` y `dispatch_delay`: efecto verdadero apreciable (Δc_vh de +0,02 a +0,23 por celda; las celdas de severidad baja de `dispatch_delay` quedan en +0,02) y detección casi nula: el efecto existe, pero el indicador por banda no lo separa de la dispersión natural (coherente con WP4). `demand_surge`: efecto verdadero ≈ 0 en c_vh y OTP del tramo; 0 detectadas es coherente.
- `blocked_segment`: **en el tramo 1 el efecto verdadero en el tramo es exactamente 0** (Δc_vh = 0,000 y ΔOTP = 0,000) y aun así se detecta 4/4. Comprobado en la semilla 3030: en S06 las llegadas verdaderas continúan, los buses esperan hasta ~25 min para salir y las alertas abiertas son del **tramo 2** (el tramo aguas abajo se queda sin buses), que el emparejamiento acepta por la tolerancia ±1. Es decir, la métrica de efecto "en el tramo afectado" no ve la perturbación real, que está en el tramo siguiente. En el tramo 2 ocurre lo contrario: efecto verdadero +0,094 a +0,136 en alta y 0/2 detectadas (no se ha investigado la causa).
- `slowdown` alta en el tramo 2: efecto +0,066 (08 h, no detectado) y +0,124 (09 h, detectado con 60 min).

> **For the document (EN):** For segment-wide slowdowns, detection follows severity: a high-severity slowdown raises the true c_vh of its segment by +0.11 and is detected in 3 of 4 runs, while low and medium slowdowns (+0.01, +0.03) are never detected. Single-vehicle faults (breakdown, dispatch delay) change the true c_vh by up to +0.23 but are mostly missed. A blocked segment leaves arrivals inside the blocked segment unchanged (buses keep arriving and wait to depart) and starves the next segment, which is where its alerts are raised.

> **For the document (EN):** On 20 clean days the detectors raised 4 alerts (0.33 per line·direction·week, 0.011 action-required alerts per service hour). Of the 16 medium/high segment-wide faults, 7 were matched (recall 0.44, 95 % CI 0.23–0.67; median delay 30 min). All 4 blocked-segment faults in segment 1 were detected and none in segment 2, although in segment 2 the true c_vh rises by up to +0.14. EWMA+CUSUM and a Shewhart chart tuned to the same in-control ARL0 (2000 bands) differ by one detection out of 16.

## Filas añadidas a DEVIATIONS.md
- 27: K3 sobre E1+E2 y `never_fulfilled_grace_polls` (no cambia `INTERFACES.md`; se anota porque cambia la definición de medida de K3 y la configuración del Ingestor).
- 25: `z_max` de Shewhart ajustado por ARL₀ (3,0 → 6,78); la pasada Shewhart reutiliza el trigger `ewma` y escribe `*_shewhart.parquet` (INTERFACES §8).
- 26: K1 en batch es 100 % por construcción; la pérdida extremo a extremo se mide en E5.
