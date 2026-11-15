# WP-05 — REPORT
## Hecho
- **Dashboard** `emtra-operator` como código: `infra/grafana/dashboards/emtra-operator.json` (zona Europe/Madrid, refresco 5 s, rango absoluto 16-nov 06:00–09:30 UTC = 07:00–10:30 Madrid, que es el tiempo simulado; el mundo arranca a las 06:00 y el dashboard muestra desde las 07:00) y datasource `FogStore` provisionado con el rol de solo lectura `grafana_ro` (migración `006_grafana_readonly.sql`: solo `SELECT`). Cinco paneles: Estado de la red (tabla coloreada, `v_line_status_latest`), Mapa (Geomap, peor estado del tramo por parada), Indicador en el tiempo (c_vh a la izquierda con sombreado 0,30/0,40; OTP a la derecha; variables `$line` y `$segment`), Alertas abiertas (`v_open_alerts`, antigüedad = `updated_at − opened_at`, tiempo de evento) y Salud (`service_health`: estado, última actualización, instantáneas/min). Ningún panel toca `estimates` (las instantáneas/min salen de `service_health.metrics`).
- **`make demo-v1`** → `scripts/demo_v1.sh` + `scripts/demo_v1_check.py`. Parámetros en `config/demo/demo_v1.yaml` (la semilla sale de `data_split.yaml`, `demo`); escenario en `config/demo/faults_v1.yaml` (`slowdown` alta, tramo 1, 08:00). Las comprobaciones leen la hora y el tramo del fallo de ese mismo fichero.
- **Estado limpio** (petición añadida). Antes de arrancar, el guion: (1) mata los procesos EMTRA de ejecuciones anteriores (`services.ingestor`, `services.reliability_monitor`, `sim.world` cuyo cwd es este repo; SIGTERM y SIGKILL a los 30 s); (2) `docker compose down -v` y `make up` (spec); (3) `python -m emtra_common.reset`: descarta las 5 sesiones MQTT persistentes (`ingestor`, `ingestor-pub`, `reliability-monitor`, `reliability-monitor-pub`, `sim-world-<semilla demo>`) conectando con sesión limpia, borra los mensajes retenidos bajo `emtra/v1/` y hace `TRUNCATE` de las 12 tablas de FogStore (todas salvo `schema_migrations`); (4) recarga `stops` y `schedule`. El paso (3) es redundante tras `down -v`, pero es independiente: se prueba solo, sobre un stack sucio.
- **Comprobación de repetibilidad:** `test_demo_v1_repeatable` ejecuta el guion dos veces seguidas y compara una huella del FogStore (`runs/demo-v1/fingerprint.json`: recuentos de `estimates`, `arrivals`, `never_fulfilled`, `band_status`, `alerts` y un SHA-256 del contenido de `band_status` y de `alerts`; solo columnas en tiempo de evento, sin `service_health`).
- `make up` ahora también carga `stops` y `schedule` (INTERFACES §6 ya lo decía); `make down` primero para los procesos EMTRA. `sim/world.py` acepta `--start HH:MM` (el motor ya lo soportaba). `emtra_common.corridor.service_datetime`. Marcador `slow` en `pyproject.toml`. `scripts/wp05_seed_selection.py`: tabla de selección de semilla (abajo).
- Tests (`tests/acceptance/wp05/`, primer commit en rojo): los 3 del spec más `test_no_estimates_query_without_time_filter` (checklist), `test_grafana_role_is_read_only`, `test_service_datetime`, `test_reset_covers_every_mqtt_client_id`, `test_reset_clears_sessions_retained_and_tables`, `test_stop_kills_leftover_processes` y `test_demo_v1_repeatable`. Los tests de arranque limpio van antes que los de la demo, para que `make accept WP=05` termine con el stack levantado y poblado, y los que no arrancan la demo usan una fixture `stack_up` (`make up`).

## No hecho / en rojo (con motivo)
- Nada en rojo: `make lint test` 51 passed; `make accept WP=05` 10/10 (10 min 29 s).
- **Capturas de los paneles (`docs/plan/reports/wp05/`): no hechas.** La extensión de Chrome no estaba conectada. Lo verificado: el dashboard y el datasource se provisionan (API de Grafana) y las consultas de los 5 paneles devuelven datos a través de `/api/ds/query` de Grafana. **No he visto el renderizado** (colores, Geomap). El Geomap usa teselas de Carto: necesita internet al grabar.
- Cada ejecución de `make accept WP=05` lanza la demo dos veces (~8,5 min) y deja el stack arriba.

