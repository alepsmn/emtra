# WP10 — Precisión de las estimaciones publicadas (F4)

**Hito:** H5 · **Depende de:** WP3 (pandas), WP7 (dbt)

## Objetivo
Medir el error de `time_left_s` por línea, grupo de banda y banda de horizonte, con dos implementaciones independientes que deben coincidir, y validar la medida contra la verdad del simulador.

## Definiciones (sección 3.4 del H3)
- Estimación k: `(polled_at, stop_id, line_id, bus_id, tau_s = time_left_s)`; llegada anunciada `polled_at + tau_s`.
- Emparejamiento: primera llegada derivada del mismo `(stop_id, line_id, bus_id)` con `polled_at ≤ arrival_time ≤ polled_at + tau_s + 1800 s`. Sin pareja → **nunca cumplida**.
- `e_k = arrival_time − (polled_at + tau_s)` en segundos (positivo = llegó tarde).
- Bandas de horizonte por `tau_s`: [0,180), [180,360), [360,720), [720,1800]; > 1800 se excluye.
- Dentro de tolerancia (Tabla 14 del H3):

| Horizonte | Adelanto máx. | Retraso máx. |
|---|---|---|
| 0–3 min | 60 s | 60 s |
| 3–6 min | 90 s | 120 s |
| 6–12 min | 150 s | 210 s |
| 12–30 min | 240 s | 360 s |

- Métricas por celda: `n`, `mae_s`, `median_signed_error_s`, `share_within_tolerance`, `share_never_fulfilled`. RMSE y MAPE no.

## Implementaciones
1. **Referencia pandas** `analytics/f4.py`: `estimate_outcomes(estimates_df, arrivals_df) -> df` y `f4_metrics(outcomes_df, by=[...]) -> df`. Funciones puras. Trabaja sobre `runs/<run_id>/estimates.parquet` + `arrivals.parquet` (o `truth_arrivals.parquet`).
2. **dbt** `cloud/dbt/models/`: `int_estimate_outcomes` (desde `stg_snapshots` y `stg_arrivals`), `mart_f4_accuracy` (por día, línea, grupo de banda, banda de horizonte). Tests `not_null`, `accepted_values(horizon_band)`, `share_within_tolerance` entre 0 y 1.

## Aceptación (`tests/acceptance/wp10/`)
```python
def test_pairing_rules_unit():      # 6 casos a mano: tarde, temprano, nunca cumplida, mismo bus_id reutilizado más tarde, tau > 1800 excluida, límite exacto de tolerancia
def test_f4_vs_truth():             # 3 h de corredor: |MAE con llegadas derivadas - MAE con truth_arrivals| <= 5 s por banda de horizonte   (K12)
def test_dbt_matches_reference():   # integración: 1 h de mundo pasada por puente + sink + dbt -> |mae_dbt - mae_py| < 0,5 s y shares iguales a 1e-6, por celda
```

## Entregable para el documento
`experiments/reports/f4.md`: tabla por banda de horizonte y grupo de banda de la línea 19 y figura MAE vs horizonte, sobre los días de E1. Se añade K12 a `experiments/kpis.py`.
