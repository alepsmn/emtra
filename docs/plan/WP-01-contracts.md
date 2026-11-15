# WP1 — Contratos y módulos compartidos

**Hito:** H4 · **Depende de:** WP0 · **Todo lo demás depende de esto.**

## Objetivo
Convertir `INTERFACES.md` en código con tests. No se diseña nada nuevo: se implementa lo que dice ese fichero. Si al implementarlo aparece un hueco o una contradicción, se corrige en `INTERFACES.md` en el mismo commit y se anota en el REPORT.

## Entregables
| INTERFACES | Código |
|---|---|
| §1 Corredor | `emtra_common/corridor.py` + `config/corridors/c19.yaml` completo (12 paradas, 2 tramos, líneas 19, C1 y 170, puntos de control) |
| §2 Horario | `emtra_common/timetable.py` |
| §3 Registro crudo | `contracts/raw.py` |
| §4 Sobre y payloads | `contracts/envelope.py`, `contracts/payloads.py`, función `make_msg_id(schema_name, *natural_key)` |
| §5 Topics | `contracts/topics.yaml` + `contracts/topics.py` (carga y helpers de formato de topic) |
| §6 FogStore | `contracts/sql/fogstore/001_*.sql …` + `emtra_common/db.py` (aplicar migraciones) + `python -m emtra_common.reference load` |
| §7 Compartidos | `clock.py`, `rng.py`, `bands.py`, `config.py`, `mqtt.py`, `health.py` (`holding.py` es de WP4) |
| §10 Semillas | `config/data_split.yaml` |

`config/sim/c19.yaml` lo crea WP2, pero `build_timetable` necesita las medias de enlace y el dwell medio: define aquí el modelo pydantic mínimo de esos dos campos y un YAML con valores provisionales (enlace 90 s, dwell 20 s).

## Aceptación (`tests/acceptance/wp01/`)
```python
def test_every_payload_roundtrips_json():       # modelo -> json -> modelo, un ejemplo válido por schema de §4
def test_extra_fields_are_rejected():
def test_msg_id_is_deterministic():             # misma clave natural -> mismo UUID; distinta -> distinto
def test_fallback_recommendation_validates():   # Candidate con todas las métricas a None
def test_topics_yaml_matches_interfaces():      # cada topic MQTT con destino Kafka tiene su par; cada schema usado existe
def test_rng_is_stable_across_processes():      # mismo (seed, stream, key) en un subproceso -> mismos números
def test_rng_streams_are_independent():         # distinta key -> secuencias distintas
def test_band_helpers():                        # band_start, band_group (límites 07:00, 10:00...), horizon_band (180 -> "3-6", 1800 -> None)
def test_los_boundaries():                      # c_vh 0.30 -> B ; 0.31 -> C ; 0.40 -> D ; OTP 0.90 -> B ; 0.899 -> C
def test_corridor_loads_and_is_consistent():    # tramos cubren todas las paradas sin solape; líneas entran/salen en paradas existentes
def test_timetable_headways():                  # línea 19 en am_peak: salidas cada 360 s; line_type("19","am_peak")=="HF"; ("170",...)=="LF"
# integración (make it):
def test_migrations_apply_on_empty_db():
def test_single_open_alert_constraint():        # dos alertas 'open' del mismo (línea, sentido, tramo) -> IntegrityError
def test_reference_load():                      # stops (12) y schedule cargados
```

## Checklist
- [ ] Ningún modelo se llama o tiene un campo `schema` (choca con pydantic).
- [ ] `event_time` y clave natural de cada schema coinciden con la tabla de §4.
- [ ] Bandas calculadas en Europe/Madrid, almacenadas en UTC.
