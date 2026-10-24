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

**Después de este informe (24-10-2026):** PR #105 fusionado en `93f9a29` (head `492f531`: `ci.ps1` verde, Codex sin hallazgos y revisión independiente final sin P1 ni P2) y desplegado en staging y producción. Las decisiones quedaron en CEM, `doc/diseno/Decisiones_Propietario_2026-10-21.md` §2.4 (CEM #69). Lo que sigue describe el estado al 23-10.

**Código final:** `67e0000`. Trae las respuestas del propietario del 23-10 (las tres preguntas y la renovación) y las correcciones de app #105: Codex, la tanda única sobre `4b978a1` los P2 de Codex sobre `69f1f77`, `70e889c` y `7e28cd5`, el P3 4 de la revisión sobre `69f1f77`, el barrido de bloqueos y diagnósticos, la vuelta del contrato de coeficientes a `7e28cd5` (decisión del 23-10 sobre `7030b62`) los dos P2 de Codex sobre `3735371` y el P2 sobre `3f9c49b`. El commit siguiente de la rama solo trae este informe y `ESTADO.md`.

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
6. **Renovar una invitación vencida:** lo decidió el propietario (opción 3, abajo).

**Respuestas del propietario del 23-10** a las tres preguntas (transmitidas por el Hilo principal; no figuran todavía en CEM):

| | Decisión | Cómo quedó |
|---|---|---|
| 1 | Al anular, se retira la invitación pendiente; aceptarla después puede dar 404; nunca se confirma ni se crea otro vínculo | Ya estaba así desde `e1667f2` (`anular_vinculo` la retira, auditada) |
| 2 | Una invitación manual no reemplaza en silencio una importada pendiente con `vinculo_id`: 409 mientras exista | `app/identidad/invitaciones.py:99` (`crear`): 409 `invitacion_importada_pendiente` mientras la importada no esté aceptada ni retirada, **también si venció**. Vale para la ruta y para `scripts.invitar` (sale con código 1). No crea ni envía nada |
| 3 | Aceptar la invitación de un vínculo importado finalizado da 409: no la marca aceptada, no lo saca de pendiente, no da acceso | `0025`: la rama de `vinculo_id` exige `vigente_hasta is null or vigente_hasta > hoy`; si no, `vinculo_no_corresponde` → 409 |

**Renovación de una invitación importada vencida: opción 3, decidida por el propietario el 23-10.** Reimportar la misma fila de un vínculo que sigue importado pendiente, cuya invitación (la que lo nombra) venció, la renueva.
- **El informe lo anuncia:** vínculo `existente`, invitación `reemplazar_vencida`.
- **Al aplicar:** se retira la vencida y se crea otra con el mismo `vinculo_id`, en la misma transacción y auditada. Exige `invitaciones.crear`. Es la única escritura de una fila igual.
- **No se renueva** si el vínculo está confirmado, si ya se aceptó o si terminó, ni si hay otra pendiente vigente para esa persona, unidad y carácter. La base lo garantiza también (`crear_invitacion_importada`: `vinculo_no_pendiente`).
- **Sigue siendo idempotente:** con la invitación renovada y vigente, la misma planilla no escribe nada.

**Siguen abiertas, sin tocar:**
- la observación de la revisión de #104 sobre invitarse a sí mismo (`Decisiones_Propietario` l. 181);
- el envío de las invitaciones creadas.

## 4. Plan mínimo, implementado

Rama `rf02-importacion-corte3`, líneas en `5f00c5b`:

- **Migración `backend/migrations/versions/0025_importacion_padron.py`:**
  - `importacion_padron` (l. 189);
  - `vinculo.importacion_id` (l. 206) y `nombre_declarado` (l. 209);
  - `invitacion.vinculo_id` (l. 221);
  - `aceptar_invitacion` (rama nueva en la l. 122) y `padron_del_consorcio` (nombre declarado en la l. 161), las dos derivadas de 0014 con reemplazos verificados;
  - `registrar_importacion_padron` (l. 231), `importar_vinculo` (l. 261), `anular_vinculo` (l. 342), `crear_invitacion_importada` (l. 441) y `vinculos_de_correos` (l. 520);
  - el downgrade se niega con datos (l. 572) y, sin datos, vuelve exacto.
