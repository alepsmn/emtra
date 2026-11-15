# AGENTS.md — EMTRA

Instrucciones para cualquier agente de código que trabaje en este repo. Léelo entero antes de tocar nada. (`CLAUDE.md` importa este fichero.)

## Qué es EMTRA

Proyecto de asignatura (ETSIST-UPM). Sistema de control de la fiabilidad de líneas de autobús de EMT Madrid, arquitectura edge–fog–cloud. Se evalúa el diseño; el prototipo corre **entero en local con datos sintéticos** (ADR 0001, ADR 0002).

- **Mundo simulado** (`sim/`): simulador SimPy de un corredor (varias líneas que comparten paradas). Emite instantáneas de estimaciones de llegada cada 30 s de tiempo simulado, como haría la API de EMT, y guarda la verdad (llegadas reales, esperas, fallos).
- **Fog**: Ingestor (deriva llegadas de las instantáneas) → ReliabilityMonitor (c_vh / OTP por banda de 15 min, EWMA + CUSUM, alertas) → FogStore (TimescaleDB) → Grafana. Mensajería MQTT (Mosquitto).
- **Cloud (local)**: Kafka ← puente MQTT↔Kafka con outbox. ActionPlanner evalúa acciones (hold vs no acción) por simulación pareada. Sink a ficheros NDJSON + DuckDB + dbt.
- **Edge**: conteo de pasajeros a bordo. Solo diseño; no se implementa.

## Orden de lectura

1. Este fichero.
2. `docs/plan/PLAN.md` — orden de los paquetes de trabajo (WP) y estado.
3. `docs/plan/INTERFACES.md` — **única fuente de verdad** de mensajes, topics, tablas, ficheros y módulos compartidos.
4. Tu `docs/plan/WP-XX-*.md`.
5. `docs/plan/KPIS.md` si tu WP mide algo.

Los specs son autocontenidos. `docs/design/EMTRA_H3.pdf` es el documento entregado; consúltalo solo si un spec te remite a él. Si un spec contradice a `INTERFACES.md`, gana `INTERFACES.md` y lo anotas en el REPORT.

## Alcance: lo más simple que cumpla

Es una asignatura, no un producto. No añadas funcionalidad, abstracciones, opciones de configuración ni servicios que el spec no pida. Si algo del spec resulta desproporcionado, implementa la versión mínima que pasa la aceptación y dilo en el REPORT.

## Reglas duras

1. **Contratos.** Lo definido en `INTERFACES.md` (y su código en `contracts/`) no se cambia en silencio. Si tu WP necesita un cambio: hazlo en `INTERFACES.md` y en `contracts/` en el mismo commit, y añade una fila a `docs/DEVIATIONS.md`.
2. **Tiempo de evento, nunca reloj de pared.** La lógica de dominio usa los tiempos de los mensajes. Prohibido `datetime.now()` / `time.time()` fuera de `emtra_common/clock.py`, del latido de salud y de las medidas de latencia. Motivo: el mundo corre a 60× y los experimentos en batch.
3. **Aleatoriedad con semilla.** Solo `emtra_common.rng.rng(root_seed, stream, *key)` (ver INTERFACES §7). Nada de `random`, `np.random.*` global ni `hash()` de Python (va salado por proceso).
4. **Núcleo puro + adaptador.** Cada servicio tiene un `core` de funciones/clases puras (entrada: eventos; salida: eventos) sin MQTT, Kafka ni BD, y un adaptador fino que lo conecta. El modo batch (`run_batch`) y el servicio en vivo usan **el mismo core**. Los experimentos de validación corren en batch.
5. **Tests de aceptación.** Los nombres y los umbrales numéricos de la sección "Aceptación" de cada spec son vinculantes. Escribe sus cuerpos en el primer commit del WP, antes de implementar. No se relajan umbrales, no se borran, no se marcan `skip`. Si uno no se puede cumplir, se queda en rojo y lo explicas en el REPORT.
6. **Sin números mágicos.** Umbrales y parámetros en `config/*.yaml`, cargados con pydantic.
7. **Nada de secretos en el repo.** No hay credenciales externas: todo es local.

## Stack

- Python 3.12, gestor `uv`. Lint `ruff`. Tipos: `mypy` (no estricto) sobre `contracts/` y `emtra_common/`.
- Librerías permitidas: pydantic v2, pyyaml, numpy, pandas, pyarrow, scipy, simpy, paho-mqtt 2.x, psycopg 3, confluent-kafka (desde WP7), duckdb y dbt-duckdb (desde WP7), fastapi, uvicorn, httpx, structlog, pytest. Otra dependencia: justifícala en el REPORT.
- Infra en `infra/docker-compose.yml`: mosquitto, timescaledb, grafana (H4); kafka KRaft (desde WP7). Los servicios propios corren como procesos Python lanzados por `make`, no hace falta un Dockerfile por servicio.
- Código, identificadores, docstrings y commits **en inglés**. Specs y REPORT en español. `docs/DEVIATIONS.md` en inglés (se pega en el documento técnico).

