# Estado de cem-aplicacion

Se lee al empezar cada sesión y se actualiza antes de terminarla (documento 14, regla 5).

**Actualizado:** 15-10-2026.

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
- **Idempotencia (documento 13, §7.4; PR #19 y #20):** migración `0003` con `plataforma.idempotencia` (clave por consorcio, persona, método, ruta y clave; huella SHA-256 del pedido; respuesta guardada 24 h; RLS forzada; `cem_app` sin `UPDATE`) y `purgar_idempotencia()` dentro de la purga diaria. `app/plataforma/idempotencia.py`: las rutas declaran `IdempotenciaDep` (encabezado `Idempotency-Key` obligatorio) y hacen su efecto en `responder_una_vez()` con un bloqueo consultivo de la clave. Mismo pedido → misma respuesta con `Idempotent-Replayed: true`; otro pedido con la misma clave → 422 `idempotencia_reutilizada`; concurrente → espera, o 409 `idempotencia_en_curso` pasados 2 s. Contrato 0.5.0. Ninguna ruta de negocio lo usa todavía: el primer `POST` de dinero, cargos o expedientes lo adopta.
- **Correo transaccional (documento 13, §12.4; PR #22):** el tipo de trabajo `notificar.correo` (`app/plataforma/correo.py`) envía desde `cem-trabajos` por SMTP con STARTTLS obligatorio y certificado verificado (OCI Email Delivery, puerto 587). Los módulos publican `correo.solicitado` con `correo.solicitar(conn, para=, asunto=, texto=, html=None, consorcio_id=None)`. 5 intentos con retroceso de 7,5 a 60 min; un 5xx o una carga inválida dejan el trabajo `muerto`. Al menos una vez: `Message-ID` fijo y encabezados `X-CEM-Clave-Envio`/`X-CEM-Intento`. Solo `cem-trabajos` tiene `CEM_SMTP_*` y `CEM_CORREO_REMITENTE` (TB-CEM-6; comprobado en Coolify y dentro de los contenedores). Pruebas con Mailpit.
- **Identidad, primer corte (H2 2a; PR #23, #24 y #25):** migración `0004` con `nucleo.sesion` y `nucleo.enlace_acceso` (solo hashes, topes de 15 minutos y 30 días en la base, RLS forzada, funciones `security definer`).
  - **Rutas:** `POST /api/v1/auth/enlace` (202 igual con cuenta o sin ella, 400 ms mínimos, correo por `correo.solicitar`), `POST /api/v1/auth/canje` (token en el cuerpo; el correo apunta a `/entrar#token=…`, página del frontend que lo lee y lo borra de la URL), `GET /api/v1/auth/sesion` y `POST /api/v1/auth/salir`.
  - **Cookies y CSRF:** cookies `__Host-` (sesión opaca `HttpOnly`, CSRF legible, nonce y dispositivo). `obtener_identidad` valida la sesión en la base (30 días deslizantes) y exige `X-CSRF-Token` en todo lo que modifica estado.
  - **Límites:** 20 por minuto por IP en `/api/v1/auth/*` y 5 por minuto por correo.
  - **Configuración:** variable `CEM_ORIGEN_PUBLICO`; contrato 0.6.0. El nginx del frontend registra el acceso sin la consulta.
- **Identidad, segundo corte (PR #27, #28 y #29):**
  - **Migración `0005`:** `nucleo.revocar_sesiones_de(persona)`, que cierra al instante todas las sesiones de una persona (RN22; la usará el módulo de mandatos), y `nucleo.purgar_identidad(retencion)`. La purga diaria borra sesiones y enlaces que dejaron de servir hace más de 90 días.
  - **Sesiones propias (RF04):** `GET /api/v1/auth/sesiones` (vigentes, con la actual marcada y el dispositivo legible, sin IP ni agente crudo), `POST /api/v1/auth/sesiones/{id}/revocar` y `POST /api/v1/auth/sesiones/revocar-otras`, con CSRF. Contrato 0.7.0.
  - **Migración `0006`, código de seis dígitos:** el mismo correo del enlace trae un código para entrar desde otro dispositivo (`POST /api/v1/auth/codigo`). Dura 10 minutos y admite cinco intentos fallidos; usar el código gasta el enlace y al revés. En la base queda `HMAC-SHA256(CEM_CODIGO_CLAVE, …)`, nunca el código. Límites: 10 por hora y 20 por día por correo. Contrato 0.8.0. En `/entrar`, sección "Entrar con un código".
- **Invitaciones y alta de personas (RF03; PR #35, fusionado el 15-10-2026):**
  - **Migración `0007`:** `nucleo.invitacion` (consorcio, unidad, vínculo, correo, nombre, hash del token, quién invitó, 7 días como máximo, aceptada o retirada) con RLS forzada por consorcio; la política exige que `invitada_por` sea la persona del ámbito. `cem_app` solo cambia `revocada_en`. Funciones `security definer` `consultar_invitacion` y `aceptar_invitacion`.
  - **Quién invita (provisorio hasta los mandatos de H2 2b):** el propietario vigente de esa unidad, a su unidad (inquilino, ocupante u otro titular). Las primeras personas de un consorcio las da de alta el equipo con `python -m scripts.invitar` dentro de `cem-backend` (sin `invitada_por`, auditoría con origen `soporte`).
  - **Rutas:** `POST /api/v1/consorcios/{id}/invitaciones` (correo con `/invitacion#token=…`; invitar de nuevo al mismo correo, unidad y vínculo retira la anterior; 20 por hora y 50 por día por persona), `GET …/invitaciones`, `POST …/invitaciones/{id}/revocar`, `POST /api/v1/auth/invitacion/consultar` y `POST /api/v1/auth/invitacion/aceptar`.
  - **Aceptar:** correo sin cuenta → persona, vínculo desde hoy (zona horaria del consorcio) y sesión, en una transacción; correo con cuenta → solo con la sesión de esa persona (409 `invitacion_requiere_sesion` u `invitacion_de_otra_persona`). Sin sesión, la defensa contra otro sitio es `Sec-Fetch-Site` y el cuerpo JSON, como en `/auth/codigo`. Toda invitación que no sirve responde igual (404 `invitacion_invalida`).
  - **Auditoría encadenada:** crear, reemplazar, retirar y aceptar escriben en `auditoria.eventos` (primer uso), con identificadores y sin el correo. Las invitaciones no se purgan: son la constancia de quién dio acceso a quién.
  - **Frontend:** página `/invitacion` (muestra edificio, unidad y vínculo; nombre editable; acepta con CSRF si hay sesión) y la navegación a `/invitacion` nunca sale de la caché del service worker. Contrato 0.9.0.
  - **Pruebas:** 27 del backend contra PostgreSQL 18 y Redis reales (RLS de otro consorcio, política de `invitada_por`, privilegios, tope de 7 días, aceptación simultánea al mismo correo nuevo, script) y 9 de Vitest con `axe`. Sin e2e todavía.
- **Segundo factor TOTP y verificación reciente (TB-CEM-3; rama `claude/identidad-invitaciones-46xhhl`, sin fusionar):**
  - **Migración `0008`:** `nucleo.factor_totp` (una fila por persona: secreto AES-256-GCM, pendiente hasta confirmar, último paso aceptado contra la repetición), `nucleo.codigo_recuperacion` (diez, HMAC, un solo uso) con RLS forzada por persona, y `nucleo.sesion.segundo_factor_en`.
  - **Rutas `/api/v1/auth/totp`:** estado, `alta` (secreto en base32 y URI `otpauth://`), `confirmar` (primer código; devuelve los diez códigos de recuperación, una sola vez), `verificar` (código de la aplicación o de recuperación: la sesión queda verificada 10 minutos) y `baja` (pide verificación reciente). Alta y baja avisan por correo.
  - **Claves:** derivadas con HKDF de `CEM_CODIGO_CLAVE`; no hay secreto nuevo que cargar en Coolify. Cambiar esa clave obliga a volver a dar de alta los segundos factores.
  - **Falta:** que el segundo factor sea obligatorio para los roles de dinero y la sesión de 12 h llegan con los mandatos (H2 2b); la página del frontend (alta con QR y verificación); contraseña Argon2id.
- **`/entrar` con una versión vieja en caché (PR #30):** `/entrar` nunca sale de la caché del service worker; el token se guarda en `sessionStorage` 15 minutos para sobrevivir a una recarga; una versión nueva esperando se aplica sola; `sw-entrar.js` rescata a los clientes que ya tienen la versión vieja.
- **Imágenes en ghcr.io (PR #31 y #32):** `ci.ps1` construye `cem-backend` y `cem-frontend` para `linux/arm64` y las sube a `ghcr.io/githolan/<imagen>:<sha>` con su digest en la constancia; `cd.ps1` solo cambia la etiqueta de las apps `dockerimage` de Coolify y verifica el digest en la VM. `deploy-map.ps1` tiene los UUID nuevos de los dos entornos. La VM ya no construye nada.
- Migración `0001`: esquemas, consorcio, persona, unidad, vínculo, auditoría encadenada, RLS forzada y privilegios de `cem_app`.
- Según `ci.ps1` sobre `951114b` (15-10-2026), hay 332 pruebas y 43 de extremo a extremo. Las 332 son 291 del backend, 3 de contratos y 38 del frontend con `axe`. De las de extremo a extremo se omite 1: la de iPhone, que solo corre en móvil. Están incluidas las de RLS contra PostgreSQL real, las de la cola con procesos reales, las de Access con un emisor de claves HTTP real y las de la copia con un S3 compatible, y `frontend/scripts/probar-imagen.sh` para la imagen.

## Cómo probar

**En una sesión de Claude Code en la nube:** el hook `.claude/hooks/session-start.sh` instala y levanta lo mismo que `backend/pruebas/compose.yml`, sin Docker:
- PostgreSQL 18 de PGDG en el 5432, en UTF8;
- Redis 7;
- Mailpit v1.31.4 con STARTTLS;
- Versity S3 Gateway v1.0.18;
- `age`, el `.venv` del backend y las dependencias del frontend.

Deja las variables `CEM_TEST_*` en el entorno. Corre todo salvo 6 pruebas de `test_copias.py`, porque `initdb` no arranca como root y la sesión corre como root. Esas 6 se corren en la corrida oficial: `pytest --deselect tests/test_copias.py`.

**Cobertura de PA:** `python -m scripts.cobertura_pa` desde `backend/` lista las PA del conjunto P0-piloto que ya tienen una prueba `test_paNN_…` y las que faltan. Con `--estricto` sale con 1 si falta alguna (puerta G5). Hoy hay 4 de 25: PA02 y PA03 en el motor de reparto, PA05 y PA15.

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
- No hay atajo de desarrollo para la autenticación: toda ruta protegida exige una sesión de `identidad` (enlace o código por correo) y responde 401 sin ella.
- **slowapi descartado (14-10-2026).** slowapi 0.1.10 busca el manejador solo entre las rutas de primer nivel; FastAPI 0.141 deja las de `include_router` anidadas (`_IncludedRouter`), así que eximía toda la API sin avisar: las 301 peticiones daban 401 y ninguna 429. El middleware propio usa el mismo motor (`limits`) y lo prueban `test_limite_de_tasa.py` (con el middleware quitado fallan 5 de 7).
- **Traefik solo es alcanzable por el túnel: comprobado el 15-10-2026, de 19:47 a 19:51 UTC, desde la PC.** Si el 443 de la VM respondiera desde Internet sin pasar por Cloudflare, `CF-Connecting-IP` se podría inventar y rotar los cupos del límite de tasa.
  - **Lo medido:** el 443 y el 80 de la IP pública no responden (`curl` da timeout con los dos Host, `nmap` los marca `filtered`), y tampoco el 22 ni los puertos que publica Docker.
  - **Controles:** la misma sonda contra 1.1.1.1 da 200 y los puertos `open`; desde la propia VM, Traefik responde 200.
  - **NO VERIFICADO:** las reglas de OCI (lista de seguridad y NSG), porque el MCP de OCI no tiene perfil. TB-8 queda cerrado en la práctica.
- Sin Redis no se atiende: el límite de tasa es un control de seguridad, no una caché. Si el propietario prefiere que la API siga sin límite cuando Redis cae, es una decisión suya y hay que registrarla.

## Desplegado

Medido el 15-10-2026 por la API de Coolify, `docker ps` en la VM y `plataforma.alembic_version`. Las cuatro apps de cada entorno son apps `dockerimage` que bajan de ghcr.io. Detalle y UUID en `doc/infra/REGISTRO_RECURSOS.md` del repositorio CEM; registro del despliegue en su `doc/deploys/2026-10-15-951114b.md`.

**Staging y producción en `951114b`** (= `main` al 15-10-2026), base `0006`, contrato 0.8.0. Identidad completa hasta el código de seis dígitos; los dos entornos tienen los mismos nombres de variables en sus 4 apps.

- **Staging, 15-10 de 20:09 a 20:11 UTC.**
  - Es el primer despliegue hecho con `cd.ps1` real, después de `ci.ps1` en verde.
  - No hubo copia previa: la base ya estaba en `0006`, porque `0bdb33d` se había desplegado a las 04:15 UTC sin registrarse.
  - La verificación sobre contenido salió en verde. Como control, antes de desplegar daba rojo.
  - Prueba de humo: `/auth/codigo` sin cuerpo → 422, sin enviar correo; `/auth/sesiones` → 401; una ruta inexistente → 404.
  - **El propietario probó desde el celular la entrada con código de seis dígitos y funcionó** (15-10, ~20:20 UTC).
- **Producción, 15-10 de 20:35 a 20:37 UTC.** `cd.ps1 -Confirmar` lo corrió el propietario en la PC, porque el clasificador no deja correrlo a Claude.
  - Copia previa verificada a la migración `0004` → `0006`: `diarias/2026-10-15/cem-20261015T203554Z.dump.age`, con 13 tablas y 9 filas.
  - `CEM_CODIGO_CLAVE`, nueva y distinta de la de staging, se cargó a las 20:22 UTC con la aprobación del propietario.
  - **Comprobado desde afuera (20:40–20:45 UTC):**
    - `/health` público con `951114b`;
    - base en `0006`;
    - un código inventado da 401 `codigo_invalido`, lo que prueba que la clave está cargada (sin ella sería 503);
    - 0 reinicios y 0 errores en los registros de las 4 apps.
  - Sigue funcionando lo que ya estaba: límite de tasa con la IP real, Access `cem-soporte`, copia diaria de las 03:30 UTC y Kuma.
- **Prueba de punta a punta en staging (propietario, 14-10 22:08 UTC):** pidió el enlace, el correo salió por OCI, `/entrar#token=…` mostró "Entraste." y `/api/v1/auth/sesion` devolvió su persona. La persona de prueba existe solo en staging.
- **Trampa de las constancias.** La app de Claude en Windows es un paquete MSIX: lo que sus procesos crean en `%LOCALAPPDATA%\cem-despliegue` queda en `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local\cem-despliegue`.
  - Para correr `cd.ps1` en una terminal propia sobre constancias que dejó Claude: `$env:CEM_DESPLIEGUE_ESTADO = '<esa carpeta>'`.
  - Si no, se niega: "no está registrada como desplegada y verificada en staging".

## Próximo paso

Orden pedido por el propietario el 15-10-2026: ordenar antes de sumar funciones.

1. **Hecho el 15-10:** `main` (`951114b`) en staging y producción.
   - Las constancias de `ci.ps1` y `cd.ps1` están en `C:\Users\holan\cem-despliegue`, fuera de `AppData`, con `CEM_DESPLIEGUE_ESTADO` como variable de usuario de Windows (decisión del propietario, 15-10). Los originales siguen en la carpeta privada de la app.
   - Falta confirmar que una terminal nueva del propietario ve la variable.
2. Carril C: los prototipos P0 y el guion de prueba ya están en el repositorio CEM, en `doc/descubrimiento/` (CEM #26 y #27, 15-10-2026). Lo que sigue es la prueba de uso para la puerta G3.
3. Identidad, lo que falta de 2a:
   - invitaciones (PR #35, fusionado el 15-10; migración `0007`): falta desplegarlas;
   - segundo factor TOTP y verificación reciente (migración `0008`, rama `claude/identidad-invitaciones-46xhhl`): falta revisarlo, fusionarlo y la página del frontend;
   - contraseña Argon2id, TOTP obligatorio para roles de dinero (con los mandatos, 2b), persona autorizada (RF53), cuenta de solo carga, borradores fuera de línea (PA16) y PA29. Passkeys pueden ir en H6.
4. Que `ci.ps1` llame a `python -m scripts.cobertura_pa` y guarde la salida en la constancia. Toda prueba nueva que cubra una PA lleva su prefijo `test_paNN_`.
5. Pendientes de las copias: push a Kuma, WAL continuo (H2c), bloqueo de objeto del bucket y el primer descifrado con la identidad real.
6. TB-8: falta leer las reglas de OCI para cerrarlo con prueba firme (ver "Decisiones vigentes"); para eso hay que configurar el perfil del MCP de OCI.
7. Probar `/instalar` en un iPhone y en un Android reales. El cartel con el QR está en el repositorio CEM (#28); falta comprobar que el QR lleva a `/instalar`.
