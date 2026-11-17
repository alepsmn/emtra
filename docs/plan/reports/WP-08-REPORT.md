# WP-08 — REPORT

Rama `wp-08-action-planner` (desde `main`). Sin merge ni push.

## Hecho
- **Regla de retención** (`emtra_common/holding.py`, `config/holding.yaml`): `hold_seconds`, `hold_control_points` (candidatas), `hold_params`. `HoldPolicy` en `sim/policy.py`.
- **Arranque desde un estado** (`sim/state.py`, `Engine(initial_state=...)`): buses colocados en su enlace con el tiempo restante de su estimación, viajes posteriores a `as_of` despachados con normalidad, cargas medias del perfil, colas esperadas. `as_of` puede no estar alineado a banda.
- **Evaluador** (`sim/evaluator.py`): réplicas con CRN en un pool de procesos arrancado y caliente, `A` de todas las líneas, recuperación y pico con `Indicators` del Monitor, `D_r`, ranking por `A`, presupuesto por lotes completos de 10.
- **Monitor, núcleo puro** (`core/state.py`, `core/recommend.py`): `CorridorTracker` (estado a partir de llegadas derivadas y estimaciones), `Requester` (cuándo pedir), `fallback_recommendation`. El batch escribe `rec_requests.parquet`; `state_at(run_dir, as_of)` y `load_requests(run_dir)` para WP12.
- **Monitor, adaptador**: `--speed`, suscripción a `fog/snapshots/#` y `fog/recommendations/#`, publicación de `fog/rec_requests/{alert_id}`, persistencia en `recommendations` deduplicada por `msg_id`, temporizador de respaldo (`fallback.py`, reloj inyectado de `emtra_common/clock.py`).
- **Servicio ActionPlanner** (`services/action_planner/`): consume `emtra.rec_requests.v1`, produce `emtra.recommendations.v1` con `correlation_id = alert_id`; latido en `service_health`. `make up` lo arranca; `demo_v1.sh stop` lo para; `demo_v1.sh` pasa la velocidad del mundo al Monitor.
- **Tests**: los 9 del spec (primer commit, en rojo) y 4 extra (`test_extra.py`): tracker, requester, petición desde el core, y respaldo en vivo sin ActionPlanner (integración).
- **Scripts**: `scripts/wp08_seed_selection.py` (semilla del test de integración), `scripts/wp08_calibration.py` (todas las tablas de abajo, solo semillas 2000–2019).

## No hecho / en rojo (con motivo)

**`test_hold_damps_dispatch_bunching` queda en rojo: 3 de 10 escenarios (umbral ≥ 7).** No se ha relajado ni marcado `skip`. Es el riesgo B del encargo: el denominador de D.

**Causa medida.** `D_r = 1 − A_cand,r / A_none,r` exige `A_none,r > 0` en cada réplica. En este corredor, con una hora de horizonte, `A_none` por réplica tiene media ≈ 140 pax·min y desviación típica ≈ 100: sale ≤ 0 en el 6 % de las réplicas a t0 + 30 y + 45 min y en el 24 % a t0 + 15 (semillas 2000–2019). Con R = 30, entre 12 y 20 de 20 escenarios tienen alguna réplica con `A_none ≤ 0`; ahí D no está definido (`damping_mean = None`). No he tapado el caso ni cambiado la definición.

De dónde sale la dispersión (tabla "Distribución de A_none"): la línea 19 aporta ≈ 100 ± 60 pax·min y la 170 (LF, 3 buses por hora) entre −43 y +20 de media según la fase de `as_of` respecto a su horario, con ± 70 a ± 110. Comprobado aparte que cambiar la referencia programada (espera hasta el siguiente bus del horario en vez de H/2) no lo arregla: `A_none ≤ 0` en 9 a 23 de 180 réplicas según el desfase.

**Segundo hallazgo, independiente del anterior.** A t0 + 30 y t0 + 45 min el estado con `dispatch_delay` alta es **idéntico** al del mismo día sin fallo (mismas cifras en las dos tablas). El corredor es de un solo sentido y sin rotación de vehículos: un bus despachado con retraso sale del corredor en ≈ 25 min y los viajes siguientes no dependen de él. Lo mismo pasa con `breakdown` (tabla por tipo de fallo). Solo a t0 + 15 la perturbación sigue dentro. Además, el hold reduce `A` también en días limpios (ΔA ≈ −11 a −24 pax·min): regulariza los headways en general. Consecuencia: con cualquier estimador estable, a + 30 y + 45 el test pasaría igual sin fallo, es decir, no mediría amortiguación de la perturbación.

