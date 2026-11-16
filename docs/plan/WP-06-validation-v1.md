# WP6 — Arnés de validación, E1 y E2

**Hito:** documento H4 (30-nov) · **Depende de:** WP2–WP4 · Definiciones en [KPIS.md](KPIS.md).

## Objetivo
Medir en batch lo construido en H4 contra la verdad del simulador y dejar el arnés al que H5 añade experimentos.

## Estructura (`experiments/`)
```
scenarios/e1_normal.yaml  e2_detection.yaml
runner.py     # expande un escenario en ejecuciones (semilla, fallos) y corre: sim batch -> ingestor.run_batch -> monitor.run_batch
kpis.py       # una función por KPI: entrada = directorios de ejecución, salida = {value, ci | None, n}
matching.py   # emparejamiento alerta <-> fallo (KPIS.md)
report.py     # tablas Markdown + CSV + figuras PNG en experiments/reports/
config/kpis.yaml   # objetivos de KPIS.md
```
- Paralelo por ejecución con `ProcessPoolExecutor`. Caché: si `runs/<run_id>/` existe con el mismo hash de config, no se recalcula.
- `report.py` marca PASS/FAIL comparando con `config/kpis.yaml`, sin lógica propia por KPI. Cada tabla lleva al pie semillas, nº de ejecuciones y git sha.
- IC: Wilson para proporciones (K5), bootstrap por ejecución para medias.

## Experimentos de este WP
- **E1** → K1, K2, K3, K4.
- **E2** → matriz VP/FP/FN por tipo y severidad; K5, K6, K7, K8 (el objetivo de K5–K6–K8 aplica solo a `slowdown` y `blocked_segment` de severidad alta, 2 tramos × 10 semillas; el resto se reporta como informativo en una tabla de detección frente a tamaño del efecto, con 10 ejecuciones de control sin fallo; ver KPIS.md) (repetir el Monitor con `Shewhart` sobre las mismas llegadas), K17.

`make experiments` corre E1 y E2 completos; `make experiments SMOKE=1` una versión reducida (2 días limpios + 6 fallos, los primeros de la población de K5).

## Aceptación (`tests/acceptance/wp06/`)
```python
def test_matching_unit():             # 8 casos a mano: doble alerta, alerta antes de t0, tramo ±1, fuera de ventana, fallo sin línea...
def test_runner_is_deterministic():   # E1 con 2 días, dos veces sin caché -> mismos parquet
def test_kpis_read_targets():         # PASS/FAIL sale de config/kpis.yaml
def test_experiments_smoke():         # SMOKE=1 termina en < 5 min y genera el informe con K1-K8 y K17
```

## Entregable para el documento
`experiments/reports/h4.md`: tabla de KPIs medidos frente a objetivo, matriz de detección por tipo y severidad, y figura de retardo de detección. Un KPI en FAIL se informa tal cual.
