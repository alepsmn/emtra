# WP13 — Material para el documento técnico

**Hito:** se ejecuta dos veces: tras WP6 (documento H4, 30-nov) y tras WP12 (documento H5, 17-dic) · **Depende de:** lo que haya en `main`.

El documento técnico está en inglés y lo montan los humanos. Este WP genera las piezas listas para pegar; no edita el `.docx`.

## Entregables (`docs/design/generated/`, todo en inglés)
`make docs` genera desde el código, sin texto escrito a mano:
- `communication.md` — tablas de topics MQTT y Kafka desde `contracts/topics.yaml`; tabla de mensajes (schema, campos, productor) desde los modelos pydantic.
- `storage.md` — tablas y vistas de FogStore desde `contracts/sql/`; estructura de `data/lake/` y modelos dbt.
- `deployment.puml` — servicios, puertos y volúmenes desde `infra/docker-compose.yml` + procesos Python del Makefile.
- `classes_<service>.puml` — un diagrama de clases por servicio con las clases del `core` (a mano si `pyreverse` sale ilegible; máx. 10 clases por diagrama).
- `components_status.md` — cada componente de la Tabla 41 del H3 con su módulo real o "designed, not implemented".
- `tests_summary.md` — por WP: nº de tests de aceptación, en verde/rojo, y el KPI que cubre cada uno.

Colores de subsistema en los diagramas: Edge `#BB7BBB`, Communication Management `#8C5924`, Knowledge & Intelligence `#2D5D1F`, Configuration Management `#5A3081`, User Interface `#2A609B`.

Además, a mano y en inglés:
- `docs/design/h4_sections.md` (primera pasada) y `h5_sections.md` (segunda): borrador de las secciones nuevas del documento — detailed design, implementation, tests, validation — construido con los REPORT de cada WP, `docs/DEVIATIONS.md` y `experiments/reports/*.md`. Frases cortas, sin relleno, cifras con su origen.

## Aceptación
```python
def test_make_docs_runs():          # make docs sale con 0 y regenera sin diferencias (idempotente)
def test_every_component_listed():  # los 15 componentes de la Tabla 41 aparecen en components_status.md
```

## Para humanos (no agente)
- Pegar las secciones en el documento, renderizar los `.puml`, revisar el límite de 50 páginas.
- Vídeo de H4: `make demo-v1` + grabación de pantalla + subtítulos a partir del guion del REPORT de WP5.
- Anexo A: horas por persona.
