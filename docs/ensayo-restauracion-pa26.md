# Ensayo de restauración PA26 en staging (RNF06)

| Dato | Valor |
|---|---|
| Fecha | 23-11-2026, 04:10 a 04:27 UTC |
| Staging | `c7c200a` (imagen `ghcr.io/githolan/cem-backend:c7c200a…`), base en `0128`, contrato `0.93.0` |
| Producción | `1535bd7`, no se tocó |
| Script | [`backend/pruebas/ensayo_restauracion_staging.py`](../backend/pruebas/ensayo_restauracion_staging.py) |
| Pruebas del script | [`backend/tests/test_ensayo_restauracion.py`](../backend/tests/test_ensayo_restauracion.py) (14 de 14) |
| Umbrales | RNF06: RPO de 15 minutos y RTO de 4 horas (documento 13, §17 y §20; H2c §6) |
| Antecedente | 27-10-2026, base `0038`: 26 s, cadena de 1518 eventos (`doc/deploys/2026-10-27-4994f7c.md` del repositorio CEM) |

## Resultado

**PA26 pasó sobre el esquema actual.** Corrida 4, consorcio del ensayo `523fe585-f95b-4111-bd53-cdd37b905a3e`.

| Control | Resultado | Control de la medición |
|---|---|---|
| Marca A | Decisión `01a206a4-5982-7d0f-8cf5-e264859c59a3`, confirmada a las 04:22:43.857 UTC, segmento `…038` | — |
| RPO observado | El WAL de A llegó al bucket (`wal-parcial/000000010000000000000038-20261123T042502Z.partial.gz.age`) **142 s** después de confirmada, contra 15 min | En las corridas 1, 3 y 4: 257 s, 45 s y 142 s. Las tres por debajo de 5 min + 1 vuelta, el intervalo de los parciales |
| Marca B | Decisión `01a206a6-91fb-753a-9f70-fc85174f90cd` a las 04:25:09.367; `pg_switch_wal()` y `wal/000000010000000000000038.gz.age` subido | — |
| Momento objetivo | 04:23:56.612 UTC (punto medio entre A confirmada y B empezada) | — |
| Copia base | `base/20261117T053929Z` (verificada), WAL `…02E` a `…038`: 11 segmentos, 46,0 MB cifrados | — |
| Restauración | `postgres:18-alpine` sin red, datos en tmpfs. **55,6 s** desde el inicio de la descarga hasta la base promovida, contra un RTO de 4 h | El registro dice `recovery stopping before commit of transaction 53513, time 2026-11-23 04:25:00.57527+00` y `selected new timeline ID` |
| A sí, B no | Restaurada: A = 1, B = 0 | Base real: A = 1, B = 1 |
| Cadena de auditoría | Restaurada: 6365 eventos, 0 enlaces rotos, 0 hashes distintos | La misma fórmula con la acción alterada da 6365 distintos (detecta una alteración). Real: 6365 eventos, 0 rotos |
| Estado anterior a B | Restaurada: 286 decisiones, 95 consorcios | Real: 287 decisiones (B), 95 consorcios. Las decisiones de soporte no escriben eventos desde `0032`: por eso el control es la decisión y no el evento |
| Tablas de `0119` a `0128` | Las 17 tablas están en la restaurada, con RLS, RLS forzada y cantidad de políticas iguales a las de la real | 15 con RLS forzada y 1 política. `nucleo.perfil_normativo` y `nucleo.perfil_normativo_requisito` sin RLS en las dos bases: catálogo por jurisdicción, sin `consorcio_id` (`0125`) |
| Copia diaria de hoy | `diarias/2026-11-23/cem-20261123T033000Z.dump.age`: SHA-256 `412210c1…a95` igual al del trabajo, descifrada y restaurada con `pg_restore --no-owner --no-acl` en **29,3 s** | 120 tablas y 59623 filas, las que registró el trabajo `plataforma.copia_diaria` de las 03:30:05 UTC |
| Limpieza | 0 contenedores, 0 volúmenes y sin carpeta del ensayo en la VM | La misma cuenta encuentra `cem-postgres-staging` (1). Comprobado aparte por `ssh`: `docker ps -a` 0, `docker volume ls` 0, `/tmp/cem-pa26-*` no existe |

**Comparado con el 27-10:** 55,6 s contra 26 s. El script cuenta también los viajes por `ssh` de cada paso (listar, leer la marca, bajar, descifrar, arrancar) y la base es más grande (6365 eventos contra 1518; 46 MB cifrados contra ~10 MB). Sigue a cuatro órdenes de magnitud del RTO.

