# WP-06 — REPORT

Estado de cierre de WP6. Rama `wp-06-validation-v1`. Los números "antes" de la calibración por indicador se reproducen con el commit `62472af`; los finales con `HEAD`.

## Hecho
- **Arnés `experiments/`**: `matching.py` (emparejamiento alerta↔fallo de KPIS.md), `runner.py` (escenario → ejecuciones; sim → ingestor → monitor, también con Shewhart; procesos `spawn`; caché por hash de semilla, fallos, configuración y código), `kpis.py` (una función por KPI: `{value, ci, n}`), `report.py` (PASS/FAIL leído de `config/kpis.yaml`, sin lógica por KPI), `__main__.py`, `scenarios/` (`e1_normal`, `e2_detection`, `e2_controls`), `config/kpis.yaml`. `make experiments` / `make experiments SMOKE=1`. Entregable: `experiments/reports/h4.md` (+ CSV y `detection_delay.png`).
- **Un solo comando** `make baseline`: calibra la gracia del Ingestor (semillas 2000–2019), regenera la línea base, los residuos (con hash) y L, h, z_max por indicador. Un test falla si `c19_residuals.npy` no corresponde a la línea base actual (SHA-256 en `c19_residuals.meta.yaml`; `load_residuals()` lanza `StaleResidualsError`).
- **Punto 0.** `make down` paró los procesos del demo (Ingestor y Monitor de 11 h) y el stack; tras `make up`, `make accept WP=03`: **18 de 18** (antes fallaba `test_live_equals_batch` por esos procesos).
- **Punto 1 — K3 y gracia.** K3: una nunca cumplida es falsa solo si el bus llega de verdad a esa parada a más tardar en (`last_seen_at + last_time_left_s`) + 300 s (`never_fulfilled_tolerance_s` en `config/kpis.yaml`); denominador: todas las emitidas en E1+E2. `never_fulfilled_grace_polls` se calibra con `python -m services.ingestor.tune_grace` (20 días limpios, semillas 2000–2019; ingestor con gracia mínima; para cada desaparición que reaparece en la misma parada se mide cuántos sondeos consecutivos faltó; N = máximo + 1, tope 12): salieron 24 candidatas, 23 reaparecen con huecos de 1 a 9 sondeos → N = 10 y `grace_s` 270. **Valores finales tras la regla del tope (punto 2 del cierre): N = 7, `grace_s` = (N − 1) × 30 s = 180** (en el código la gracia por tiempo y por sondeos se exigen a la vez; así coinciden). KPIS.md actualizado.
- **Punto 2 — efecto en la verdad:** máximo sobre el tramo afectado y sus adyacentes (|Δtramo| ≤ 1): por tramo se promedia (ejecución con fallo − misma semilla sin fallo) sobre las bandas completas de la hora siguiente a t₀; se toma la mayor subida de c_vh (HF) y la mayor bajada de OTP (LF).
- **Punto 3 — población de K5:** `slowdown` y `blocked_segment`, severidad alta, tramos 1 y 2, 10 semillas de validación por combinación (3030–3039, t₀ = 07:15 + 15 min × k): 40 ejecuciones. Informativos (52 ejecuciones, 4 semillas): `slowdown`/`blocked_segment` baja y media; `breakdown`, `dispatch_delay` y `demand_surge` en las 3 severidades. Tabla de detección frente a tamaño del efecto en `h4.md`. 10 ejecuciones de control sin fallo (semillas 3030–3039). KPIS.md y WP-06 actualizados.
- **Punto 4 — calibración por indicador:** L, h y z_max por separado para celdas c_vh y celdas OTP, mismo `arl0_target` (2000). `config/reliability.yaml` guarda `{cvh, otp}`; los detectores eligen el límite según el indicador de la banda (el estado de la terna es común). `make baseline` ejecutado. Semilla de la demo: **51 → 47** (ver abajo). `make accept WP=04`, `05`, `06`: ver el siguiente apartado.
- **Cierre, punto 1 — `test_ewma_detects_step`:** +3σ en ≤ 6 bandas para celdas c_vh y en **≤ 8 bandas para celdas OTP** (≥ 95/100 en ambos). Con el límite OTP calibrado (L = 5,28) es el 93 % en 6 bandas; el de 8 pasa. `WP-04-monitor.md` actualizado.
- **Cierre, punto 2 — regla de bus oculto por el tope de la lista.** Verificado con los datos y luego implementado tal como se pidió (detalle abajo). `api_max_buses_per_line: 2` en `config/ingestor.yaml` (un test comprueba que es igual al tope del predictor), `core.is_absence`, `make baseline` recalibra la gracia contando solo los sondeos que cuentan, DEVIATIONS 30. `make accept WP=03`: 21/21. **K2 = 14,46 s (PASS, sin cambio); K3 = 214/294 = 0,73 (FAIL).**
- **Cierre, punto 3:** K5 y K6 quedan como no cumplidos; frase para el documento más abajo.
- **Cierre, punto 4:** hora de la alerta de la demo con la semilla 47 más abajo; `make lint test` y `make accept WP=03 04 05 06` en verde.