**Alternativas medidas** (semillas de calibración, 20 escenarios `dispatch_delay` alta línea 19, mejor hold rankeado; escenarios con efecto > 0 e IC 95 % que excluye 0; el umbral del test equivale a 14 de 20):

| Opción | Qué cambia | t0 + 15 | t0 + 30 | t0 + 45 | Mismo día sin fallo (S04; + 15 / + 30 / + 45) |
|---|---|---|---|---|---|
| 0. Spec: media de `D_r` | nada | 0 de 20 | 3 de 20 | 5 de 20 | 0 / 2 / 5 |
| 1. `D_r` con `A` esperada dados los pasos de bus (D\|h) | cómo se calcula `A`: llegadas de pasajeros integradas, no muestreadas; ignora el tope de capacidad | 13 | 20 | 20 | 11 / 20 / 20 |
| 2. Razón de medias: `1 − media(A_cand) / media(A_none)`, IC por bootstrap pareado | cómo se agrega D; `A` no cambia | 17 | 20 | 20 | 15 / 20 / 20 |
| 3. Razón de medias sobre la `A` de la opción 1 | las dos cosas | 19 | 20 | 20 | 20 / 20 / 20 |
| 4. Diferencia pareada ΔA = `A_cand − A_none` (pax·min), IC t | la magnitud: deja de ser una fracción | 20 | 20 | 20 | 20 / 20 / 20 |

**Recomendación** (la decisión es vuestra; no he implementado ninguna):
1. **Estimador: opción 2.** Es el cambio mínimo: `A` queda como está y D pasa a ser la razón de medias, que es además lo que dice el glosario de AGENTS.md (`D = 1 − A_action / A_no_action`). La opción 4 es la más robusta pero K9 está escrito sobre D; conviene publicarla junto a D.
2. **Desfase: t0 + 15 min.** Es el único con la perturbación dentro del corredor y el que corresponde al uso real: cuando el Monitor detecta un `dispatch_delay` lo hace en la primera banda (tabla para WP12) y pide con `as_of` ≈ t0 + 17,5 min. Con la opción 2 da 17 de 20 en calibración. La regla que yo había declarado para elegirlo (más escenarios con la D del spec) da 45 min, que es lo que hay ahora en `config/action_planner.yaml`; no la considero defendible a la vista del segundo hallazgo.
3. **Texto del documento.** Lo medido es que el hold reduce el exceso de espera acumulado de la hora siguiente en un 12–17 % (razón de medias, S04) con y sin fallo. No es amortiguación específica de la perturbación. El efecto atribuible al fallo solo se ve a + 15 y es pequeño (S08: ΔA −18,7 con fallo frente a −11,4 sin él; S04: −25,4 frente a −24,0).

Otras cosas no hechas:
- **No he mirado Grafana.** El dashboard no se ha tocado; el panel de Salud mostrará una fila más (`action_planner`) porque lee `service_health`.
- El camino "el ActionPlanner responde tarde, después del respaldo" no se ha ejercitado: quedarían dos recomendaciones de la misma petición (ids distintos), las dos persistidas.

## Supuestos y decisiones tomadas

Decisiones de spec recibidas (no reabiertas; spec WP-08 corregido en el commit que las implementa):
1. **Candidatas de hold**: puntos de control que sirve la línea alertada, en el tramo alertado o después, sin la última parada de la línea, máximo 2, en orden de recorrido. Respaldo: el primero. (`hold_control_points`; DEVIATIONS 31.)
2. **Instante del estado en `test_hold_damps_dispatch_bunching`**: fijo tras t0, alineado a banda, `test_state_offset_min` en `config/action_planner.yaml`; escenarios = bloque `wp08_validation` (3100–3109) de `config/data_split.yaml`. `alpha` y `hold_max_s` son los del spec. Regla de elección declarada en `scripts/wp08_calibration.py::choose_offset`: el desfase con más escenarios de calibración cuya mejor hold tiene la D del spec > 0 con IC sin 0; empate, el más temprano. Resultado: 45 min (0, 3 y 5 de 20). Ver la recomendación de arriba.
3. **Indicador de recuperación**: el de la terna alertada, con `Indicators` del core del Monitor sobre las llegadas verdaderas de la réplica. Añadí `Indicators.fresh()` (instancia nueva sobre el mismo horario) para no reconstruir el índice del horario en cada réplica; no cambia ningún cálculo.
4. **Core puro del Monitor**: estado, petición y contenido del respaldo en `core/`; temporizador en el adaptador con reloj inyectado. El batch escribe `rec_requests.parquet` (INTERFACES §8, DEVIATIONS 32).
5. **Duplicados**: el Monitor deduplica por `msg_id` (conjunto en memoria) y la tabla por `recommendation_id` (`ON CONFLICT DO NOTHING`).
6. **`emtra_common/holding.py`** creado aquí; INTERFACES §7 corregido. `Engine(initial_state=...)` implementado.

