# WP11 — Intervalo calibrado para el pasajero (F12)

**Hito:** H5 · **Depende de:** WP10 (`estimate_outcomes`), WP7 (cloud→fog)

## 11.1 Calibración (`analytics/calibrate.py`, cloud)
- Split conformal asimétrico (sección 3.9 del H3) sobre los errores `e_k` de las ejecuciones de calibración (semillas 2000–2019, INTERFACES §10).
- Celda: (`line_id`, `horizon_band`, `band_group`). Si `n < 200` → (`line_id`, `horizon_band`); si tampoco → (`horizon_band`). Las celdas de respaldo viajan en la misma lista con los campos ausentes a `None`.
- Confianza 1 − α (default α = 0,10): `q_lo` = cuantil empírico de nivel `⌊(n+1)·α/2⌋/n` de `e`; `q_hi` = cuantil de nivel `⌈(n+1)(1 − α/2)⌉/n`.
- Intervalo para una estimación τ: `[max(0, τ + q_lo), τ + q_hi]`.
- `python -m analytics.calibrate` calcula y publica `emtra.calibration.v1` en Kafka. Se lanza con `make calibrate` (sin Airflow; desviación de D6).

## 11.2 PassengerInfoService (`services/passenger_info/`, FastAPI, fog)
- Mantiene en memoria la calibración retenida de `calibration/current` y el último estado de `fog/status/#`.
- `GET /arrival?stop_id=&line_id=` → `{tau_s, interval_s: [lo, hi], confidence: 0.9, low_confidence: bool, calibration_version}` con la estimación más reciente de `estimates` para esa parada y línea. `low_confidence = true` si el tramo de la parada está `outside`. 404 si no hay estimación.
- `GET /` → página móvil mínima (HTML + JS sin framework): elegir parada y línea, mostrar "4 a 9 min, 9 de cada 10 veces". Ocupación: "no disponible" (F2 queda en diseño).

## Aceptación (`tests/acceptance/wp11/`)
```python
def test_quantiles_finite_sample():   # vector conocido -> q_lo, q_hi exactos
def test_fallback_cells():            # celda con n=50 -> usa (line, horizon_band)
def test_interval_never_negative():
def test_coverage_on_validation():    # calibrar con 2000-2019, evaluar con 3000-3009: cobertura global en 0,90 ± 0,02 (K13). Si falla, escribe el informe y falla: NO se ajusta alpha
def test_width_vs_single_interval():  # misma evaluación con un único (q_lo, q_hi): se informa la anchura mediana de ambos (K14)
def test_api_contract():              # respuesta con todos los campos; low_confidence sigue al estado
# integración (make it):
def test_calibration_reaches_fog():   # make calibrate -> retenido en calibration/current -> GET /arrival responde con esa versión
```

## Checklist
- [ ] Ninguna semilla de validación en la calibración.
- [ ] `experiments/reports/f12.md` con cobertura y anchura por celda (E4); K13 y K14 añadidos a `experiments/kpis.py`.