## No hecho / en rojo (con motivo)
No hay tests en rojo. Tres KPIs quedan **no cumplidos**, medidos y reportados tal cual (los objetivos no se han cambiado):
- **K5: 26 de 40 (0,65; IC 0,50–0,78)** frente a ≥ 0,80. **K6: 45 min** frente a ≤ 30 min (mediana de los 26 detectados). Se declaran no cumplidos; no se ha hecho ninguna calibración más.
- **K3: 214 de 294 (0,73; IC 0,67–0,78)** frente a ≤ 0,01.
  - **Verificación previa de la causa (datos de E1+E2, `scripts/wp06_k3_hidden.py`).** En `blocked_segment` la lista de la línea estaba llena (2 entradas) en **todos** los sondeos de ausencia en 196 de las 201 falsas (media 9,9 de 10). Es el tope de la lista: en la lista de S08 (semilla 3030) las estimaciones de B19-007 a B19-011 se quedan en 170–200 s durante 14 minutos (06:30–06:44) —B19-010 estuvo retenido 620 s en S06— y la lista enseña solo 2 de ellos en cada sondeo; los otros entran y salen. Los `breakdown` genuinos (60 nunca cumplidas no falsas) tienen la lista llena solo en 3 de 60 casos. Causa confirmada.
  - **Pero la regla pedida solo explica una parte.** La segunda cláusula (una presente con estimación posterior a la última del ausente) deja pasar la mayoría: las estimaciones de los buses retenidos se desplazan hacia delante con el reloj, y casi cualquier presente tiene una hora absoluta posterior a la última del ausente. Medido con N = 10 antes de implementar: solo 2 de las 201 quedaban totalmente explicadas por el tope y 76 de 201 no se habrían confirmado. Implementada la regla (N = 7, 180 s): `blocked_segment` 249 de 272 falsas (antes 201 de 210), `slowdown` 11 de 13, días limpios (E1+E2) 2 emitidas, las 2 falsas, `breakdown` 0 de 60 (las genuinas), `dispatch_delay` y `demand_surge` 0 emitidas. Es decir, la regla elimina las nunca cumplidas de `dispatch_delay`/`demand_surge` y de los días limpios casi por completo, pero no las de `blocked_segment`.
  - **Una regla más simple (lista llena = no cuenta) cubriría 244 de 249 falsas de `blocked_segment`, pero también ocultaría las 23 nunca cumplidas genuinas de `blocked_segment` (bus retenido que llega tarde), todas con la lista llena.** No está implementada; la decisión es vuestra.
- **K1** pasa pero es 100 % por construcción en batch (DEVIATIONS 26).

## Supuestos y decisiones tomadas
- **Rama:** `wp-06-validation-v1` sale de `wp-05-dashboard-demo`; cuando volví al repo estaba en `main` (WP5 ya mergeado) y la devolví a la rama de WP6.
- **Ubicaciones:** la población de K5 usa t₀ = 07:15 a 09:30 (una por semilla) y los dos tramos; los informativos conservan las 4 ubicaciones de antes (semillas 3030–3033, t₀ 07:30/08:15/08:45/09:15; `breakdown`/`dispatch_delay` en líneas 19, C1, 170, 19; `demand_surge` en S02, S09, S04, S07). Nada se ha reubicado según resultados.
- **K7 y K17** se calculan en los 20 días limpios (5 días = 1 semana, 3 línea·sentido, 18 h/día). **K6** es la mediana solo de los detectados. **K8** (Shewhart): `z_max` por indicador por ARL₀ ≥ 2000 sobre los residuos de cada indicador (DEVIATIONS 25).
- **Tests existentes que he tocado, sin cambiar umbrales:** `test_midpoint_rule` (WP3), caso (b): alimenta `never_fulfilled_grace_polls + 2` sondeos vacíos en lugar de 8 (con N = 7, 8 ya no bastan; la intención del caso no cambia). `test_tuned_arl0` (WP4): parametrizado por indicador, mismo umbral (≥ 0,85 × ARL₀ objetivo). `test_ewma_detects_step` (WP4): ≥ 95/100 por indicador, con 6 bandas (c_vh) u 8 (OTP) por decisión de spec (cierre, punto 1).
- **Gracia por sondeos y `allowed_lateness_s`:** el comentario de `config/reliability.yaml` ("> grace_s del Ingestor") ya no se cumple (150 < 180). He comprobado que no afecta: el Monitor no lee las nunca cumplidas. Comentario y spec WP-04 corregidos.
- **Efecto en la verdad:** si la falla tiene línea (`breakdown`, `dispatch_delay`) solo cuentan las ternas de esa línea; si no, todas. Cuentan las bandas completas dentro de [t₀, t₀ + 1 h) (4 si t₀ está alineado a :15; 3 si no).
- **Dependencias nuevas:** ninguna.

