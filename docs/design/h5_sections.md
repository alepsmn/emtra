# H5 — changes to the technical document after H4

Documento de trabajo del segundo incremento. **No se pega todavía.** Se escribe como cambios sobre `h4_sections.md`, que queda congelado al entregar H4 (30-nov): así no hay dos copias del mismo texto que diverjan. En H5 (freeze 11-dic) el documento final = documento H4 entregado + estos cambios.

> Notas en español entre `>`: instrucciones para vosotros. Texto en inglés: para pegar. Numeración de tablas provisional (`Table H5-n`); se fija al montar el documento final.
>
> **Estado a 21-nov:** WP7 (nube) y WP8 (ranking de acciones) hechos y mezclados. WP9–WP13 pendientes: sus secciones son huecos con lo que deben contener.
>
> **Fuente de las cifras:** `docs/plan/reports/WP-07-REPORT.md`, `WP-08-REPORT.md` y `docs/DEVIATIONS.md` (filas 31–36). Son resultados de tests de aceptación, **no** de la validación final: los KPIs K9–K16 se miden en WP12 (E3–E5) y sustituyen a estas cifras donde coincidan.

---

## A. Cambios al texto de H4

**A.1 — Table 51 (state of the functionalities).** Al cerrar H5, cambiar a *Implemented* las filas de F10, F11, F4, F12 y la nube. Cambiar el título de la columna a *State at H5*.

**A.2 — 6.1, primer párrafo.** Sustituir "The first one, delivered at this milestone, covers the measuring half of the loop…" por un párrafo que cubra los dos incrementos:

> The first increment, delivered at H4, covers the measuring half of the loop: record the service, compute the indicator, detect and alert. The second, delivered at H5, closes it: rank the actions by paired simulation, let the operator decide, apply the decision in the simulated world and verify the recovery; it also adds the cloud tier, the accuracy of the published estimates and the arrival interval for passengers.

**A.3 — Table 52 (changes with respect to the design): filas nuevas.**

| Design (H3) | Prototype | Reason |
|---|---|---|
| ActionPlanner ranks hold, short-turn, reinforce, detour and no action (Table 41) | Only hold and no action are evaluated | As planned in §5.11; the rest stays as design |
| A hold candidate per control point downstream of the alerted segment; the fallback holds at the first one (§5.8, D5) | Candidates are the control points served by the alerted line in the alerted segment or after it, the last stop excluded, at most two; the fallback uses the first | The corridor has two segments and two control points (S04, S08): read literally, an alert on segment 2 would have no candidate |
| The disturbance is given to the simulator explicitly (§5.8 step 4) | The disturbance is implicit in the corridor state sent with the request: bus positions, latest estimates and loads | The fog does not know the cause of an alert, only what it observes |
| Damping D = 1 − A_action / A_no_action, averaged over the replications (§5.8 step 5) | D = 1 − mean(A_action) / mean(A_no_action) over the R replications, with a 95 % paired percentile bootstrap interval | Within one hour the excess wait without action can be zero or negative in a replication (5–8 % of them), so the per-replication ratio is undefined |
| A = accumulated excess wait of all passengers until the horizon, against scheduled headways (FR10) | A = excess wait of the cohort arriving in the hour after the state, all lines: complete real wait minus complete wait to the next scheduled bus of the timetable; the simulation runs a 30-minute tail so that those waits end | Half the headway as reference ignored the phase of the timetable, and truncating the waits at the horizon left a negative bias on low-frequency lines |
| The ReliabilityMonitor consumes derived arrivals (Table 47) | It also reads the snapshots, to describe where each bus is when it requests a recommendation | The cloud does not read the fog store; the request must carry the state |
| Daganzo's headway-based holding as fallback (D5) | Simplified forward-headway rule: hold = clip(α·(H − h), 0, hold_max) at a control point | Smallest rule that keeps the idea; parameters in configuration |
| Evaluation model fitted independently of the observed world (§5.8) | The ActionPlanner evaluates with the same model that generates the simulated world | Follows from the synthetic corridor. Declared limitation: the measured damping is optimistic |

> Si WP9–WP12 añaden filas a `DEVIATIONS.md`, pasarlas aquí con el mismo formato.

---

## B. Capítulo 6: secciones nuevas

### 6.5. Cloud tier

**The fog never depends on the cloud.** The bridge is the only path between them. Every message received from MQTT is first written to a local outbox (SQLite) and acknowledged to the broker only after that write; a separate loop delivers the outbox to Kafka in order and marks each message as sent once Kafka confirms it. If the cloud is unreachable, the outbox grows and the fog keeps working; when it returns, the outbox drains. Delivery is at least once: after a crash of the bridge a message can be sent twice, and consumers remove duplicates by the deterministic message identifier.

**Cold storage keeps the cloud layout without a managed service.** A sink writes each Kafka topic to compressed newline-delimited JSON files partitioned by UTC date, the same layout as a cloud bucket. A file becomes visible only when it is complete. dbt models on DuckDB deduplicate by message identifier and test every staging model.

| Measure (acceptance test, Kafka stopped 30 s, 2,000 messages at 50/s) | Value |
|---|---|
| Effective outage | 30.2 s |
| Maximum outbox depth | 1,687 messages |
| Drain after Kafka returns (broker start included) | 4.6 s |
| Messages published in MQTT / distinct in Kafka | 2,000 / 2,000 |
| Lost / duplicated | 0 / 0 |
| Order per key | Preserved |

