# WP-08 — REPORT

Rama `wp-08-action-planner` (desde `main`). Sin merge ni push.

## Hecho
- **Regla de retención** (`emtra_common/holding.py`, `config/holding.yaml`): `hold_seconds`, `hold_control_points` (candidatas), `hold_params`. `HoldPolicy` en `sim/policy.py`.
- **Arranque desde un estado** (`sim/state.py`, `Engine(initial_state=...)`): buses colocados en su enlace con el tiempo restante de su estimación, viajes posteriores a `as_of` despachados con normalidad, cargas medias del perfil, colas esperadas. `as_of` puede no estar alineado a banda.
- **Evaluador** (`sim/evaluator.py`): réplicas con CRN en un pool de procesos arrancado y caliente, `A` de la cohorte de todas las líneas contra el horario, recuperación y pico con `Indicators` del Monitor, D por razón de medias con bootstrap pareado, ranking por `A`, presupuesto por lotes completos de 10.
- **Monitor, núcleo puro** (`core/state.py`, `core/recommend.py`): `CorridorTracker` (estado a partir de llegadas derivadas y estimaciones), `Requester` (cuándo pedir), `fallback_recommendation`. El batch escribe `rec_requests.parquet`; `state_at(run_dir, as_of)` y `load_requests(run_dir)` para WP12.
- **Monitor, adaptador**: `--speed`, suscripción a `fog/snapshots/#` y `fog/recommendations/#`, publicación de `fog/rec_requests/{alert_id}`, persistencia en `recommendations` deduplicada por `msg_id`, temporizador de respaldo (`fallback.py`, reloj inyectado de `emtra_common/clock.py`).
- **Servicio ActionPlanner** (`services/action_planner/`): consume `emtra.rec_requests.v1`, produce `emtra.recommendations.v1` con `correlation_id = alert_id`; latido en `service_health`. `make up` lo arranca; `demo_v1.sh stop` lo para; `demo_v1.sh` pasa la velocidad del mundo al Monitor.
- **Tests**: los 9 del spec (primer commit, en rojo) y 4 extra (`test_extra.py`): tracker, requester, petición desde el core, y respaldo en vivo sin ActionPlanner (integración).
- **Scripts**: `scripts/wp08_seed_selection.py` (semilla del test de integración), `scripts/wp08_calibration.py` (tablas con semillas 2000–2019), `scripts/wp08_validation_report.py` (tabla por escenario del test con 3100–3109, con y sin fallo; solo informe).

## No hecho / en rojo (con motivo)

**Nada en rojo.** `test_hold_damps_dispatch_bunching` pasa con 10 de 10 escenarios (umbral ≥ 7) tras las correcciones de abajo (decisiones vuestras, no mías). K9 cumplido en este test; la medida de E3 es de WP12.

**Lo que hay que decir en el documento:** con las mismas semillas **sin fallo** también salen 10 de 10, con D parecida (0,19–0,25 sin fallo frente a 0,17–0,26 con fallo) y ΔA casi igual. El hold reduce un ~20 % el exceso de espera de la hora siguiente de todo el corredor, haya o no `dispatch_delay`: regulariza headways en general. No es amortiguación específica de la perturbación. A t0 + 30 y + 45 el estado con fallo es además idéntico al del día limpio (el bus retrasado ya ha salido del corredor; REPORT anterior).