## Cómo reproducir (un comando) y cuánto tarda
`make baseline` ~1,5 min (gracia ~30 s, línea base y residuos, tuner). `make experiments` ~43 s en frío con 20 núcleos (E1 10 días + E2 112 ejecuciones + 10 controles). `make experiments SMOKE=1` ~15 s. Final: `make lint` verde; `make test` 81 pasan; `make accept WP=03` 21/21 (97 s), `WP=04` 19/19 (89 s), `WP=05` 10/10 (630 s, dos demos completas con semilla 47), `WP=06` 18/18 (17 s).

## Resultados medidos (tablas/figuras que sirven al documento)
Semillas de validación: E1 3000–3009; E2 limpios 3010–3029, fallos 3030–3039, controles 3030–3039. Detalles completos en `experiments/reports/h4.md`.

### K3 a lo largo de las decisiones
| paso | K3 | emitidas |
|---|---|---|
| definición original (cualquier llegada verdadera), gracia 120 s | 250/270 = 0,93 | 270 |
| definición nueva (llegada ≤ última estimada + 300 s), gracia por sondeos N = 10 | 207/273 = 0,76 | 273 |
| tras la regla del tope de la lista, N = 7 (final) | 214/294 = 0,73 [0,67; 0,78] | 294 |

### Antes y después del punto 4 (calibración por indicador)
"Antes" = límites comunes L 4,69, h 8,87, z_max 6,78 (commit `62472af`); "después" = por indicador. Después de los puntos 1–3 en ambos casos.

| KPI | antes | después | objetivo |
|---|---|---|---|
| K3 falsas "nunca cumplidas" | 207/273 = 0,76 [0,70; 0,81] | 207/273 = 0,76 [0,70; 0,81] | ≤ 0,01 |
| K5 recall (alta, 2 tramos × 10) | 22/40 = 0,55 [0,40; 0,69] | 26/40 = 0,65 [0,50; 0,78] | ≥ 0,80 |
| K6 retardo mediano | 45 min (n = 22) | 45 min (n = 26) | ≤ 30 min |
| K7 falsas por línea·sentido·semana | 0,33 [0,08; 0,67] (4 alertas) | 0,17 [0; 0,42] (2 alertas) | ≤ 1 |

K3 no cambia porque no depende de los detectores. K5 sube por `blocked_segment` en el tramo 2. K8 pasa de +0,20 (recall 0,55 frente a 0,35 de Shewhart) a +0,275 (0,65 frente a 0,375); K17 de 0,011 a 0,0028 alertas/h.

**K5 y K6 por tipo × tramo** (detectadas/10; mediana de retardo de las detectadas; efecto verdadero medio, c_vh):

| tipo | tramo | antes | después | efecto Δc_vh |
|---|---|---|---|---|
| blocked_segment | 1 | 10/10, 45 min | 10/10, 45 min | +0,675 |
| blocked_segment | 2 | 0/10 | 4/10, 45 min | +0,121 |
| slowdown | 1 | 9/10, 45 min | 9/10, 45 min | +0,240 (ΔOTP −0,271) |
| slowdown | 2 | 3/10, 60 min | 3/10, 45 min | +0,137 |

**Calibración de los detectores** (ARL₀ por bootstrap de los residuos de la línea base, 20 días; objetivo 2000):

| celdas | residuos | EWMA L (ARL₀) | CUSUM h (ARL₀) | Shewhart z_max (ARL₀) |
|---|---|---|---|---|
| comunes (antes) | 6860 | 4,69 (2047) | 8,87 (2038) | 6,78 (2287) |
| c_vh | 4302 | 3,95 (2006) | 7,47 (2027) | 5,08 (2151) |
| OTP | 2558 | 5,28 (2008) | 10,36 (2031) | 7,25 (2558) |

### Detección frente a tamaño del efecto (informativos, después del punto 4)
Medias sobre 4 ejecuciones; Δc_vh = mayor subida del c_vh verdadero sobre el tramo y sus adyacentes.

