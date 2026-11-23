# Humo de carga en staging con el perfil del pico mensual (RNF01)

| Dato | Valor |
|---|---|
| Fecha | 23-11-2026, 04:28 a 04:35 UTC |
| Staging | `c7c200a`, base `0128`, contrato `0.93.0` |
| Producción | `1535bd7`, no se tocó |
| Script | [`backend/pruebas/carga_staging.py`](../backend/pruebas/carga_staging.py) |
| Pruebas del script | [`backend/tests/test_carga_staging.py`](../backend/tests/test_carga_staging.py) (8 de 8, una de ellas una corrida corta entera contra un backend real) |

## Perfil y umbrales (de los documentos, sin inventar)

- **Perfil** (documento 13, §19.1): piloto con hasta 120.000 peticiones por día; pico mensual a 10 veces el promedio horario. 120.000 / 86.400 × 10 = **13,9 pedidos por segundo**.
- **Umbral** (documento 08, RNF01, y documento 13, §19.3 y §20): el 95 % de las consultas en menos de **1 s**, «bajo 500 sesiones».
- **Sin umbral en los documentos:** p50, p99 y tasa de errores. Se informan sin veredicto.
- **Mezcla:** propietarios: sesión, sus edificios, sus invitaciones, resumen, Mis expensas (`mi-cuenta`), comunicados y reparaciones; Consejo, además: panel, liquidación oficial, reclamos y liquidaciones importadas. Escrituras: pedidos de reparación de propietarios.
- **Sesiones:** 100 personas, cada una con su sesión real (invitación aceptada por HTTP) y su IP de prueba (RFC 5737). No 500: cada invitación encola un correo a `.invalid` y 500 dejarían 500 trabajos en la cola. **La condición de 500 sesiones de RNF01 queda SIN VERIFICAR.**

## Resultado

**Con 0,3 CPU y 512 MiB en `cem-postgres-staging`, RNF01 SE CUMPLE en staging al pico (latencia; las 500 sesiones siguen SIN VERIFICAR).** Los límites los subió el propietario el 23-11-2026 (ver «Repetición»). La corrida de abajo, con 0,15 CPU y 256 MiB, queda como antecedente.

### Repetición con 0,3 CPU y 512 MiB (consorcio `bda461ef-5897-45c4-8ffc-f37d18e194e9`, corrida `a5d96402`)

| Dato | Valor |
|---|---|
| Cambio | Sí del propietario en el chat (23-11-2026): `PATCH /api/v1/databases/<cem-postgres-staging>` con `limits_cpus` `0.3` y `limits_memory` `512m`, y `POST …/restart`. Leído después: API `0.3` y `512m`; `docker inspect` `NanoCpus=300000000`, `Memory=536870912`, `healthy` desde 12:45:59 UTC. Control de la API: token inventado → 401 |
| Configuración de PostgreSQL | Sin cambios: `shared_buffers` 128 MB, `max_connections` 100, `cem_app` con tope de 40 conexiones |
| Ventana | 12:47:13 a 12:50:13 UTC; escena 12,2 s |
| Pedidos | 2502 en 179,9 s: **13,9 por segundo logrados**, 0 tarde, `medicion_valida: true` |
| Lecturas | 2492: p50 **17,0 ms**, p95 **43,7 ms**, p99 95,9 ms, máximo 215,9 ms |
| Escrituras | 10, todas 201; p50 60,4 ms, máximo 111,7 ms |
| Códigos | 200: 2492; 201: 10; **0 errores** |
| Ruta más lenta (p95) | `{c}/reclamos-liquidacion` 202,3 ms (5 pedidos); `{c}/panel` 142,6 ms (8) |
| PostgreSQL en la ventana | 0 `terminated by signal`, 0 `recovery mode`, 0 `too many connections`; `OOMKilled=false`, 0 reinicios. Control de la búsqueda: el registro empieza en el reinicio (12:45:59) y la misma búsqueda encuentra su único `ready to accept connections` |
| `cem-postgres-staging` (`docker stats`, 11 muestras) | CPU 14,1 % al armar la escena, después **5 a 8,5 %** (tope 30 %); memoria de 46 a **173 MiB** de 512 |
| `cem-backend-staging` (con el generador) | CPU 15 a 18 %, 379 MiB de 512 |
| VM | carga hasta 1,90; libres de 311 a 522 MB de 11927 (5 GB disponibles) |
| Cierre | 0 sesiones abiertas, 0 personas siguen entrando; `/health` `ok` con `c7c200a`, `deps` `db ok, redis ok`, `cola` 0 pendientes y 0 muertos |

