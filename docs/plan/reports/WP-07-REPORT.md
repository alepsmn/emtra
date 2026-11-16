# WP-07 — REPORT
## Hecho
- **Kafka** (`apache/kafka:3.9.1`, KRaft, un nodo, heap 512 MB, volumen propio, auto-creación de topics desactivada) en `infra/docker-compose.yml`, con healthcheck. `infra/kafka/create_topics.py` (idempotente, generado desde `contracts/topics.yaml`; lo ejecuta `make up`).
- **Puente** (`services/bridge/`). `core.py` (puro, sin MQTT ni Kafka): `Outbox` sobre una conexión SQLite (`outbox(msg_id PK, topic, key, value, sent)`, orden = rowid, `INSERT OR IGNORE`) y `Router` (rutas MQTT↔Kafka y claves generadas de `topics.yaml`). `__main__.py` (adaptador): el callback de MQTT guarda en el outbox y solo entonces devuelve (paho envía el PUBACK al volver); el hilo principal entrega el outbox a Kafka en orden (`enable.idempotence=true`, `acks=all`) y marca `sent` al confirmarse; un hilo aparte consume de Kafka (commit manual tras el PUBACK de MQTT) y publica a QoS 1, retenido donde diga `topics.yaml`. Latido con `outbox_depth` dentro de `metrics`.
- **Sink** (`services/cloud_sink/`). `core.py` (puro, con `Clock` inyectado): `data/lake/raw/<topic>/dt=YYYY-MM-DD/part-<n>.ndjson.gz`, con `dt` = fecha UTC de `event_time`. Se escribe como `part-<n>.ndjson.gz.open` y se renombra al cerrar, de modo que dbt solo ve ficheros completos. Rotación por nº de mensajes (5 000) o por tiempo (30 s), por topic; el offset se confirma después de cerrar. Al arrancar descarta los `.open` huérfanos (sus mensajes no se confirmaron). Mensaje con `event_time` ilegible → `dt=unknown` (no se pierde ni bloquea el sink).
- **dbt** (`cloud/dbt/`, dbt-duckdb): `stg_snapshots`, `stg_arrivals`, `stg_status`, `stg_alerts`, todos deduplicados por `msg_id` antes de los tests. `make dbt` = `dbt build`. Un topic sin ficheros (p. ej. sin alertas en 30 min) compila a una relación vacía con las mismas columnas en vez de fallar.
- **Cableado:** `make up` ahora crea los topics y arranca puente y sink (`scripts/demo_v1.sh cloud`, idempotente); `make down` los para. `demo_v1.sh` los para antes de `reset`, los relanza después, y borra `data/bridge` y `data/lake` junto al volumen de Kafka (ver decisión 3). Puente y sink son servicios exigidos en línea en la comprobación final del demo. El panel de Salud de Grafana muestra `outbox_depth`. `emtra_common/reset.py` incluye el client id `bridge`.
- Tests: los 6 del spec (primer commit, en rojo) y 5 extra (`test_extra.py`): rutas, rotación del sink con reloj manual, deduplicación en staging con duplicados reales y topic ausente (sin Docker), reentrega de retenidos sin reenvío, latido con `outbox_depth` (con Kafka parado).

## No hecho / en rojo (con motivo)
- **Nada en rojo.** Aceptación vinculante cumplida sin relajar umbrales ni `skip`.
- **No he visto la columna nueva del panel de Salud renderizada** en Grafana. Lo verificado: el JSON es válido, `test_panel_queries_run` (WP5) ejecuta la consulta y una consulta equivalente vía `/api/ds/query` devuelve `outbox_depth` = 771 con Kafka parado.
- Ver limitaciones en "Supuestos".

