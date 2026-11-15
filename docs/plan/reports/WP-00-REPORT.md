# WP-00 — REPORT
## Hecho
- `pyproject.toml` (uv, ruff, mypy, pytest) con las librerías de AGENTS.md hasta WP6 (sin confluent-kafka, duckdb ni dbt: llegan en WP7). `uv.lock` incluido.
- `Makefile` con los 14 comandos de la tabla; los que aún no tienen contenido imprimen `TODO WPxx` y salen con 0. `make accept WP=03` ejecuta `tests/acceptance/wp03`.
- `infra/docker-compose.yml`: mosquitto 2.0.20, timescaledb 2.17.2-pg16, grafana-oss 11.3.0, healthcheck en los tres, versiones fijadas. Grafana con acceso anónimo Viewer y datasource `FogStore` provisionado.
- `.github/workflows/ci.yml`: `make setup` + `make lint test`.
- Estructura de directorios de AGENTS.md con `__init__.py` (y `.gitkeep` en los que no son paquetes).
- Tests de aceptación `test_makefile_has_all_targets` y `test_compose_is_valid`, escritos en el primer commit (en rojo) y ahora en verde.

## No hecho / en rojo (con motivo)
- Nada en rojo. `make` no venía instalado en la WSL; se extrajo `make` 4.4.1 del .deb a `~/.local/bin` (sin root). Para CI y otros equipos: `sudo apt install make`.
- No probado en un clon limpio (checklist 1); `make setup lint test up down` sí verificado en el árbol de trabajo.

## Supuestos y decisiones tomadas
- Puerto del host de TimescaleDB: **5433** (→5432 del contenedor). El 5432 lo ocupa otro contenedor de la máquina (`vinoteca-db`). Los servicios deben conectar a `localhost:5433`. Dentro del compose (Grafana) sigue siendo `timescaledb:5432`.
- Credenciales locales `emtra/emtra/emtra` en el compose (BD solo local, sin secretos reales).
- `make test` ejecuta `tests/unit` y `tests/acceptance` (los de WP0 no necesitan servicios; `test_compose_is_valid` exige el binario docker, también en CI de GitHub). Sin `skip`.
- `make lint` aplica mypy solo a `contracts/` y `emtra_common/`, como pide AGENTS.md.
- `make up` usa `--wait --wait-timeout 90`; falla si los tres no están healthy en 90 s.

## Cómo reproducir (un comando) y cuánto tarda
`make setup && make lint test && make up` — `up` deja los 3 contenedores healthy en ~11 s (imágenes ya descargadas); `uv sync` ~ segundos con caché.

## Resultados medidos (tablas/figuras que sirven al documento)
Ninguno aplicable.

## Filas añadidas a DEVIATIONS.md
Ninguna.