### Historia del rojo y correcciones
1. **Primera entrega (3 de 10).** `D_r = 1 − A_cand,r / A_none,r` por réplica no está definida cuando `A_none,r ≤ 0`, y eso pasa en el 5–8 % de las réplicas. Es un error de spec, no un umbral. **Corrección:** D = 1 − media(A_cand)/media(A_none) sobre las réplicas, con IC 95 % por bootstrap pareado (percentil, 2 000 remuestras, índices de `emtra_common.rng`); vacía solo si media(A_none) ≤ 0. Desfase del estado del test: **t0 + 15 min, por principio**. Es el único con el fallo aún dentro del corredor y coincide con cuándo pide el Monitor (≈ t0 + 17,5 min). **La regla `choose_offset` se ha retirado**: elegía 45 min por resultado.
2. **Sesgo de la referencia de A (H/2).** Para quien llegaba después de `as_of` la referencia era H/2 sin condicionar a la fase del horario, y la espera real sí depende de ella. **Corrección:** espera programada = tiempo hasta la siguiente salida programada de su línea en su parada (horario de `emtra_common/timetable`). ΔA y el ranking no cambiaron (diferencia máxima 5,7·10⁻¹⁴ pax·min; ranking igual en 60 de 60).
3. **Sesgo por truncado en el horizonte.** Con la referencia ya anclada, la 170 seguía saliendo −21 pax·min a t0 + 45 (tabla "antes" abajo). Causa: las dos esperas se truncaban en T = `as_of` + 1 h y los buses reales van con retraso respecto al horario.
   - Medido en la 170: el primer bus llega entre 13 y 48 s después de su hora programada.
   - Ejemplo, S04 a + 45: bus programado en t0 + 30 s, real en t0 + 78 s. Quien llega entre + 30 y + 78 pierde el programado: espera programada ≈ 1 152 s frente a < 48 s real, unos −1 150 s por pasajero.
   - En un periodo completo eso se compensa con +48 s para cada pasajero que llega después del bus real. Pero la compensación del último periodo cae justo antes de T, donde ambas esperas se truncan a T − t y la diferencia es 0.
   - Sesgo por parada ≈ −r·δ·(T − último paso programado antes de T): depende de la fase del horario en T (del desfase) y escala con H. Estimado en S04, −11 pax·min; medido, −9,6.

   **Corrección:** A es la **cohorte** que llega en `[as_of, as_of + horizon_s)` (más la cola inicial, que cuenta desde `as_of`), con espera real y programada **completas**. El motor simula `horizon_s + tail_s` (1 800 s). Quien sigue esperando al final de la cola cuenta con la espera hasta entonces en los dos términos: medido, **0** en las 60 × 3 × 30 réplicas. Recuperación y pico, solo con las bandas completas de la hora.
4. **Cola inicial en paradas por delante de todos los buses de la línea** (último paso desconocido en el estado): ahora se dimensiona con el último paso programado según el horario. Antes quedaba a 0. Solo afecta a la cola; la política sigue sin ver bus anterior allí.

**Qué había probado antes con "cambiar la referencia programada no lo arregla".** Un script de scratch con 6 semillas (2000–2005), R = 30, día limpio, referencia anclada al horario pero con el mismo truncado en T. Solo miré cuántas réplicas tenían `A_none ≤ 0` (9, 23 y 20 de 180, frente a 38, 17 y 15 con H/2). El patrón por línea estaba en la salida (170: +37,5 / +8,0 / −13,0) y no lo analicé, por dos motivos: buscaba el problema de dispersión del denominador, no un sesgo, y no calculé IC sobre semillas, así que la tendencia pasó por ruido. Además, aquella prueba arrastraba el truncado, así que no podía arreglarlo.

**Queda declarado (comprobación del punto 2):** ninguna línea sale negativa fuera de su IC, pero **C1 varía con el desfase** (34 / 41 / 15 pax·min a + 15 / + 30 / + 45, IC sin solapar entre + 30 y + 45) y la 19 sube algo (94 → 114). No lo he investigado más: era un solo intento.

Otras cosas no hechas:
- **No he mirado Grafana.** El dashboard no se ha tocado; el panel de Salud mostrará una fila más (`action_planner`) porque lee `service_health`.
- El camino "el ActionPlanner responde tarde, después del respaldo" no se ha ejercitado: quedarían dos recomendaciones de la misma petición (ids distintos), las dos persistidas.
- `make it`, `make accept WP=04/05/06` no se han repetido tras estas correcciones (no se pedían). Los cambios no tocan el Monitor ni las salidas del simulador sin estado (`kpis.csv` idéntico); `make accept WP=08` incluye los dos tests de integración del lazo.

## Supuestos y decisiones tomadas

