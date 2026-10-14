# Estado de cem-aplicacion

Se lee al empezar cada sesión y se actualiza antes de terminarla (documento 14, regla 5).

**Actualizado:** 14-10-2026.

## Qué existe

- `contratos/`: modelos Pydantic (error uniforme, salud, consorcios, unidades), enumeraciones, `esquemas.json` y `openapi.json` comprometidos.
- `backend/`: FastAPI con `/health`, errores uniformes, `require_scope`, `GET /api/v1/me/consorcios`, `GET /api/v1/consorcios/{id}/unidades` y el motor de reparto.
- `GET /api/v1/health/deps`: PostgreSQL (`select 1` con `cem_app`) y Redis (`PING`); 200 con `{"estado":"ok","db":"ok","redis":"ok"}` o 503 con la que falla, sin detalles (van a los registros). Kuma puede buscar `"db":"ok"`.
- **Límite de tasa (cierra TB-8):** middleware propio `app/limite.py` sobre `limits` con Redis (`cem:rl:*`), 300 pedidos por minuto por IP para toda la API; `/health` y `/api/v1/health/deps` exentas. Sin Redis la API responde 503 (falla cerrada) y la app no arranca sin `CEM_REDIS_URL`. La IP real: el nginx del frontend escribe `X-Forwarded-For` con `CF-Connecting-IP` y `arranque.sh` exige `FORWARDED_ALLOW_IPS` (uvicorn solo acepta ese encabezado de esa red).
- **JWT de Cloudflare Access (primer paso del cierre de TB-1):** `app/acceso.py` verifica `Cf-Access-Jwt-Assertion` (RS256 con las claves de `<equipo>/cdn-cgi/access/certs`, audiencia de la aplicación de Access, emisor, vencimiento) en toda ruta de `/api/v1/soporte/`; la primera es `GET /api/v1/soporte/sesion`, que devuelve quién es el operador. Un encabezado inventado no pasa aunque el pedido llegue sin Access. Sin `CEM_ACCESS_EQUIPO` y `CEM_ACCESS_AUD`, esas rutas rechazan todo y la app arranca igual.
- `frontend/`: PWA (React 18, Vite 5, TypeScript) con página de inicio que lee `/health`; tipos generados desde `contratos/openapi.json`; imagen `nginxinc/nginx-unprivileged:1.27-alpine` (uid 101, puerto 8080) que reenvía `/api/` y `/health` al backend con `resolver` (AD-05). **Desplegado en staging y en producción** desde `main`, hoy en `38aca25`: `cem-frontend-staging` el 14-10-2026 a las 02:14 UTC y `cem-frontend` a las 02:16 UTC, después de validar staging. El frontend es dueño del dominio de cada entorno y el backend no tiene dominio propio: el frontend le llega por los alias de red `cem-backend-staging` y `cem-backend`. El detalle y las comprobaciones están en `doc/infra/REGISTRO_RECURSOS.md` del repositorio CEM.
- Página `/instalar` y enlace "Instalá la aplicación en tu teléfono" en el inicio (pedido del propietario del 14-10-2026): botón propio donde el navegador ofrece `beforeinstallprompt` (Chrome y Edge), pasos de Safari → Compartir → Agregar a inicio en iPhone y iPad, y aviso de "ya está instalada" en modo `standalone`. Es el enlace para compartir por WhatsApp o en un QR de cartelera. En producción desde el 14-10-2026 (`38aca25`): `https://cem.holancloud.com/instalar` responde, y el JavaScript servido contiene la ruta. Ojo: un 200 no alcanza como prueba, porque la aplicación devuelve `index.html` para cualquier ruta.
- Cola de trabajos, outbox y planificador (documento 13, §12; plan H1, semana 2; PR #8): migración `0002` con `plataforma.jobs`, `outbox` y `ejecuciones_programadas` con RLS forzada; funciones `security definer` para tomar, terminar y fallar trabajos; procesos `cem-trabajos` y `cem-planificador` en la misma imagen, elegidos con `CEM_PROCESO`; `GET /api/v1/health/cola` para Kuma. El error de un trabajo se guarda redactado; un resultado que no es JSON (o con NaN) deja el trabajo `muerto` en el primer intento en vez de repetir el efecto; el lote de `cem-trabajos` es 1 por defecto (`CEM_TRABAJOS_LOTE`; un valor inválido impide el arranque) (PR #12).
- **Registros JSON (documento 13, §16.1; PR #12):** los tres procesos escriben una línea JSON por registro a stdout (`app/registros.py`, `app/registros.json`) con `momento`, `nivel`, `proceso`, `modulo`, `mensaje` y, cuando existen, `id_correlacion`, `consorcio_id`, `persona_id` y `duracion_ms`. Correos, tokens, claves (también dentro de una URL), teléfonos e importes salen redactados. uvicorn corre con `--log-config app/registros.json --no-access-log` y una línea por petición reemplaza su registro de acceso. El `id_correlacion` del cuerpo de error es el de esa petición; en un trabajo, es su id.
- **Copia diaria verificada (H0b; documento 13, §17; PR #14, #15 y #16):** `plataforma.copia_diaria` a las 03:30 UTC (la encola `cem-planificador`, la corre `cem-trabajos`). `pg_dump -Fc` con `cem_backup` sobre una instantánea exportada; restauración en un PostgreSQL efímero dentro del contenedor y comparación de filas y huella por tabla calculadas en esa misma instantánea; si no coinciden, no se sube. Cifra con `age` a `CEM_COPIAS_CLAVE_PUBLICA`, sube a `diarias/AAAA-MM-DD/` (el día 1 también a `mensuales/`) y relee el objeto comparando el SHA-256. `cem_backup` en `crear_roles.sql`: `pg_read_all_data` + BYPASSRLS, solo lectura, sin REPLICATION hasta el WAL. La imagen trae PostgreSQL 18 (PGDG) y `age` 1.2.1 (584 MB). Las pruebas usan un S3 compatible (Versity) y un par `age` de prueba.
- **Integración y despliegue (`despliegue/`, PR #11):** `ci.ps1` corre la suite oficial completa sobre una copia LF del commit y deja una constancia en `%LOCALAPPDATA%\cem-despliegue`; `cd.ps1` despliega por la API de Coolify solo la punta de `main` con `ci.ps1` en verde, fuera de 02:45-03:10 UTC, producción solo con `-Confirmar` y la SHA ya verificada en staging, y verifica por `ssh oci` y Traefik; `-SoloVerificar` solo lee. `deploy-map.ps1` (UUID de Coolify por entorno, comprobado con `test-deploy-map.ps1`), `test-compuertas.ps1` y la plantilla de registro (§18.6). Cubre lo que el plan llama `cem-despliegue`.
- **Pruebas de extremo a extremo (PR #13):** `pnpm e2e` construye las imágenes reales de `cem-frontend` y `cem-backend`, las levanta con Redis en una red propia y corre Playwright 1.57.0 + `@axe-core/playwright` (escritorio y Pixel 7): inicio con el `/health` del backend real, `/api/` hasta el backend, `/instalar` (`beforeinstallprompt` real, iPhone, standalone simulado porque Chromium no emula `display-mode`), manifiesto, service worker, sin conexión, cabeceras y CSP, y `axe` WCAG 2.2 AA en cada página.
- Migración `0001`: esquemas, consorcio, persona, unidad, vínculo, auditoría encadenada, RLS forzada y privilegios de `cem_app`.
- 160 pruebas (146 del backend, 3 de contratos, 11 del frontend con `axe`) más 27 de extremo a extremo (1 omitida: iPhone solo en móvil), incluidas las de RLS contra PostgreSQL real, las de la cola con procesos reales, las de Access con un emisor de claves HTTP real y las de la copia con un S3 compatible, y `frontend/scripts/probar-imagen.sh` para la imagen.

## Cómo probar

**Corrida oficial** (Linux, PostgreSQL 18 y Redis 7 reales, las mismas imágenes que producción), desde la raíz:

```bash
docker compose -f backend/pruebas/compose.yml run --rm --build cem-backend-pruebas
docker compose -f backend/pruebas/compose.yml run --rm cem-backend-pruebas sh -c "ruff check .. && ruff format --check .."
docker compose -f backend/pruebas/compose.yml down
```

`test_la_suite_oficial_corre_en_postgresql_18` falla en otra versión. Con un PostgreSQL y un Redis propios:

```bash
cd backend
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
export CEM_TEST_ADMIN_URL=postgresql+psycopg://postgres:postgres@localhost:5432/postgres
export CEM_TEST_REDIS_URL=redis://localhost:6379/0
.venv/bin/pytest && .venv/bin/ruff check .. && .venv/bin/ruff format --check ..
cd ../contratos && ../backend/.venv/bin/pytest
cd ../frontend && pnpm install && pnpm lint && pnpm test && pnpm build
```

Todo junto, como antes de desplegar (Windows, PowerShell 7): `pwsh despliegue/ci.ps1 -Sha <sha>`. Extremo a extremo: `cd frontend && pnpm e2e`.

**Frontend comprobado el 13-10-2026** (imágenes de ambos construidas en local, red Docker con el backend bajo el alias `cem-backend`): `/` y rutas de la SPA → `200` con `Cache-Control: no-cache` y CSP; activos → caché de un año; `/health` → JSON del backend; `/api/v1/me/consorcios` → `401 no_autenticado`; sin `CEM_BACKEND_DESTINO` el contenedor sale con código 1. **AD-05:** se levantó un backend nuevo con otra IP (`172.18.0.2` → `.5`) y se borró el anterior sin reiniciar nginx; `/health` siguió en `200` con la SHA nueva. En Chromium: página sin errores de consola (la CSP bloquea scripts en línea, comprobado), service worker activo, `axe` sin faltas (contraste incluido) y, sin conexión, el shell carga desde caché y avisa que no hay servicio.

## Decisiones vigentes

- Monorrepositorio (AD-23); contratos como fuente única y tipos TypeScript generados desde `contratos/openapi.json` (AD-06).
- Migraciones en SQL explícito con Alembic, aplicadas con `cem_migrate` (AD-09). Los roles los crea una vez `backend/scripts/crear_roles.sql` con un superusuario, porque `cem_migrate` no tiene `CREATEROLE`.
- El ámbito se fija con `set_config(..., true)` (equivale a `SET LOCAL`); si falta, las políticas no devuelven filas.
- No hay atajo de desarrollo para la autenticación: hasta que exista `identidad`, toda ruta protegida responde 401.
- **slowapi descartado (14-10-2026).** slowapi 0.1.10 busca el manejador solo entre las rutas de primer nivel; FastAPI 0.141 deja las de `include_router` anidadas (`_IncludedRouter`), así que eximía toda la API sin avisar: las 301 peticiones daban 401 y ninguna 429. El middleware propio usa el mismo motor (`limits`) y lo prueban `test_limite_de_tasa.py` (con el middleware quitado fallan 5 de 7).
- **NO VERIFICADO: que Traefik solo sea alcanzable por el túnel.** Si el puerto 443 de la VM respondiera desde Internet sin pasar por Cloudflare, `CF-Connecting-IP` se podría inventar y rotar cupos del límite de tasa. Comprobarlo (lista de seguridad de OCI o `curl` a la IP pública) antes de dar TB-8 por cerrado del todo.
- Sin Redis no se atiende: el límite de tasa es un control de seguridad, no una caché. Si el propietario prefiere que la API siga sin límite cuando Redis cae, es una decisión suya y hay que registrarla.

## Desplegado

- **Staging en `357775c` (14-10-2026, 14:03-14:08 UTC):** `cem-backend-staging`, `cem-trabajos-staging`, `cem-planificador-staging` y `cem-frontend-staging`, los cuatro `healthy`. `/health` con `version_contrato` 0.4.0, `/api/v1/health/deps` y `/api/v1/health/cola` en `ok`, registros JSON en los tres procesos. Variables (en Coolify, no aquí): en el backend `CEM_REDIS_URL`, `FORWARDED_ALLOW_IPS`, `CEM_ACCESS_EQUIPO` y `CEM_ACCESS_AUD`; en `cem-trabajos-staging`, `CEM_DATABASE_URL_BACKUP`, `CEM_COPIAS_CLAVE_PUBLICA`, `CEM_COPIAS_BUCKET=cem-copias-seguridad-staging` y las cuatro `CEM_OCI_S3_*` como referencia a las compartidas del entorno. Rol `cem_backup` creado en `cem-postgres-staging`.
- **Primera copia real (staging):** al arrancar, el planificador encoló la ventana de las 03:30 del 14-10 y `cem-trabajos-staging` la dejó `hecho` al primer intento: `diarias/2026-10-14/cem-20261014T033000Z.dump.age`, 48.622 bytes, 10 tablas y 7 filas verificadas. Leído aparte con la clave S3 de staging: mismo tamaño y SHA-256 que informó el trabajo, cabecera `age-encryption.org/v1` y ningún texto en claro. **NO VERIFICADO:** el descifrado con la identidad real `cem-clave-copias-age` (ensayo trimestral, §17.4).
- **Producción en `357775c` (14-10-2026, 14:22-14:25 UTC), solo `cem-backend` y `cem-frontend`:** migración `0002` aplicada; `/health` con `version_contrato` 0.4.0, `/deps` y `/cola` en `ok`, registros JSON. Variables nuevas en `cem-backend`: `CEM_REDIS_URL` (referencia a `CEM_REDIS_CLAVE`, compartida) y `FORWARDED_ALLOW_IPS`. Rol `cem_backup` creado en `cem-postgres`. Límite de tasa con la IP real: dos pedidos desde la PC del propietario contaron bajo su IP pública; 301 pedidos desde una IP de prueba dieron 300 × 401 y un 429; otra IP sigue en 401; `/health` exenta. Soporte sin Access configurado: todo 401 (también un JWT inventado). Desde las 17:17 UTC también `cem-trabajos` y `cem-planificador` (creados por el propietario con `prod_5_procesos.ps1`): el planificador es líder y la primera copia de producción quedó en `cem-copias-seguridad` (`diarias/2026-10-14/…dump.age`, 48.511 bytes, 10 tablas y 5 filas verificadas; leída aparte con el SDK de OCI: mismo tamaño y SHA-256, cabecera `age`). **Falta:** la aplicación de Access `cem-soporte` y los monitores de Kuma de producción.

## Para terminar producción

- **Soporte:** crear la aplicación de Access `cem-soporte` (`cem.holancloud.com/api/v1/soporte*` y `/soporte*`, correos de operadores) y cargar `CEM_ACCESS_EQUIPO` y su `CEM_ACCESS_AUD` en `cem-backend`.
- **Kuma:** monitores `cem-dependencias` (`/api/v1/health/deps`) y `cem-trabajos` (`/api/v1/health/cola`) de producción, iguales a los de staging con `Host: cem.holancloud.com`.

## Próximo paso

1. Terminar producción (ver "Para terminar producción").
2. Pendientes de las copias: push a Kuma (una copia que nunca corrió hoy no alerta), copia antes de cada migración en `cd.ps1`, WAL continuo (H2c), bloqueo de objeto del bucket y el primer descifrado con la identidad real.
3. `pnpm e2e` dentro de `ci.ps1`; probar `/instalar` en un iPhone y un Android reales.
4. Hook `SessionStart` que instale PostgreSQL 18 y Redis 7 en las sesiones de Claude.
5. Tabla `plataforma.idempotencia` (encabezado `Idempotency-Key`, §7.4).
6. Módulo `identidad`: personas, sesiones, passkeys.