**Lectura (INFERENCIA, no medida):** con 0,15 CPU la base no alcanzaba a responder, las conexiones se acumulaban (hasta el tope de `cem_app`) y la memoria por conexión llevó el contenedor a 256 MiB; con el doble de CPU no hay cola y la memoria no pasa de 173 MiB. No se probó cuál de los dos límites alcanza solo.

### Antecedente: corrida con 0,15 CPU y 256 MiB

**RNF01 NO SE CUMPLÍA en staging con el perfil del pico.** El cuello es `cem-postgres-staging`: llegó al tope de CPU y de memoria de su contenedor y el kernel mató cuatro procesos de PostgreSQL por memoria (`signal 9`), cada uno con un reinicio de todas las conexiones y una recuperación de 4 a 7 s.

### Corrida al pico (consorcio `3f601bc6-1a08-44ae-97f0-7b65b49ec72a`, corrida `c002fb41`)

| Medida | Valor |
|---|---|
| Escena | 100 unidades y personas, 3 del Consejo, 100 vínculos confirmados por HTTP: 14,2 s. Las 7 rutas del propietario y las 11 del Consejo dieron 200 antes de medir |
| Pedidos | 2502 planeados en 180 s; salieron en 249,8 s: **10,0 por segundo logrados** |
| Salidos tarde | 2346 de 2502: los 64 hilos quedaron esperando respuestas lentas. **La medición no cumple la tasa pedida** y el script sale con 1 |
| Lecturas | 2492: p50 **4517 ms**, p95 **17198 ms**, p99 22765 ms, máximo 46979 ms |
| Escrituras | 10: 3 con 201, 7 con 503; p50 18605 ms |
| Códigos | 200: 2032; 201: 3; **503: 467 (18,7 %)** |
| Causa de los 503 | Backend: «base de datos no disponible» (342 `connection failed`, 108 `server closed the connection`). PostgreSQL: 387 `the database system is in recovery mode`, 302 `not yet accepting connections`, 28 `too many connections for role` |
| Caídas de PostgreSQL | 04:28:50, 04:29:53, 04:30:53 y 04:31:58 UTC: `client backend … was terminated by signal 9: Killed`; listo otra vez a las 04:28:57, 04:29:57, 04:30:57 y 04:32:00. `docker inspect`: `OOMKilled=true` |
| `cem-planificador-staging` | 4 reinicios (uno por caída de la base) |
| Cierre | 0 sesiones abiertas, 0 personas siguen entrando (100 pedidos de control con 401) |

### Control: la misma herramienta a 2 pedidos por segundo (consorcio `bc1c97a5-b20f-4508-8ee7-72ebffe1c90a`)

20 personas, 2 del Consejo, 60 s, sin escrituras: 120 pedidos, 2,02 por segundo logrados, 0 tarde, **0 errores**, p50 **17,7 ms**, p95 **29,7 ms**, p99 218,3 ms, máximo 400,9 ms. Ninguna caída de PostgreSQL. Cierre: 0 sesiones abiertas. **El instrumento mide latencias de milisegundos cuando la base no está saturada:** lo del pico es de staging, no del generador.

### La VM durante el pico (`docker stats --no-stream`, 15 muestras cada ~18 s)