Decisiones de spec recibidas (no reabiertas; spec WP-08 corregido en el commit que las implementa):
1. **Candidatas de hold**: puntos de control que sirve la línea alertada, en el tramo alertado o después, sin la última parada de la línea, máximo 2, en orden de recorrido. Respaldo: el primero. (`hold_control_points`; DEVIATIONS 31.)
2. **Instante del estado en `test_hold_damps_dispatch_bunching`**: fijo tras t0, alineado a banda, `test_state_offset_min` = 15 en `config/action_planner.yaml` (por principio, ver arriba; la regla `choose_offset` se retiró); escenarios = bloque `wp08_validation` (3100–3109) de `config/data_split.yaml`. `alpha` y `hold_max_s` son los del spec.
7. **Estimador de D, referencia y cohorte de A, cola inicial por horario** (correcciones de revisión, arriba). Spec 8.3, KPIS (K9), INTERFACES (`Candidate`), DEVIATIONS 34 (reescrita) y 36.
3. **Indicador de recuperación**: el de la terna alertada, con `Indicators` del core del Monitor sobre las llegadas verdaderas de la réplica. Añadí `Indicators.fresh()` (instancia nueva sobre el mismo horario) para no reconstruir el índice del horario en cada réplica; no cambia ningún cálculo.
4. **Core puro del Monitor**: estado, petición y contenido del respaldo en `core/`; temporizador en el adaptador con reloj inyectado. El batch escribe `rec_requests.parquet` (INTERFACES §8, DEVIATIONS 32).
5. **Duplicados**: el Monitor deduplica por `msg_id` (conjunto en memoria) y la tabla por `recommendation_id` (`ON CONFLICT DO NOTHING`).
6. **`emtra_common/holding.py`** creado aquí; INTERFACES §7 corregido. `Engine(initial_state=...)` implementado.

Mías, a revisar:
- **`h_fwd`** = tiempo desde que el bus anterior de la línea **salió** de la parada (el spec dice "al salir del punto de control"); antes el motor pasaba a la política el headway de llegadas, que solo usaba `NoAction`. `BusView` lleva además `h_sched_s`. Las salidas del simulador sin política no cambian (comprobado byte a byte con una ejecución de E2).
- **`bus_id` = n-ésimo viaje del día de la línea**, sea cual sea el inicio de la ejecución (DEVIATIONS 33). Antes el ordinal contaba desde el inicio de la ejecución; solo cambia en mundos lanzados con `--start` distinto de 06:00.
- **Cola inicial**: los pasajeros se reparten uniformemente en el hueco desde el último paso; su espera programada desde `as_of` es `máx(siguiente programado tras su llegada, as_of) − as_of`.
- **Estado**: `as_of` = marca de agua que cierra la banda (≈ fin de banda + 150 s). Un bus sin estimación ni llegada en 180 s (`state_stale_s`) no está. El último paso de cada línea por cada parada se deduce de las posiciones con tiempos nominales.
- **Limitación**: un despacho retrasado que aún no es visible (línea 170, retraso de 20 min) no está en el estado y la réplica no lo despacha: "un hueco es un hueco". Una ralentización en curso no se conoce (spec).
- **`recovery_time_s`** = inicio de la primera de 2 bandas completas seguidas dentro de rango, desde `as_of`, solo con bandas completas de la hora (la cola no cuenta). Con `as_of` no alineado hay 3 bandas completas en la hora.
- **Presupuesto**: se envían las R réplicas y al vencer se cuentan los lotes completos de 10 en orden de réplica (`replications` ∈ {10, 20, 30}). Las réplicas ya en ejecución no se pueden cancelar: terminan y se descartan.
- **Margen del viaje de vuelta**: el ActionPlanner usa `budget_s − 0,5 s` (`budget_margin_s`); el tránsito medido es ≈ 0,1 s.
- **Respaldo**: `runtime_s = 0`, `replications = 0`. Su `recommendation_id` es distinto del de la recomendación simulada de la misma petición.
- **`workers: 4`**. Con la cola de 30 min, R = 30 tarda 0,90 s con 2 workers y 0,40 s con 4, frente a 1,5 s útiles del presupuesto a 60×. Son 4 procesos Python en reposo más.
- **`demo_v1.yaml`**: `action_planner` añadido a los servicios exigidos en línea.
- **`emtra_common/reset.py`**: sin cambios. El ActionPlanner no tiene sesión MQTT (solo Kafka) y `truncate_tables` ya vacía `recommendations`.
- **Umbrales propios corregidos** (no del spec): en `test_state_roundtrip_runs` había puesto "≥ 15 buses en ruta"; son 6–7 en hora punta. Ahora compara con la verdad.
- **Dependencias nuevas**: ninguna.

## Cómo reproducir (un comando) y cuánto tarda
`make accept WP=08` — 6 min 6 s (13 tests, necesita Docker). Tablas: `uv run python -m scripts.wp08_calibration` (≈ 2 min) y `uv run python -m scripts.wp08_validation_report` (≈ 20 s). Semilla del test de integración: `uv run python -m scripts.wp08_seed_selection` (≈ 10 s).

Ejecuciones finales, tras las correcciones:
```
make lint test        -> ruff y mypy sin errores; 96 passed, 25 deselected (55 s)
make accept WP=08     -> 13 passed (6 min 6 s)
make experiments      -> 38 s; kpis.csv idéntico byte a byte al anterior
```
Primera entrega (antes de las correcciones), para la historia: `make it` 25 passed; `make accept WP=04` 19, `WP=05` 10, `WP=06` 18 passed.