Mías, a revisar:
- **`h_fwd`** = tiempo desde que el bus anterior de la línea **salió** de la parada (el spec dice "al salir del punto de control"); antes el motor pasaba a la política el headway de llegadas, que solo usaba `NoAction`. `BusView` lleva además `h_sched_s`. Las salidas del simulador sin política no cambian (comprobado byte a byte con una ejecución de E2).
- **`bus_id` = n-ésimo viaje del día de la línea**, sea cual sea el inicio de la ejecución (DEVIATIONS 33). Antes el ordinal contaba desde el inicio de la ejecución; solo cambia en mundos lanzados con `--start` distinto de 06:00.
- **`A`**: espera real de quien sube más la espera acumulada de quien sigue en la parada al final, menos la programada (H/2 por pasajero; `x − x²/(2H)` si llega a `x < H` del final; para la cola inicial, `H −` tiempo desde el último bus). El término programado no depende de la acción.
- **Estado**: `as_of` = marca de agua que cierra la banda (≈ fin de banda + 150 s). Un bus sin estimación ni llegada en 180 s (`state_stale_s`) no está. El último paso de cada línea por cada parada se deduce de las posiciones con tiempos nominales.
- **Limitación**: un despacho retrasado que aún no es visible (línea 170, retraso de 20 min) no está en el estado y la réplica no lo despacha: "un hueco es un hueco". Una ralentización en curso no se conoce (spec).
- **`recovery_time_s`** = inicio de la primera de 2 bandas completas seguidas dentro de rango, desde `as_of`. Con `as_of` no alineado hay 3 bandas completas en la hora.
- **Presupuesto**: se envían las R réplicas y al vencer se cuentan los lotes completos de 10 en orden de réplica (`replications` ∈ {10, 20, 30}). Las réplicas ya en ejecución no se pueden cancelar: terminan y se descartan.
- **Margen del viaje de vuelta**: el ActionPlanner usa `budget_s − 0,5 s` (`budget_margin_s`); el tránsito medido es ≈ 0,1 s.
- **Respaldo**: `runtime_s = 0`, `replications = 0`. Su `recommendation_id` es distinto del de la recomendación simulada de la misma petición.
- **`workers: 4`**. Con 2 ya sobra (0,5 s frente a 1,5 s); 4 deja margen en el portátil de la demo. Son ≈ 4 procesos Python en reposo más.
- **`demo_v1.yaml`**: `action_planner` añadido a los servicios exigidos en línea.
- **`emtra_common/reset.py`**: sin cambios. El ActionPlanner no tiene sesión MQTT (solo Kafka) y `truncate_tables` ya vacía `recommendations`.
- **Umbrales propios corregidos** (no del spec): en `test_state_roundtrip_runs` había puesto "≥ 15 buses en ruta"; son 6–7 en hora punta. Ahora compara con la verdad.
- **Dependencias nuevas**: ninguna.

## Cómo reproducir (un comando) y cuánto tarda
`make accept WP=08` — 5 min 47 s, 13 tests (necesita Docker). Tablas: `uv run python -m scripts.wp08_calibration` — ≈ 2 min. Semilla del test de integración: `uv run python -m scripts.wp08_seed_selection` — ≈ 10 s.