| Contenedor | Antes | Durante | Después |
|---|---|---|---|
| `cem-postgres-staging` (límite 0,15 CPU y 256 MiB) | 0,3 % CPU, 128 MiB | **14,3 a 15,5 % CPU** (el tope de 0,15 CPU todo el tiempo); memoria de 25 a **252,6 MiB** de 256, bajando a cero en cada caída | 0,1 % CPU, 135 MiB |
| `cem-backend-staging` (límite 0,5 CPU y 512 MiB; tiene también el generador) | 0,4 %, 295 MiB | 5,6 a 25,1 %, 306 a 393 MiB | — |
| `cem-frontend-staging` (nginx) | 0 % | 0 a 1,7 %, ≤ 5,2 MiB | — |
| `coolify-proxy` (Traefik) | 1,3 % | 0,1 a 2,8 % | — |
| VM (`uptime`, `free -m`) | carga 1,08; 667 MB libres de 11927 | carga hasta 4,82; libres hasta 342 MB | carga 1,9 |

Después del pico, staging volvió solo: `/health` `ok` con `c7c200a`, `/api/v1/health/deps` `db ok, redis ok`, `/api/v1/health/cola` 0 pendientes y 0 muertos; el slot `cem_copia_continua` activo; ninguna caída más en el control.

## Hallazgos

1. **Defecto de capacidad de staging (no del código), corregido el 23-11-2026:** `cem-postgres-staging` tenía 0,15 CPU y 256 MiB. Con 10 pedidos por segundo satura la CPU y se queda sin memoria; el kernel mata un proceso por minuto y PostgreSQL reinicia todas las conexiones. El documento 13, §19.2 proponía 0,3 CPU y 512 MB para `cem-postgres`. El propietario aprobó subirlos; con 0,3 CPU y 512 MiB la repetición dio 0 errores y p95 de 43,7 ms.
2. **`too many connections for role`** (28 veces): durante las recuperaciones, el rol de la aplicación llegó a su tope de conexiones. Sin las caídas no pasó (0 en la repetición).
3. **La medición al pico no es válida como cifra de RNF01**: la tasa lograda fue 10,0 y no 13,9 por segundo, porque la base no respondía. Lo válido es el veredicto: con menos tasa que la del pico ya no cumple.
4. **Producción no se midió** ni se cambiaron sus límites: `cem-postgres` sigue con los suyos (SIN VERIFICAR si alcanzan; cambiarlos es decisión del propietario para el pase a producción).
5. **El generador corre dentro de `cem-backend-staging`** (como el smoke y los ensayos): comparte el medio CPU del backend. El control a 2 por segundo muestra que eso no explica segundos de latencia, pero a la tasa del pico le resta CPU al backend. Una corrida desde otro contenedor queda SIN VERIFICAR.

## Lo que queda en staging

Consorcios `CEM LAB — Carga de pico mensual (SINTETICO, PRUEBA, NO REAL)` `3f601bc6-…` (100 unidades, 100 personas `@cem-prueba.invalid`, 3 mandatos dados de baja, 3 reparaciones pedidas) y `bc1c97a5-…` (20 unidades y personas, 2 mandatos dados de baja). 120 correos de invitación a `.invalid` y los avisos de las 3 reparaciones (a los 3 del Consejo), cerrados sin enviar por `cem-trabajos`. Todas las sesiones revocadas.

## Procedimiento (tarjeta de tarea)

### Tarea CARGA-1: humo de carga con el perfil del pico mensual, en staging

| Campo | Valor |
|---|---|
| Efectividad | Staging `consorcio-en-mano-staging`; verificado con `c7c200a` el 23-11-2026 |
| Quién | Equipo de la plataforma, en la PC con `ssh oci` (Git Bash) |

#### Precondiciones