## Supuestos y decisiones tomadas
- **Mundo de 06:00 a 10:15 (4,25 h).** La hora de calentamiento (06:00–07:00) llena el corredor antes de lo que se enseña; el dashboard arranca a las 07:00. Con el mundo acabando a las 10:00 la banda 09:45 no se cierra (se cierra con marca de agua ≥ 10:00 + `allowed_lateness_s` = 150 s) y no habría 12 bandas: el cuarto de hora final es la cola de cierre. Está en `config/demo/demo_v1.yaml`. Las comprobaciones siguen mirando las bandas 07:00–10:00.
- `online` = `status = 'ok'` y última actualización ≤ 30 s (`health_max_age_s`, 3 latidos). Solo Ingestor y Monitor tienen latido; el mundo no.
- "Se abre una alerta entre 08:00 y 09:00" = `opened_at` ∈ [08:00, 09:00] (`opened_at` es el fin de banda). "Antes de las 08:00" = `opened_at < 08:00` con nivel final `action_required`.
- **Semilla de la demo: 51 (decisión de presentación).** Con el calentamiento de 06:00, la semilla 42 deja de servir: 4 de las 16 bandas HF de 07:00–08:00 salen en LOS E/F antes del fallo. Criterio acordado: 0 bandas HF en E/F entre 07:00 y 08:00, 0 alertas abiertas antes de las 08:00 y primera `action_required` atribuible no más tarde de las 08:45; preferencia por que la alerta sea de la línea 19. Se midió en batch (mismo motor, calentamiento y fallo) con las semillas 42–61 (`uv run python -m scripts.wp05_seed_selection 42 61`, 8 s). Cumplen 51, 53, 54, 56, 57 y 59; **ninguna con la alerta en la línea 19** (solo la 42 y la 47 tienen la primera alerta en la línea 19 en ≤ 08:45: la 42 tiene 4 bandas HF en E/F y la 47 una alerta antes de las 08:00), así que se toma la primera que cumple: 51. (56 y 59 tienen la alerta en el tramo 2, lo que no pasaría la comprobación del tramo 1 de la demo.) Actualizados `config/data_split.yaml` (`demo`), `INTERFACES.md` §10 y el spec WP-05. Es una elección de la semilla que se enseña, hecha mirando el resultado de la demo; no afecta a las mediciones de detección (K5–K6 usan 3000+).

  | semilla | bandas HF E/F 07–08 (de 16) | alertas antes de las 08:00 | 1.ª `action_required` atribuible | línea | tramo | cumple |
  |---|---|---|---|---|---|---|
  | 42 | 4 | 0 | 08:30 | 19 | 1 | no |
  | 43 | 2 | 0 | 08:45 | 170 | 1 | no |
  | 44 | 3 | 0 | 08:45 | 170 | 1 | no |
  | 45 | 0 | 0 | — | — | — | no |
  | 46 | 1 | 0 | 08:45 | 170 | 1 | no |
  | 47 | 0 | 1 | 08:45 | 19 | 1 | no |
  | 48 | 2 | 0 | 08:45 | 170 | 1 | no |
  | 49 | 1 | 0 | 08:45 | 170 | 1 | no |
  | 50 | 5 | 0 | 08:30 | C1 | 2 | no |
  | **51** | **0** | **0** | **08:45** | **170** | **1** | **sí** |
  | 52 | 1 | 0 | 08:15 | C1 | 1 | no |
  | 53 | 0 | 0 | 08:45 | 170 | 1 | sí |
  | 54 | 0 | 0 | 08:45 | 170 | 1 | sí |
  | 55 | 1 | 0 | — | — | — | no |
  | 56 | 0 | 0 | 08:30 | C1 | 2 | sí |
  | 57 | 0 | 0 | 08:45 | 170 | 1 | sí |
  | 58 | 2 | 0 | 08:45 | 170 | 1 | no |
  | 59 | 0 | 0 | 08:45 | C1 | 2 | sí |
  | 60 | 2 | 0 | 08:45 | 170 | 1 | no |
  | 61 | 2 | 0 | 08:30 | 170 | 1 | no |

  "Atribuible" = KPIS.md: tramo a ≤ 1 del fallo, primera emisión con nivel `action_required` en [08:00, 09:00]. La hora es la de esa emisión (fin de banda).
- Disponibilidad de los servicios: el guion espera a que ambos escriban latido y espera 3 s (`ready_grace_s`) a que se establezcan las suscripciones MQTT; no hay un apretón de manos real. Si una suscripción tardara más, se perderían instantáneas y la huella lo delataría.
- El fin del mundo se detecta esperando a que los recuentos de FogStore no cambien durante 5 s (`settle_stable_s`).
- **Acceso al dashboard:** `http://localhost:3000/d/emtra-operator`. Grafana tiene acceso anónimo con rol Viewer y sin formulario de login: **no hay usuario ni contraseña**, se abre directamente. El script imprime la URL. Al terminar `make demo-v1` el stack queda levantado (contenedores y los procesos del Ingestor y del Monitor); `make down` lo para todo.
- El reset exige servicios parados (si no, el latido reescribe `service_health`): el guion los mata antes; `test_reset_…` los para explícitamente.
- Variables `$line` y `$segment` son listas fijas (`19,C1,170` y `1,2`), no consultas: con la tabla vacía al cargar, una variable de consulta quedaría sin valor.
- Dependencias nuevas: ninguna.