```
make lint test        -> ruff y mypy sin errores; 95 passed, 1 failed (test_hold_damps_dispatch_bunching), 25 deselected (53 s)
make it               -> 25 passed (22 min 51 s)
make accept WP=08     -> 12 passed, 1 failed (test_hold_damps_dispatch_bunching) (5 min 47 s)
make accept WP=04     -> 19 passed (1 min 28 s)
make accept WP=05     -> 10 passed (10 min 31 s); dos demos completas con el ActionPlanner arrancado
make accept WP=06     -> 18 passed (13 s)
make experiments      -> h4.md regenerado (39 s, 132 ejecuciones recalculadas); kpis.csv idéntico al anterior
```

## Resultados medidos (tablas/figuras que sirven al documento)

### Tiempos (K10)
R = 30, 2 holds + no acción, horizonte 1 h; mediana de 3 (`wp08_calibration timing`).

| workers | arranque del pool (s) | total R = 30 (s) | por réplica (ms) | por simulación (ms) |
|---|---|---|---|---|
| 1 | 0,7 | 0,94 | 31 | 10 |
| 2 | 0,7 | 0,48 | — | — |
| 8 | 1,0 | 0,17 | — | — |

`test_runtime` (2 workers): 0,51 s frente a < 120 s.

### Extremo a extremo a 60× (riesgo A)
Mundo con `dispatch_delay` alta, línea 19, 08:00, semilla 42; presupuesto 2 s; pool de 4 workers; ejecución de `make accept WP=08`.

| Medida | Valor |
|---|---|
| Alerta `action_required` en MQTT → recomendación en FogStore | 0,34 s |
| `runtime_s` del ranking | 0,29 s |
| Réplicas dentro del presupuesto | 30 de 30 |
| Sin ActionPlanner: alerta → respaldo en FogStore | 2,10 s (presupuesto 2 s) |

Dos ejecuciones previas de desarrollo con 8 workers dieron 0,28 s y 0,30 s de extremo a extremo (`runtime_s` 0,19 y 0,20). El tránsito (MQTT → puente → Kafka → ActionPlanner y vuelta) es ≈ 0,05–0,1 s. En 1,5 s de presupuesto útil caben más de 30 réplicas incluso con 1 worker (0,94 s).

Selección de la semilla (`wp08_seed_selection`, mundo 06:00–08:45, criterio: ninguna `action_required` antes del fallo y una de la línea 19 en los 60 min siguientes): cumplen 42, 44, 47, 48 y 58 de 42–61, todas con la alerta a las 08:15 en el tramo 1. Elegida la primera, 42.

### `test_hold_damps_dispatch_bunching` (semillas de validación 3100–3109, estado en t0 + 45 min)
Solo lo que define el test; las alternativas no se han calculado con estas semillas.

| semilla | mejor hold (rank) | D medio | IC 95 % | A_none media | A_none mín | réplicas con A_none ≤ 0 (de 30) |
|---|---|---|---|---|---|---|
| 3100 | S04 (1) | 0,082 | −0,024 a 0,188 | 153 | 23 | 0 |
| 3101 | S04 (1) | no definido | — | 156 | −15 | 1 |
| 3102 | S04 (1) | −0,527 | −1,837 a 0,782 | 165 | 1 | 0 |
| 3103 | S04 (1) | 0,058 | −0,015 a 0,132 | 194 | 24 | 0 |
| 3104 | S04 (1) | 0,139 | −0,004 a 0,281 | 145 | 10 | 0 |
| 3105 | S08 (1) | no definido | — | 121 | −7 | 1 |
| 3106 | S04 (1) | no definido | — | 120 | −13 | 1 |
| 3107 | S04 (1) | 0,117 | 0,038 a 0,196 | 194 | 50 | 0 |
| 3108 | S04 (1) | 0,070 | 0,019 a 0,120 | 204 | 63 | 0 |
| 3109 | S08 (1) | 0,136 | 0,079 a 0,193 | 206 | 29 | 0 |

Cumplen 3 de 10 (3107, 3108, 3109). En 3 D no está definido; en la 3102 una réplica con `A_none` = 1 pax·min dispara D. `A_none` de las 300 réplicas: mín −15, p10 60, mediana 154, media 166, p90 289, máx 416; 3 réplicas ≤ 0. En los 10 escenarios la mejor hold queda en el rank 1, por delante de no acción.

### D frente al desfase del estado y al punto de control (semillas 2000–2019)
`dispatch_delay` alta, línea 19, t0 08:00. "D" es la del spec; ΔA, RoM, D\|h y RoM\|h son las alternativas de arriba. Las columnas "IC sin 0" cuentan escenarios.