## Estructura del repo

```
AGENTS.md  CLAUDE.md  Makefile  pyproject.toml
contracts/            # envelope, payloads, topics.yaml, sql/fogstore/*.sql
emtra_common/         # clock, rng, bands, corridor, timetable, config, mqtt/db helpers, health
sim/                  # engine, predictor, emitter, faults, policy, world, evaluator (WP8)
services/
  ingestor/           # F1
  reliability_monitor/# F3, F9, respaldo D5
  bridge/             # MQTT<->Kafka con outbox
  cloud_sink/         # Kafka -> NDJSON local
  action_planner/     # F10, F11
  operator_api/       # decisiones
  passenger_info/     # F12
analytics/            # F4 (referencia pandas), calibración conformal
cloud/dbt/            # dbt-duckdb
infra/                # docker-compose.yml, mosquitto/, grafana/
config/               # *.yaml
experiments/          # escenarios, runner, kpis, informes
tests/{unit,integration,acceptance}/
scripts/              # demo_v1.sh, demo_final.sh
docs/{plan,adr,design}/  docs/DEVIATIONS.md
runs/  data/          # salidas, fuera de git
```

## Comandos

| Comando | Qué hace |
|---|---|
| `make setup` | `uv sync` |
| `make lint` | ruff + mypy |
| `make test` | tests unitarios (sin Docker) |
| `make it` | tests de integración (levanta el compose) |
| `make accept WP=03` | aceptación de un WP |
| `make up` / `make down` | infraestructura + servicios |
| `make demo-v1` | demo de H4 de principio a fin (WP5) |
| `make experiments` | experimentos de validación (WP6, WP12) |
| `make demo-final` | demo de H5 con el lazo cerrado (WP12) |
| `make dbt` / `make calibrate` | modelos dbt (WP7) / calibración F12 (WP11) |
| `make it-resilience` | experimento E5 con el stack completo (WP12) |
| `make docs` | material generado para el documento (WP13) |

## Flujo de trabajo

Un WP por sesión, en el orden de `PLAN.md`.

1. Comprueba en `PLAN.md` que los WP anteriores están marcados como hechos. Si no, para.
2. Rama `wp-XX-slug`. Primer commit: tests de aceptación (en rojo).
3. Implementa con commits pequeños. `make lint test` antes de cada commit.
4. Escribe `docs/plan/reports/WP-XX-REPORT.md` (plantilla abajo) y marca el WP como hecho en `PLAN.md`.
5. Para. Un humano revisa y hace el merge.

### Plantilla de REPORT

```
# WP-XX — REPORT
## Hecho
## No hecho / en rojo (con motivo)
## Supuestos y decisiones tomadas
## Cómo reproducir (un comando) y cuánto tarda
## Resultados medidos (tablas/figuras que sirven al documento)
## Filas añadidas a DEVIATIONS.md
```

## Glosario

- **Banda**: intervalo de 15 min alineado a :00/:15/:30/:45 en Europe/Madrid.
- **Grupo de banda**: `early` 06–07, `am_peak` 07–10, `midday` 10–16, `pm_peak` 16–20, `evening` 20–24.
- **HF/LF**: headway programado ≤ 10 min / > 10 min. HF → c_vh; LF → OTP.
- **c_vh** = σ_d / mean(h_sch), con d_i = h_obs,i − h_sch,i y σ_d desviación típica **muestral** (n−1).
- **OTP** = cuota de llegadas con −60 s ≤ retraso ≤ 5 min (la tolerancia de adelanto es `otp_early_tolerance_s` en `config/reliability.yaml`).
- **EWT** = W_obs − W_sch, con W = Σh² / (2Σh).
- **Tramo (segment)**: grupo contiguo de paradas del corredor, `segment_id` entero y ordenado.
- **Llegada derivada**: punto medio entre la última instantánea con el bus acercándose y la primera en que ya no aparece.
- **Nunca cumplida**: estimación de un bus que desaparece sin llegada derivada.
- **CRN**: common random numbers — mismas semillas con acción y sin acción.
- **D (amortiguación)** = 1 − A_action / A_no_action, A = exceso de espera acumulado de todos los pasajeros del corredor hasta la recuperación.
