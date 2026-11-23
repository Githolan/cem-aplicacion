# Ensayo de restauración PA26 en staging (RNF06)

| Dato | Valor |
|---|---|
| Fecha | 23-11-2026: corrida 5 (la que vale) de 05:31 a 05:38 UTC; corridas 1 a 4 de 04:10 a 04:27 UTC |
| Staging | `c7c200a` (imagen `ghcr.io/githolan/cem-backend:c7c200a…`), base en `0128`, contrato `0.93.0` |
| Producción | `1535bd7`, no se tocó |
| Script | [`backend/pruebas/ensayo_restauracion_staging.py`](../backend/pruebas/ensayo_restauracion_staging.py) |
| Pruebas del script | [`backend/tests/test_ensayo_restauracion.py`](../backend/tests/test_ensayo_restauracion.py) (20 de 20; 8 controles por mutación, ninguno sobrevivió) |
| Umbrales | RNF06: RPO de 15 minutos y RTO de 4 horas (documento 13, §17 y §20; H2c §6) |
| Antecedente | 27-10-2026, base `0038`: 26 s, cadena de 1518 eventos (`doc/deploys/2026-10-27-4994f7c.md` del repositorio CEM) |

## Resultado

**PA26 pasó sobre el esquema actual.** Corrida 5, consorcio del ensayo `daf4df7a-a457-48d2-9809-7fd1d82a12ef`, con el script corregido por los dos hallazgos de Codex en #287 (sección siguiente). **Las cifras de la corrida 4 (RPO de 142 s, 55,6 s) quedan reemplazadas por estas:** aquel RPO contaba el primer parcial subido después de A sin probar que lo tuviera.

| Control | Resultado | Control de la medición |
|---|---|---|
| Marca A | Decisión `01a206e3-9a81-76e8-95ea-2e09fe9455d8`, confirmada a las 05:31:49.346 UTC; posición `0/3960D020` (`pg_current_wal_flush_lsn()` apenas confirmada), segmento `…039` | — |
| Receptor con A | `flush_lsn` del slot `cem_copia_continua` = `0/3960D020` ≥ A a las 05:31:52.543 (3,2 s después de A) | `pg_receivewal --synchronous`: `flush_lsn` es lo escrito y sincronizado en el `.partial` |
| RPO observado | `wal-parcial/000000010000000000000039-20261123T053527Z.partial.gz.age`, el primer parcial sellado después de que el receptor tenía A: subido (`LastModified`) **219 s** después de A y visto a los **223 s**, contra 15 min | Comprobado por restauración (fila «Restauración con lo de ese momento») |
| Marca B | Decisión `01a206e7-1024-790c-9c1d-703d3f1755c9` a las 05:35:35.967; `pg_switch_wal()` y `wal/000000010000000000000039.gz.age` subido | — |
| Momento objetivo | 05:33:42.656 UTC (punto medio entre A confirmada y B empezada) | — |
| Copia base | `base/20261117T053929Z` (verificada), WAL `…02E` a `…039`: 12 segmentos, 47,8 MB cifrados | — |
| Restauración (RTO) | `postgres:18-alpine` sin red, datos en tmpfs. **54,6 s** desde el inicio de la descarga hasta la base promovida; la espera de la promoción recibió lo que quedaba del plazo y el total se comparó con el RTO de 14400 s | El registro dice `recovery stopping before commit of transaction 54207, time 2026-11-23 05:35:04.777399+00` y `selected new timeline ID` |
| A sí, B no | Restaurada: A = 1, B = 0 | Base real: A = 1, B = 1 |
| Cadena de auditoría | Restaurada: 6608 eventos, 0 enlaces rotos, 0 hashes distintos | La misma fórmula con la acción alterada da 6608 distintos. Real: 6608 eventos, 0 rotos |
| Estado anterior a B | Restaurada: 290 decisiones, 98 consorcios | Real: 291 decisiones (B), 98 consorcios. Las decisiones de soporte no escriben eventos desde `0032`: por eso el control es la decisión |
| Tablas de `0119` a `0128` | Las 17 tablas están en la restaurada, con RLS, RLS forzada y cantidad de políticas iguales a las de la real | `nucleo.perfil_normativo` y `nucleo.perfil_normativo_requisito` sin RLS en las dos bases: catálogo por jurisdicción, sin `consorcio_id` (`0125`) |
| Restauración con lo de ese momento (RPO) | Otra restauración, sin objetivo, con la copia base, los 11 segmentos completos (`…02E` a `…038`) que había al RPO y el parcial contado como `…039`: **A = 1**, B = 0; 30,3 s | A está en el segmento `…039` (posición `0/3960D020`) y los completos llegan hasta `…038`: A solo puede venir del parcial. B = 0 porque se escribió después |
| Copia diaria de hoy | `diarias/2026-11-23/cem-20261123T033000Z.dump.age`: SHA-256 `412210c1…a95` igual al del trabajo, descifrada y restaurada con `pg_restore --no-owner --no-acl` en **28,5 s** | 120 tablas y 59623 filas, las que registró el trabajo `plataforma.copia_diaria` de las 03:30:05 UTC |
| Limpieza | 0 contenedores, 0 volúmenes y sin carpeta del ensayo en la VM | La misma cuenta encuentra `cem-postgres-staging` (1). Comprobado aparte por `ssh`: `docker ps -a` 0, `docker volume ls` 0, `/tmp/cem-pa26-*` no existe |