## Cómo reproducir (un comando) y cuánto tarda
`make demo-v1` — ≈ 5 min de reloj (≈ 35 s de infraestructura y reset + 4 min 15 s de mundo a 60× + cierre; estimado: con el mundo de 07:00–10:15 medí 3 min 53 s, y el calentamiento añade 1 min). Repetibilidad y aceptación: `make accept WP=05` — 10 min 29 s (dos demos). Parar: `make down`.

## Resultados medidos (tablas/figuras que sirven al documento)
**Repetibilidad.** Semilla 51, mundo 06:00–10:15. Las dos ejecuciones del test (desde cero cada una) dan la misma huella: `estimates` 16 588, `arrivals` 797, `never_fulfilled` 1, `band_status` 80, `alerts` 1; SHA-256 de `band_status` `6a5e3361…` y de `alerts` `ae2cd965…`. (La huella anterior, con semilla 42 y mundo desde las 07:00, también se repitió en 4 ejecuciones; ya no aplica.)

**Línea 19 (c_vh, nivel de servicio), fallo a las 08:00:**

| banda | tramo 1 | tramo 2 |
|---|---|---|
| 07:00 | C (0,33) | A (0,13) |
| 07:15 | A (0,11) | D (0,42) |
| 07:30 | C (0,32) | C (0,35) |
| 07:45 | B (0,25) | D (0,51) |
| 08:00 | **D (0,52)** | F (0,92) |
| 08:15 | C (0,39) | E (0,59) |
| 08:30 | C (0,39) | B (0,26) |
| 08:45 | B (0,27) | D (0,50) |
| 09:00 | A (0,19) | C (0,31) |
| 09:15 | A (0,17) | B (0,21) |
| 09:30 | D (0,43) | D (0,41) |
| 09:45 | A (0,19) | D (0,48) |

**Alertas:** una sola, línea 170, tramo 1, `action_required` (disparador `ewma`), abierta a las 08:45 (45 min después del fallo) y cerrada a las 09:15. Ninguna antes de las 08:00.

**Lectura honesta de la demo.** Como en la versión anterior, la alerta es de la línea 170 (OTP), no de la 19: con ninguna de las 20 semillas cumple a la vez "0 alertas antes de las 08:00", "≤ 08:45" y línea 19. En la línea 19 el efecto del `slowdown` es una banda D (0,52) en el tramo 1 a las 08:00; no llega a E/F ni dispara alerta de esa línea. El tramo 2 de la línea 19 sigue oscilando entre A y F antes y después del fallo (D a las 07:15 y 07:45): coincide con la dispersión alta que WP4 midió en ese tramo en días limpios (c_vh 0,364 ± 0,177 en am_peak). Para el vídeo: narrar que la detección sale por la línea 170 y que el tramo 2 es ruidoso por naturaleza.

**Guion minuto a minuto (base de los subtítulos; 60×: 1 min de reloj = 1 h simulada). Deducido de los datos de FogStore, no visto en pantalla: hay que contrastarlo con el vídeo.**
| reloj | hora simulada | qué se ve |
|---|---|---|
| 0:00 | — | Salida del script: estado limpio, contenedores nuevos, reset, servicios en marcha, URL del dashboard |
| 0:35 | 06:00 | Arranca el mundo (calentamiento; el dashboard muestra desde las 07:00). Panel Salud: Ingestor y Monitor `online`, instantáneas/min subiendo |
| 1:50 | 07:15 | Se cierra la primera banda del rango visible (07:00) y aparecen los primeros estados |
| 2:35 | 08:00 | Se inyecta el `slowdown` en el tramo 1 (no se ve: el fog no conoce la causa) |
| 2:50 | 08:15 | Se cierra la banda de las 08:00: línea 19, tramo 1 sube a D (0,52); el tramo 2 está en F |
| 3:20 | 08:45 | Se abre la alerta `action_required` de la línea 170, tramo 1 (panel Alertas abiertas) |
| 3:50 | 09:15 | La alerta se cierra (panel vacío) |
| 4:35 | 10:00 | Última banda con dato (09:45); el mundo sigue 15 min (cola de cierre) |
| 4:50 | 10:15 | Fin del mundo; el script comprueba y termina con `demo-v1 OK`. El stack queda arriba |

(Relojes aproximados: 35 s de preparación + tiempo simulado desde las 06:00 / 60; el panel refresca cada 5 s.)

## Filas añadidas a DEVIATIONS.md
Ninguna. INTERFACES §6 recibe una línea sobre el rol `grafana_ro` (no cambia ningún contrato existente).