## Resultados medidos (tablas/figuras que sirven al documento)

### Tiempos (K10)
R = 30, 2 holds + no acción, horizonte 1 h + cola de 30 min; mediana de 3 (`wp08_calibration timing`). Antes de la cola: 0,94 s con 1 worker y 0,48 s con 2.

| workers | arranque del pool (s) | total R = 30 (s) | por réplica (ms) | por simulación (ms) |
|---|---|---|---|---|
| 1 | 0,9 | 1,85 | 62 | 21 |
| 2 | 0,9 | 0,90 | — | — |
| 4 | 0,9 | 0,40 | — | — |

`test_runtime` (2 workers): 0,70 s frente a < 120 s.

### Extremo a extremo a 60× (riesgo A)
Mundo con `dispatch_delay` alta, línea 19, 08:00, semilla 42; presupuesto 2 s; pool de 4 workers; ejecución final de `make accept WP=08`.

| Medida | Valor |
|---|---|
| Alerta `action_required` en MQTT → recomendación en FogStore | 0,48 s |
| `runtime_s` del ranking | 0,40 s |
| Réplicas dentro del presupuesto | 30 de 30 |
| Sin ActionPlanner: alerta → respaldo en FogStore | 2,09 s (presupuesto 2 s) |

Dos ejecuciones previas de desarrollo con 8 workers dieron 0,28 s y 0,30 s de extremo a extremo (`runtime_s` 0,19 y 0,20). El tránsito (MQTT → puente → Kafka → ActionPlanner y vuelta) es ≈ 0,05–0,1 s. En 1,5 s de presupuesto útil caben más de 30 réplicas incluso con 1 worker (0,94 s).

Selección de la semilla (`wp08_seed_selection`, mundo 06:00–08:45, criterio: ninguna `action_required` antes del fallo y una de la línea 19 en los 60 min siguientes): cumplen 42, 44, 47, 48 y 58 de 42–61, todas con la alerta a las 08:15 en el tramo 1. Elegida la primera, 42.

### A de no acción por línea: antes y después de corregir la referencia
Día limpio, estado en 08:00 + desfase, semillas 2000–2019; media de las 20 semillas (cada una, media de R = 30) e IC 95 % t sobre las semillas, pax·min.

**Antes** (referencia anclada al horario, pero esperas truncadas en T; la versión H/2 original da el total de la última columna):

| desfase | línea 19 | C1 | 170 | total | total con H/2 |
|---|---|---|---|---|---|
| +15 | 85,5 [79,2; 91,8] | 26,9 [21,7; 32,1] | 37,2 [36,4; 38,0] | 149,6 [141,7; 157,5] | 69,2 [55,1; 83,3] |
| +30 | 94,4 [86,7; 102,2] | 34,2 [29,5; 38,9] | 23,0 [13,7; 32,3] | 151,6 [138,2; 165,0] | 150,8 [136,0; 165,7] |
| +45 | 103,3 [95,2; 111,4] | 9,9 [5,4; 14,5] | −21,4 [−32,3; −10,6] | 91,8 [78,4; 105,2] | 137,1 [121,8; 152,4] |

**Después** (cohorte con espera completa, cola de 30 min, cola inicial por horario donde no hay último paso):

| desfase | línea 19 | C1 | 170 | total |
|---|---|---|---|---|
| +15 | 94,2 [87,8; 100,5] | 34,0 [29,5; 38,6] | 18,3 [17,2; 19,4] | 146,5 [138,7; 154,3] |
| +30 | 106,1 [97,6; 114,6] | 40,9 [37,0; 44,8] | 28,6 [20,8; 36,4] | 175,6 [163,2; 188,0] |
| +45 | 114,3 [107,1; 121,6] | 14,9 [11,1; 18,8] | 19,7 [15,1; 24,2] | 148,9 [138,9; 158,9] |

Pasajeros de la cohorte aún esperando al final de la cola: 0. Ninguna línea negativa. C1 varía con el desfase (declarado arriba).

### Opciones a t0 + 15 min (semillas 2000–2019)
`dispatch_delay` alta, línea 19, t0 08:00, y las mismas semillas sin fallo, con la A corregida. 0 = media de `D_r` (spec original); 2 = razón de medias con bootstrap pareado (la implementada); 4 = diferencia pareada ΔA, IC t. Las columnas "IC sin 0" cuentan escenarios de 20.