| Condición | Cómo se comprueba |
|---|---|
| Staging sano | `ssh -o BatchMode=yes oci "docker ps --format '{{.Names}} {{.Status}}'"`: todos los `cem-*-staging` `healthy` |
| Nadie más usa staging en la ventana | Preguntar; el pico puede tirar la base de staging (ver Hallazgos) |
| Contenedor del backend | `ssh -o BatchMode=yes oci "docker ps -q \| xargs docker inspect --format '{{.Name}} {{index .Config.Labels \"coolify.resourceName\"}}'" \| grep ' cem-backend-staging$'` |
| Host de la base | El de `cem-postgres-staging` en `doc/infra/REGISTRO_RECURSOS.md` del repositorio CEM (el 23-11-2026: `ai3mquqkmpcqkg30paos0nxr`) |

#### Advertencias

> **WARNING:** con 0,15 CPU y 256 MiB la corrida al pico hizo caer la base de staging cuatro veces en 4 minutos; con 0,3 CPU y 512 MiB no cayó. Antes de correrla, comprobar los límites (`docker inspect <cem-postgres-staging> --format '{{.HostConfig.NanoCpus}} {{.HostConfig.Memory}}'` → `300000000 536870912`). No correrla durante una prueba con personas ni durante un despliegue.

> **WARNING:** nunca contra producción. La guarda se niega si `CEM_ORIGEN_PUBLICO` no es de staging.

> **CAUTION:** cada persona encola un correo a `.invalid`. No subir `--personas` a 500 sin decidirlo antes.

#### Pasos

1. Generar un UUID nuevo para el consorcio: `python -c "import uuid; print(uuid.uuid4())"`.
2. Correr la guarda (bash, desde la raíz del repositorio):
   `ssh -o BatchMode=yes oci "docker exec -i -w /app -e PYTHONPATH=/app <cem-backend-staging> python - --consorcio <uuid> --base <host> --url https://coolify-proxy --host cem-staging.holancloud.com --sin-verificar-certificado --simular" < backend/pruebas/carga_staging.py`
   - Resultado esperado: JSON con `"simulacion": true` y `"pedidos": 2502`.
   - Si no: anotar `negado: …` y detenerse.
3. Tomar la muestra «antes»: `ssh -o BatchMode=yes oci "uptime; free -m; docker stats --no-stream"`.
4. Correr la carga (mismo comando del paso 2 sin `--simular`, con `-e PYTHONIOENCODING=utf-8`), guardando la salida en un archivo.
5. Mientras corre, repetir el paso 3 cada 15 a 20 s (unas 15 veces).
6. Tomar la muestra «después» (paso 3).
7. Leer el JSON del final: `resultado.lecturas.p95_ms`, `resultado.codigos`, `resultado.tasa_lograda`, `resultado.medicion_valida`, `sesiones_abiertas_al_cerrar`, `personas_que_siguen_entrando`.
   - Resultado esperado: `medicion_valida: true`, `rnf01.cumple_latencia: true`, 0 sesiones abiertas, 0 personas entrando.
   - Si no: anotar las cifras, y mirar el registro de `cem-postgres-staging` de la ventana:
     `ssh -o BatchMode=yes oci "docker logs --timestamps --since <inicio> <cem-postgres-staging> 2>&1 | grep -E 'terminated by signal|ready to accept'"`.
8. Comprobar que staging volvió: desde el backend, `GET /health`, `/api/v1/health/deps` y `/api/v1/health/cola` con `Host: cem-staging.holancloud.com` (`estado ok`, 0 muertos).
   - Si no: avisar al propietario; no reiniciar nada a mano.
9. Control a baja tasa (SIN VERIFICAR como paso obligatorio; se hizo el 23-11-2026): mismo comando con `--personas 20 --consejo 2 --tasa 2 --duracion 60 --escrituras 0`.
   - Resultado esperado: 0 errores y p95 de decenas de ms.

#### Cierre

- Anotar en ESTADO.md: consorcio, tasa pedida y lograda, p50/p95/p99, códigos, caídas de la base y las muestras de la VM.
- Las sesiones las revoca el script; confirmar `sesiones_abiertas_al_cerrar: 0`.