| tipo | severidad | detectadas | Δc_vh medio | Δc_vh detectadas | Δc_vh no detectadas | ΔOTP |
|---|---|---|---|---|---|---|
| slowdown | baja | 1/4 | +0,027 | +0,037 | +0,023 | 0 |
| slowdown | media | 1/4 | +0,041 | +0,038 | +0,041 | −0,077 |
| blocked_segment | baja | 1/4 | +0,081 | +0,236 | +0,029 | 0 |
| blocked_segment | media | 2/4 | +0,226 | +0,439 | +0,012 | 0 |
| breakdown | baja | 1/4 | +0,329 | +0,544 | +0,221 | −0,125 |
| breakdown | media | 0/4 | +0,179 | — | +0,179 | −0,143 |
| breakdown | alta | 0/4 | +0,150 | — | +0,150 | 0 |
| dispatch_delay | baja | 0/4 | +0,106 | — | +0,106 | −0,133 |
| dispatch_delay | media | 1/4 | +0,164 | — | +0,164 | −0,306 |
| dispatch_delay | alta | 3/4 | +0,214 | +0,232 | +0,178 | −0,291 |
| demand_surge | baja/media/alta | 0/12 | ≤ +0,008 | — | ≤ +0,008 | 0 |

Lectura (solo lo medido): `demand_surge` no tiene efecto verdadero y no se detecta. `breakdown` tiene un efecto verdadero grande (Δc_vh +0,15 a +0,33) y casi no se detecta (1/12), con un efecto comparable al de `slowdown` alta (+0,14 a +0,24), que sí se detecta en el tramo 1; en `blocked_segment` y `dispatch_delay` los runs detectados tienen más efecto que los no detectados. El efecto máximo de `blocked_segment` en el tramo 1 (+0,675) está en el tramo adyacente (2): las alertas salen allí, no en el tramo bloqueado.

### Semilla de la demo (punto 4)
`scripts/wp05_seed_selection.py` con los mismos criterios (0 bandas HF en E/F entre 07:00 y 08:00, 0 alertas antes de las 08:00, primera `action_required` atribuible ≤ 08:45), semillas 42–61, calibración por indicador. **La 51 deja de cumplir** (no hay ninguna `action_required` atribuible). Cumplen 47 (línea 19, tramo 1, 08:30), 53 (línea 170, tramo 1, 08:45), 56, 57 y 59 (C1, tramo 2). Elegida la **47** (con el demo real, `make accept WP=05`: primera `action_required` de la **línea 19, tramo 1, abierta a las 08:30 hora simulada de Madrid**, la hora del fin de la banda 08:15–08:30, 30 min después del fallo de las 08:00; luego la línea 170, tramo 1, a las 08:45, y C1, tramo 2, a las 09:00; las tres cerradas a las 09:15–09:30; ninguna alerta antes de las 08:00). Es la primera que cumple y la única con alerta en la línea 19 en el tramo 1, que era la preferencia de WP5. Actualizados `config/data_split.yaml`, INTERFACES §10 y el spec WP-05. `make accept WP=05` con la 47: 10/10. El REPORT de WP5 conserva sus tablas con la 51.

> **For the document (EN):** The detection targets are not met for high-severity segment faults. Of 40 faults (slowdown and blocked segment, both segments, 10 seeds each), 26 were detected (recall 0.65, 95 % CI 0.50–0.78; target ≥ 0.80) and the median detection delay of the detected ones was 45 min, three bands (target ≤ 30 min). Detection depends on the fault type and the segment, as the table shows, together with the true effect on c_vh (fault run minus the same seed without fault, worst over the fault's segment and its neighbours, hour after onset).
>
> | Fault type | Segment | Detected / n | Median delay of detected (min) | True effect Δc_vh |
> |---|---|---|---|---|
> | Blocked segment | 1 | 10 / 10 | 45 | +0.675 |
> | Blocked segment | 2 | 4 / 10 | 45 | +0.121 |
> | Slowdown | 1 | 9 / 10 | 45 | +0.240 |
> | Slowdown | 2 | 3 / 10 | 45 | +0.137 |
>
> A blocked segment in segment 1 is always detected because its effect appears in the next segment (segment 2); false alerts on clean days are 0.17 per line·direction·week (target ≤ 1).

## Filas añadidas a DEVIATIONS.md
- 25 (K8: `z_max` de Shewhart por ARL₀) y 26 (K1 en batch), de antes; 25 ahora es por indicador.
- 27: K3 sobre E1+E2 y `never_fulfilled_grace_polls`.
- 28: K3 nueva definición (llegada ≤ última estimada + 300 s; todas las emitidas), población de K5 (alta, 2 tramos × 10 semillas) y medida del efecto en la verdad.
- 29: L, h y z_max por indicador; semilla de la demo 51 → 47 (INTERFACES §10).
- 30: regla de bus oculto por el tope de la lista (`api_max_buses_per_line`); la fila 27 queda con N = 7 y `grace_s` = 180.
