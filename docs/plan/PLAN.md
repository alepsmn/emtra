# PLAN.md — H4 + H5

Un agente, un WP por sesión, en este orden. Cada WP termina con algo ejecutable y su REPORT. No se empieza un WP con el anterior sin marcar.

## Fechas

| Fecha | Qué |
|---|---|
| 20-nov | `make demo-v1` funciona desde cero (WP0–WP5) |
| 21–24 nov | vídeo (≤ 3 min, subtitulado) y presentación — humanos |
| 25-nov | presentación H4 |
| 30-nov | documento H4; KPIs congelados en `config/kpis.yaml` |
| 11-dic | `make experiments` y `make demo-final` verdes (WP7–WP12) |
| 16-dic / 17-dic | presentación / documento H5 |

## Paquetes

| # | Fichero | Hito | Entrega | Hecho |
|---|---|---|---|---|
| WP0 | [WP-00-repo.md](WP-00-repo.md) | H4 | repo, Makefile, compose de fog | [x] |
| WP1 | [WP-01-contracts.md](WP-01-contracts.md) | H4 | `contracts/` y `emtra_common/` según INTERFACES | [x] |
| WP2 | [WP-02-simulator.md](WP-02-simulator.md) | H4 | simulador de corredor: batch y mundo, fallos, verdad | [x] |
| WP3 | [WP-03-ingestor.md](WP-03-ingestor.md) | H4 | F1: llegadas derivadas | [x] |
| WP4 | [WP-04-monitor.md](WP-04-monitor.md) | H4 | F3 + F9: indicador, detectores, alertas | [ ] |
| WP5 | [WP-05-dashboard-demo.md](WP-05-dashboard-demo.md) | H4 | F6 + `make demo-v1` | [ ] |
| WP6 | [WP-06-validation-v1.md](WP-06-validation-v1.md) | H4 (doc) | arnés de experimentos, E1 y E2 | [ ] |
| WP7 | [WP-07-bridge-cloud.md](WP-07-bridge-cloud.md) | H5 | Kafka, puente con outbox, sink local, dbt | [ ] |
| WP8 | [WP-08-action-planner.md](WP-08-action-planner.md) | H5 | F10: ranking por simulación pareada; respaldo D5 | [ ] |
| WP9 | [WP-09-decisions-recovery.md](WP-09-decisions-recovery.md) | H5 | F11: decisión, hold aplicado en el mundo, recuperación | [ ] |
| WP10 | [WP-10-f4-accuracy.md](WP-10-f4-accuracy.md) | H5 | F4: precisión de las estimaciones | [ ] |
| WP11 | [WP-11-f12-interval.md](WP-11-f12-interval.md) | H5 | F12: intervalo calibrado y API de pasajero | [ ] |
| WP12 | [WP-12-validation-final.md](WP-12-validation-final.md) | H5 | E3–E5, `make experiments`, `make demo-final` | [ ] |
| WP13 | [WP-13-docs.md](WP-13-docs.md) | H4 y H5 | tablas y diagramas generados para el documento | [ ] |

WP6 puede hacerse después del 20-nov: no bloquea la demo, pero sus tablas van en el documento del 30-nov. WP13 se ejecuta dos veces (tras WP6 y tras WP12).

## Cómo encaja H4 con H5

| Lo que H4 deja hecho | Lo que H5 construye encima |
|---|---|
| Motor del simulador con política de control inyectable y estado inicial como parámetro (WP2) | Evaluador pareado desde el estado actual (WP8); el mundo aplica el hold decidido (WP9) |
| `core` + `run_batch` de Ingestor y Monitor (WP3, WP4) | Experimentos E3–E5 sin tocar los servicios (WP12) |
| Tablas `recommendations`, `decisions`, `recoveries` y vista `v_alert_timeline` ya en el DDL (WP1) | Persistencia del lazo y paneles de H5 (WP8, WP9) |
| `estimates` + `truth_arrivals` (WP2, WP3) | F4 y calibración F12 (WP10, WP11) |
| Arnés `experiments/` con `kpis.py` y emparejamiento (WP6) | Se añaden escenarios y funciones de KPI (WP12) |

## Qué queda en diseño

F2 (conteo por cámara), F5 (causas), F7/F8 (eventos planificados), F13 (alternativas), ConfigurationManager en caliente (FR17), acciones distintas de hold, fuente real `LiveEmtSource` (D10: la interfaz existe, la implementación no), nodo fog de reserva.

## Definición de terminado (todos los WP)

- [ ] `make lint test` y `make accept WP=XX` en verde (o rojo explicado en el REPORT).
- [ ] Parámetros nuevos en `config/*.yaml`.
- [ ] Cambios de contrato reflejados en `INTERFACES.md` + `DEVIATIONS.md`.
- [ ] `docs/plan/reports/WP-XX-REPORT.md` escrito; WP marcado aquí.