**Comparado con el 27-10:** 54,6 s contra 26 s. El script cuenta también los viajes por `ssh` de cada paso y la base es más grande (6608 eventos contra 1518; 47,8 MB cifrados contra ~10 MB). Sigue a cuatro órdenes de magnitud del RTO.

## Hallazgos de Codex en #287 (P2, los seis reales y corregidos)

1. **RPO sin prueba de contenido.** El script aceptaba el primer parcial con sello posterior a la confirmación de A. `copia_continua.subir_parcial()` sella con la hora de la vuelta una copia del archivo del receptor: si el receptor está atrasado, ese parcial puede tener solo lo anterior a A, y el RPO informado quedaba corto. La restauración posterior usaba el WAL completo subido después de B, así que encontrar A ahí no validaba el parcial.
   - **Arreglo (el método más fuerte de los dos posibles):** se toma la posición de A; el parcial cuenta solo si su sello es posterior al momento en que `flush_lsn` del receptor llegó a esa posición (condición de elección); y **se prueba restaurando con exactamente los artefactos que había en el almacén en ese momento** (la copia base, los segmentos completos de entonces y ese parcial), sin objetivo, exigiendo A. Si A no está, el ensayo falla y no informa RPO.
   - En esta corrida el receptor estaba al día (3,2 s): el parcial elegido sale igual que antes por el intervalo de 5 min, pero ahora está probado.
2. **RTO con plazo nuevo.** El reloj empezaba antes de listar y bajar, pero la espera de la promoción recibía 4 horas nuevas después de bajar, descifrar y arrancar, y el total no se comparaba con el RTO.
   - **Arreglo:** la espera recibe lo que queda del plazo total (`restante`), falla si ya no queda nada, y el total se compara con el RTO (`exigir_rto`).
3. **Controles por mutación** (mutado, la prueba esperada falla en la aserción esperada, restaurado e idéntico con `cmp`; ninguno sobrevivió):