- **`backend/app/consorcios/importacion.py`:**
  - `leer_planilla` (l. 273), `_unidades` (l. 358, alcance y privacidad), `_invitaciones` (l. 473, con la renovación), `analizar` (l. 604), `_aplicar` (l. 682) y `_escribir` (l. 730);
  - las rutas `informe` (l. 828) y `aplicar` (l. 854).
- **`backend/app/consorcios/padron.py:305`:** `POST …/vinculos/{v}/anular`.
- **`backend/scripts/anular_vinculo.py`:** anulación por soporte, agregada al `Dockerfile`.
- **`backend/app/identidad/invitaciones.py`:** el 409 explícito al aceptar (`_vinculo_no_corresponde`, l. 392) y el 409 de la invitación manual (`crear`, l. 99).
- **Contrato 0.17.0:** `contratos/cem_contracts/importacion.py` y cuatro códigos de error; `openapi.json` y `esquemas.json` regenerados.
- **Pruebas:** `backend/tests/test_importacion_padron.py`, con 67 funciones y 96 casos. `conftest.py` (`vaciar_mandatos`) y `test_vinculo_0014.py` quedaron al día.

## 5. Criterio exacto de terminado, y dónde está

| Criterio | Estado |
|---|---|
| D1 a D4 respondidas | Hecho (§3) |
| Rama desde `94a2114`, empujada, sin PR | Hecho |
| Suite en verde | 1534 passed sobre `67e0000`; `ci.ps1` dio verde sobre `69f1f77`, `70e889c`, `7e28cd5`, `3735371` y `3f9c49b` (lo corrió el Hilo principal); la base `94a2114`, en el mismo entorno, da 1422 passed (ver «Entorno») |
| Prueba de cierre del corte | `test_cierre_informe_con_errores_sembrados_no_escribe_nada_y_limpio_se_aplica` |
| Mutaciones | 46 de 46 detectadas (dos del barrido se retiraron con el tope y el recorte de coeficientes) |
| `ruff` y `openapi.json` | Limpio y regenerado |
| `ESTADO.md` | Hecho |
| Revisión independiente | **Revisión independiente final: PENDIENTE sobre el SHA final después de estas correcciones.** La de `129730f` no vale como final: sus P1 y P2 cambiaron el código |
| Codex | Revisó `6a4bb7e` en app #105 (PR en borrador, lo abrió el Hilo principal): un P1 y dos P2, corregidos en `1acb5c3`. Revisó `4b978a1`: dos P2, corregidos en `5f00c5b`. Revisó `69f1f77`: dos P2, corregidos en `2430829`. Revisó `70e889c`: dos P2, corregidos en `65ee21f`. Revisó `7e28cd5`: dos P2, corregidos con el barrido en `7366be4`. **Codex sobre el SHA final: PENDIENTE** (lo repite el Hilo principal) |

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
| la invitación manual reemplaza la importada | 3 |
| acepta un vínculo finalizado | 1 |
| reimportar no renueva la vencida | 2 |
| la base da otra invitación a un vínculo no pendiente | 1 |
| la anulación busca el vínculo antes del efecto idempotente | 1 |
| el informe cuenta los vínculos de una cuenta borrada | 1 |
| acepta un byte nulo | 2 |
| el informe no bloquea la vencida de otro vínculo | 1 |
| la base retira la vencida de otro vínculo | 1 |
| correo sin `FORMATO_CORREO` | 5 |
| CSV no estricto | 2 |
| el informe compara contra unidades fuera de su alcance | 2 |
| encabezados repetidos con `columnas.count` (O(n²)) | 1 |
| `encode("utf-8")` sin rechazar el surrogate | 1 |
| solapes por pares (un diagnóstico por par) | 1 |
| la importación no toma la persona antes de la auditoría | 1 (deadlock detectado) |
| la anulación bloquea el vínculo antes que la invitación | 1 (deadlock detectado) |
| la importación no bloquea invitaciones ni personas antes de leer y auditar | 2 (deadlock detectado) |
| invitaciones simultáneas con todas las líneas | 1 |
| designación ambigua con todas las escrituras | 1 |
| se repone el tope de 20 grupos | 1 |
| la consulta única mezcla los esquemas de los grupos | 1 |
| designación ambigua recorriendo el grupo por fila | 1 |
| la invitación manual no toma `persona:<correo>` | 1 (deadlock detectado) |
| la invitación manual no vuelve a leer con la persona tomada | 1 |
| la segunda lectura de la manual bloquea (`for update`) | 1 (deadlock de tres transacciones detectado) |
| la manual no controla las invitaciones aparecidas durante la espera | 1 |
| búsqueda de vínculos existentes con el candidato equivocado | 1 |