*Table H5-1. Behaviour of the bridge during a cloud outage.*

> K16 se mide en E5 con 2 minutos de corte y el stack completo; cuando exista, sustituye o acompaña a esta tabla.
>
> Limitaciones a declarar (REPORT WP7): el outbox no se purga; Mosquitto guarda como mucho 1 000 mensajes QoS 1 para un cliente desconectado, así que un puente caído más de ~20 s a 50 msg/s perdería mensajes antes de llegar a él; el camino de duplicados tras matar el puente está garantizado por diseño y test unitario, no observado.

### 6.6. Ranking of actions (F10)

**An action is evaluated against doing nothing, on the same random numbers.** When an "action required" alert opens, the ReliabilityMonitor sends the state of the corridor (where each bus is, its latest estimate, its load) to the ActionPlanner. The ActionPlanner starts the simulator from that state and runs R = 30 paired replications per candidate: hold at each eligible control point, and no action. Each pair shares its random numbers, so the difference between candidates is the effect of the action and not of the noise.

**The measure is the excess wait of the passengers.** A is the excess wait of the passengers of all lines who arrive in the hour after the state: their complete real wait minus their wait to the next scheduled bus. Damping D = 1 − mean(A_action) / mean(A_no_action), with a 95 % paired bootstrap interval. Candidates are ranked by A.

**The fog never waits for the cloud.** If the ranking does not arrive within its budget, the ReliabilityMonitor issues a fallback recommendation from the holding rule, flagged as such.

| Measure (acceptance test, world at 60×) | Value |
|---|---|
| Ranking time, 30 replications, 4 workers | 0.40 s |
| Alert → recommendation stored in the fog | 0.48 s |
| Alert → fallback stored, ActionPlanner stopped (budget 2 s) | 2.09 s |

*Table H5-2. Response time of the ranking.*

> K10 se mide en E3 (p95 de `runtime_s` frente a 120 s).

### 6.7. Decision and recovery (F11)

> Hueco: WP9. Debe contener: la página de decisión del operador (candidatos, D con su intervalo, marca de respaldo; un botón por candidato y rechazar); el mundo aplica el hold desde la hora de la decisión; la verificación de la recuperación (dos bandas seguidas dentro del rango u horizonte de 90 min); la decisión rechazada también genera recuperación (el caso "no acción" observado); la línea temporal alerta → recomendación → decisión → recuperación en el dashboard. Si se adopta el modo de demo que pausa el mundo hasta la decisión, decir que es solo de demo y que en operación rige el respaldo.

### 6.8. Estimate accuracy (F4) and passenger interval (F12)

> Hueco: WP10 y WP11. F4: MAE de las estimaciones por banda de horizonte, separando días limpios y con fallo (K3 sobrerreporta nunca cumplidas con fallo; ver 7.4 de H4). F12: intervalo conformal al 90 % por celda, calibrado con semillas 2000–2019 y evaluado con 3000+, API `GET /arrival`.

### 6.4 (demonstration) — sustituir al cerrar H5

> Hueco: WP12, `make demo-final`. Describir el lazo completo: alerta → recomendación → decisión del operador → hold aplicado → recuperación verificada, y el corte de la nube.

---

## C. Capítulo 7: validación del segundo incremento

### 7.2 — añadir

> Hueco: WP12. Población de E3 = escenarios cuya alerta se abre (la población operativa de F10), con semillas de validación consecutivas hasta completar cada celda y tope declarado; la selección es por el disparo, nunca por el resultado. Medido en WP8 con semillas de calibración: abren `action_required` el `dispatch_delay` medio en 6 de 20 casos, el alto en 8 de 20 y el `breakdown` en 1 de 20.

### 7.3 — tabla de KPIs del segundo incremento

> Hueco: WP12. K9–K16 con valor, IC, n, objetivo y si se cumple, mismo formato que la Tabla 56.

### 7.4 — puntos que la discusión debe cubrir (ya conocidos)

**The hold regularises the corridor in general, not only after a fault.** In the acceptance test of WP8 (validation seeds 3100–3109, state 15 minutes after a high-severity dispatch delay) the best-ranked hold reduced the excess wait in all 10 scenarios, with D between 0.17 and 0.26. With the same seeds and no fault it did the same, with D between 0.19 and 0.25. K9 therefore measures a generic regularisation of headways, not a damping specific to the disturbance.

**The damping is optimistic.** The ActionPlanner evaluates with the same model that generates the world, so the evaluation has no model error.

**K9 is read together with K5 and K6.** The action only acts on detected faults and starts about three bands late (7.4 of H4): the benefit for the passenger is roughly the share of faults detected times the damping once triggered.

**Vehicle faults are detected in the first band or not at all.** Few scenarios open an alert (see 7.2), which limits the size of E3.

> Las cifras de este apartado son del test de aceptación de WP8; en el documento final se sustituyen por las de E3 cuando existan.

---

## D. Conclusiones y trabajo futuro

> Hueco: al cerrar WP12. Conclusión del segundo incremento (si el lazo mejora el servicio frente a no actuar, cuánto, y con qué techo impuesto por la detección); trabajo futuro: regla por vehículo, bandas más cortas, ajustar el simulador con datos reales para tener error de modelo, resto de acciones (short-turn, refuerzo, desvío).