| # | Mutación | Prueba que la detecta |
|---|---|---|
| M1 | Las marcas cuentan desde la confirmación de A en vez de desde la sincronización del receptor | `test_las_marcas_cuentan_el_parcial_tomado_con_a_sincronizada` |
| M2 | Sello del parcial `>=` en vez de `>` | `test_el_parcial_tiene_que_ser_posterior_al_segundo_de_la_marca` |
| M3 | Los artefactos al RPO toman los completos hasta `S` en vez de `S − 1` | `test_los_artefactos_al_rpo_son_los_de_ese_momento` |
| M4 | Los artefactos al RPO sin el parcial | `test_los_artefactos_al_rpo_son_los_de_ese_momento` |
| M5 | `restante` devuelve el RTO entero | `test_la_promocion_espera_lo_que_queda_del_rto_y_no_un_plazo_nuevo` |
| M6 | `exigir_rto` con `>=` | ídem |
| M7 | `restaurar` sin `exigir_rto` | `test_la_restauracion_falla_si_el_total_pasa_el_rto` |
| M8 | `restaurar` pasa un inicio nuevo a la espera | ídem (falla en la aserción del inicio recibido) |

### Tercer hallazgo P2 de Codex en #287 (en `7b375c9`, real y corregido)

4. **RPO con plazos nuevos y sin comparar.** Con el receptor atrasado, la espera de la sincronización y la de la subida al almacén recibían 900 s cada una, y `rpo_observado_s` se registraba sin compararlo con el umbral: sincronizar a los 10 min y subir 10 min después se aceptaba con 1200 s.
   - **Arreglo:** un solo plazo desde la confirmación de A para las dos esperas (`restante`) y `exigir_rpo`: falla si `rpo_observado_s` pasa 900 s.
   - **Revisión del resto del script** (misma regla: ningún paso con un plazo nuevo dentro de un camino con límite, ninguna cifra medida sin comparar):
     - `restaurar_con`: bajar y descifrar tenían 3600 s fijos cada uno; ahora reciben `tope_paso`, el menor entre 3600 s y lo que queda del RTO.
     - `comprobar_rpo`: los segundos de su restauración se registraban sin comparar; ahora pasan por `exigir_rto`.
     - `diaria`: bajar, descifrar, el arranque (300 s) y `pg_restore` (3600 s) tenían plazos propios y el total no se comparaba; ahora comparten el plazo del RTO desde el inicio de la diaria y el total pasa por `exigir_rto`.
     - Queda con plazo propio solo la espera del segmento de B (10 min): es de preparación y no mide ningún umbral de RNF06.
   - **Staging no se volvió a correr:** la corrida 5 dio RPO 219 s (subido) y 223 s (visto), dentro de 900 s, y la restauración 54,6 s, dentro del RTO de 14 400 s. El cambio solo agrega comprobaciones y acota plazos; con esos números no cambia el resultado.
   - **Controles por mutación** (mismo método; ninguno sobrevivió; archivo restaurado, `cmp` idéntico y SHA-256 igual antes y después):

| # | Mutación | Prueba que la detecta |
|---|---|---|
| R1 | Espera del receptor con 900 s nuevos | `test_las_esperas_del_rpo_comparten_un_plazo_desde_a` |
| R2 | Espera de la subida con 900 s nuevos | ídem |
| R3 | El plazo del RPO empieza ahora y no en la confirmación de A | ídem |
| R4 | `marcas` sin `exigir_rpo` | `test_las_marcas_fallan_si_el_rpo_pasa_los_15_minutos` |
| R5 | `exigir_rpo` con `>=` | `test_el_rpo_y_el_tope_de_cada_paso_no_pasan_su_limite` |
| R6 | `tope_paso` sin acotar por lo que queda | ídem |
| R7 | `restaurar_con` baja con 3600 s fijos | `test_bajar_y_descifrar_de_la_restauracion_usan_lo_que_queda_del_rto` |
| R8 | `restaurar_con` descifra con 3600 s fijos | ídem |
| R9 | `diaria` baja con 3600 s fijos | `test_la_diaria_comparte_un_plazo_y_falla_si_pasa_el_rto` |
| R10 | `diaria` descifra con 3600 s fijos | ídem |
| R11 | `diaria` espera el arranque con 300 s fijos | ídem |
| R12 | `pg_restore` con 3600 s fijos | ídem |
| R13 | `diaria` sin `exigir_rto` | ídem |
| R14 | `comprobar_rpo` sin `exigir_rto` | `test_la_comprobacion_del_rpo_falla_si_pasa_el_rto` |

