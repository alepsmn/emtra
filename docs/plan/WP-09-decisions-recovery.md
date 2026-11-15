# WP9 — Decisión del operador y verificación de la recuperación (F11)

**Hito:** H5 · **Depende de:** WP8

## 9.1 OperatorAPI (`services/operator_api/`, FastAPI, fog)
- `GET /alerts/{alert_id}/recommendation` → última recomendación (FogStore).
- `POST /decisions` `{alert_id, recommendation_id, choice: int | "declined", operator_id}` → 404 si la recomendación no existe, 409 si ya tiene decisión → publica `fog/decisions/{alert_id}` con `action` y `params` copiados del candidato elegido (`no_action` si `declined`).
- `GET /ui/alerts/{alert_id}` → página HTML mínima (plantilla del servidor, sin framework) con la tabla de candidatos y un botón por candidato más "rechazar".
- Sin autenticación (limitación declarada).

## 9.2 El mundo aplica la decisión (`sim/world.py`)
Suscrito a `fog/decisions/#`. Si `action == "hold"`: instala `HoldPolicy(**params)` en esa línea desde `decided_at`. La política se retira cuando llega el `recovery` de esa alerta.

## 9.3 Verificación (`services/action_planner/recovery.py`)
- Al recibir `emtra.decisions.v1`: seguir `emtra.status.v1` de la terna hasta 2 bandas seguidas `inside` o hasta el horizonte (default 90 min de tiempo de evento).
- Emitir `emtra.recovery`: `recovery_time_s` desde `decided_at`; `peak_deviation` observado; `damping_sim_mean/ci95` = el D del candidato elegido en la recomendación (`None` si era respaldo). El caso sin acción no es observable: se declara así (sección 2.2 del H3).
- Una decisión `declined` también genera `recovery`: es el caso "no acción" observado.
- Núcleo puro (`RecoveryTracker`) que recibe decisiones y estados.

## 9.4 Monitor y dashboard
- El Monitor persiste `decisions` (de `fog/decisions`) y `recoveries` (de `fog/recoveries`).
- Paneles nuevos en `emtra-operator`: ranking de la recomendación de la alerta seleccionada (candidatos, D e IC, marca `fallback`), enlace a `/ui/alerts/{alert_id}`, y línea temporal alerta → recomendación → decisión → recuperación (`v_alert_timeline`).

## Aceptación (`tests/acceptance/wp09/`)
```python
def test_decision_requires_existing_recommendation():   # 404
def test_one_decision_per_recommendation():             # segunda -> 409
def test_recovery_tracker_recovered():                  # outside, outside, inside, inside -> recovered; tiempo y pico exactos
def test_recovery_tracker_horizon():                    # nunca vuelve -> not_recovered_within_horizon
def test_declined_is_recorded_as_no_action():
# integración (make it):
def test_closed_loop():       # mundo con dispatch_delay alta -> alerta -> recomendación -> POST decisión hold -> truth_arrivals con hold_s > 0 tras decided_at -> recovery en FogStore; los 4 eventos comparten correlation_id
```

## Checklist
- [ ] `v_alert_timeline` devuelve los cuatro eventos de la alerta de la demo.
- [ ] Capturas de los paneles nuevos en `docs/plan/reports/wp09/`.
