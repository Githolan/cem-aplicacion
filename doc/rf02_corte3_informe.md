# RF02, corte 3 (H2b 2b-5): informe previo y resultado

**Fecha:** 23-10-2026.

**Bases leídas:**
- `cem-aplicacion` `main` = `94a2114ec5c2c2ba4ba6ac0090ab4bfb43bf05bc`;
- `CEM` `main` = `bc799d6114f3548d7407c951157e05399e36013c`;
- decisiones en `CEM`, rama `decisiones/propietario-2026-10-21` (PR #69) = `64aea50`.

**Historia de este informe:**
1. **Primera versión:** frenó con D1 a D4 abiertas.
2. **D2:** la resolvió el propietario el 22-10.
3. **D1, D3 y D4:** las respuestas del propietario llegaron el 23-10, transmitidas por el Hilo principal de trabajo. **No figuran todavía en CEM `64aea50`**: quedan registradas acá, en §3.

Sin decisiones materiales abiertas, se hizo la Fase 2 en la rama `rf02-importacion-corte3`, desde `94a2114`.

**Código final:** `76a0b5b`. El commit siguiente de la rama solo trae este informe y `ESTADO.md`.

## 1. Estado real encontrado

**Diseño** (`CEM/doc/diseno/H2b_5_Diseno.md` §4, l. 118-144). Define:
- la entrada CSV (l. 120) y `ultimo_dia` → `vigente_hasta = ultimo_dia + 1` (l. 121);
- el correo como identidad (l. 122);
- el informe sin escritura y la aplicación todo o nada con alcance (l. 123-136);
- PA01 (l. 137) y la anulación (l. 138-144).

**No definía** la reimportación, la idempotencia, la trazabilidad ni la fila corregida: es D1.

**Especificación** (`CEM/doc/08_Especificacion_App_v2.md`): RF02 en la l. 232 y PA01 en la l. 1127.

**Base, hasta 0024:**
- `vinculo` tiene un `EXCLUDE` por persona, unidad, tipo y rango (`0001` l. 114-117);
- `persona` es global, con correo único y la política `propia` (`0001` l. 82-87 y 184);
- invitación: 7 días como máximo (`0007` l. 54); el índice `invitacion_pendiente_unica` está en `0007` l. 60-61;
- coeficientes: solo los carga soporte (`0021` l. 131);
- plazo de las invitaciones manuales: `VIGENCIA_INVITACION = timedelta(days=7)` (`app/identidad/invitaciones.py:61`).

**#104:**
- `vinculos_importados_pendientes()` (`0024` l. 54-70) excluye al importado en `app/contexto.py:125` y en `app/consorcios/router.py:32` y `:54`;
- antes de este corte, **ninguna vía creaba vínculos `importacion`**.

**`aceptar_invitacion` en 0014** (l. 154-165): reutiliza cualquier vínculo que se superponga con `[hoy, ∞)` (`limit 1`) o crea uno desde hoy.

**`padron_del_consorcio`** (`0014` l. 263-298): muestra `persona.nombre`, el nombre global de la persona.

## 2. Desvío respecto del diseño

- **Ya decidido, no se reabre:** `H2b_5_Diseno.md` l. 122 sigue presentando como abierto qué ve un importado. #104 lo resolvió. El documento de CEM necesita esa corrección; no la hice, porque está fuera de este frente.
- **Documental:** `0024` y `test_importado_pendiente.py` citan `Decisiones_Propietario_2026-10-21.md`, que está en el PR #69 de CEM y todavía no en `main`.
- **Coeficientes:** el CSV los admite (l. 120) pero no se escriben (D3).
- **Fecha futura:** §2.3 (l. 57) suponía que no había alta con fecha futura. La importación la habilita, así que la anulación entró en este corte.
- **Motivo de la anulación:** se exige y **no se guarda**, porque la fila se borra y el evento no lleva el motivo (§4 y E31). Conservarlo pediría una tabla.
- **Nombre de una persona nueva:** §4 dice que se crea "con su correo y nombre", así que el nombre de la planilla pasa a ser su nombre global. Con 0014, en cambio, la persona elegía el nombre al aceptar. Lo implementé como dice §4.

## 3. Decisiones

| | Decisión del propietario | Cómo quedó |
|---|---|---|
| D1 | `importacion_padron` con RLS. Reimportar contenido idéntico es un no-op idempotente. Un archivo que pretende corregir relaciones ya aplicadas se rechaza entero | Tabla con RLS forzada y `vinculo.importacion_id`. Una fila igual a lo existente no escribe nada, y un archivo sin cambios no registra ni una importación. Superponerse con un vínculo existente sin ser igual es `corrige_existente` y bloquea el archivo |
| D2 | La invitación lleva `vinculo_id` opcional, que completa solo el importador; la aceptación toma exactamente ese vínculo o falla explícitamente; las manuales siguen como en 0014 (22-10, PR #69 de CEM) | `invitacion.vinculo_id`. `cem_app` no puede insertarlo: solo lo hace `crear_invitacion_importada`. Si el vínculo ya no corresponde, 409 `invitacion_vinculo_no_corresponde`. **Ver la pregunta 1** |
| D3 | Los coeficientes se leen, validan e informan, sin escribirse | Informe por grupo: suma tal cual, unidades sin coeficiente y diferencias con el esquema vigente. Un valor inválido o contradictorio es un problema |
| D4.1 | Una importación que crea invitaciones exige `padron.gestionar` + `invitaciones.crear`; si falta alguna, se rechaza todo antes de escribir | Se evalúa por fila contra su unidad (general, si la unidad es nueva). Si falta, 403 auditado |
| D4.2 | Una pendiente del mismo vínculo se conserva; una manual o de otro vínculo es un error bloqueante; una vencida se anuncia y se reemplaza al aplicar, de forma atómica | Una fila igual no toca nada. Una pendiente vigente da `invitacion_pendiente`. Una vencida sale como `reemplazar_vencida` y al aplicar se retira, auditada, en la misma transacción. La base lo vuelve a garantizar |
| D4.3 | El mismo plazo de las manuales | `VIGENCIA_INVITACION`, 7 días, que es el tope de `0007` |

**Inferencias tomadas sin preguntar.** Se pueden revisar; ninguna cambia el modelo.
1. **Corrección frente a relación nueva:** "corregir" quiere decir superponerse con un vínculo existente de la misma persona, unidad y carácter sin ser igual. En fechas que no se superponen, es una relación nueva. Una fila que falta en el archivo no da de baja nada.
2. **Unidad duplicada (PA01):** dos designaciones que solo difieren en mayúsculas o espacios son la misma unidad (`designacion_ambigua`).
3. **Vínculo ya terminado:** no lleva invitación. Dos vínculos nuevos vigentes o futuros de la misma persona, unidad y carácter son un error (`invitaciones_simultaneas`), porque el índice admite una sola pendiente.
4. **Coeficientes incompletos:** se informan sin bloquear, porque no se escriben. Un valor inválido o contradictorio sí bloquea.
5. **Privacidad:**
   - el informe no devuelve correos ni nombres y no distingue si un correo tiene cuenta en otro consorcio;
   - el padrón muestra el nombre de la planilla mientras el vínculo está pendiente (P1 de la revisión);
   - una persona existente se reutiliza sin cambiarle el nombre.
6. **Renovar una invitación vencida:** reimportar un vínculo ya aplicado no la renueva, porque una fila igual es un no-op. La renovación es por invitación manual, que se envía, como hoy.

**Preguntas para el propietario.** Ninguna bloqueó el corte.
1. **Anulación (sí/no):** la anulación retira, auditadas, las invitaciones pendientes que nombran al vínculo (P2 de la revisión). Sin eso, la invitación seguía vigente 7 días y bloqueaba volver a cargarlo corregido (`invitacion_pendiente`). Consecuencia: después de una anulación, aceptar responde 404 `invitacion_invalida`, no el 409 explícito. El 409 queda para un vínculo que ya no existe con la invitación viva, o que es de otra persona, unidad o carácter. ¿Te sirve así?
2. **Invitación manual sobre una importada (sí/no):** una invitación manual al mismo correo, unidad y carácter retira la importada (`app/identidad/invitaciones.py:118-135`, sin cambios), y la nueva no tiene `vinculo_id`. ¿La ruta manual debería responder 409 ante una pendiente con `vinculo_id`?
3. **Vínculo finalizado antes de aceptar (sí/no):** si el vínculo importado se finalizó antes de que la persona acepte, la aceptación lo nombra igual. No da nada, pero sale del estado pendiente. ¿Debería responder `vinculo_no_corresponde`?
4. **Siguen abiertas, sin tocar:**
   - la observación de la revisión de #104 sobre invitarse a sí mismo (`Decisiones_Propietario` l. 181);
   - el envío de las invitaciones creadas.

## 4. Plan mínimo, implementado

Rama `rf02-importacion-corte3`, líneas en `76a0b5b`:

- **Migración `backend/migrations/versions/0025_importacion_padron.py`:**
  - `importacion_padron` (l. 183);
  - `vinculo.importacion_id` (l. 200) y `nombre_declarado` (l. 203);
  - `invitacion.vinculo_id` (l. 215);
  - `aceptar_invitacion` (rama nueva en la l. 118) y `padron_del_consorcio` (nombre declarado en la l. 155), las dos derivadas de 0014 con reemplazos verificados;
  - `registrar_importacion_padron` (l. 225), `importar_vinculo` (l. 255), `anular_vinculo` (l. 335), `crear_invitacion_importada` (l. 432) y `vinculos_de_correos` (l. 497);
  - el downgrade se niega con datos (l. 538) y, sin datos, vuelve exacto.
- **`backend/app/consorcios/importacion.py`:**
  - `leer_planilla` (l. 271), `analizar` (l. 549), `_aplicar` (l. 624) y `_escribir` (l. 672);
  - las rutas `informe` (l. 760) y `aplicar` (l. 786).
- **`backend/app/consorcios/padron.py:305`:** `POST …/vinculos/{v}/anular`.
- **`backend/scripts/anular_vinculo.py`:** anulación por soporte, agregada al `Dockerfile`.
- **`backend/app/identidad/invitaciones.py:370`:** el 409 explícito.
- **Contrato 0.17.0:** `contratos/cem_contracts/importacion.py` y cuatro códigos de error; `openapi.json` y `esquemas.json` regenerados.
- **Pruebas:** `backend/tests/test_importacion_padron.py`, con 52 funciones y 74 casos. `conftest.py` (`vaciar_mandatos`) y `test_vinculo_0014.py` quedaron al día.

## 5. Criterio exacto de terminado, y dónde está

| Criterio | Estado |
|---|---|
| D1 a D4 respondidas | Hecho (§3) |
| Rama desde `94a2114`, empujada, sin PR | Hecho |
| Suite en verde | 1496 passed sobre `76a0b5b`; la base `94a2114`, en el mismo entorno, da 1422 passed (ver «Entorno») |
| Prueba de cierre del corte | `test_cierre_informe_con_errores_sembrados_no_escribe_nada_y_limpio_se_aplica` |
| Mutaciones | 18 de 18 detectadas |
| `ruff` y `openapi.json` | Limpio y regenerado |
| `ESTADO.md` | Hecho |
| Revisión independiente | Hecha sobre `129730f` (ver abajo) |
| Codex | **SIN VERIFICAR:** no hay Codex en esta sesión |

**Entorno.** La imagen `backend/pruebas/Dockerfile` no se construye acá: el proxy de la sesión rechaza `deb.debian.org` y `apt.postgresql.org` con un 403. La misma suite corrió con los servicios del compose (PostgreSQL 18, Redis 7, Versity y Mailpit) sobre una imagen local con:
- Python 3.12-slim;
- PostgreSQL 18.6, sacado de la imagen oficial `postgres:18`;
- `age` 1.2.1.

La imagen oficial y `ci.ps1` quedan **SIN VERIFICAR**. Se excluyó `test_smoke_staging.py`, que necesita staging.

**Mutaciones** (cada una aplicada sola; la columna dice cuántas pruebas fallan):

| Mutación | Fallan |
|---|---|
| `ultimo_dia` sin + 1 | 3 |
| aplica con problemas | 4 |
| commit a mitad de la escritura | 28 |
| sin alcance de `padron.gestionar` | 2 |
| sin `invitaciones.crear` | 1 |
| reimportar escribe | 1 |
| corrección ignorada | 1 |
| la aceptación ignora `vinculo_id` | 4 |
| la base retira una pendiente vigente | 1 |
| la pendiente vigente no bloquea | 1 |
| anula un vínculo ya iniciado | 1 |
| anula uno confirmado por la API | 1 |
| designación exacta | 2 |
| invitación para un vínculo terminado | 3 |
| relación sin correo aceptada | 1 |
| el padrón muestra el nombre global | 1 |
| anular no retira la invitación | 1 |
| acepta el año 9999 | 1 |

Las 15 primeras se corrieron sobre `129730f`; las 3 últimas, sobre `e1667f2`.

**Revisión independiente** (un subagente sin mi conclusión, sobre `129730f`): un P1, un P2 y cinco P3.

Corregidos en `e1667f2`, cada uno con su prueba:
- **P1:** el padrón delataba que un correo tenía cuenta en otro consorcio, y mostraba su nombre real. Ahora muestra `nombre_declarado`.
- **P2:** anular dejaba la invitación pendiente y bloqueaba la recarga.
- **P3:**
  - el año 9999, un byte nulo o un campo enorme daban 500;
  - una carrera con otra ruta daba 500; ahora da 409 sin escribir nada;
  - `vinculos_de_correos` no tenía su barrera en la base;
  - el informe de coeficientes nombraba unidades fuera del alcance.

No corregidos: los dos P3 restantes, que son las preguntas 2 y 3. Corresponde una sola revisión, así que **no hubo una segunda revisión** sobre `76a0b5b`.