### Cuarto hallazgo P2 de Codex en #287 (en `f62bd38`, real y corregido)

- `--diaria` aceptaba el «ready to accept connections» del servidor temporal de la inicialización cuando todavía no estaba la marca `init process complete` (`split(...)[-1]` devolvía el registro entero). `createdb` y `pg_restore` podían correr contra ese servidor, que el entrypoint apaga: una copia buena podía fallar a veces.
- Arreglo: `postgres_inicializado(registro)` exige la marca de fin de inicialización y, después de ella, la de listo.
- Prueba: `test_postgres_inicializado_exige_el_fin_de_la_inicializacion`. Control por mutación: con el predicado viejo, la prueba falla; restaurado, `cmp` idéntico. 35 de 35 en el compose `cem-h6b`; ruff limpio.
- Staging no se repitió: la corrida 5 restauró la diaria bien (28,5 s) y el cambio solo endurece la espera. **SIN VERIFICAR** por ejecución en staging.

### Quinto hallazgo P2 de Codex en #287 (en `0b9e82c`, real y corregido)

- El RPO restaba la hora de la base (`clock_timestamp()` en la VM) de la hora de la PC (`datetime.now`). Con la PC atrasada, un RPO alto podía pasar: 10 min de atraso daban 25 min de plazo y mostraban 20 min como 10.
- Arreglo: un solo reloj, el monotónico de la PC, tomado **antes** de pedir A. La confirmación de A ocurre después, así que lo medido es una cota superior del real. El plazo compartido y `rpo_observado_s` salen de ese reloj. `rpo_subido_s` (`LastModified` del bucket menos la hora de A en la base) queda como dato informativo, sin umbral.
- Pruebas: las de las marcas pasan al reloj monotónico, y una nueva, `test_el_rpo_no_depende_de_la_hora_de_la_pc`, falla si vuelve la resta entre relojes. Control por mutación: con la resta vieja en `rpo` o en el inicio del plazo, fallan 3 pruebas cada vez (por la aserción, no por un import); restaurado, `cmp` idéntico. 36 de 36; ruff limpio.
- **Las cifras de la corrida 5 cruzan relojes** (219 s: bucket contra base; 223 s: PC contra base). Los tres relojes se sincronizan por NTP y el margen es amplio (219 s contra 900 s), pero la medición con un solo reloj **no corrió en staging: SIN VERIFICAR**. La prueba de contenido (restaurar solo con lo que había en el momento del RPO: A presente, B ausente) no depende de relojes y sigue valiendo.

### Sexto hallazgo P2 de Codex en #287 (en `d1eade8`, real y corregido)

- La comprobación del RPO volvía a elegir la copia base al final. Una base que empezó antes de A, pero se verificó y subió después del RPO (`verificada.json` se publica recién al terminar), podía entrar en una restauración que dice usar «lo que había al RPO». Solo el inventario de WAL estaba congelado.
- Arreglo: al ver el WAL de A, `marcas()` congela también la base verificada (`self.rpo["base"]`), antes de leer el reloj; `comprobar_rpo()` usa exactamente esa.
- Prueba: `test_la_comprobacion_del_rpo_usa_la_base_congelada_al_rpo`. Control por mutación: con la búsqueda nueva al final, la prueba recibe la base posterior y falla; restaurado, `cmp` idéntico. 37 de 37; ruff limpio.
- La corrida 5 no se repitió: su comprobación usó `base/20261117T053929Z`, verificada seis días antes de la corrida, así que estaba disponible al RPO y esa prueba sigue valiendo.

## Cómo se hizo