Las 15 primeras se corrieron sobre `129730f`, las 3 siguientes sobre `e1667f2`, 2 sobre `1e1b504`, 2 sobre `2ceef5d`, 3 sobre `1acb5c3`, 5 sobre `5f00c5b`, 2 sobre `2430829`, 3 sobre `65ee21f` 4 sobre `7366be4` (repetidas sobre `a47778a`), 2 sobre `a47778a`, 3 sobre `a9c4d11` y las 2 últimas sobre `67e0000`.

**Revisión independiente** (un subagente sin mi conclusión, sobre `129730f`): un P1, un P2 y cinco P3.

Corregidos en `e1667f2`, cada uno con su prueba:
- **P1:** el padrón delataba que un correo tenía cuenta en otro consorcio, y mostraba su nombre real. Ahora muestra `nombre_declarado`.
- **P2:** anular dejaba la invitación pendiente y bloqueaba la recarga.
- **P3:**
  - el año 9999, un byte nulo o un campo enorme daban 500 (el byte nulo quedó bien recién en `1acb5c3`: la prueba lo ponía en una fecha y pasaba por la fecha inválida; lo encontró Codex);
  - una carrera con otra ruta daba 500; ahora da 409 sin escribir nada;
  - `vinculos_de_correos` no tenía su barrera en la base;
  - el informe de coeficientes nombraba unidades fuera del alcance.

Los dos P3 restantes los resolvió el propietario el 23-10 (respuestas 2 y 3) y están en `1e1b504`.

**Barrido sobre `7e28cd5`** (dos P2 de Codex y la clase completa, decisión del propietario del 23-10), corregido en `7366be4` y `76a103a`. Cada corrección tiene su prueba y su mutación.

*A. Bloqueos.* El orden único es invitación → persona → vínculo → auditoría (`auditoria:<consorcio>`).

| Ruta | Orden | Estado |
|---|---|---|
| Importación (aplicación, renovación, reemplazo de vencidas, creación de invitaciones) | Antes de leer el estado y antes del primer evento: las invitaciones pendientes de todos los correos de la planilla (`for update`, en orden de id), después `persona:<correo>` de esos correos (en orden y con `lower(btrim())`), después los vínculos (los inserta) y la auditoría al final | **Corregida.** Antes la renovación auditaba antes de bloquear la invitación vencida (Codex) |
| Informe | No bloquea nada: solo lee | Cubierta |
| `aceptar_invitacion` (0014/0025) y `aceptar_invitacion_propia` | Invitación → persona → vínculo (rama `vinculo_id`) → auditoría | Cubierta, sin cambios |
| `anular_vinculo` | Invitaciones que lo nombran → vínculo → auditoría | Cubierta (`65ee21f`) |
| `revocar` (`app/identidad/invitaciones.py`) | Invitación → auditoría | Cubierta, sin cambios |
| Invitación manual (`crear()`, la ruta y `scripts.invitar`) | Pendientes de esa clave (`for update`) → `persona:<correo>` → se vuelven a leer **sin bloquear** → retiro de las bloqueadas al principio, inserción y auditoría | **Corregida en `a9c4d11` y `67e0000`.** En `a9c4d11` (Codex sobre `3735371`): sin pendiente previa no tomaba nada, y una importación del mismo correo, unidad y carácter se trababa con ella. En `67e0000` (Codex sobre `3f9c49b`): la segunda lectura bloqueaba después de la persona |

