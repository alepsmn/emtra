# WP-05 — REPORT
## Hecho
- **Dashboard** `emtra-operator` como código: `infra/grafana/dashboards/emtra-operator.json` (zona Europe/Madrid, refresco 5 s, rango absoluto 16-nov 05:30–09:30 UTC, que es el tiempo simulado) y datasource `FogStore` provisionado con el rol de solo lectura `grafana_ro` (migración `006_grafana_readonly.sql`: solo `SELECT`). Cinco paneles: Estado de la red (tabla coloreada, `v_line_status_latest`), Mapa (Geomap, peor estado del tramo por parada), Indicador en el tiempo (c_vh a la izquierda con sombreado 0,30/0,40; OTP a la derecha; variables `$line` y `$segment`), Alertas abiertas (`v_open_alerts`, antigüedad = `updated_at − opened_at`, tiempo de evento) y Salud (`service_health`: estado, última actualización, instantáneas/min). Ningún panel toca `estimates` (las instantáneas/min salen de `service_health.metrics`).
- **`make demo-v1`** → `scripts/demo_v1.sh` + `scripts/demo_v1_check.py`. Parámetros en `config/demo/demo_v1.yaml`; escenario en `config/demo/faults_v1.yaml` (`slowdown` alta, tramo 1, 08:00). Las comprobaciones leen la hora y el tramo del fallo de ese mismo fichero.
- **Estado limpio** (petición añadida). Antes de arrancar, el guion: (1) mata los procesos EMTRA de ejecuciones anteriores (`services.ingestor`, `services.reliability_monitor`, `sim.world` cuyo cwd es este repo; SIGTERM y SIGKILL a los 30 s); (2) `docker compose down -v` y `make up` (spec); (3) `python -m emtra_common.reset`: descarta las 5 sesiones MQTT persistentes (`ingestor`, `ingestor-pub`, `reliability-monitor`, `reliability-monitor-pub`, `sim-world-<semilla demo>`) conectando con sesión limpia, borra los mensajes retenidos bajo `emtra/v1/` y hace `TRUNCATE` de las 12 tablas de FogStore (todas salvo `schema_migrations`); (4) recarga `stops` y `schedule`. El paso (3) es redundante tras `down -v`, pero es independiente: se prueba solo, sobre un stack sucio.
- **Comprobación de repetibilidad:** `test_demo_v1_repeatable` ejecuta el guion dos veces seguidas y compara una huella del FogStore (`runs/demo-v1/fingerprint.json`: recuentos de `estimates`, `arrivals`, `never_fulfilled`, `band_status`, `alerts` y un SHA-256 del contenido de `band_status` y de `alerts`; solo columnas en tiempo de evento, sin `service_health`).
- `make up` ahora también carga `stops` y `schedule` (INTERFACES §6 ya lo decía); `make down` primero para los procesos EMTRA. `sim/world.py` acepta `--start HH:MM` (el motor ya lo soportaba). `emtra_common.corridor.service_datetime`. Marcador `slow` en `pyproject.toml`.
- Tests (`tests/acceptance/wp05/`, primer commit en rojo): los 3 del spec más `test_no_estimates_query_without_time_filter` (checklist), `test_grafana_role_is_read_only`, `test_service_datetime`, `test_reset_covers_every_mqtt_client_id`, `test_reset_clears_sessions_retained_and_tables`, `test_stop_kills_leftover_processes` y `test_demo_v1_repeatable`.

## No hecho / en rojo (con motivo)
- Nada en rojo: `make lint test` 51 passed; `make accept WP=05` 10/10.
- **Capturas de los paneles (`docs/plan/reports/wp05/`): no hechas.** La extensión de Chrome no estaba conectada. Lo verificado: el dashboard y el datasource se provisionan (API de Grafana) y las consultas de los 5 paneles devuelven datos a través de `/api/ds/query` de Grafana. **No he visto el renderizado** (colores, Geomap). El Geomap usa teselas de Carto: necesita internet al grabar.
- Cada ejecución de `make accept WP=05` lanza la demo dos veces (~8,5 min) y deja el stack arriba.

## Supuestos y decisiones tomadas
- **Horizonte del mundo 3,25 h (07:00–10:15), no 3 h.** Con 3 h la banda 09:45 no se cierra (se cierra con marca de agua ≥ 10:00 + `allowed_lateness_s` = 150 s) y no habría 12 bandas. El cuarto de hora extra es la cola de cierre. Está en `config/demo/demo_v1.yaml`.
- `online` = `status = 'ok'` y última actualización ≤ 30 s (`health_max_age_s`, 3 latidos). Solo Ingestor y Monitor tienen latido; el mundo no.
- "Se abre una alerta entre 08:00 y 09:00" = `opened_at` ∈ [08:00, 09:00] (`opened_at` es el fin de banda). "Antes de las 08:00" = `opened_at < 08:00` con nivel final `action_required`.
- **El fallo se detecta con semilla 42 sin cambiar semilla ni hora.**
- Disponibilidad de los servicios: el guion espera a que ambos escriban latido y espera 3 s (`ready_grace_s`) a que se establezcan las suscripciones MQTT; no hay un apretón de manos real. Si una suscripción tardara más, se perderían instantáneas y la huella lo delataría.
- El fin del mundo se detecta esperando a que los recuentos de FogStore no cambien durante 5 s (`settle_stable_s`).
- El reset exige servicios parados (si no, el latido reescribe `service_health`): el guion los mata antes; `test_reset_…` los para explícitamente.
- Variables `$line` y `$segment` son listas fijas (`19,C1,170` y `1,2`), no consultas: con la tabla vacía al cargar, una variable de consulta quedaría sin valor.
- Dependencias nuevas: ninguna.