- **La identidad privada** (`cem-clave-copias-age`, bóveda `cem-claves` de OCI) la lee el script con el SDK de OCI y el perfil `DEFAULT` de la PC, la tiene solo en memoria y la escribe en la entrada estándar del `ssh` del contenedor que descifra. Ese contenedor no tiene red, la guarda en su tmpfs y la borra al terminar; el script comprueba que `/run/clave` quedó vacío. Es la misma vía del 27-10 («la identidad de la bóveda solo en su tmpfs»). En la corrida 5 se descifró dos veces (la restauración a un momento y la del RPO), por la misma vía.
- **Nada en claro tocó el disco:** el texto cifrado baja a `/tmp/cem-pa26-<corrida>` de la VM; lo descifrado va a un volumen tmpfs. El WAL queda descifrado pero comprimido y lo descomprime `restore_command`, de a un segmento; un parcial va como su segmento.
- **La base de staging no se tocó** más que por las dos marcas (decisiones de soporte en un consorcio LAB nuevo) y `pg_switch_wal()`. La restauración va a un PostgreSQL efímero y separado.
- **Lo que queda en staging:** los consorcios `CEM LAB — Ensayo PA26 <corrida> (SINTETICO, PRUEBA, NO REAL)` de las corridas 1, 3, 4 y 5 (`1e48b3b3-0afd-4674-9ddc-02f977044067`, `35a17ef1-725b-4f85-acad-5f8e41635431`, `523fe585-f95b-4111-bd53-cdd37b905a3e`, `daf4df7a-a457-48d2-9809-7fd1d82a12ef`), una unidad y dos decisiones cada uno. Ninguna persona, sesión ni correo.

### Corridas que no cuentan

| Corrida | Qué pasó | Arreglo |
|---|---|---|
| 1 | Marcas y RPO bien (257 s). El PostgreSQL efímero no encontró `/restaurar/datos`: un volumen tmpfs se vacía cuando ningún contenedor lo monta, y el contenedor que descifró era `--rm` | El que descifra queda vivo, durmiendo, hasta la limpieza. Limpieza: 0 restos |
| 2 | Error de sintaxis del script; no escribió nada | Corregido |
| 3 | Restaurada en 60,1 s, A sí y B no, cadena íntegra y tablas bien. Falló un control mal planteado: esperaba un evento más en la real por B, y las decisiones de soporte no escriben eventos desde `0032` | El control pasa a ser la cantidad de decisiones. La copia diaria no llegó a correr. Limpieza: 0 restos |
| 4 | Todo verde (RPO 142 s, 55,6 s, cadena de 6365 eventos, copia diaria en 29,3 s), pero con el RPO sin prueba de contenido y el RTO sin plazo único (hallazgos de Codex) | Reemplazada por la corrida 5 |

## Procedimiento (tarjeta de tarea)

### Tarea PA26-1: ensayo de restauración a un momento, en staging

| Campo | Valor |
|---|---|
| Efectividad | Staging `consorcio-en-mano-staging`; verificado con `c7c200a`, base `0128`, el 23-11-2026 |
| Quién | Equipo de la plataforma, en la PC con `ssh oci` y el SDK de OCI (`C:/tmp/cemoci/v`) |

#### Precondiciones

| Condición | Cómo se comprueba |
|---|---|
| Staging desplegado y sano | `ssh -o BatchMode=yes oci "docker ps --format '{{.Names}} {{.Status}}'"`: los cinco `cem-*-staging` `healthy`, una sola instancia de cada uno |
| Versión de la base conocida | `echo "select version_num from plataforma.alembic_version" \| ssh -o BatchMode=yes oci "docker exec -i <cem-postgres-staging> psql -U postgres -d cem -At"` |
| Copia continua al día | Monitor de Kuma de `cem-copia-continua-staging` arriba |
| Hay una copia base verificada | El paso 2 la nombra |
| El perfil `DEFAULT` de OCI lee la bóveda | El paso 3 lo usa; sin él, el script falla antes de escribir |

#### Materiales

