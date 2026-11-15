# WP0 — Repo y stack base

**Hito:** H4 · **Depende de:** —

## Objetivo
Esqueleto del repo de `AGENTS.md` y la infraestructura de fog en Docker.

## Entregables
- `pyproject.toml` (uv, ruff, mypy, pytest) con las librerías de AGENTS.md que hagan falta hasta WP6.
- `Makefile` con todos los comandos de AGENTS.md. Los que aún no tienen contenido imprimen `TODO WPxx` y salen con 0.
- `infra/docker-compose.yml`:
  - `mosquitto` (eclipse-mosquitto:2, listener 1883 sin TLS, persistencia),
  - `timescaledb` (timescale/timescaledb, pg16),
  - `grafana` (grafana/grafana-oss, provisioning montado de `infra/grafana/`),
  - healthcheck en los tres. Versiones de imagen fijadas, no `latest`.
- `.github/workflows/ci.yml`: `make lint test` (sin Docker).
- Directorios vacíos de la estructura con `__init__.py` donde toque.

## Aceptación (`tests/acceptance/wp00/`)
```python
def test_makefile_has_all_targets():   # cada comando de la tabla de AGENTS.md existe en el Makefile
def test_compose_is_valid():           # `docker compose config` sale con 0
```
Manual: `make up` deja los 3 contenedores `healthy` en < 90 s; `make down` los para.

## Checklist
- [ ] `make setup && make lint test` en verde en un clon limpio.
- [ ] Grafana accesible en `localhost:3000` sin login (acceso anónimo de solo lectura).