## Cómo reproducir (un comando) y cuánto tarda
`make demo-v1` — **3 min 53 s** de reloj en la ejecución medida (≈ 35 s de infraestructura y reset + 3 min 15 s de mundo a 60× + cierre). Repetibilidad y aceptación: `make accept WP=05` — 8 min 33 s. Parar: `make down`.

## Resultados medidos (tablas/figuras que sirven al documento)
**Repetibilidad.** 4 ejecuciones desde cero (1 manual, 2 en el test, 1 manual) con huella idéntica: `estimates` 13 370, `arrivals` 654, `never_fulfilled` 2, `band_status` 60, `alerts` 2; SHA-256 de `band_status` `a9e947c5…`, de `alerts` `dc93bde7…`.

**Línea 19 (c_vh, nivel de servicio), mundo 07:00–10:00, fallo a las 08:00:**

| banda | tramo 1 | tramo 2 |
|---|---|---|
| 07:00 | A (0,18) | sin datos |
| 07:15 | B (0,26) | E (0,56) |
| 07:30 | D (0,42) | E (0,59) |
| 07:45 | C (0,33) | E (0,70) |
| 08:00 | C (0,35) | E (0,56) |
| 08:15 | **E (0,67)** | F (1,09) |
| 08:30 | B (0,25) | E (0,68) |
| 08:45 | C (0,38) | C (0,38) |
| 09:00 | C (0,32) | E (0,57) |
| 09:15 | B (0,25) | F (0,92) |
| 09:30 | A (0,18) | D (0,41) |
| 09:45 | B (0,23) | C (0,36) |

**Alertas:** línea 170, tramo 1, `action_required` (ewma + cusum), abierta a las 08:45 (45 min después del fallo; cerrada a las 10:00); línea 19, tramo 2, `action_required`, abierta a las 09:30 (sigue abierta). Ninguna antes de las 08:00.

**Lectura honesta de la demo.** La alerta que cumple la comprobación es de la línea 170 (OTP), no de la 19. En la línea 19 el tramo 2 está en E/F ya desde las 07:15, antes del fallo: coincide con la dispersión alta que WP4 midió en ese tramo en días limpios (c_vh 0,364 ± 0,177 en am_peak); no he separado cuánto es arranque en frío del corredor (empieza vacío a las 07:00) y cuánto dispersión propia. El efecto visible del `slowdown` en la línea 19 es el pico del tramo 1 a las 08:15 (E, 0,67). Para el vídeo conviene narrar eso y no prometer "el tramo 2 estaba sano antes".

**Guion minuto a minuto (base de los subtítulos; 60×: 1 min de reloj = 1 h simulada). Deducido de los datos de FogStore, no visto en pantalla: hay que contrastarlo con el vídeo.**
| reloj | hora simulada | qué se ve |
|---|---|---|
| 0:00 | — | Salida del script: estado limpio, contenedores nuevos, reset, servicios en marcha, URL del dashboard |
| 0:35 | 07:00 | Arranca el mundo. Panel Salud: Ingestor y Monitor `online`, instantáneas/min subiendo. Mapa vacío (grises) hasta cerrarse las primeras bandas |
| 0:50 | 07:15 | Aparecen los primeros estados; línea 19 tramo 2 ya en rojo |
| 1:35 | 08:00 | Se inyecta el `slowdown` en el tramo 1 (no aparece en el dashboard: el fog no conoce la causa) |
| 1:50 | 08:15 | Línea 19, tramo 1: el indicador salta a 0,67 (E); el tramo 1 se pinta en rojo en el Mapa |
| 2:20 | 08:45 | Se abre la alerta `action_required` de la línea 170, tramo 1 (panel Alertas abiertas) |
| 3:05 | 09:30 | Segunda alerta, línea 19 tramo 2 |
| 3:35 | 10:00 | Última banda con dato (09:45). El mundo sigue 15 min más (cola de cierre) |
| 3:50 | 10:15 | Fin del mundo; el script comprueba y termina con `demo-v1 OK`. El stack queda arriba |

(Los relojes son aproximados: 35 s de preparación + tiempo simulado / 60. La hora del primer fallo en pantalla depende de lo que tarde el panel en refrescar, 5 s.)

## Filas añadidas a DEVIATIONS.md
Ninguna. INTERFACES §6 recibe una línea sobre el rol `grafana_ro` (no cambia ningún contrato existente).
