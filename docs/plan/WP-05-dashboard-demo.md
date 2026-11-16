# WP5 — Dashboard del operador (F6) y demo v1

**Hito:** H4 · **Depende de:** WP0–WP4 · **Cierra H4: después de este WP, `make demo-v1` es lo que se graba.**

## 5.1 Dashboard (`infra/grafana/`)
Provisionado como código: datasource PostgreSQL de solo lectura sobre FogStore y dashboard `emtra-operator` en JSON. Nada se crea a mano en la UI. Lee FogStore directamente (decisión D11). Zona horaria Europe/Madrid, refresco 5 s, rango por defecto desde las 07:00 simuladas.

Paneles:
1. **Estado de la red** — tabla línea × tramo con el último estado, coloreada (`v_line_status_latest`).
2. **Mapa** — Geomap con las paradas coloreadas por el peor estado de su tramo (`stops` + `v_line_status_latest`).
3. **Indicador en el tiempo** — c_vh (u OTP) por banda con el rango de referencia sombreado; variables `$line`, `$segment`.
4. **Alertas abiertas** — nivel, tramo, distancia al rango, disparadores, antigüedad (`v_open_alerts`).
5. **Salud** — estado y última actualización de cada servicio, instantáneas por minuto (`service_health`).

## 5.2 Demo v1 (`scripts/demo_v1.sh`, `make demo-v1`)
Guion fijo, reproducible, ≈ 3 min de reloj:

1. `make down` + volúmenes fuera; `make up` (infra, migraciones, carga de `stops` y `schedule`).
2. Arranca Ingestor y Monitor con la línea base commiteada.
3. Arranca el mundo: `--seed 47 --speed 60` (`data_split.yaml`), de 06:00 a 10:15 simuladas (06:00–07:00 es calentamiento y 10:00–10:15 la cola que cierra la última banda; el dashboard muestra desde las 07:00), con `config/demo/faults_v1.yaml`: un `slowdown` de severidad alta en el tramo 1 a las 08:00 (`{type: slowdown, severity: high, t0: "08:00", segment_id: 1}`).
4. Imprime la URL del dashboard.
5. Al terminar el mundo, **comprueba** contra FogStore y sale con código ≠ 0 si falla algo:
   - hay `band_status` de las 12 bandas para la línea 19 en ambos tramos;
   - antes de las 08:00 no hay ninguna alerta `action_required`;
   - entre las 08:00 y las 09:00 se abre ≥ 1 alerta `action_required` en el tramo 1 (cualquier línea; el `slowdown` no tiene línea);
   - todos los servicios figuran `online` en `service_health`.
6. Deja el stack levantado para inspección; `make down` lo para.

Si con la semilla de la demo el fallo no dispara la alerta, se cambia la semilla o la hora del fallo en `config/demo/` (no los umbrales del monitor) y se anota en el REPORT.

## Aceptación (`tests/acceptance/wp05/`)
```python
def test_dashboard_json_valid():        # carga; cada panel tiene consulta; solo usa tablas/vistas que existen en contracts/sql
def test_panel_queries_run():           # integración: cada SQL de panel se ejecuta sin error contra FogStore poblado y tarda < 2 s
def test_demo_v1_end_to_end():          # integración, @slow: scripts/demo_v1.sh sale con 0
```

## Entregables para humanos
- Capturas de cada panel durante la demo en `docs/plan/reports/wp05/` (para el documento).
- En el REPORT: guion minuto a minuto de lo que se ve (base de los subtítulos del vídeo).

## Checklist
- [ ] `make demo-v1` dos veces seguidas desde cero da el mismo resultado.
- [ ] Ningún panel consulta `estimates` sin filtro temporal.