- `C:\tmp\app-h6b\backend\pruebas\ensayo_restauracion_staging.py` (o la misma ruta en el repositorio clonado).
- `C:\tmp\cemoci\v\Scripts\python.exe` (Python con el SDK de OCI).
- Imagen `postgres:18-alpine` en la VM.

#### Advertencias

> **WARNING:** nunca apuntar este script a producción. La guarda lo niega por las etiquetas de Coolify; no editarla.

> **WARNING:** la identidad de la bóveda abre también las copias de producción. No redirigir la salida del script a un archivo compartido ni correrlo con `-v`/depuración que imprima la entrada estándar.

> **CAUTION:** si `cem-copia-continua-staging` está caído, el paso 3 espera hasta 15 min y falla por RPO.

#### Pasos

1. Abrir PowerShell o Git Bash en la carpeta del repositorio de la aplicación.
2. Correr la guarda sin escribir:
   `& "C:\tmp\cemoci\v\Scripts\python.exe" "C:\tmp\app-h6b\backend\pruebas\ensayo_restauracion_staging.py" --version-esperada 0128`
   - Resultado esperado: una línea `staging: {...}` con `"bucket": "cem-copias-seguridad-staging"` y `base en 0128`, y un JSON con `"simulado": true`, la última `copia_base` y `segmentos_desde_ella`.
   - Si no: anotar el `NEGADO:` completo y detenerse.
3. Correr el ensayo con la copia diaria:
   `$env:PYTHONIOENCODING = "utf-8"; & "C:\tmp\cemoci\v\Scripts\python.exe" "C:\tmp\app-h6b\backend\pruebas\ensayo_restauracion_staging.py" --version-esperada 0128 --identidad-boveda --diaria --ejecutar`
   - Resultado esperado, en orden: `ok  marca A`, `ok  el receptor sincronizó … ≥ …`, `ok  el WAL de A está en el bucket … contra un RPO de 15 min`, `ok  marca B`, `ok  wal/… subido`, `identidad borrada del tmpfs`, `ok  restaurada y promovida en N s, dentro del RTO de 14400 s`, `ok  la restaurada tiene la marca A y no la B`, `ok  cadena íntegra`, `ok  las 17 tablas de 0119 a 0128`, `ok  con lo que había en el almacén al RPO … la restaurada tiene A (1)`, `ok  copia diaria`, `ok  limpieza`. Código de salida 0.
   - Si no: anotar la línea `FALLÓ:` y el JSON final; detenerse. No repetir sin entender la falla.
4. Comprobar la limpieza aparte:
   `ssh -o BatchMode=yes oci "docker ps -a --format '{{.Names}}' | grep -c pa26; docker volume ls --format '{{.Name}}' | grep -c pa26; ls -d /tmp/cem-pa26-*"`
   - Resultado esperado: `0`, `0` y `No such file or directory`.
   - Si no: anotar el nombre que quedó y avisar; no borrar a mano sin anotarlo.

#### Casos de falla

1. `NEGADO: recursos con más de un contenedor`.
   - Resultado esperado: hay un despliegue en curso. Esperar a que termine y volver al paso 2.
2. `FALLÓ: el receptor con A sincronizada: no llegó en 900 s`, `FALLÓ: el WAL de A en el bucket: no llegó en 900 s` o `FALLÓ: con lo que había al RPO … la restaurada no tiene A`.
   - Resultado esperado: RNF06 no se cumple. Anotar el estado del monitor de Kuma de la copia continua y detenerse.
3. `FALLÓ: la base restaurada se detuvo`.
   - Resultado esperado: el final del registro de PostgreSQL va en el mensaje. Anotarlo y detenerse.

#### Cierre

- Anotar en ESTADO.md: corrida, consorcio del ensayo, RPO observado, segundos de restauración, eventos de la cadena, la copia diaria (objeto, tablas y filas).
- Dejar staging como estaba: el script no cambia configuración; los consorcios LAB del ensayo quedan (de solo agregar).