Pruebas con dos conexiones reales (la primera conexión toma los bloqueos en el orden de la otra operación; la mutación produce `deadlock detected`):
- importación frente a aceptación;
- renovación frente a `revocar` (nueva);
- anulación frente a aceptación.

*B. Diagnósticos*, ahora lineales en la cantidad de filas:

| Diagnóstico | Antes | Ahora |
|---|---|---|
| `relacion_duplicada` | Mensaje fijo, pero la búsqueda recorría las anteriores | Un conjunto por clave (`65ee21f`) |
| `vinculo_solapado` | Un diagnóstico por par | Barrido ordenado, uno por fila con un ejemplo (`65ee21f`) |
| `invitaciones_simultaneas` | Cada fila nombraba todas las demás | Un ejemplo y el total (Codex) |
| `designacion_ambigua` | Cada fila nombraba todas las escrituras | Un ejemplo |
| `coeficiente_contradictorio` | Mensaje fijo por fila | Sin cambios |
| `sin_coeficiente` y `distintos_del_vigente` | Listas completas por grupo | **Siguen completas** (contrato de `7e28cd5`, decisión del 23-10): sin recortes, sin totales y sin tope de grupos. El cálculo quedó sin factores de más (ver «Riesgo residual G × D») |
| `corrige_existente` | Recorría todos los vínculos existentes de la clave | Búsqueda binaria: no se superponen entre sí (EXCLUDE); las vigencias vacías quedan afuera |
| Encabezado (repetidas, desconocidas) | Una pasada (`2430829`); un problema por columna desconocida | Sin cambios: lineal |

**P2 de Codex sobre `3f9c49b`**, en `67e0000`, con el contrato sin cambios. La segunda lectura de la invitación manual, con `for update` y ya con `persona:<correo>` tomada, invertía el orden invitación → persona. Una aceptación que bloqueaba la invitación nueva de una importación y esperaba la persona trababa tres transacciones.
- **La segunda lectura ya no bloquea.** Lo que apareció durante la espera se responde sin tocarlo:
  - si nombra un vínculo importado, 409 `invitacion_importada_pendiente`;
  - si es otra manual, 409 `idempotencia_en_curso`: el pedido se repite, sin escribir nada.
  - **Inferencia:** el código existente más cercano; el contrato no se toca. La descripción del 409 de la ruta en el OpenAPI sigue nombrando solo la invitación importada.
- **Solo se retiran las bloqueadas al principio.**
- **Prueba con tres transacciones intercaladas y conexiones reales:** una conexión retiene la persona; la manual (la ruta) espera; una importación confirma la invitación nueva; la aceptación real (`aceptar_invitacion_propia`) la bloquea y espera la persona. Termina con la manual en 409 y la aceptación en `ok`. Con la mutación, `deadlock detected`.
- **Otra prueba:** una manual aparecida durante la espera no se retira, y el pedido repetido sí la reemplaza.
- **Revisión de las demás rutas: ninguna hace una segunda lectura con bloqueo de invitaciones después de la persona.**
  - La importación bloquea invitaciones antes que personas. Lo que aparece mientras espera lo ve el análisis, sin bloquear, y una pendiente vigente nueva rechaza la planilla antes de escribir.
  - `crear_invitacion_importada` solo encuentra las ya bloqueadas al principio.
  - `aceptar_invitacion` bloquea su invitación antes de la persona.
  - `anular_vinculo` y `revocar` no toman la persona.