| escenario | punto | n | D definido | D medio (definidos) | D > 0, IC sin 0 | ΔA medio (pax·min) | ΔA < 0, IC sin 0 | RoM medio | RoM > 0, IC sin 0 | D\|h definido | D\|h medio | D\|h > 0, IC sin 0 | RoM\|h medio | RoM\|h > 0, IC sin 0 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| t0 + 15 min | S04 | 20 | 0 | — | 0 | -25,4 | 20 | -0,983 | 17 | 13 | 0,280 | 13 | 0,400 | 19 |
| t0 + 15 min | S08 | 20 | 0 | — | 0 | -18,7 | 20 | -1,005 | 17 | 13 | 0,214 | 13 | 0,312 | 19 |
| t0 + 15 min | mejor rankeada | 20 | 0 | — | 0 | -25,4 | 20 | -0,982 | 17 | 13 | 0,280 | 13 | 0,397 | 19 |
| t0 + 30 min | S04 | 20 | 8 | 0,119 | 2 | -16,8 | 20 | 0,117 | 20 | 20 | 0,123 | 20 | 0,134 | 20 |
| t0 + 30 min | S08 | 20 | 8 | 0,005 | 3 | -10,7 | 20 | 0,072 | 20 | 20 | 0,084 | 20 | 0,084 | 20 |
| t0 + 30 min | mejor rankeada | 20 | 8 | 0,123 | 3 | -17,1 | 20 | 0,118 | 20 | 20 | 0,125 | 20 | 0,135 | 20 |
| t0 + 45 min | S04 | 20 | 5 | 0,153 | 5 | -21,7 | 20 | 0,166 | 20 | 20 | 0,157 | 20 | 0,168 | 20 |
| t0 + 45 min | S08 | 20 | 5 | 0,135 | 4 | -13,9 | 20 | 0,109 | 20 | 20 | 0,090 | 20 | 0,097 | 20 |
| t0 + 45 min | mejor rankeada | 20 | 5 | 0,161 | 5 | -22,1 | 20 | 0,170 | 20 | 20 | 0,157 | 20 | 0,167 | 20 |

El RoM medio negativo a t0 + 15 lo causan escenarios cuya media de `A_none` queda cerca de 0 (la razón de medias también se dispara ahí); por eso 17 y no 20.

Lo mismo **sin fallo** (mismas semillas e instantes):

| escenario | punto | n | D definido | D medio (definidos) | D > 0, IC sin 0 | ΔA medio (pax·min) | ΔA < 0, IC sin 0 | RoM medio | RoM > 0, IC sin 0 | D\|h definido | D\|h medio | D\|h > 0, IC sin 0 | RoM\|h medio | RoM\|h > 0, IC sin 0 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| limpio, 08:00 + 15 min | S04 | 20 | 0 | — | 0 | -24,0 | 20 | 0,429 | 15 | 11 | 0,332 | 11 | 0,342 | 20 |
| limpio, 08:00 + 15 min | S08 | 20 | 0 | — | 0 | -11,4 | 20 | 0,204 | 15 | 11 | 0,196 | 10 | 0,177 | 20 |
| limpio, 08:00 + 30 min | S04 | 20 | 8 | 0,160 | 2 | -16,8 | 20 | 0,118 | 20 | 20 | 0,124 | 20 | 0,136 | 20 |
| limpio, 08:00 + 30 min | S08 | 20 | 8 | -0,020 | 2 | -10,8 | 20 | 0,074 | 20 | 20 | 0,087 | 20 | 0,087 | 20 |
| limpio, 08:00 + 45 min | S04 | 20 | 5 | 0,153 | 5 | -21,7 | 20 | 0,166 | 20 | 20 | 0,157 | 20 | 0,168 | 20 |
| limpio, 08:00 + 45 min | S08 | 20 | 5 | 0,135 | 4 | -13,9 | 20 | 0,109 | 20 | 20 | 0,090 | 20 | 0,097 | 20 |

### Distribución de `A_none` (pax·min por réplica; R = 30 por escenario, 20 escenarios)