## Supuestos y decisiones tomadas
Decisiones de spec recibidas (no reabiertas):
1. **Garantía del puente: al menos una vez, sin pérdidas.** Sin transacciones de Kafka. Tras matar el puente puede haber repetidos; lo vinculante es que estén todos los `msg_id` y que, por clave, la primera aparición de cada `msg_id` siga el orden de publicación (así lo comprueban los tests). Los duplicados se eliminan aguas abajo por `msg_id`.
2. **Contradicción del spec en 7.4.** `stg_snapshots` es una fila por estimación, así que `unique(msg_id)` no puede cumplirse. Allí el test de unicidad es sobre (`msg_id`, `estimate_pos`) (macro propia `unique_combination`, sin `dbt_utils` para no añadir paquete ni red). `stg_arrivals`, `stg_status` y `stg_alerts` mantienen `unique(msg_id)`. Todos deduplican antes del test. **Spec WP-07 corregido** (7.2, 7.3 y 7.4) en el commit de dbt. Consecuencia: un sondeo sin estimaciones no genera fila en `stg_snapshots`.
3. **Retenidos.** Al volver a suscribirse, el broker reentrega los retenidos de `fog/status/…`. No se borran las filas enviadas: la clave primaria `msg_id` las absorbe. Demostrado en `test_retained_redelivery_is_not_resent` (la reentrega llega —se ve en el log— y Kafka sigue con 1 mensaje) y en el unitario del outbox.
4. **Rotación por tiempo y `outbox_depth`** son infraestructura. Reloj de pared solo vía `emtra_common/clock.py` (`SystemClock` inyectado en `Sink`; el puente no usa reloj). `dt=` en UTC (INTERFACES no dice otra cosa). `outbox_depth` va en `metrics`: sin cambio de contrato.

Mías, a revisar:
- **Orden de confirmación:** MQTT se confirma tras el commit en SQLite (`synchronous=NORMAL`, WAL): sobrevive a la muerte del proceso, no a un fallo del sistema operativo. Suficiente para el prototipo; `FULL` costaría un fsync por mensaje.
- **El outbox no se purga**, así que crece sin límite (≈ 7 000 filas por demo de 4 h simulada). Con `msg_id` deterministas, un Kafka nuevo con un outbox viejo no recibiría nada: por eso el demo borra `data/bridge` y `data/lake` y el RUNBOOK lo avisa. Si se purga algún día, hará falta otra defensa contra la reentrega de retenidos.
- **Mosquitto** conserva por defecto como máximo 1 000 mensajes QoS 1 en cola para un cliente desconectado. Con el puente caído más de ~20 s a 50 msg/s se perderían mensajes **antes** de llegar al puente. No lo he cambiado (afecta también a Ingestor y Monitor); el test de reinicio mata el puente ~2 s.
- **Reintento ante fallo de entrega:** si Kafka declara fallida una entrega (tras `delivery.timeout.ms`, 5 min por defecto), el puente espera a que se asienten las demás y reenvía lo no confirmado desde el principio. Con productor idempotente el orden se conserva; es camino de código que **no he ejercitado** (los cortes medidos duran 30 s).
- **Sin `--outbox` en CLI:** la ruta del outbox, del lake, los grupos y los límites de rotación están en `config/cloud.yaml` (pydantic); los tests usan una copia con `--config`. `kafka_bootstrap` en `config/infra.yaml`.
- `make up` lanza procesos Python en segundo plano (hasta ahora solo lo hacía el demo). Lo pide el checklist del spec.
- **Dependencias:** `confluent-kafka`, `duckdb` y `dbt-duckdb`, todas de la lista de AGENTS.md. `dbt-duckdb` arrastra `dbt-core` 1.12.7 y unas 60 dependencias transitivas (`uv.lock`: +680 líneas). Telemetría de dbt desactivada (`send_anonymous_usage_stats: false`); `.user.yml` ignorado.
- **Correcciones a mis propios tests tras el primer commit** (ningún umbral cambiado): `test_cloud_to_fog_retained` tenía una carrera (un suscriptor tardío podía coincidir con la publicación en vivo, `retain=0`; ahora la comprobación decisiva es un suscriptor nuevo creado después de ver el mensaje); el helper `sink_caught_up` esperaba para siempre en un topic vacío.
- **Aviso operativo:** `scripts/demo_v1.sh stop` usa `pgrep -f` con el cwd del repo y se mata a sí mismo si lo invoca un shell cuyo texto contiene `-m services.ingestor ` (etc.). Me pasó una vez al lanzarlo dentro de un `bash -c` largo. Preexistente; no lo he tocado.
- Los `FAIL … Disconnected` de librdkafka llenan `runs/cloud/*.log` mientras Kafka está caído (~1 100 líneas tras varios cortes). El estado visible es `outbox_depth`.

## Cómo reproducir (un comando) y cuánto tarda
`make accept WP=07` — 4 min 6 s (11 tests, con Docker). `make it` — 17 min 24 s (23 tests). `make dbt` — segundos. `make accept WP=05` — 10 min 29 s (dos demos).