**Dos P2 de Codex sobre `3735371` y barrido de la clase**, en `a9c4d11`, con el contrato sin cambios (`git diff 7e28cd5 -- contratos/` vacío):
1. **`designacion_ambigua`:** las grafías combinadas y los dos primeros ejemplos se calculan **una sola vez por designación normalizada**. Antes, cada fila copiaba y recorría su grupo: con 8000 variantes de mayúsculas, unos 8 segundos. La prueba usa esas 8000 variantes, exige menos de 3 segundos y que cada fila nombre un ejemplo distinto de su grafía.
2. **Invitación manual frente a importación sin pendiente previa:** la manual ahora bloquea las pendientes de la clave y después `persona:<correo>`, antes de retirar, insertar o auditar, y vuelve a leer las pendientes con la persona tomada. La prueba usa tres conexiones reales con un orden determinista: una retiene la cadena de auditoría, la importación llega primera y la manual después. Termina con la importación en 200 y la manual en 409 `invitacion_importada_pendiente`; con la mutación, `deadlock detected`.

*Barrido (a), escrituras de RF02 que pueden chocar con una restricción única de otra ruta concurrente.* Todas quedan serializadas antes de la auditoría:
- **`invitacion_pendiente_unica`:**
  - frente a la invitación manual: ahora por `persona:<correo>`;
  - frente a otra importación del mismo consorcio: por `importacion:<consorcio>`; el índice incluye el consorcio;
  - `aceptar_invitacion` y la anulación no insertan invitaciones.
- **`persona_correo_unico`:** `importar_vinculo` y la aceptación (0014, 0015) crean personas bajo `persona:<correo>`; la importación lo toma antes de auditar.
- **EXCLUDE de `vinculo`:** la importación y la aceptación insertan vínculos bajo `persona:<correo>`; el fin y la anulación no insertan.
- **`unidad`:** no tiene restricción única; las importaciones del mismo consorcio se serializan por `importacion:<consorcio>`. `scripts.alta_consorcio` (soporte) queda fuera de este PR.

*Barrido (b), validaciones que recorrían un grupo por cada fila.* Todas quedan en una sola pasada por grupo:
- `designacion_ambigua`: corregida;
- `relacion_duplicada`: un conjunto;
- `vinculo_solapado`: un barrido ordenado;
- `invitaciones_simultaneas`: un ejemplo y el total, con acceso directo;
- `corrige_existente`: búsqueda binaria;
- `coeficiente_contradictorio` y coeficientes por grupo: un diccionario por grupo;
- encabezado: `Counter`;
- pendientes de una clave: a lo sumo una, por el índice.

**Decisión del propietario del 23-10 sobre `7030b62`**, en `a47778a`:
- se revierten `sin_coeficiente_total`, `distintos_del_vigente_total` y el recorte a 20 ejemplos: `git diff 7e28cd5 -- contratos/` sale vacío;
- se saca el tope de 20 grupos: una planilla con 25 grupos se informa y se aplica (prueba y mutación);
- se conservan las correcciones de bloqueos y de complejidad del barrido, con sus pruebas, y sus mutaciones se repitieron sobre `a47778a`.

`_coeficientes_informe` quedó sin factores de más:
- cada valor de la planilla se agrega una sola vez a su grupo;
- cada grupo se emite una sola vez;
- los coeficientes guardados se leen en **una sola consulta**, solo para los grupos con esquema vigente (antes, una por grupo). La prueba con dos esquemas confirma que cada grupo se compara con el suyo.

**Riesgo residual G × D (no resuelto, a decisión del propietario).** Con listas completas y sin tope, `sin_coeficiente` nombra, en cada grupo, cada unidad sin porcentaje. La respuesta es G × D por definición:
- G son los grupos;
- D son las unidades de la planilla más las del consorcio dentro del alcance.