| escenario | réplicas | mín | p10 | mediana | media | p90 | máx | réplicas ≤ 0 | escenarios con alguna ≤ 0 | ídem con A\|h | media (desv. típ.) por línea: 19 | C1 | 170 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `dispatch_delay` alta, t0 + 15 min | 600 | -210 | -57 | 73 | 85 | 246 | 482 | 145 (24 %) | 20 de 20 | 7 de 20 | 98 (63) | 31 (41) | -43 (111) |
| `dispatch_delay` alta, t0 + 30 min | 600 | -56 | 25 | 157 | 152 | 269 | 374 | 33 (6 %) | 12 de 20 | 0 de 20 | 101 (45) | 32 (36) | 20 (71) |
| `dispatch_delay` alta, t0 + 45 min | 600 | -61 | 26 | 124 | 137 | 264 | 393 | 34 (6 %) | 15 de 20 | 0 de 20 | 104 (62) | 21 (44) | 12 (71) |
| limpio, 08:00 + 15 min | 600 | -158 | -64 | 59 | 69 | 229 | 454 | 185 (31 %) | 20 de 20 | 9 de 20 | 82 (61) | 31 (41) | -43 (111) |
| limpio, 08:00 + 30 min | 600 | -54 | 22 | 155 | 151 | 268 | 373 | 32 (5 %) | 12 de 20 | 0 de 20 | 99 (44) | 32 (36) | 20 (71) |
| limpio, 08:00 + 45 min | 600 | -61 | 26 | 124 | 137 | 264 | 393 | 34 (6 %) | 15 de 20 | 0 de 20 | 104 (62) | 21 (44) | 12 (71) |

### D por tipo de fallo (informativo, sin objetivo)
Línea 19, t0 08:00, estado en t0 + 45 min, semillas 2000–2019. `dispatch_delay` media y alta y `breakdown` alta dan cifras idénticas entre sí y al día limpio: a + 45 min el estado es el mismo. `breakdown` media difiere porque el bus vuelve a los 30 min.

| escenario | punto | n | D definido | D medio (definidos) | D > 0, IC sin 0 | ΔA medio (pax·min) | ΔA < 0, IC sin 0 | RoM medio | RoM > 0, IC sin 0 | D\|h definido | D\|h medio | D\|h > 0, IC sin 0 | RoM\|h medio | RoM\|h > 0, IC sin 0 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| dispatch_delay media | S04 | 20 | 5 | 0,153 | 5 | -21,7 | 20 | 0,166 | 20 | 20 | 0,157 | 20 | 0,168 | 20 |
| dispatch_delay media | S08 | 20 | 5 | 0,135 | 4 | -13,9 | 20 | 0,109 | 20 | 20 | 0,090 | 20 | 0,097 | 20 |
| dispatch_delay alta | S04 | 20 | 5 | 0,153 | 5 | -21,7 | 20 | 0,166 | 20 | 20 | 0,157 | 20 | 0,168 | 20 |
| dispatch_delay alta | S08 | 20 | 5 | 0,135 | 4 | -13,9 | 20 | 0,109 | 20 | 20 | 0,090 | 20 | 0,097 | 20 |
| breakdown media | S04 | 20 | 4 | 0,136 | 3 | -21,9 | 20 | 0,182 | 20 | 20 | 0,170 | 20 | 0,180 | 20 |
| breakdown media | S08 | 20 | 4 | 0,097 | 4 | -15,4 | 20 | 0,134 | 20 | 20 | 0,110 | 20 | 0,114 | 20 |
| breakdown alta | S04 | 20 | 5 | 0,153 | 5 | -21,7 | 20 | 0,166 | 20 | 20 | 0,157 | 20 | 0,168 | 20 |
| breakdown alta | S08 | 20 | 5 | 0,135 | 4 | -13,9 | 20 | 0,109 | 20 | 20 | 0,090 | 20 | 0,097 | 20 |

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
`experiments/reports/kpis.csv` sale **idéntico byte a byte** al de antes de WP8 (las 132 ejecuciones se recalcularon: el hash del código cambió). `h4.md` solo cambia en la línea del commit, así que he dejado el fichero como estaba.

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
- 34: `damping_mean` / `damping_ci95` vacíos cuando `A_none,r ≤ 0` en alguna réplica (pendiente de la decisión sobre D).
- 35: el Monitor consume `fog/snapshots/{stop_id}` (INTERFACES §5, `topics.yaml`).

INTERFACES tocado en §4 (comentarios de `CorridorState` y `Candidate`), §5, §7 (`holding.py`, etiqueta WP4 → WP8), §8, §9 y §10 (bloque 3100–3109).
