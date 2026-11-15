# ADR 0001 — Prototipo solo con datos sintéticos

- Estado: aceptada
- Fecha: 2026-11-15
- Autor (humano que aprueba): Milton Paredes

## Contexto
El H3 preveía ajustar el simulador con las capturas reales de la API de EMT y medir F4 y F12 sobre ellas. La asignatura evalúa el diseño y admite datos sintéticos coherentes con el sistema. Usar las capturas exige un parser de GTFS, un mapa de sentidos, fixtures sacados de la VM de los colectores y una carga a la nube, sin que ninguna medida gane una verdad contra la que compararse.

## Decisión
Todo el prototipo usa un corredor sintético definido en `config/corridors/c19.yaml`. El simulador genera el horario, las instantáneas y la verdad. No hay parser de GTFS ni replay de capturas reales. Las capturas y los colectores quedan fuera del repo.

## Consecuencias
- El Ingestor conserva la interfaz `DataSource` (D10) con dos implementaciones: fichero y MQTT. `LiveEmtSource` queda en diseño.
- F4 y F12 se miden sobre el error del predictor simulado; la cobertura del intervalo se valida con semillas separadas, no con semanas separadas.
- El paso 1 de la Tabla 48 (ajuste con registros de F1) y el paso 7 (validación frente a días reales) se sustituyen por parámetros fijados a mano y una comprobación de plausibilidad.
- El ActionPlanner evalúa con el mismo modelo que genera el mundo: la amortiguación medida es optimista. Se declara como limitación.
- El experimento E6 (datos reales, línea 19) desaparece.
