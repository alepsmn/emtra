# WP12 — Validación final (E3–E5) y demo final

**Hito:** H5 · **Depende de:** WP6–WP11 · Definiciones en [KPIS.md](KPIS.md).

## 12.1 Experimentos
Sobre el arnés de WP6:
- **E3** (`scenarios/e3_damping.yaml`): por escenario, simular hasta que el Monitor (batch) abre la alerta `action_required`; construir el `CorridorState` en ese instante; evaluar con `sim.evaluator`. Tres resultados por escenario: mejor candidata simulada, regla de respaldo, no acción. K9, K10. K11 se mide en E5 (necesita el lazo en vivo).
- **E4**: integrar el informe de WP11 (K13, K14).
- **E5** (`make it-resilience`, stack completo):
  1. mundo a 60× durante 1 h simulada con un `dispatch_delay` alto; a mitad, `docker compose stop kafka` 2 min de reloj y `start`. Medir K1 y K16 (msg_id publicados en MQTT frente a msg_id en Kafka al final). Durante el corte, el dashboard sigue actualizándose y la alerta recibe recomendación `fallback`.
  2. un script decide automáticamente cada recomendación (elige rank 1) → K11.
  3. 50 rps durante 60 s contra `GET /arrival` (httpx asíncrono, sin locust) → K15.
- `make experiments` genera `experiments/reports/h5.md`: todos los KPIs K1–K17 con valor, IC, objetivo y PASS/FAIL.

## 12.2 Demo final (`scripts/demo_final.sh`, `make demo-final`)
Guion ≈ 4 min de reloj a 60×, semilla 42:
1. Stack completo desde cero; `make calibrate` con una calibración precalculada.
2. Mundo 07:00–10:30 con un `dispatch_delay` alto de la línea 19 a las 08:00.
3. Alerta `action_required` → ranking en el dashboard.
4. El script espera a la recomendación y acepta el rank 1 por la OperatorAPI (o lo hace un humano con `DEMO_MANUAL=1`).
5. El mundo aplica el hold; el indicador vuelve al rango; aparece el `recovery`.
6. Consulta de pasajero antes y durante la alerta (`low_confidence`).
7. Comprobaciones finales contra FogStore (línea temporal completa de la alerta con el mismo `correlation_id`; `recovery.status == "recovered"`); código ≠ 0 si falla.

## Aceptación (`tests/acceptance/wp12/`)
```python
def test_e3_smoke():                # 2 escenarios, R=10 -> informe con K9 y K10
def test_report_has_all_kpis():     # h5.md contiene K1..K17, cada uno con PASS/FAIL o "not measured" justificado
def test_demo_final_end_to_end():   # integración, @slow: scripts/demo_final.sh sale con 0
```

## Checklist
- [ ] Ningún parámetro del sistema se ha cambiado mirando resultados de semillas de validación.
- [ ] Los KPIs en FAIL están comentados en el REPORT con su causa probable.