Salidas pegadas (ejecuciones finales, rama `wp-07-bridge-cloud`):
```
make lint test        -> ruff y mypy sin errores; 85 passed, 23 deselected (28,8 s)
make it               -> 23 passed (17 min 24 s)
make accept WP=07     -> 11 passed (4 min 6 s)
make accept WP=05     -> 10 passed (10 min 29 s); demo-v1, semilla 47: 4 comprobaciones ok
```
Checklist manual con `docker compose stop kafka` (estado limpio, Ingestor + Monitor + puente + sink vivos, mundo de 30 min semilla 42): FogStore pasa de 0 a 1 431 `estimates`, 60 `arrivals`, 5 `band_status`; los 4 servicios en `service_health` con `ok`; Grafana `/api/health` ok, dashboard HTTP 200 y consulta de salud respondida; el latido del puente reporta `outbox_depth` = 771. Al levantar Kafka el outbox queda en 771 filas, 0 sin enviar, y el sink escribe 706 + 60 + 5 = 771 líneas: sin pérdidas.

## Resultados medidos (tablas/figuras que sirven al documento)
**Corte de Kafka (K16)** — 2 000 mensajes a 50 msg/s (40 s), Kafka parado 30 s empezando a los 5 s de tráfico (no a mitad exacta: 30 s de corte no caben centrados en 40 s). Ejecución de `make accept WP=07`:

| Medida | Valor |
|---|---|
| Corte efectivo de Kafka | 30,2 s |
| Profundidad máxima del outbox | 1 687 mensajes |
| Vaciado tras volver Kafka (desde `compose start` hasta outbox a 0; incluye el arranque del broker) | 4,6 s |
| `msg_id` publicados en MQTT | 2 000 |
| `msg_id` distintos en Kafka | 2 000 (0 perdidos, 0 ajenos) |
| Mensajes totales en Kafka / duplicados | 2 000 / **0** |
| Orden por clave (primera aparición) | correcto en las 4 claves |

(Una ejecución previa de desarrollo dio 1 738 y 4,5 s.)

**Reinicio del puente** — 1 000 mensajes a 50 msg/s, `SIGKILL` a los 8 s, reinicio 2 s después con el mismo outbox y la misma sesión MQTT: al morir, 400 filas en el outbox (1 sin enviar). MQTT 1 000 `msg_id`; Kafka 1 000 distintos, 0 perdidos, **0 duplicados**, orden por clave correcto. Ojo: en esta ejecución la muerte no cayó entre «entregado a Kafka» y «marcado enviado», así que **el camino de duplicados no se ha observado**; solo está garantizado por diseño y por el test unitario.

**Sink** — demo-v1 completo (semilla 47, 4 h 15 min simuladas), lake tras la demo:

| Topic | Ficheros | Líneas | `msg_id` distintos | Mensajes en Kafka |
|---|---|---|---|---|
| `emtra.snapshots.v1` | 9 | 6 100 | 6 100 | 6 100 |
| `emtra.arrivals.v1` | 9 | 797 | 797 | 797 |
| `emtra.status.v1` | 7 | 80 | 80 | 80 |
| `emtra.alerts.v1` | 2 | 10 | 10 | 10 |
| `emtra.never_fulfilled.v1` | 1 | 1 | 1 | 1 |
| **Total** | 28 | 6 988 | 6 988 | 6 988 |

Un solo `dt=2026-11-16` en todos; el outbox tiene 6 988 filas, 0 sin enviar. `test_sink_writes_partitions` comprueba además dos días UTC y un `event_time` con `+01:00` (23:30 Z → `dt` del día 16).

**dbt — filas por modelo de staging.**

| Modelo | 30 min de mundo (test, semilla 42) | Demo completa (`make dbt`) | FogStore (demo) |
|---|---|---|---|
| `stg_snapshots` | 1 431 | 16 368 | `estimates` 16 368 |
| `stg_arrivals` | 60 | 797 | `arrivals` 797 |
| `stg_status` | 5 | 80 | `band_status` 80 |
| `stg_alerts` | 0 (topic vacío) | 10 | `alerts` 3 (10 versiones) |

`dbt build`: 13 de 13 (4 modelos, 9 tests). Las filas de staging coinciden con los `msg_id` distintos del lake; `stg_snapshots` con las estimaciones (1 431 = suma de `arrivals` de los 706 sondeos del lake; los que no traen estimaciones no dan fila).

## Filas añadidas a DEVIATIONS.md
Ninguna: no hay cambio de contrato (`INTERFACES.md`, `contracts/` y `topics.yaml` sin tocar). `outbox_depth` es una clave nueva dentro de `service_health.metrics` (decisión 4). La corrección del spec WP-07 (7.2–7.4) no es un contrato; queda anotada aquí.
