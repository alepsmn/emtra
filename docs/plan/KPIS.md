# KPIS.md — indicadores de validación y experimentos

Los objetivos son **propuestos**. El grupo los revisa y los congela el 30-nov en `config/kpis.yaml`; después solo cambian con fila en `DEVIATIONS.md`. Ningún experimento ajusta parámetros del sistema según sus propios resultados: si un KPI no se cumple, se informa.

Todo se mide en simulación contra la verdad del simulador (`runs/<run_id>/truth_*.parquet`, `faults.parquet`), con semillas de validación (INTERFACES §10).

## KPIs

| ID | F | Qué mide | Cómo | Objetivo propuesto | Exp. | WP |
|---|---|---|---|---|---|---|
| K1 | F1 | Cuota de instantáneas emitidas que llegan al registro | filas en `estimates` / estimaciones emitidas | ≥ 99 % | E1, E5 | 6 |
| K2 | F1 | Error de la llegada derivada | p95 de \|derivada − verdadera\| | ≤ 15 s | E1 | 6 |
| K3 | F1 | Falsas "nunca cumplidas" | nunca cumplidas cuyo bus llega de verdad a esa parada a más tardar en (última hora estimada = `last_seen_at + last_time_left_s`) + 300 s / todas las emitidas (E1 y E2 juntos) | ≤ 1 % | E1, E2 | 6 |
| K4 | F3 | Error del indicador | MAE de c_vh del sistema vs c_vh con llegadas verdaderas, por banda | ≤ 0,05 | E1 | 6 |
| K5 | F9 | Recall de detección | fallos `slowdown` y `blocked_segment` de severidad alta (2 tramos × 10 semillas) con alerta emparejada | ≥ 80 % | E2 | 6 |
| K6 | F9 | Retardo de detección | mediana de `t_open − t0` | ≤ 30 min (2 bandas) | E2 | 6 |
| K7 | F9 | Falsas alertas | alertas sin fallo por línea·sentido·semana | ≤ 1 | E2 | 6 |
| K8 | F9 | EWMA+CUSUM frente a Shewhart | recall de ambos a igual tasa de falsas alertas | ≥ Shewhart (se informa) | E2 | 6 |
| K9 | F10 | Amortiguación de la acción mejor rankeada | D medio e IC 95 % por escenario | D > 0 con IC que excluye 0 en ≥ 70 % de escenarios | E3 | 12 |
| K10 | F10 | Tiempo de respuesta del ranking | p95 de `runtime_s` | ≤ presupuesto (120 s) | E3 | 12 |
| K11 | F11 | Decisiones con verificación | decisiones con `emtra.recovery` / decisiones | 100 % | E5 | 12 |
| K12 | F4 | Error del MAE calculado | \|MAE con llegadas derivadas − MAE con llegadas verdaderas\| por banda de horizonte | ≤ 5 s | E1 | 10 |
| K13 | F12 | Cobertura del intervalo | cuota de llegadas dentro del intervalo al 90 % | 0,90 ± 0,02 | E4 | 11 |
| K14 | F12 | Anchura del intervalo | anchura mediana por celda vs un único intervalo global | ≤ la del intervalo único | E4 | 11 |
| K15 | F12 | Latencia de la consulta | p95 de `GET /arrival` a 50 rps | < 200 ms | E5 | 12 |
| K16 | D4 | Mensajes perdidos tras un corte de la nube | msg_id en MQTT − msg_id en Kafka | 0 | E5 | 12 |
| K17 | F9 | Carga del operador | alertas `action_required` por hora de servicio | ≤ 2 | E2 | 6 |

## Experimentos

| Exp | Ejecuciones | Salida |
|---|---|---|
| E1 | 10 días simulados sin fallos | K1–K4, K12: sistema vs verdad |
| E2 | 4 semanas limpias (20 días) + fallos con t₀ en `am_peak`: **población de K5** = `slowdown` y `blocked_segment`, severidad alta, 2 tramos × 10 semillas (40); **informativos** = `slowdown`/`blocked_segment` baja y media, y `breakdown`, `dispatch_delay`, `demand_surge` en las 3 severidades, 4 semillas (52); más 10 ejecuciones de control (mismas semillas, sin fallo) para medir el efecto en la verdad | matriz VP/FP/FN por tipo y severidad, K5–K8, K17, tabla de detección frente a tamaño del efecto |
| E3 | 20 escenarios (`dispatch_delay` y `breakdown`, severidad media/alta, 5 semillas), R = 30 | K9, K10; hold simulado vs regla de respaldo vs no acción |
| E4 | calibración con semillas 2000–2019, evaluación con 3000–3009 | K13, K14 |
| E5 | stack completo: Kafka caído 2 min con el mundo corriendo; decisión automática de cada recomendación; carga de la API de pasajero | K1, K11, K15, K16 |

`make experiments` corre E1–E4 (batch). E5 es `make it-resilience` (necesita Docker).

## Emparejamiento alerta ↔ fallo (E2)

Fallo f = (línea, sentido, tramo s, t₀). Alerta a = (línea, sentido, tramo s_a, t_open).

- Candidata si coinciden línea y sentido, `|s_a − s| ≤ 1` y `t₀ ≤ t_open ≤ t₀ + 60 min`. Para fallos sin línea (`slowdown`, `demand_surge`, `blocked_segment`) vale cualquier línea del tramo.
- Emparejamiento voraz por `t_open` ascendente; cada alerta y cada fallo, una vez como mucho.
- VP = parejas; FN = fallos sin pareja; FP = alertas sin pareja (incluidas todas las de días limpios).
- K5 cuenta cualquier nivel de alerta; K17 solo `action_required`.
- **Alcance de K5–K6–K8:** el objetivo de detección aplica solo a `slowdown` y `blocked_segment` de severidad **alta**, en los dos tramos, 10 semillas de validación por combinación. Las severidades baja y media, `breakdown`, `dispatch_delay` y `demand_surge` se miden y se reportan como informativos, sin objetivo, en una tabla de detección frente a tamaño del efecto. Tamaño del efecto (independiente del detector): diferencia, entre la ejecución con fallo y la misma semilla sin fallo, del c_vh verdadero (HF) y del OTP verdadero (LF) en las bandas completas de la hora siguiente a t₀; se promedia por tramo y se toma el peor valor sobre el tramo del fallo y sus adyacentes (mayor subida de c_vh, mayor bajada de OTP).