Una planilla con decenas de miles de columnas de grupo vacías da G × U, siendo U las unidades del consorcio. Medido sobre `a47778a` (TestClient, PostgreSQL 18; U = 503 unidades del consorcio, R = filas de la planilla, D = R + U):

| G | R | D | G × D | Entrada | Respuesta | Bytes por celda G × D | Tiempo |
|---|---|---|---|---|---|---|---|
| 1 | 100 | 603 | 603 | 612 B | 22 KB | 36,5 | 0,08 s |
| 10 | 100 | 603 | 6 030 | 1,6 KB | 64 KB | 10,7 | 0,07 s |
| 100 | 100 | 603 | 60 300 | 12 KB | 488 KB | 8,1 | 0,12 s |
| 10 | 1 000 | 1 503 | 15 030 | 16 KB | 292 KB | 19,4 | 0,08 s |
| 100 | 1 000 | 1 503 | 150 300 | 107 KB | 1,4 MB | 9,1 | 0,10 s |
| 1 000 | 100 | 603 | 603 000 | 117 KB | 4,7 MB | 7,8 | 0,12 s |
| 2 000 | 1 | 504 | 1 008 000 | 37 KB | 8,1 MB | 8,0 | 0,16 s |
| 10 000 | 1 | 504 | 5 040 000 | 189 KB | 40,3 MB | 8,0 | 0,98 s |

La respuesta es de unos 8 bytes por celda G × D, más una parte fija. Con el millón de caracteres permitido caben unos 50 000 grupos vacíos. Con 500 unidades en el consorcio, eso son unos 200 MB de respuesta. No inventé un límite: cualquier solución pasa por cambiar el contrato o por rechazar, y eso lo decide el propietario.

**Riesgo residual, que ya existía:** se toma un bloqueo por cada invitación pendiente y por cada correo de la planilla, y todos se mantienen hasta el final de la transacción. Una planilla con miles de correos distintos ocupa la tabla de bloqueos compartida (`max_locks_per_transaction`).

**Dos P2 de Codex sobre `70e889c` y el P3 4 de la revisión sobre `69f1f77`** (decisión del propietario del 23-10), corregidos en `65ee21f`, cada uno con su prueba y su mutación:
1. **Solapes:**
   - **Detección:** un barrido por fecha de inicio para cada persona, unidad y carácter, con a lo sumo un diagnóstico por fila y sin recorrer todos los pares. Los duplicados se buscan en un conjunto.
   - **Resultado:** el archivo se sigue rechazando con `vinculo_solapado`. La prueba usa 400 filas que se superponen todas: cada fila tiene un diagnóstico, y antes eran 159 600 en total.
2. **Orden de bloqueos:** invitación, persona (`persona:<correo>`), vínculo y, al final, la cadena de auditoría. Es el orden que ya seguían `aceptar_invitacion` (0014 y 0015) y el alta de mandato (0016).
   - **Cambio:** la importación toma, antes de escribir el primer evento, los bloqueos de persona de todos los vínculos que va a crear, en orden y con la misma expresión que la base (`lower(btrim(correo))`).
   - **Riesgo residual, que ya existía:** cada correo distinto es un bloqueo consultivo hasta el final de la transacción, y antes también lo era (lo tomaba `importar_vinculo`). Una planilla con miles de correos distintos ocupa la tabla de bloqueos compartida (`max_locks_per_transaction`).
3. **P3 4 (aceptar frente a anular): era real.** `aceptar_invitacion` bloquea la invitación y después el vínculo; `anular_vinculo` bloqueaba el vínculo y después, al retirarla, la invitación. Ahora `anular_vinculo` bloquea primero las invitaciones pendientes que nombran al vínculo.

