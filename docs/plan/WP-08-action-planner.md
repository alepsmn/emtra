# WP8 — ActionPlanner (F10) y respaldo (D5)

**Hito:** H5 · **Depende de:** WP2, WP4, WP7 · Decisiones D5, D7, D9.

## Objetivo
Por cada alerta `action_required`: el Monitor pide una recomendación con el estado del corredor; el ActionPlanner simula cada acción candidata y la no acción en pares con CRN y devuelve el ranking. Si no llega a tiempo, el Monitor propone la retención de respaldo.

## 8.1 Monitor: petición y respaldo (`services/reliability_monitor/`)
- Al abrir una alerta `action_required` o subir a ese nivel: construir `CorridorState` (INTERFACES §4) y publicar `fog/rec_requests/{alert_id}`. Por bus: última parada cuya llegada se ha derivado, su hora, y `eta_next_s` de la última estimación. Buses con última llegada en la última parada del corredor no se incluyen.
- Volver a pedir solo si `distance_to_range` cambia más de 0,05 desde la última petición.
- **Respaldo:** si en `budget_s` (default 120 s de reloj de pared, dividido por `speed` en modo mundo; es tiempo de cómputo, no de dominio) no llega `fog/recommendations/{alert_id}`, publicar una `recommendation` con `fallback=true`: candidatos `hold` (regla de abajo, en el primer punto de control aguas abajo del tramo alertado) y `no_action`, con las métricas a `None`.
- Persistir en FogStore lo que llegue por `fog/recommendations`.

Regla de retención (`emtra_common/holding.py`, función pura, simplificación de Daganzo [8]):
`hold_s = clip(alpha · (H − h_fwd), 0, hold_max_s)`, con `H` el headway programado, `h_fwd` el headway con el bus anterior de la misma línea al salir del punto de control, `alpha = 0,6`, `hold_max_s = 120`. `HoldPolicy(line_id, stop_id, alpha, hold_max_s)` implementa `ControlPolicy` con ella.

## 8.2 Arranque desde un estado (`sim/state.py`)
`Engine(..., initial_state=CorridorState, start=as_of, horizon_s=3600)`:
- Cada bus del estado se coloca en el enlace posterior a `last_stop_seq`, con el tiempo restante de enlace `eta_next_s` si existe; si no, con el muestreado.
- Trips del horario con salida > `as_of` que no corresponden a ningún bus del estado: se despachan con normalidad.
- Cargas a bordo: carga media del perfil de demanda en esa parada. Colas en parada: valor esperado `λ · (as_of − último paso de la línea)`.
- La perturbación no se modela aparte: va implícita en las posiciones (un bus averiado no está; un hueco es un hueco). Limitación a documentar: una ralentización en curso no se conoce.

## 8.3 Evaluador (`sim/evaluator.py`) y servicio (`services/action_planner/`)
- Candidatas: `no_action` + un `hold` por punto de control aguas abajo del tramo alertado (máx. 2), sobre la línea alertada.
- R réplicas (default 30); **la réplica r usa la misma semilla raíz para todas las candidatas** (CRN).
- Por réplica y candidata:
  - `A` = exceso de espera acumulado (pax·min) de **todas las líneas del corredor** desde `as_of` hasta el horizonte: Σ (espera real − espera con headways programados);
  - `recovery_time_s`: primer instante desde el que el c_vh del tramo queda dentro de rango 2 bandas seguidas; `None` si no ocurre;
  - `peak_deviation`: máx(c_vh − límite del rango);
  - `onboard_delay_pax_min`: Σ hold × carga a bordo. No entra en D; se muestra como contra.
- `D_r = 1 − A_cand,r / A_none,r`; media e IC 95 % (t pareada).
- Ranking por media de `A` ascendente. `pros` / `cons`: texto fijo por acción (Tabla 7 del H3: hold = "sin recursos extra; rápido de aplicar" / "retrasa a los pasajeros a bordo") + las cifras.
- Paralelo con `ProcessPoolExecutor`. Por lotes de 10 réplicas; al vencer `budget_s` se devuelve lo que haya si R ≥ 10 (campo `replications` con el valor real); si no, nada.
- Servicio: consume `emtra.rec_requests.v1`, produce `emtra.recommendations.v1` con `correlation_id = alert_id`. El estado sale del mensaje; la nube no lee FogStore.

## Aceptación (`tests/acceptance/wp08/`)
```python
def test_holding_rule():                          # h_fwd = H -> 0 ; h_fwd = H/2 -> alpha*H/2 ; tope hold_max_s
def test_state_roundtrip_runs():                  # estado construido de una ejecución a las 08:30 -> Engine arranca y simula 1 h sin error; nº de buses en ruta coincide
def test_identical_policies_give_zero_damping():  # candidata == no_action -> D_r == 0 exacto en todas las réplicas
def test_hold_damps_dispatch_bunching():          # dispatch_delay alta, 10 escenarios: D medio > 0 con IC que excluye 0 en >= 7     (K9)
def test_ranking_is_sorted_by_A():
def test_budget_respected():                      # simulación artificialmente lenta -> respuesta parcial o nada en <= budget + 5 s
def test_runtime():                               # R=30, 2 candidatas + no_action, 1 h, 2 workers -> < 120 s (@slow)                 (K10)
def test_fallback_when_cloud_silent():            # sin respuesta en budget -> recommendation fallback=true válida
# integración (make it):
def test_alert_to_recommendation_roundtrip():     # mundo con dispatch_delay alta -> recomendación no fallback en FogStore con correlation_id = alert_id
```

## Checklist
- [ ] `A` incluye todas las líneas del corredor (FR10).
- [ ] Ninguna réplica usa semillas distintas entre candidatas.
- [ ] El REPORT trae tiempo por réplica y D por tipo de fallo.