| escenario | punto | 0: D_r definido | 0: > 0, IC sin 0 | 2: D medio | 2: D > 0, IC sin 0 | 2: D no definido | 4: ΔA medio (pax·min) | 4: ΔA < 0, IC sin 0 |
|---|---|---|---|---|---|---|---|---|
| con fallo | S04 | 0 | 0 | 0,184 | 20 | 0 | −30,0 | 20 |
| con fallo | S08 | 0 | 0 | 0,142 | 20 | 0 | −22,8 | 20 |
| con fallo | mejor rankeada | 0 | 0 | 0,184 | 20 | 0 | −30,0 | 20 |
| sin fallo | S04 | 0 | 0 | 0,198 | 20 | 0 | −28,7 | 20 |
| sin fallo | S08 | 0 | 0 | 0,106 | 20 | 0 | −15,6 | 20 |
| sin fallo | mejor rankeada | 0 | 0 | 0,198 | 20 | 0 | −28,7 | 20 |

La opción 0 sigue sin estar definida en ningún escenario: en todos hay alguna réplica con `A_none ≤ 0`.

| escenario | réplicas | mín | p10 | mediana | media | p90 | máx | réplicas ≤ 0 | escenarios con media ≤ 0 | por línea, media (desv. típ.): 19 | C1 | 170 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `dispatch_delay` alta | 600 | −125 | 49 | 148 | 164 | 315 | 552 | 34 (6 %) | 0 de 20 | 112 (68) | 34 (38) | 18 (77) |
| sin fallo | 600 | −157 | 35 | 131 | 147 | 304 | 537 | 40 (7 %) | 0 de 20 | 94 (68) | 34 (38) | 18 (77) |

### `test_hold_damps_dispatch_bunching` (semillas de validación 3100–3109, estado en t0 + 15 min)
Mejor hold rankeada (S04, rank 1 en los 20 casos). Las columnas "sin fallo" son informativas (`scripts/wp08_validation_report.py`); las de "con fallo" son las del test.

| semilla | con fallo: D | IC 95 % | ΔA (pax·min) | sin fallo: D | IC 95 % | ΔA (pax·min) |
|---|---|---|---|---|---|---|
| 3100 | 0,189 | 0,127 a 0,261 | −27,1 | 0,228 | 0,154 a 0,323 | −27,4 |
| 3101 | 0,190 | 0,127 a 0,262 | −27,5 | 0,209 | 0,140 a 0,292 | −27,8 |
| 3102 | 0,185 | 0,135 a 0,244 | −36,1 | 0,233 | 0,171 a 0,312 | −36,1 |
| 3103 | 0,174 | 0,110 a 0,245 | −27,2 | 0,203 | 0,134 a 0,286 | −26,6 |
| 3104 | 0,235 | 0,177 a 0,307 | −42,6 | 0,252 | 0,193 a 0,335 | −40,9 |
| 3105 | 0,218 | 0,151 a 0,301 | −32,8 | 0,221 | 0,153 a 0,314 | −27,6 |
| 3106 | 0,168 | 0,112 a 0,230 | −27,6 | 0,186 | 0,124 a 0,261 | −27,6 |
| 3107 | 0,170 | 0,118 a 0,235 | −26,2 | 0,189 | 0,131 a 0,264 | −25,9 |
| 3108 | 0,216 | 0,158 a 0,282 | −37,9 | 0,194 | 0,138 a 0,264 | −26,0 |
| 3109 | 0,256 | 0,190 a 0,334 | −40,6 | 0,232 | 0,170 a 0,305 | −35,9 |

Con fallo, 10 de 10 cumplen (umbral ≥ 7). Sin fallo, también 10 de 10.

Distribución de `A_none` en esas semillas (300 réplicas):

| escenario | mín | p10 | mediana | media | p90 | máx | réplicas ≤ 0 | escenarios con media ≤ 0 |
|---|---|---|---|---|---|---|---|---|
| con fallo | −131 | 48 | 144 | 162 | 314 | 483 | 15 (5 %) | 0 de 10 |
| sin fallo | −152 | 22 | 122 | 140 | 301 | 471 | 24 (8 %) | 0 de 10 |

### D por tipo de fallo (informativo, sin objetivo)
Línea 19, t0 08:00, estado en t0 + 15 min, semillas 2000–2019, D por razón de medias. `breakdown` media y alta coinciden: a + 15 el bus averiado aún no ha vuelto.