## Cómo se hizo

- **La identidad privada** (`cem-clave-copias-age`, bóveda `cem-claves` de OCI) la lee el script con el SDK de OCI y el perfil `DEFAULT` de la PC, la tiene solo en memoria y la escribe en la entrada estándar del `ssh` del contenedor que descifra. Ese contenedor no tiene red, la guarda en su tmpfs y la borra al terminar; el script comprueba que `/run/clave` quedó vacío. Es la misma vía del 27-10 («la identidad de la bóveda solo en su tmpfs»).
- **Nada en claro tocó el disco:** el texto cifrado baja a `/tmp/cem-pa26-<corrida>` de la VM; lo descifrado va a un volumen tmpfs. El WAL queda descifrado pero comprimido y lo descomprime `restore_command`, de a un segmento.
- **La base de staging no se tocó** más que por las dos marcas (decisiones de soporte en un consorcio LAB nuevo) y `pg_switch_wal()`. La restauración va a un PostgreSQL efímero y separado.
- **Lo que queda en staging:** los consorcios `CEM LAB — Ensayo PA26 <corrida> (SINTETICO, PRUEBA, NO REAL)` de las corridas 1, 3 y 4 (`1e48b3b3-0afd-4674-9ddc-02f977044067`, `35a17ef1-725b-4f85-acad-5f8e41635431`, `523fe585-f95b-4111-bd53-cdd37b905a3e`), una unidad y dos decisiones cada uno. Ninguna persona, sesión ni correo.

### Corridas que no cuentan

| Corrida | Qué pasó | Arreglo |
|---|---|---|
| 1 | Marcas y RPO bien (257 s). El PostgreSQL efímero no encontró `/restaurar/datos`: un volumen tmpfs se vacía cuando ningún contenedor lo monta, y el contenedor que descifró era `--rm` | El que descifra queda vivo, durmiendo, hasta la limpieza. Limpieza: 0 restos |
| 2 | Error de sintaxis del script; no escribió nada | Corregido |
| 3 | Restaurada en 60,1 s, A sí y B no, cadena íntegra y tablas bien. Falló un control mal planteado: esperaba un evento más en la real por B, y las decisiones de soporte no escriben eventos desde `0032` | El control pasa a ser la cantidad de decisiones. La copia diaria no llegó a correr. Limpieza: 0 restos |

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
   - Resultado esperado, en orden: `ok  marca A`, `ok  el WAL de A está en el bucket … contra un RPO de 15 min`, `ok  marca B`, `ok  wal/… subido`, `identidad borrada del tmpfs`, `ok  restaurada y promovida en N s contra un RTO de 4 h`, `ok  la restaurada tiene la marca A y no la B`, `ok  cadena íntegra`, `ok  las 17 tablas de 0119 a 0128`, `ok  copia diaria`, `ok  limpieza`. Código de salida 0.
   - Si no: anotar la línea `FALLÓ:` y el JSON final; detenerse. No repetir sin entender la falla.
4. Comprobar la limpieza aparte:
   `ssh -o BatchMode=yes oci "docker ps -a --format '{{.Names}}' | grep -c pa26; docker volume ls --format '{{.Name}}' | grep -c pa26; ls -d /tmp/cem-pa26-*"`
   - Resultado esperado: `0`, `0` y `No such file or directory`.
   - Si no: anotar el nombre que quedó y avisar; no borrar a mano sin anotarlo.

#### Casos de falla

1. `NEGADO: recursos con más de un contenedor`.
   - Resultado esperado: hay un despliegue en curso. Esperar a que termine y volver al paso 2.
2. `FALLÓ: el WAL de A en el bucket: no llegó en 900 s`.
   - Resultado esperado: RNF06 no se cumple. Anotar el estado del monitor de Kuma de la copia continua y detenerse.
3. `FALLÓ: la base restaurada se detuvo`.
   - Resultado esperado: el final del registro de PostgreSQL va en el mensaje. Anotarlo y detenerse.

#### Cierre

- Anotar en ESTADO.md: corrida, consorcio del ensayo, RPO observado, segundos de restauración, eventos de la cadena, la copia diaria (objeto, tablas y filas).
- Dejar staging como estaba: el script no cambia configuración; los consorcios LAB del ensayo quedan (de solo agregar).
