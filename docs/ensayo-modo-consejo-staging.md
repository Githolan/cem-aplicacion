# Ensayo de Modo Consejo en staging (H6) — corrida del 23-11-2026

Script: `backend/pruebas/ensayo_modo_consejo_staging.py` (cómo se corre y qué deja, en su docstring).
Datos sintéticos (CEM LAB, `@cem-prueba.invalid`); nunca producción (sigue en `1535bd7`).

## Corrida

| Dato | Valor |
|---|---|
| Fecha | 23-11-2026, 04:07:55 a 04:08:41 UTC (corrida), exportación a continuación |
| Staging | `c7c200a` (PR #285), migración `0128` |
| `cem-backend-staging` | `yepjjanuzkdvtibdk1hngn21-20261123T040314` |
| `cem-trabajos-staging` | `wqndx1oq0tdsshbjutmbbifg-20261123T040351` |
| Corrida | `56bb7458` |
| Consorcio nuevo | `d0ca5169-7891-4061-bf8f-d26eae1a1aa2` («CEM LAB — Modo Consejo sintético (SINTETICO, PRUEBA, NO REAL)») |
| Exportación | `01a20697-bfb9-7acf-8d42-9d194e9a9f17` |
| Script | el de este commit (rama `h6/ensayo-modo-consejo`) |

## Condiciones previas (leídas por presencia, sin imprimir valores)

- `cem-trabajos-staging`: `age` presente; `CEM_ORIGEN_PUBLICO` presente y de staging; `CEM_DATABASE_URL_SOPORTE_EXPORTACION` presente.
- `CEM_AVISOS_TOPE_DIARIO` sin definir en `cem-backend-staging` y en `cem-trabajos-staging`: vale el valor por omisión, 1 (`app/config.py`).
- `docker stats` antes de correr: ningún contenedor sobre 4 % de CPU (sin carga de la prueba de carga de H6b).

## Pasos y resultados

| # | Paso | Resultado |
|---|---|---|
| 0 | `--simular`: guarda y lectura local del PDF | exit 0; período 2026-09-01, 16 datos, 0 diferencias, enlace `no_encontrado` |
| 1 | Escena: `alta_consorcio` (CABA), Modo Consejo, 4 personas por invitación, 2 mandatos de Consejo, 3 vínculos confirmados por HTTP | ok |
| 2 | Liquidación A y B: subida, antivirus real, lectura de `cem-trabajos` con el lector determinista (16 datos, sin diferencias), cada dato confirmado | ok |
| 3 | Bandeja: el inicio cuenta 1 por confirmar y `pendientes_consejo_para_avisar` lo mismo para los dos consejeros | ok |
| 4 | Una oficial por período: 409 `periodo_ya_confirmado`; con reemplazo y motivo, confirmada; A `reemplazada` | ok |
| 5 | Mis expensas: solo PB-A (1.301,00) y el resumen; panel y liquidación oficial, 403 auditado | ok |
| 6 | Consejo: oficial vigente y panel (2.500,50, dos rubros, vencimientos 2026-10-13 y 2026-10-23, señal `no_encontrado`, CABA versión 1) | ok |
| 7 | Presupuesto (control: propietario 403); desvíos +100,50 (7,18 %) y −100,00 (−9,09 %) | ok |
| 8 | Reclamo: citar la reemplazada 409 `liquidacion_reemplazada`; contado en el inicio; respondido | ok |
| 9 | Reparaciones: presupuesto y factura por el antivirus real; aprobación con decisión; gasto de la reemplazada 422 `gasto_no_oficial`; cierre por el gasto oficial; mismo gasto en otra 409 `gasto_ya_enlazado` (PA11) | ok |
| 10 | Comunicado con cuenta 422 `texto_con_cuenta` (no queda); emitido a 3 (3 correos); leído; sin confirmar 403 | ok |
| 11 | Ayuda: consulta y reclamo en representación (auditado); presupuesto 403 con `ayuda_no_admitida` en la auditoría; revocada, 403 `ayuda_revocada` | ok |
| 12 | Bandeja: 1 reclamo y 1 pedido de reparación, igual que el aviso; aviso diario (código real, solo este consorcio) uno por consejero, sin textos, cuenta ni nombres | ok |
| 13 | 87 eventos sin la cuenta; 29 correos del consorcio, todos a `.invalid`; 0 trabajos muertos | ok |
| 14 | Cadena de auditoría: 87 eventos, 0 rotos, 0 distintos (control: 87 de 87 con la acción alterada) | ok |
| 15 | Cierre: 0 sesiones abiertas, ningún navegador sigue entrando | ok |
| 16 | `--exportar`: negada al propietario sin mandato (sin archivo); armada por el Consejo; las 16 entidades nuevas con filas; el texto del reclamo viaja; ninguna cuenta; mandato de consejo1 dado de baja | exit 0 |

## Control independiente en la base (solo lectura, después)

- Mandatos vigentes del consorcio: 0. Sesiones abiertas de sus personas: 0.
- Importaciones: 1 `confirmada`, 1 `reemplazada`.
- Correos del consorcio fuera de `@cem-prueba.invalid`: 0.
- `plataforma.gasto_ia` del consorcio: 0 (ninguna lectura con el modelo). Control: la tabla entera también tiene 0 filas, así que no hay un caso positivo conocido; la ausencia de IA la garantiza además la lectura previa (`formato = mis_expensas`, origen `determinista` en cada dato).

## Defectos de la aplicación

Ninguno.

## Hallazgo en el script (revisión de Codex en #286, P1)

En `--exportar`, la guarda corría dentro del `try` cuyo `finally` llamaba siempre a `cerrar_exportacion()`, que abría `CEM_DATABASE_URL` sin validar el entorno: una invocación rechazada con código 3 igual daba de baja el mandato y revocaba las sesiones de los ids recibidos. **No afectó la corrida de staging** (la guarda pasó: era staging). Arreglo: la guarda corre antes del bloque que limpia; `cerrar_exportacion()`, `cerrar()`, `Corrida.tx()` (toda escritura de la corrida en la base), el alta del consorcio y el aviso diario la repiten y se niegan solos fuera de staging. Pruebas nuevas con un mandato vigente y una sesión abierta reales: otra base y otro origen dan 3 y quedan intactos; la limpieza llamada sola se niega (control: con la guarda en orden, sí da de baja y revoca). Mutación: con el `finally` incondicional de antes, las dos primeras fallan en `assert _intactos(...) == (True, True)` (quedaba `(False, False)`); sin la guarda en `cerrar`/`tx` o en `cerrar_exportacion`, falla la tercera (`DID NOT RAISE Negado`); restaurado, `cmp` idéntico. La corrida no se repitió: el arreglo no cambia ningún paso cuando la guarda pasa.

## SIN VERIFICAR

- La entrega real del aviso diario por el planificador: el ensayo arma el aviso con el código real pero solo para este consorcio (la corrida entera mandaría a todos los edificios de staging).
- El navegador: el ensayo va por HTTP a través de nginx, no por las pantallas.