| escenario | punto | D medio | D > 0, IC sin 0 | ΔA medio (pax·min) | ΔA < 0, IC sin 0 |
|---|---|---|---|---|---|
| dispatch_delay media | S04 | 0,176 | 20 | −28,0 | 20 |
| dispatch_delay media | S08 | 0,142 | 20 | −22,5 | 20 |
| dispatch_delay alta | S04 | 0,184 | 20 | −30,0 | 20 |
| dispatch_delay alta | S08 | 0,142 | 20 | −22,8 | 20 |
| breakdown media | S04 | 0,150 | 20 | −29,0 | 20 |
| breakdown media | S08 | 0,082 | 20 | −15,9 | 20 |
| breakdown alta | S04 | 0,150 | 20 | −29,0 | 20 |
| breakdown alta | S08 | 0,082 | 20 | −15,9 | 20 |

`A_none` medio: `dispatch_delay` media 159, alta 164; `breakdown` media y alta 199 pax·min (5–6 % de réplicas ≤ 0, ningún escenario con media ≤ 0).

### Para WP12: escenarios tipo E3 que abren `action_required`
Línea 19, t0 08:00, semillas 2000–2019, mundo 06:00–09:30; alerta de la línea 19 en los 60 min siguientes al t0 real del fallo. El retardo es hasta el fin de la banda que la abre; la petición sale ≈ 2,5 min después.

| tipo | severidad | abren `action_required` | retardo: 15 min | 30 | 45 | 60 | mediana (min) | peticiones por alerta (media) |
|---|---|---|---|---|---|---|---|---|
| dispatch_delay | media | 6 de 20 | 6 | 0 | 0 | 0 | 15 | 2,8 |
| dispatch_delay | alta | 8 de 20 | 8 | 0 | 0 | 0 | 15 | 3,2 |
| breakdown | media | 1 de 20 | 0 | 1 | 0 | 0 | 22 | 4,0 |
| breakdown | alta | 1 de 20 | 0 | 1 | 0 | 0 | 22 | 4,0 |

Lectura para E3 (20 escenarios, 5 semillas por celda): cabe esperar ≈ 4 escenarios evaluables de 20, casi todos `dispatch_delay`. O se detecta en la primera banda o no se detecta. Una alerta genera ≈ 3 peticiones porque `distance_to_range` se mueve más de 0,05 entre bandas.

### `make experiments`: K2–K8 y K17 frente al REPORT de WP6
`experiments/reports/kpis.csv` sale **idéntico byte a byte** al de antes de WP8 (también en la ejecución final, tras las correcciones: comparado con `diff`) (las 132 ejecuciones se recalcularon: el hash del código cambió). `h4.md` solo cambia en la línea del commit, así que he dejado el fichero como estaba.

| KPI | REPORT de WP6 | Ahora |
|---|---|---|
| K2 | 14,46 s | 14,46 s |
| K3 | 214/294 = 0,73 | 0,728 (n = 294) |
| K4 | — (PASS) | 0,0090 |
| K5 | 26/40 = 0,65 | 0,65 (n = 40) |
| K6 | 45 min | 45 min (n = 26) |
| K7 | 0,17 | 0,167 |
| K8 | +0,275 | +0,275 |
| K17 | 0,0028 alertas/h | 0,0028 |

`band_status` y `alerts` no cambian: además de estos KPIs, `test_live_equals_batch` (WP4) sigue en verde con el Monitor nuevo, y los ficheros del simulador de una ejecución de E2 salen iguales byte a byte con el motor modificado.

## Filas añadidas a DEVIATIONS.md
- 31: candidatas de hold y punto de control del respaldo.
- 32: el Monitor batch escribe `rec_requests.parquet`; estado, petición y respaldo son funciones puras del core.
- 33: `bus_id` = n-ésimo viaje del día de la línea.
- 34 (reescrita): D por razón de medias con IC por bootstrap pareado; vacía solo si media(A_none) ≤ 0.
- 36: A = cohorte de la hora siguiente contra el horario, con espera completa (cola de 30 min); cola inicial por horario donde no hay último paso.
- 35: el Monitor consume `fog/snapshots/{stop_id}` (INTERFACES §5, `topics.yaml`).

INTERFACES tocado en §4 (comentarios de `CorridorState` y `Candidate`, este último reescrito para D), §5, §7 (`holding.py`, etiqueta WP4 → WP8), §8, §9 y §10 (bloque 3100–3109).
