# WP3 — Ingestor (F1)

**Hito:** H4 · **Depende de:** WP1, WP2

## Objetivo
A partir de las instantáneas, registrar las estimaciones, derivar las llegadas y las estimaciones nunca cumplidas, y publicar la marca de agua que cierra las bandas.

## Diseño (`services/ingestor/`)

### `core.py` — puro
```python
class ArrivalDeriver:
    def process(self, snap: RawSnapshot) -> list[Arrival | NeverFulfilled | Watermark]: ...
```
Estado por `(stop_id, line_id, bus_id)`: `last_seen_at`, `last_time_left_s`.

En cada instantánea de una parada:
- Bus presente → actualizar estado.
- Bus seguido que **no** aparece:
  - `last_time_left_s ≤ pass_threshold_s` (default 90) → **llegada** en el punto medio `(last_seen_at + polled_at) / 2`, `uncertainty_s = (polled_at − last_seen_at) / 2`.
  - si no → candidato a **nunca cumplida**; se confirma si no reaparece en `grace_s` (default 120). Si reaparece, se cancela.
- Sondeo fallido de la parada: no hay instantánea, no se decide nada; la incertidumbre de la siguiente llegada crece con el hueco real.
- **Marca de agua:** tras procesar cada instantánea, si el mínimo sobre todas las paradas del corredor del último `polled_at` procesado ha avanzado, emitir `Watermark`. Una parada sin instantáneas durante `stale_stop_s` (default 300) deja de frenar la marca de agua.

`stop_sequence` sale de `emtra_common.corridor`.

### Fuentes (decisión D10)
```python
class DataSource(Protocol):
    def snapshots(self) -> Iterator[RawSnapshot]: ...   # orden no decreciente de polled_at
```
`FileSource(path)` y `MqttSource(topic="emtra/v1/sim/raw/#")`. La fuente real de EMT queda en diseño.

### Adaptadores
- `batch.py`: `run_batch(run_dir)` (INTERFACES §9).
- `__main__.py`: servicio. Por cada instantánea: insertar en `estimates`; publicar `fog/snapshots/{stop_id}`; por cada salida del core, insertar en `arrivals` / `never_fulfilled` y publicar en su topic; publicar `fog/watermark`. Latido en `service_health` con `snapshots_per_min`.
- Inserciones idempotentes (`ON CONFLICT DO NOTHING` por `msg_id`).

## Aceptación (`tests/acceptance/wp03/`)
```python
def test_midpoint_rule():               # 3 casos a mano: llega; desaparece lejos (nunca cumplida); desaparece y reaparece (nada)
def test_poll_gap_widens_uncertainty(): # falta un sondeo -> uncertainty_s == 30
def test_watermark_is_min_over_stops(): # una parada atrasada frena la marca; una parada muda > stale_stop_s deja de frenarla
def test_watermark_advances_without_arrivals():  # instantáneas vacías (sin buses) -> la marca avanza igual
def test_arrival_error_vs_truth():      # 3 h de corredor (WP2), sin fallos: p95(|derivada - verdadera|) <= 15 s   (K2)
def test_never_fulfilled_false_rate():  # misma ejecución: nunca cumplidas con llegada verdadera <= 1 %           (K3)
def test_all_true_arrivals_found():     # >= 98 % de las llegadas verdaderas tienen llegada derivada emparejada
def test_msg_ids_deterministic():       # procesar dos veces la misma ejecución -> mismos msg_id
# integración (make it):
def test_live_equals_batch():           # 30 min de mundo a 60x por MQTT -> filas en arrivals == arrivals.parquet del batch (por msg_id)
def test_restart_no_duplicates():       # reiniciar el servicio a mitad -> 0 filas duplicadas en arrivals
```

## Checklist
- [ ] `pass_threshold_s` y `grace_s` finales y el error obtenido, en el REPORT.
- [ ] El core no importa paho, psycopg ni nada de E/S.
