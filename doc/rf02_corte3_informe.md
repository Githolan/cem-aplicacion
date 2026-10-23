# RF02, corte 3 (H2b 2b-5): informe previo

**Fecha:** 23-10-2026. **Bases leídas:** `cem-aplicacion` `main` = `94a2114ec5c2c2ba4ba6ac0090ab4bfb43bf05bc`; `CEM` `main` = `bc799d6114f3548d7407c951157e05399e36013c`. Las dos coinciden con lo pedido.

**Actualización del 23-10:** D2 queda resuelta por la decisión del propietario del 22-10, «La invitación nombra su vínculo». Está en `doc/diseno/Decisiones_Propietario_2026-10-21.md` §2.4 (l. 170) de la rama `decisiones/propietario-2026-10-21` de CEM (PR #69, head `64aea50`), que todavía no está en `main`. La misma sección (l. 169) acota D4.2.

**Resultado:** siguen sin definir D1, D3, D4.1, D4.2 y D4.3. **No hubo Fase 2:** no se creó la rama `rf02-importacion-corte3` ni se escribió código.

## 1. Estado real encontrado

- **Diseño** (`CEM/doc/diseno/H2b_5_Diseno.md` §4, líneas 118-144). Define:
  - la entrada CSV (l. 120) y `ultimo_dia` inclusivo → `vigente_hasta = ultimo_dia + 1` (l. 121);
  - el correo obligatorio como identidad (l. 122);
  - el informe sin escritura y la aplicación todo o nada con `padron.gestionar`, alcance por unidad y alcance general para crear unidades (l. 123-136);
  - PA01 (l. 137) y la anulación de un vínculo no iniciado (l. 138-144).
- **No define:** la reimportación, la idempotencia de un segundo archivo, la trazabilidad archivo → importación → vínculo, ni qué pasa con una fila existente frente a una corregida.
- **Especificación** (`CEM/doc/08_Especificacion_App_v2.md`): RF02 en la l. 232 ("informe de duplicados, fechas solapadas y coeficientes incompletos") y PA01 en la l. 1127 ("rechazo o revisión explícita sin sobrescribir").
- **Base, 0001 a 0024:**
  - `vinculo` tiene un `EXCLUDE` por persona, unidad, tipo y rango (`0001` l. 114-117) y `fuente` con el valor `importacion` (`0014` l. 36).
  - `nucleo.persona` es global, con el correo único (`0001` l. 82-87). Su política `propia` (l. 184) no deja que `cem_app` vea a otras personas: buscar o crear por correo exige una función `security definer`.
  - Invitación: dura 7 días como máximo, impuesto por la base (`0007` l. 54); el índice `invitacion_pendiente_unica` está en `0007` l. 60-61.
  - Coeficientes: solo con origen `soporte` y con una decisión (`0021` l. 131-132).
- **#104:**
  - `nucleo.vinculos_importados_pendientes()` (`0024` l. 54-70) deja afuera al importado pendiente en `app/contexto.py:125` y `app/consorcios/router.py:32` y `:54`;
  - `/me/invitaciones` y su aceptación: `app/identidad/invitaciones.py:515-560`;
  - las pruebas: `backend/tests/test_importado_pendiente.py`, que crean el vínculo importado a mano (l. 70). **Todavía no hay ninguna vía que cree vínculos `importacion`.**
- **Cómo acepta hoy:** `nucleo.aceptar_invitacion` (`0014` l. 102-185) reutiliza **cualquier** vínculo de la misma persona, unidad y tipo que se superponga con `[hoy, ∞)`, con `limit 1` (l. 154-158). Si no encuentra ninguno, crea uno nuevo desde hoy con `fuente = invitacion` (l. 159-165).

## 2. Desvío respecto del diseño

- **Ya decidido y no se reabre:** `H2b_5_Diseno.md` l. 122 sigue presentando como abierto qué ve un importado. #104 lo implementó así:
  - se autentica, pero no tiene ámbito;
  - no aparece en Contexto ni en `/me/consorcios`;
  - ve y acepta sus invitaciones;
  - lo habilita la aceptación que nombra su vínculo (E62).

  El documento de diseño necesita esa corrección en CEM. No la hice: está fuera de este frente.
- **Documental:** `0024` (docstring, l. 3) y `test_importado_pendiente.py` (l. 4) citan `doc/diseno/Decisiones_Propietario_2026-10-21.md`. Ese archivo **no está** en CEM `main` `bc799d6`; está solo en la rama del PR #69 (`64aea50`).
- **Coeficientes:** el diseño admite "opcionalmente, los coeficientes por grupo" en el CSV (l. 120). Por la API, en cambio, los coeficientes son de solo lectura (§3, `0021` l. 131). Ver D3.
- **Fecha futura:** el diseño (§2.3, l. 57) dice que hasta el corte 3 no hay alta con fecha futura. La importación la habilita, así que la anulación (§4, l. 138-144) **tiene que** entrar en este corte.

## 3. Decisiones pendientes

### D1. Reimportación, fila existente y fila corregida (PA01), con su trazabilidad

- **Hecho:**
  - §4 solo dice que una unidad duplicada no sobrescribe (l. 137);
  - el reintento del mismo pedido ya lo cubre `Idempotency-Key` (`app/plataforma/idempotencia.py`);
  - no hay ninguna tabla que registre una importación (archivo, hash, quién, cuándo), ni ninguna columna que ate un vínculo a su importación.
- **Inferencia:** un segundo archivo con otra clave no está definido. El documento 13 (l. 428) usa, para los extractos, "el archivo completo se registra con su hash". Es un patrón ya usado, pero para otro dominio.
- **Alternativas:**
  1. Si algo del archivo ya existe, se rechaza el archivo entero.
  2. Una fila idéntica a lo existente queda "ya existe, sin cambios" (no-op) y se aplica lo nuevo. Una fila que difiere de lo existente es un error que bloquea el archivo: se corrige con fin o anulación y después se reimporta.
  3. Lo mismo que la 2, pero la fila corregida queda como advertencia y se omite.
- **Trazabilidad:**
  - **(a)** una tabla `nucleo.importacion_padron` (consorcio, SHA-256 del archivo, quién, cuándo, conteos) con RLS forzada y `vinculo.importacion_id`, más los eventos de auditoría;
  - **(b)** solo auditoría, con un evento por importación que lleve el hash y la lista de ids.
- **Recomendación:** la 2, con (a). "Idéntica" quiere decir la misma unidad, el mismo correo, el mismo tipo y las mismas fechas. Un mismo hash ya aplicado se informa como "este archivo ya se aplicó el dd-mm" y no escribe nada.
- **Pregunta D1:** ¿2 + (a)? Respondé 1, 2 o 3, y (a) o (b).

### D2. La invitación nombra su vínculo — RESUELTA (propietario, 22-10-2026)

- **Decisión** (`Decisiones_Propietario_2026-10-21.md` §2.4, l. 170, PR #69 de CEM):
  - `invitacion.vinculo_id` es opcional y lo completa solo el importador;
  - si viene, `aceptar_invitacion` toma **exactamente** ese vínculo y falla con un error explícito si ya no corresponde: anulado, otra persona, otra unidad u otro carácter;
  - sin `vinculo_id`, la invitación sigue como en `0014` (l. 154-165);
  - se implementa en el corte 3, en `0025`.
- **Lo que resuelve:** dos vínculos sucesivos de la misma persona, unidad y tipo dejan de elegirse al azar (`limit 1`, `0014` l. 158), y una invitación de un vínculo terminado o anulado ya no crea un vínculo nuevo desde hoy.
- **Inferencia, sin pregunta:** para un vínculo ya terminado (`vigente_hasta <= hoy`) la importación **no** crea invitación. No le daría acceso a nadie y ocuparía `invitacion_pendiente_unica`. Lo cuento como parte de "crearla válidamente" (§2.4, l. 165).
- **Sigue abierto, sin tocarlo:** la observación de la revisión independiente (l. 181): ¿quien acepta debería ser otra persona que quien invita?

### D3. Coeficientes en el CSV

- **Hecho:** `registrar_esquema_coeficientes` rechaza todo origen que no sea `soporte` y exige `decision_id` (`0021` l. 110-136). `coeficientes.aprobar` está fuera de alcance.
- **Alternativas:**
  1. Por la API, los coeficientes solo se informan: incompletos, la suma tal cual y la diferencia con el esquema vigente. No se escriben. El equipo los carga con `scripts.cargar_coeficientes`.
  2. Un script de soporte que importe el CSV entero, coeficientes incluidos, con la decisión.
  3. Abrir la escritura por la API, lo que pide `coeficientes.aprobar`: descartada por la consigna.
- **Recomendación:** la 1.
- **Pregunta D3:** ¿la 1? (sí/no)

### D4. La invitación que crea la importación: vencimiento, facultad y la pendiente previa

- **Hecho:**
  - la invitación dura 7 días como máximo, impuesto por la base (`0007` l. 54);
  - la importación no la envía (decisión vigente);
  - crear invitaciones es la acción `invitaciones.crear`, distinta de `padron.gestionar`;
  - la política de inserción exige `invitada_por = persona del ámbito` (`0007` l. 71-72).
- **Inferencia:**
  - si nadie avisa, la invitación vence sin que la persona la vea, salvo que entre por su cuenta dentro de esos 7 días;
  - con solo `padron.gestionar`, la importación crearía invitaciones sin tener la facultad de invitar.
- **Preguntas** (numeradas):
  1. ¿La importación exige además `invitaciones.crear` sobre la unidad de cada fila que crea una invitación? **Recomiendo que sí**: si falta, es un error del informe y se rechaza el archivo.
  2. Si ya hay una invitación pendiente al mismo correo, unidad y tipo. El propietario ya fijó que una reimportación respeta el índice **sin retirar en silencio una invitación vigente** (§2.4, l. 169). Eso descarta retirar como hace la ruta de invitar (`app/identidad/invitaciones.py:121`). Quedan:
     - **(b)** si la vigente nombra **el mismo** vínculo, se conserva y la fila queda "ya existe";
     - **(c)** si es manual (sin `vinculo_id`) o nombra otro vínculo, es un error del informe que bloquea el archivo;
     - **(d)** una vencida que no se retiró ocupa igual el índice: se retira **y el informe lo dice** antes de aplicar.

     **Recomiendo (b) + (c) + (d).** ¿Sí o no?
  3. ¿Aceptás que la invitación sin enviar venza a los 7 días, y que para renovarla haya que reimportar o invitar a mano? La alternativa es cambiar el tope de la base, que es otra decisión. **Recomiendo que sí.**

### Sin pregunta (se sigue el diseño o una regla existente)

- **El informe no revela si un correo ya tiene cuenta** en otro consorcio. "Ya existe" se informa solo dentro del consorcio: unidades, vínculos e invitaciones. Así no se pueden enumerar personas entre consorcios, con la misma regla del 202 de `/auth/enlace`.
- **Anulación:** como en §4, l. 138-144.

## 4. Plan mínimo de implementación (con las recomendaciones; cambia según las respuestas)

1. **Migración `0025`:**
   - `nucleo.importacion_padron`, con RLS habilitada y forzada, y `vinculo.importacion_id` (D1-a);
   - `nucleo.importar_padron(...)`, `security definer`: busca o crea la persona por correo con el bloqueo `persona:<correo>` de `aceptar_invitacion`, crea las unidades y los vínculos `importacion` sin confirmar con `registrado_por`, y las invitaciones con su `vinculo_id` (D4);
   - `nucleo.invitacion.vinculo_id` opcional, con una FK compuesta por consorcio; `aceptar_invitacion` con la rama que, cuando viene `vinculo_id`, toma exactamente ese vínculo y responde, por ejemplo, `vinculo_no_corresponde`; la ruta lo mapea a un 409 explícito (D2, decidida);
   - `nucleo.anular_vinculo(vinculo, motivo, actor, origen, referencia)`: la API solo anula un vínculo sin confirmar; `soporte`, con la referencia del respaldo. Audita sin el motivo;
   - downgrade con su guarda.
2. **`contratos/`:** modelos del informe y del resultado, y regenerar `openapi.json`.
3. **`app/consorcios/importacion.py`:**
   - parseo del CSV: se rechaza la columna `vigente_hasta` y se convierte `ultimo_dia`;
   - el informe: `POST …/padron/importacion/informe`, que no escribe;
   - la aplicación: `POST …/padron/importacion`, con `Idempotency-Key`, en una transacción;
   - el alcance fila por fila con `padron.gestionar` (más `invitaciones.crear`), y el alcance general para crear unidades;
   - `POST …/vinculos/{v}/anular`.
4. **Un script de soporte** para anular un vínculo confirmado.
5. **Pruebas contra PostgreSQL real**, en `test_importacion_padron.py`:
   - errores sembrados: duplicados, solapes, fechas inválidas, coeficientes incompletos y elementos existentes;
   - atomicidad, con una fila mala al final;
   - alcance: por unidad, general y una fila fuera del mandato;
   - el importado sigue sin ámbito;
   - reintento con la misma clave y reimportación (idéntica, corregida y el mismo hash);
   - `invitacion_pendiente_unica`;
   - `vinculo_id`: la aceptación nombra exactamente el vínculo, aun con otro sucesivo de la misma persona; error explícito si fue anulado, o si es de otra persona, unidad o carácter; control: una invitación manual sin `vinculo_id` se comporta igual que en `0014`;
   - RLS de la tabla nueva contra otro consorcio;
   - anulación: API y soporte, un vínculo ya iniciado (rechazada) y uno confirmado por la API (rechazada).

## 5. Criterio exacto de terminado

- D1, D3 y D4 respondidas (D2 ya decidida).
- La rama `rf02-importacion-corte3`, desde `94a2114`, empujada y sin PR.
- La suite oficial en verde con `docker compose -f pruebas/compose.yml run --rm -T cem-backend-pruebas sh -c 'pytest ...'`, incluida la prueba de cierre del corte (§1, l. 18): un informe sobre un padrón con duplicados, solapes y coeficientes incompletos sembrados nombra cada error y no escribe nada. Como control, el mismo padrón limpio se aplica.
- Una mutación por regla clave detectada: la conversión de `ultimo_dia`, la atomicidad y el alcance.
- `ruff` limpio en los `.py` tocados; `openapi.json` regenerado.
- `ESTADO.md` actualizado; Codex y una revisión independiente sobre el head final.