**Cómo se controló el deadlock:** con dos conexiones reales a PostgreSQL. Una toma los bloqueos en el orden de `aceptar_invitacion`, es decir, persona y después un evento de auditoría (o invitación y después vínculo). La otra corre la importación (o la anulación) por la API, y la prueba espera, con el rol administrador, a que quede esperando un bloqueo. Con la corrección, las dos terminan bien; con la mutación, PostgreSQL responde `deadlock detected`.

**Dos P2 de Codex sobre `69f1f77`** (decisión del propietario del 23-10), corregidos en `2430829`, cada uno con su prueba y su mutación. La revisión independiente final sobre `69f1f77` no encontró P1 ni P2 y dejó 13 P3, que quedan **pendientes, sin cambios de código**.
1. **Encabezados repetidos:** se cuentan en una sola pasada (`collections.Counter`), con la misma validación y la misma respuesta. La prueba lee 50 000 columnas en menos de un segundo; con `columnas.count` (la mutación) tarda más y falla.
2. **Surrogate suelto (`"\ud800"`):** `leer_planilla` lo rechaza como `fila_mal_formada`, antes del CSV y de cualquier consulta.
   - **Cómo es determinista el hash:** es el SHA-256 de `archivo.encode("utf-8", "surrogatepass")`. Da siempre los mismos bytes para el mismo texto y coincide con el SHA-256 UTF-8 de siempre cuando no hay surrogates (la prueba lo controla).
   - **Hallazgo:** por la API el 500 no se reproduce. El contrato (Pydantic) rechaza ese JSON antes del importador con 422 `peticion_invalida`, y ya lo hacía sobre `69f1f77`: lo comprobé corriendo la misma prueba con el `importacion.py` de ese head. La corrección protege la función, que antes lanzaba `UnicodeEncodeError` llamada con ese texto.

**Tanda única sobre `4b978a1`** (decisión del propietario del 23-10), corregida en `5f00c5b`, cada corrección con su prueba y su mutación:
1. **P2 de la revisión independiente:** una invitación pendiente que nombra otro vínculo es bloqueante aunque haya vencido (D4.2). Lo marca el informe y lo defiende `crear_invitacion_importada`. Solo se reemplaza una manual vencida, o la vencida del mismo vínculo en la renovación.
2. **P2 de Codex:** el correo usa `cem_contracts.identidad.FORMATO_CORREO`, también en la base (`importar_vinculo`). `ana@@ejemplo.com` ya no crea una persona.
3. **P2 de Codex:** el CSV se lee en modo estricto. `"Ana"xyz` o unas comillas sin cerrar dan `fila_mal_formada`, nunca un campo alterado.
4. **Alcance y privacidad (revisión independiente):** con `padron.gestionar` acotado, el informe compara solo contra las unidades que la persona gestiona. Una designación ajena (exista o no, escrita igual o ambigua) da el mismo `fuera_de_alcance`, con el mismo mensaje, y nunca se nombra. Una planilla solo con unidades ajenas da el mismo 403 en todos los casos.

**Los P3 de la revisión independiente final sobre `4b978a1` quedan PENDIENTES, sin cambios de código** (decisión del propietario: no amplían este corte). Su lista no llegó a esta sesión, así que no la transcribo: está en la revisión que corrió el Hilo principal.

**Codex en app #105** (sobre `6a4bb7e`), corregido en `1acb5c3`, cada hallazgo con su prueba y su mutación:
- **P1:** un reintento de la anulación con la misma clave respondía 404, porque el vínculo se buscaba antes de `responder_una_vez`. Ahora la búsqueda y la facultad van dentro del efecto, y el reintento repite el 200.
- **P2:** `vinculos_de_correos` contaba vínculos de una persona borrada lógicamente. Ahora filtra `eliminado_en is null`, como `importar_vinculo`.
- **P2:** `csv.reader` acepta un byte nulo, que llegaba a PostgreSQL desde la unidad o el nombre. Ahora se rechaza antes de cualquier consulta.

**Revisión independiente final: PENDIENTE sobre el SHA final después de estas correcciones.**
