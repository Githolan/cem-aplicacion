# Estado de cem-aplicacion

Se lee al empezar cada sesión y se actualiza antes de terminarla (documento 14, regla 5).

**Actualizado:** 23-10-2026.

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
- **Invitaciones y alta de personas (RF03; PR #35, en `main` desde el 15-10-2026):**
  - **Migración `0007`:** `nucleo.invitacion` (consorcio, unidad, vínculo, correo, nombre, hash del token, quién invitó, 7 días como máximo, aceptada o retirada) con RLS forzada por consorcio.
    - Una política restrictiva **solo de `insert`** exige que `invitada_por` sea la persona del ámbito.
    - Retirar o reemplazar la pendiente de otra propietaria de la unidad, o la que cargó el equipo, lo decide `puede_invitar`. Con la política anterior, que alcanzaba también a los `update`, eso daba 500 (corregido en el #35).
    - `cem_app` solo cambia `revocada_en`. Funciones `security definer`: `consultar_invitacion` y `aceptar_invitacion`.
  - **Quién invita (provisorio hasta los mandatos de H2 2b):** el propietario vigente de esa unidad, a su unidad (inquilino, ocupante u otro titular). Las primeras personas de un consorcio las da de alta el equipo con `python -m scripts.invitar` dentro de `cem-backend` (sin `invitada_por`, auditoría con origen `soporte`).
  - **Rutas:** `POST /api/v1/consorcios/{id}/invitaciones` (correo con `/invitacion#token=…`; invitar de nuevo al mismo correo, unidad y vínculo retira la anterior; 20 por hora y 50 por día por persona), `GET …/invitaciones`, `POST …/invitaciones/{id}/revocar`, `POST /api/v1/auth/invitacion/consultar` y `POST /api/v1/auth/invitacion/aceptar`.
  - **Aceptar:** correo sin cuenta → persona, vínculo desde hoy (zona horaria del consorcio) y sesión, en una transacción; correo con cuenta → solo con la sesión de esa persona (409 `invitacion_requiere_sesion` u `invitacion_de_otra_persona`). Sin sesión, la defensa contra otro sitio es `Sec-Fetch-Site` y el cuerpo JSON, como en `/auth/codigo`. Toda invitación que no sirve responde igual (404 `invitacion_invalida`).
  - **Auditoría encadenada:** crear, reemplazar, retirar y aceptar escriben en `auditoria.eventos` (primer uso), con identificadores y sin el correo. Las invitaciones no se purgan: son la constancia de quién dio acceso a quién.
  - **Frontend:** página `/invitacion` (muestra edificio, unidad y vínculo; nombre editable; acepta con CSRF si hay sesión) y la navegación a `/invitacion` nunca sale de la caché del service worker. Contrato 0.9.0.
  - **Pruebas:** 29 del backend contra PostgreSQL 18 y Redis reales y 9 de Vitest con `axe`. Las del backend cubren: RLS de otro consorcio, política de `invitada_por`, otra propietaria y el equipo que retiran o reemplazan, privilegios, tope de 7 días, aceptación simultánea al mismo correo nuevo y el script. No hay e2e propias de invitaciones.
- **Segundo factor TOTP y verificación reciente (TB-CEM-3; PR #37 y #42, en staging y producción con `099ab21` desde el 16-10):**
  - **Migración `0008`:** `nucleo.factor_totp` (una fila por persona: secreto AES-256-GCM, pendiente hasta confirmar, último paso aceptado contra la repetición), `nucleo.codigo_recuperacion` (diez, HMAC, un solo uso) con RLS forzada por persona, y `nucleo.sesion.segundo_factor_en`.
  - **Rutas `/api/v1/auth/totp`:** estado, `alta` (secreto en base32 y URI `otpauth://`), `confirmar` (primer código; devuelve los diez códigos de recuperación, una sola vez), `verificar` (código de la aplicación o de recuperación: la sesión queda verificada 10 minutos) y `baja` (pide verificación reciente). Alta y baja avisan por correo.
  - **Claves:** derivadas con HKDF de `CEM_CODIGO_CLAVE`; no hay secreto nuevo que cargar en Coolify. Cambiar esa clave obliga a volver a dar de alta los segundos factores.
  - **Frontend, página `/cuenta/segundo-factor` (PR #42):**
    - **Alta:** QR dibujado en el navegador como SVG con `uqr` 0.1.3 (sin servicio externo; la CSP no cambia), enlace `otpauth://` y la clave en bloques de cuatro para escribirla a mano.
    - **Confirmación:** muestra los diez códigos de recuperación una sola vez.
    - **Verificación:** acepta el código de la aplicación o uno de recuperación, escrito como venga.
    - **Desactivar:** si falta la verificación reciente, la página lo explica.
    - Todo lo que cambia estado lleva CSRF. Se llega desde `/cuenta` (PR #46).
  - **Pruebas de la página:** 14 de Vitest con `axe` y 3 de extremo a extremo contra el nginx de la imagen, con la API simulada desde Playwright.
    - Las de extremo a extremo comprueban: QR y códigos enteros a 360 px, sin violar la CSP; `axe` con contraste; sin sesión, el backend real responde 401. El control comprueba que la vigilancia sí ve un estilo en línea.
    - El QR se decodificó con OpenCV y dio la URI exacta; como control, con la mitad borrada no da nada.
  - **Falta:** que el segundo factor sea obligatorio para los roles de dinero y la sesión de 12 h llegan con los mandatos (H2 2b); probarlo con una aplicación real en un teléfono; contraseña Argon2id.
- **`/entrar` con una versión vieja en caché (PR #30):** `/entrar` nunca sale de la caché del service worker; el token se guarda en `sessionStorage` 15 minutos para sobrevivir a una recarga; una versión nueva esperando se aplica sola; `sw-entrar.js` rescata a los clientes que ya tienen la versión vieja.
- **Imágenes en ghcr.io (PR #31 y #32):** `ci.ps1` construye `cem-backend` y `cem-frontend` para `linux/arm64` y las sube a `ghcr.io/githolan/<imagen>:<sha>` con su digest en la constancia; `cd.ps1` solo cambia la etiqueta de las apps `dockerimage` de Coolify y verifica el digest en la VM. `deploy-map.ps1` tiene los UUID nuevos de los dos entornos. La VM ya no construye nada.
- Migración `0001`: esquemas, consorcio, persona, unidad, vínculo, auditoría encadenada, RLS forzada y privilegios de `cem_app`.
- Según `ci.ps1` sobre `3251caf` (15-10-2026), hay 370 pruebas y 43 de extremo a extremo. Las 370 son 320 del backend, 3 de contratos y 47 del frontend con `axe`. De las de extremo a extremo se omite 1: la de iPhone, que solo corre en móvil. Están incluidas las de RLS contra PostgreSQL real, las de la cola con procesos reales, las de Access con un emisor de claves HTTP real y las de la copia con un S3 compatible, y `frontend/scripts/probar-imagen.sh` para la imagen.

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

Medido el 21-10-2026 (despliegue de `ec056c7`) por `cd.ps1` (estado, imagen y digest leídos de cada contenedor, `/health` con la SHA) y `plataforma.alembic_version` (lectura en transacción de solo lectura). Las cuatro apps de cada entorno son apps `dockerimage` que bajan de ghcr.io. Detalle y UUID en `doc/infra/REGISTRO_RECURSOS.md` del repositorio CEM; registros de despliegue en su `doc/deploys/` (el último, `2026-10-21-ec056c7.md`).

**Staging y producción en `ec056c7`** (21-10-2026), base `0022`, contrato 0.15.0.

- **Control diario de los índices GiST (E58; PR #99, migración `0022`).** El trabajo `plataforma.verificar_indices` corre a las 04:15 UTC en `cem-trabajos`.
  - Con `cem_backup`, en una sola foto `REPEATABLE READ`, compara el conteo secuencial contra el del índice, por cada GiST de `nucleo` y cada `consorcio_id`. El escaneo por índice se confirma con `EXPLAIN`.
  - Si difieren, hace `REINDEX` con `cem_app` (`MAINTAIN`) y vuelve a comparar. No es `CONCURRENTLY`: PostgreSQL no lo admite en índices de exclusión.
  - Avisa a Kuma por push (`CEM_INDICES_AVISO_URL`): `up` si estaba limpio, `down` si reparó o no pudo. Si no repara, el trabajo queda muerto.
  - Corrido a mano en los dos entornos: staging, 4 índices y 5 valores sin diferencias; producción, 4 índices y 0 valores. Faltan los monitores Push de Kuma y la variable: los monitores los crea el propietario.
  - Mutaciones: 5 de 5. CI de `70aafea` y de la punta `ec056c7` en verde (1348 de backend). Codex sin hallazgos.

Antes, **`a0b81da`** (21-10-2026), base `0021`:


- **2b-5, corte 2: coeficientes jurídicos y regla de reparto (D4, RF05, PA02; PR #95, migración `0021`).**
  - `nucleo.esquema_coeficientes`, `coeficiente_juridico` y `regla_reparto`, con RLS forzada; `cem_app` solo lee. Los porcentajes se guardan tal como dice la fuente (pueden sumar 99,96 %), y la regla `proporcional_a_coeficientes` reparte con los centavos por restos mayores.
  - `nucleo.registrar_esquema_coeficientes` (solo soporte) exige la decisión de respaldo y todas las unidades activas. `scripts/cargar_coeficientes.py` carga un CSV dentro del contenedor de `cem-backend`. `app/liquidacion/esquemas.repartir_por_esquema` bloquea el reparto si el esquema no cubre exactamente las unidades activas (PA02).
  - Proponer y aprobar por la API esperan las decisiones de 2b-6.
- **E60 (PR #97):** `alta_mandato` y `cargar_coeficientes` no estaban en la imagen (`No module named`, visto al validar `85a3377` en staging, que no pasó a producción). Ahora están, y `tests/test_scripts_en_la_imagen.py` vigila el `COPY` del Dockerfile.
- **E58 (PR #95):** el índice GiST de exclusión de `nucleo.vinculo` llegó a marcar como muerta la entrada de un vínculo vivo (PostgreSQL 18.4 y 18.6). La suite hace `REINDEX` antes de bajar y antes de subir. En producción, la misma versión: está en el tracker de CEM como riesgo abierto.
- CI de las cabezas de #95 (`91efab5`) y #97 (`77210de`) y de la punta `a0b81da` en verde (1339 de backend, 58 de contratos, 277 de Vitest y las de extremo a extremo). Codex sin hallazgos sobre `91efab5` y `77210de`.
- Verificación:
  - staging: smoke con salida 0 (388 eventos de auditoría, 0 rotos);
  - copia previa a la migración en los dos entornos;
  - base en `0021` con la función, las 3 tablas y RLS forzada;
  - el script corre dentro del contenedor, consulta la base y rechaza una unidad inexistente sin escribir nada;
  - 0 errores en los registros.
  - **SIN VERIFICAR:** cargar un esquema real. Exige registrar una decisión de respaldo, y eso no entra en la autorización.
- **2b-5, corte 1: fin de vínculo (PA17, PR #94, migración `0020`).**
  - `POST /api/v1/consorcios/{c}/vinculos/{v}/finalizar` con `padron.gestionar` sobre la unidad, idempotente y sin devolver el motivo.
  - `nucleo.finalizar_vinculo` cierra la vigencia hoy (día del consorcio), conserva la fila y la confirmación, y revoca las sesiones solo si a la persona no le queda otro vínculo ni mandato vigente en el consorcio. Dos fines de la misma persona se serializan con un bloqueo por persona.
  - Un fin registrado no se mueve (trigger `vinculo_fin_inalterable`): corregirlo es anularlo entero. La vigencia vacía exige un fin registrado. Un vínculo futuro no se finaliza (`vinculo_no_iniciado`).
  - CI de `f2fcc37` y de la punta `1e2b465` en verde (1284 de backend, 58 de contratos, 277 de Vitest y las de extremo a extremo). Codex sin hallazgos sobre `f2fcc37`, después de corregir un P1 y un P2.
  - Verificación: copia previa a la migración en los dos entornos, smoke de staging con salida 0, la ruta responde 401 sin sesión (control: 404) y 0 errores en los registros. Producción en `0020`, con 0 mandatos, 0 decisiones y 0 vínculos finalizados.
- **Abierto:** E27, E29, E33, E40 (cuando haya escala), E55, E56, E58 y A13.

Antes, **`96245d4`** (20-10-2026), base `0019`, contrato 0.15.0. Lo abierto de ese bloque lo reemplaza el de `1e2b465`.

- **E52 a E54 (PR #92):**
  - `/invitacion` e `/invitacion-mandato` ya no explican un 403, un 429 o un 5xx como un problema de conexión; piden volver a abrir el enlace o hablan del servicio.
  - `/cuenta/segundo-factor` ofrece entrar de nuevo ante un 401 en cualquier acción, y un 5xx habla del servicio, también en la consulta inicial.
  - `/invitaciones` deja de decir «Consultando…» cuando la lista no se pudo consultar.
  - Pruebas: 277 de Vitest y 161 de extremo a extremo. Mutación: sin la corrección fallan 17 pruebas. Dos revisiones independientes con subagente (Codex sin cuota).
  - Misma SHA en staging y producción, sin migración. Smoke de staging con salida 0; los textos nuevos aparecen en el bundle servido.
- **Prueba en un teléfono real de la UI nueva: SIN VERIFICAR.** Staging está detrás de Access y entrar le corresponde al propietario. La lista de 11 pasos está en `doc/diseno/G3_Reevaluacion_2026-10-20.md` §4 del repositorio CEM. Esa lista cubre E46 y no los avisos de E52 a E54.
- **G3: no pasa todavía.**
  - PT02 se hizo como evaluación simulada, por decisión del propietario del 20-10 (CEM, `G3_Decisiones_y_Medicion_2026-10-20.md` §10).
  - Faltan la segunda medición de la velocidad, antes del 23-11, y el consorcio de Autogestión.
- **Abierto:** E27, E29, E33, E40 (cuando haya escala), E55, E56 y A13.

Antes, **`acf6705`** (20-10-2026), base `0019`: E46, el foco al retirar una invitación (PR #89). Lo abierto de ese bloque lo reemplaza el de `96245d4`.

Antes, **`6486c05`** (20-10-2026), base `0019`, contrato 0.15.0. Lo abierto de ese bloque lo reemplaza el de `acf6705`.

- **Punto 4 de §7 de 2b-4b: una decisión formal no tiene fecha futura (PR #85, migración `0019`). En staging y producción desde `e6576d5`.** Decisión del propietario del 19-10-2026 (CEM #55).
  - Con el día local del consorcio (`nucleo.hoy_del_consorcio`, no la fecha UTC), hasta hoy se admite y desde mañana se rechaza con `fecha_futura` (422). Vale para la API y para soporte, y no hay decisiones "programadas".
  - La ruta lo niega con `ctx.hoy` antes de tocar la base. `registrar_decision` lo repite como segunda barrera.
  - El upgrade se niega con `decisiones_futuras_previas` si ya hay una decisión futura; el downgrade vuelve a la función de `0016`.
  - E45: cinco pruebas de `0018` y `0014` fallaban según la hora. Usaban `date.today()` (UTC) contra el día de Buenos Aires, y el padrón se comparaba por igualdad. Están corregidas en el mismo PR.
  - Mutaciones 13/13. Validación de `0019` en la base con `ROLLBACK`: 11/11 en los dos entornos; antes del despliegue, en staging daba 6 MAL.
- **UI para el celular (PR #86).** Decisión de UX en `doc/diseno/UX_Invitaciones_y_Formularios_Movil.md` del repositorio CEM (#56).
  - `/invitaciones` muestra solo las pendientes; aceptadas, retiradas y vencidas van en un historial plegado, con tarjetas compactas. Nada se borra en la base.
  - `/mandatos`: `textarea` para "Qué se decidió" y "Dónde consta", SHA-256 en una línea, botones a todo el ancho hasta 480 px y 44 px como mínimo.
- **Deuda (PR #87):**
  - E34: `fallas.ts` distingue servicio no disponible (503 o 5xx con código), límite de pedidos (429), recargar (otro 4xx con código) y conexión (sin respuesta), también en la primera carga;
  - E35: la lista se recarga al retirar, y una recarga fallida no pisa el aviso del retiro;
  - E39: el smoke de staging corre por la entrada estándar y no deja archivo.
- **CI con imágenes** en verde sobre `e6576d5` y `6486c05`. En `6486c05`: 1239 del backend, 58 de contratos, 232 del frontend y 131 de extremo a extremo.
- **Revisiones:** independientes en #85 y #87. Codex revisó #87: un P2, corregido, y la última cabeza sin hallazgos.
- **Despliegues:** misma SHA en staging y producción, con copia previa verificada para `0019`. Smoke de staging con salida 0 y `/tmp` vacío en los dos. Registros en `doc/deploys/2026-10-20-e6576d5.md` y `2026-10-20-6486c05.md` del repositorio CEM.
- **Producción:** base `0019`, 0 decisiones y 0 mandatos.
- **Abierto:** E46 (el foco cae en `body` al retirar una invitación), E27, E29, E33, E40 (cuando haya escala) y A13. El diseño definitivo de las pantallas espera a G3.

Antes, **`4aa71e7`** (19-10-2026, H2b 2b-4c completo), base `0018`, contrato 0.14.0. Lo abierto de ese bloque lo reemplaza el de `6486c05`.

- **H2b 2b-4c: lectura de mandatos y decisiones, destinatario del mandato y pantalla `/mandatos` (PR #82 y #83), 19-10. En staging y producción.** Decisiones del propietario sobre §7 de 2b-4b (puntos 1, 2, 3, 5 y 6; el 4 sigue abierto). Diseño: `doc/diseno/H2b_4c_Diseno.md` del repositorio CEM (#53).
  - **PR #82, migración `0018`:**
    - `mandatos.ver` y `decisiones.ver` en los paquetes de `miembro_consejo` y `administrador` (el externo, sin lectura general; los mandatos que ya existían no se tocan);
    - cinco lecturas `security definer` que solo ejecuta `cem_app`, con segunda barrera en la base (mandato vigente hoy con la facultad, sin recurso): `mandatos_del_consorcio`, `mandato_detalle`, `decisiones_del_consorcio`, `decision_detalle` y `candidatos_a_mandato`. Listas mínimas: CUIT solo del externo, nombre de la cuenta para MC y AD, referencias y hashes solo en el detalle, nunca correo ni teléfono;
    - destinatario por la API: MC y AD necesitan un vínculo confirmado, vigente o futuro, en el consorcio (`persona_sin_vinculo`, el mismo motivo si la cuenta no existe o es de otro consorcio); el externo entra sin cuenta (`externo_por_invitacion`) y se asocia por A7; soporte conserva la vía de `0016`; el origen se valida antes que la persona;
    - E15: una vigencia vacía (`hasta <= desde`) no revoca sesiones al vencer;
    - rutas `GET /api/v1/consorcios/{c}/mandatos`, `…/mandatos/candidatos`, `…/mandatos/{m}`, `…/decisiones` y `…/decisiones/{d}`, con 403 auditado sin oráculo; el smoke de staging suma "lectura de mandatos y decisiones" (11 controles).
  - **PR #83, pantalla `/mandatos`** (enlazada desde el inicio): listas, detalle, registrar decisión, alta y baja con confirmación, CSRF e `Idempotency-Key`, motivo por código, aviso A15 que no concede nada, foco al resultado, descarte de respuestas viejas, 360 px.
  - CI con imágenes en verde sobre `4aa71e7`: 1232 del backend, 58 de contratos, 209 del frontend y 131 de extremo a extremo. Mutaciones: 26/26 (`0018` y rutas) y 25/25 (pantalla). Codex revisó #82 y #83 (ocho P2 en total, corregidos; la última ronda sin hallazgos), además de revisiones adversariales independientes documentadas en los PR.
  - Despliegue (Claude, mandato del propietario del 19-10) con la misma SHA en staging y producción y copia previa verificada. Validación de `0018` en la base con `ROLLBACK`: 16/16 en los dos entornos. Registro en `doc/deploys/2026-10-19-4aa71e7.md` del repositorio CEM.
  - **Producción:** 0 mandatos, 0 decisiones y 0 facultades de lectura otorgadas: todavía puede bajar. **Staging ya no baja de `0018`.**
  - **Abierto:** el punto 4 de §7 de 2b-4b (fecha futura de las decisiones), E34 (también en `Mandatos.tsx`), E27, E29, E33, E35, E39, E40 y A13.

Antes, **`81d3d55`** (19-10-2026, H2b 2b-4b completo), base `0017`, contrato 0.13.0. Lo que sigue describe ese momento: el downgrade, lo abierto y la pantalla los reemplaza el bloque de `4aa71e7`.

- **H2b 2b-4b: mandatos y decisiones por la aplicación, y E15 (PR #78, #79 y #80), 19-10.** Diseño: `doc/diseno/H2b_4b_Diseno.md` del repositorio CEM (#51).
  - **PR #78, migración `0016`:**
    - A15 (P2): quien registró la decisión no da el alta ni remueve con ella;
    - E26: los errores de alta, baja y decisión salen como motivos, no como `check_violation`;
    - R10/P1-d en los dos sentidos: Consejo y administración externa de la misma persona son incompatibles, con bloqueo por correo. El upgrade se niega si ya hay conflictos (`mandatos_incompatibles_previos`).
  - **PR #79, rutas:**
    - `POST /api/v1/consorcios/{c}/decisiones` (`decisiones.registrar`), `POST …/mandatos` (`mandatos.alta`) y `POST …/mandatos/{m}/baja` (`mandatos.baja`), con `Idempotency-Key` y CSRF;
    - A15 en el evaluador (`no_registro_la_decision`, con las marcas `decision_del_equipo` y `sin_decision`);
    - un mandato inexistente se evalúa con `SIN_OBJETO`: 403 auditado sin oráculo para quien no puede;
    - 11 códigos de error nuevos y seis modelos en `contratos/`.
    - El smoke de staging suma el paso "decisión, alta y remoción por HTTP, con A15" (10 controles).
  - **PR #80, migración `0017` (E15):** `nucleo.revocar_sesiones_por_vencimiento(tope)` y el trabajo horario `consorcio.revocar_por_vencimiento`, que lo llama con tope de 200.
    - Revoca las sesiones de quien tenía un mandato vencido por fecha, una sola vez, con la marca `vencimiento_procesado_en`.
    - Un mandato cargado ya vencido no revoca.
    - El downgrade se niega si hay marcas.
  - CI con imágenes en verde sobre `81d3d55`: 1147 del backend, 58 de contratos, 171 del frontend y 121 de extremo a extremo. Mutaciones: 27/27, 20/20 y 16/16. Codex limpio en #78 y #79; en #80 se quedó sin cupo y lo reemplazó una revisión adversarial independiente, documentada en el PR.
  - Despliegue (Claude, mandato del propietario del 19-10), en tres ciclos con la misma SHA en staging y producción: `c41a13d` (`0016`), `f2e20dd` (rutas) y `81d3d55` (`0017`), con copia previa verificada antes de cada migración.
    - Validación en la base con `ROLLBACK`: 12/12 de `0016` y 13/13 de `0017` en los dos entornos.
    - El trabajo de vencimientos corrió en los dos entornos (`hecho`, 0 procesados).
    - Registros en `doc/deploys/2026-10-19-{c41a13d,f2e20dd,81d3d55}.md` del repositorio CEM.
  - **Producción:** 0 mandatos, 0 decisiones y 0 marcas; sin barrera de downgrade. Staging sigue sin bajar de `0015`.
  - **Abierto:** E27, E29, E33 a E35, E39 (borrar el script del smoke como root) y E40 (índice de vencimientos), A13, y las preguntas al propietario del diseño 2b-4b §7. La pantalla de mandatos todavía no existe: las rutas se usan por la API.

Antes, **`06828b2`** (19-10-2026, H2b 2b-4 completo), base `0015`, contrato 0.13.0.

- **H2b 2b-4, vínculos, padrón e invitación de mandatos (PR #72, #73, #74 y #75), 19-10. En staging y producción.** Diseño: `doc/diseno/H2b_4_Diseno.md` del repositorio CEM (#48), con P1 a P6 decididas por el propietario.
  - **PR #72, migración `0014`:** el vínculo guarda su procedencia y se confirma (P4: atestigua el carácter sobre la unidad, no la identidad civil; no confirma el titular ni quien lo registró, también como restricción en la base). Padrón mínimo (P5) por `padron_del_consorcio()` con `padron.ver`. **E31 resuelto:** los eventos de decisión, alta y baja de mandatos ya no llevan nombre, CUIT ni referencias.
  - **PR #73:** rutas de padrón y de confirmación, `Idempotency-Key` al invitar con el cupo dentro del bloqueo, y P6 (correo de la invitación solo si está pendiente y la emitió quien la lista; si no, `null`).
  - **PR #74:** pantalla para invitar (`/invitaciones`) con clave de reintento, CSRF, formulario bloqueado durante el envío y foco del teclado. El frontend nunca concede permisos: la API decide.
  - **PR #75, migración `0015` (A7, P1):** invitación de una persona a un mandato sin persona, con cuatro ojos (quien emite no aprueba) y referencia obligatoria; página `/invitacion-mandato`; `desasociar_persona_de_mandato` conserva CUIT, mandato y vigencia, revoca las sesiones y audita, y se rechaza si la persona tiene un mandato vigente de Consejo en el consorcio. Camino de soporte (`python -m scripts.mandato_soporte`, en la imagen) solo cuando los cuatro ojos no son posibles, con motivo y auditoría. `cem-trabajos` no envía a dominios `.invalid`.
  - **Smoke autenticado de staging (P3):** `backend/pruebas/smoke_staging.py`, fuera de la imagen, con guarda dura (`CEM_ORIGEN_PUBLICO` de staging, host de la base y nombre del consorcio). Corrida `b0951906`: 9/9, sesiones revocadas al final y 0 correos enviados. El consorcio sintético `CEM — prueba automatizada (staging)` es permanente y está en `REGISTRO_RECURSOS.md` del repositorio CEM.
  - CI con imágenes en verde sobre `06828b2`: 1060 del backend, 58 de contratos, 171 del frontend y 121 de extremo a extremo. Revisión de Codex limpia en #72 y #73; en #74 y #75 Codex se quedó sin cupo y la reemplazaron revisiones adversariales independientes (documentado en cada PR; E36 del tracker de CEM).
  - Despliegue (Claude, mandato del propietario del 19-10), en tres ciclos con la misma SHA en staging y producción: `ea008cd` (#72 y #73, `0014`), `41af9ac` (#74) y `06828b2` (#75, `0015`), con copia previa verificada antes de cada migración. Validación en la base con `ROLLBACK`: 20/20 de `0014` y 15/15 de `0015` en los dos entornos. Registros en `doc/deploys/2026-10-19-{ea008cd,41af9ac,06828b2}.md` del repositorio CEM.
  - **Barrera de downgrade:** staging no baja de `0015` desde el smoke (hay mandatos sintéticos, un vínculo confirmado, una invitación de mandato y una desasociación). **Producción sigue con 0 mandatos reales** y sin barrera; desde el primero, no se baja sin el propietario.
  - **Abierto:** E15 (un mandato que vence por fecha no revoca sesiones), E26, E27, E29, E33 a E35, 2b-4b (A15) y A13.

Antes, **`175e77f`** (19-10-2026, defectos previos a 2b-4), base `0013`, contrato 0.11.0. Los dos entornos tienen los mismos nombres de variables en sus 4 apps.

- **Defectos previos a 2b-4 (PR #69 y #70), 19-10. En staging y producción.** Encontrados al diseñar 2b-4 (CEM #48); no cambian producto.
  - **PR #69, migración `0013` (issue #66, E21 del tracker de CEM):** `registrar_decision` y `cambiar_modo` atan el actor a la sesión como `alta_mandato` y `baja_mandato`: solo orígenes `api` (actor = `plataforma.persona_actual()`) y `soporte` (sin persona en la sesión y sin actor). `baja_mandato` deja de aceptar un actor con `soporte`. El downgrade restaura las definiciones anteriores sin tocar datos. 23 pruebas nuevas.
  - **PR #70 (E25, E28 y E30):** invitar a una unidad que no existe o es de otro consorcio responde 404 `no_encontrado` (antes 403); `require_scope` no da ámbito en un consorcio dado de baja (404, como `/me/consorcios`); el listado de invitaciones no devuelve el correo de las aceptadas (`correo` es `null`; D6). 6 pruebas nuevas.
  - CI con imágenes en verde sobre `175e77f`: 908 del backend, 58 de contratos, 134 del frontend y 103 de extremo a extremo. Revisión de Codex limpia en las cabezas exactas de los dos PR.
  - Despliegue (Claude, mandato del propietario del 19-10): staging y después producción, con copia previa verificada en los dos. Validación en la base: las tres funciones con el control nuevo, `security definer`, dueño `cem_migrate` y `search_path` fijo; 9 casos que fallan antes de escribir con el motivo de `0013`, comparados con la misma corrida sobre `0012` antes del despliegue, donde ninguno daba esos motivos; nada escrito; cadena de auditoría intacta. Rutas sin sesión → 401. Registro en `doc/deploys/2026-10-19-175e77f.md` del repositorio CEM.
  - **Producción sigue sin mandatos:** la barrera del primer mandato real no se cruzó. Desde el primero, `0013` y `0012` no se bajan sin el propietario.
  - **Sin verificar en los entornos:** los flujos HTTP con sesión de E25, E28 y E30 (no hay cuentas de prueba; decisión P3 del diseño de 2b-4). Probados por las pruebas de integración de `ci.ps1`.

Antes, **`18446f0`** (19-10-2026, H2b 2b-3 completo), base `0012`:

- **H2b 2b-3, tabla de verdad, políticas y rutas (PR #65, #64 y #67), 19-10. En staging y producción.** Diseño: `doc/diseno/H2b_3_Tabla_de_verdad.md` y `H2b_3_Plan_de_implementacion.md` del repositorio CEM (#46).
  - **PR #65, políticas y evaluador puro:** `contratos/cem_contracts/politicas.py` (modelo `Politica` estricto, booleanos `StrictBool`, `fuente_normativa` obligatoria) y `politicas_datos/*.json` (10 módulos, 29 acciones, una por vista). `backend/app/autorizacion/evaluador.py`: `permitido = ámbito ∧ (vínculo ∨ ámbito declarado ∨ facultad) ∧ filtros`, negar por defecto, gramática de recursos A8. La tabla de verdad es un CSV de 267 filas que se prueba entera. Las 6 acciones económicas se niegan con `requiere_controles_2b6` hasta 2b-6.
  - **PR #64, migración `0012`:** causas de baja (`remocion` solo con decisión de asamblea; las otras con instrumento; `error_carga` no toca fechas y el mandato no vale para ninguna), un solo mandato de administración por consorcio y fecha (A14), CUIT obligatorio y válido para el administrador externo (A11), mandato con cuenta (A12), nadie se da de alta ni de baja a sí mismo (A4). `nucleo.mandato_vigente(mandato, fecha)` es la única definición de vigencia y `nucleo.consorcios_con_mandato_vigente()` resuelve C4 sin política RLS "propios". Alta y baja atan el actor a la sesión y solo admiten los orígenes `api` y `soporte`. El downgrade se niega si hay mandatos (`downgrade_con_datos`).
  - **PR #67, `Contexto` y rutas:** el `Contexto` lleva vínculos con su unidad, mandatos vigentes, facultades y si hay administrador vigente, todo con `consorcio_id` explícito y la fecha del consorcio. Un mandato vigente da ámbito (C3). `requiere(accion)` en cada ruta con ámbito; la denegación responde el error genérico y audita `autorizacion.denegada` con el motivo interno. `puede_invitar` ya no existe: invitaciones y unidades pasan por las políticas. `/me/consorcios` suma los consorcios con mandato vigente.
  - CI con imágenes en verde sobre `18446f0`: 879 del backend, 58 de contratos, 134 del frontend y 103 de extremo a extremo.
  - Despliegue (Claude, con la autorización del propietario del 18-10): `d41475d` (PR #65 y #64) en staging y producción, con copia previa verificada en los dos, y después `18446f0` (PR #67) en los dos, sin migración. Validación funcional en la base con `ROLLBACK` en los dos entornos; políticas verificadas dentro de la imagen desplegada; rutas sin sesión → 401. Registros en `doc/deploys/2026-10-19-d41475d.md` y `2026-10-19-18446f0.md` del repositorio CEM.
  - **Producción no tiene mandatos:** desde el primer mandato real, `0012` no se baja a `0011` (barrera del propietario).
  - **Abierto:** E15 (un mandato que vence por fecha no revoca sesiones), el resto de E18, A7, A13, A15 y los controles económicos de 2b-6. El issue #66 se cerró con `0013` (arriba).


- **H2b 2b-2, mandatos (`484f8ef`, PR #61), 18-10. En staging y producción como `f242295` (hoy reemplazada por `18446f0`).** El diseño suma los criterios 9 y 10 (CEM #43).
  - Migración `0011`:
    - catálogo `nucleo.facultad` (21 facultades, 6 económicas) y `nucleo.paquete_facultad` (paquetes de D2);
    - `nucleo.mandato` (D1, con decisión de respaldo obligatoria, sin superposición del mismo tipo y persona);
    - `nucleo.facultad_otorgada` (acotada al consorcio y, si tiene recurso, a ese recurso);
    - `decision_formal.sha256_acta` (opcional).
  - `cem_app` solo lee. Escriben dos funciones `security definer`:
    - `nucleo.alta_mandato`: paquete del tipo o un subconjunto;
    - `nucleo.baja_mandato`: cierra la vigencia con la fecha del consorcio, revoca las sesiones del mandatario (RN22) y no borra nada.
  - `python -m scripts.alta_mandato --archivo … [--simular]` registra el arranque de D3 (decisión, modo inicial y mandatos) en una transacción. **Nunca con una referencia inventada** (criterio 9).
  - Las políticas y las rutas que leen los mandatos llegaron con 2b-3 (arriba).
  - CI con imágenes en verde: 433 del backend, 3 de contratos, 134 del frontend y 103 de extremo a extremo.
  - Staging lo desplegó Claude (03:40–03:42 UTC), con copia previa verificada.
  - A las 19:22–19:27 UTC Claude desplegó `f242295` en staging y producción, con el sí del propietario. Producción migró de `0010` a `0011` con copia previa verificada.
    - `cd.ps1` solo despliega la punta de `main`, y `484f8ef` dejó de serlo al fusionar este `ESTADO.md`. Por eso se desplegó `f242295`, que tiene el mismo código.
    - Los dos entornos se validaron en la base con `ROLLBACK` y quedaron healthy, sin reinicios ni errores. La validación funcional en la base corrió con `ROLLBACK`. La integridad (conteos y cadena de auditoría recalculada) y la regresión están en `doc/infra/REGISTRO_RECURSOS.md` del repositorio CEM.
  - **Abierto:** un mandato que vence por fecha, sin baja, no revoca sesiones (tracker E15). Pareja 3750 sigue sin modo ni mandatos: espera el acta real.

- **H2b 2b-1, modo de operación (`000734d`, PR #59), 17-10.** Diseño: `doc/diseno/H2b_Mandatos_Padron_y_Permisos.md` del repositorio CEM (#40).
  - Migración `0010`: `nucleo.decision_formal` (registro mínimo con referencia del acta) y `nucleo.modo_operacion` (historial, uno vigente por consorcio). `cem_app` solo lee; escriben `nucleo.registrar_decision` y `nucleo.cambiar_modo` (`security definer`, consorcio del ámbito, auditoría). El modo solo cambia con decisión de asamblea (RN26, **PA31**).
  - `require_scope` usa la fecha del consorcio (`nucleo.hoy_del_consorcio`; la base corre en GMT) y el `Contexto` lleva el modo vigente. `ModoOperacion`: `consejo` reemplaza a `administrador_tradicional`.
  - CI con imágenes en verde: 418 del backend, 3 de contratos, 134 del frontend y 103 de extremo a extremo. Cobertura de PA: **5 de 25** (se suma PA31).
  - Staging y producción los desplegó Claude (18:18–18:24 UTC, corrida `/seguisolo` con producción autorizada), con copia previa verificada en los dos. Producción incluye también `f9130aa`.
  - Las 8 decisiones del diseño quedaron cerradas el 17-10; 2b-2 llegó a staging el 18-10 (arriba).

- **Formulario de la contraseña plegado (`f9130aa`, PR #57), 17-10.**
  - Qué trae: en `/cuenta` la sección muestra el estado y un botón "Cambiar la contraseña" (o "Definir una contraseña"); los campos aparecen al abrirlo, con el foco en el primero, y se pliegan al guardar o con "Cancelar". Plegado, el navegador no autocompleta la actual.
  - CI con imágenes en verde: 405 del backend, 3 de contratos, 134 del frontend y 103 de extremo a extremo.
  - Staging lo desplegó Claude (05:22 UTC); producción llegó con `000734d` (18:24 UTC). Comprobado en la página servida de los dos.
- **Primer consorcio en staging, 17-10:** Pareja 3750, 37 unidades, con `scripts.alta_consorcio`; el propietario aceptó la invitación como dueño de la 3-A. En producción todavía no hay consorcio.

- **Ver la contraseña, 503 sin base y alta de consorcio (`69374bc`, PR #52, #53, #54 y #55), 17-10.**
  - Qué trae: botón "Mostrar"/"Ocultar" en los campos de contraseña (#55); la entrada con contraseña da 503 sin base y todos sus errores llevan `no-store` (#52); `python -m scripts.alta_consorcio --archivo … [--simular]` dentro de `cem-backend` para que el equipo dé de alta un consorcio y sus unidades (#53); la e2e corre con 4 navegadores y separa las pruebas lentas (#54).
  - CI con imágenes en verde: 405 pruebas del backend, 3 de contratos, 133 del frontend y 103 de extremo a extremo.
  - Despliegue: staging lo hizo Claude (03:48–03:50 UTC) y producción el propietario (04:01–04:03 UTC). Sin migraciones. Registro en `doc/deploys/2026-10-17-69374bc.md` del repositorio CEM.
  - Comprobado afuera con controles: el botón sale en la página servida, el script está en la imagen y los errores 422 y 401 llevan `no-store`. **NO VERIFICADO en vivo:** el 503 sin base (cubierto por las pruebas).
  - Primer latido de la copia de producción en Kuma (monitor 21): 17-10, 03:30:10 UTC.
  - **Falta:** dar de alta el primer consorcio de producción con `scripts.alta_consorcio` (hecho en staging: Pareja 3750).

- **Pantallas de la contraseña (`9865ab4`, PR #50), 17-10.**
  - Qué trae: "Entrar con contraseña" en `/entrar`, con el paso del segundo factor, y la sección "Contraseña" en `/cuenta` para definirla o cambiarla.
  - CI con imágenes en verde: 378 pruebas del backend, 3 de contratos, 130 del frontend y 87 de extremo a extremo.
  - Despliegue: staging lo hizo Claude (00:52–00:54 UTC) y producción el propietario (00:56–00:58 UTC). Sin migraciones.
  - Comprobado en los dos entornos con controles; registro en `doc/deploys/2026-10-17-9865ab4.md` del repositorio CEM.
  - **NO VERIFICADO:** definir una contraseña y entrar con ella en un navegador real.
  - **Resuelto en `69374bc` (#52):** sin base, la entrada con contraseña daba 500 en vez de 503, y algunos errores no llevaban `no-store`.

- **Mi cuenta, modo lectura y contraseña (`ce41e90`, PR #46, #47 y #48), 16-10.** Registro en `doc/deploys/2026-10-16-ce41e90.md` del repositorio CEM.
  - **Qué trae:**
    - `/cuenta`: quién soy, sesiones vigentes, cerrar una o las otras, salir y el estado del segundo factor, con enlace desde el inicio;
    - modo lectura ampliada (RF53, sin la "ayuda por persona autorizada", que espera a 2b);
    - contraseña Argon2id: `nucleo.contrasena` y `nucleo.desafio_contrasena`, entrada en dos pasos con TOTP, y definición y cambio con verificación reciente. El backend está; las pantallas, no.
  - **CI:** antes de fusionar corrió sobre la combinación de las tres ramas: 378 pruebas del backend, 3 de contratos, 93 del frontend y 69 de extremo a extremo.
  - **Staging:** `cd.ps1` por Claude, con copia previa de 17 tablas y 32 filas.
  - **Producción:** `cd.ps1 -Confirmar` por el propietario (21:15–21:18 UTC), con copia previa de 17 tablas y 13 filas.
  - **Comprobado en los dos entornos:**
    - base en `0009` con la RLS forzada;
    - una cuenta inventada da 401 `credenciales_invalidas`, y como control otro sitio da 403 `csrf_invalido`;
    - `/cuenta` está en el JavaScript servido;
    - 0 reinicios y 0 errores.
  - **NO VERIFICADO:** definir una contraseña y entrar con ella. Falta su pantalla, que está propuesta como tarea aparte.

- **Aviso de la copia a Kuma, formulario de `/entrar` y cobertura de PA (`7c23fd0`, PR #40, #41 y #44), 16-10.** Registro en `doc/deploys/2026-10-16-7c23fd0.md` del repositorio CEM.
  - **Despliegue:** staging lo hizo Claude (17:34–17:37 UTC) y producción el propietario (19:09–19:11 UTC). No hubo migraciones.
  - **Aviso de la copia:** `CEM_COPIAS_AVISO_URL` en `cem-trabajos(-staging)` apunta al push interno de los monitores Push `cem-copia-diaria-staging` (20) y `cem-copia-diaria` (21). Tienen intervalo de 26 h y avisan por Infra Alert.
  - **Probado en staging con una copia real:** 0 → 1 latido, en verde. Controles: con un token inventado da `fallido` y sin la variable da `sin_configurar`; ninguno de los dos agrega latidos.
  - **NO VERIFICADO:** el primer latido de producción, que llega con la copia de las 03:30 UTC del 17-10.

- **Segundo factor (`099ab21`), 16-10.** Registro en `doc/deploys/2026-10-16-099ab21.md` del repositorio CEM.
  - **CI:** `ci.ps1` con imágenes en verde.
  - **Staging, 00:31–00:34 UTC:** `cd.ps1` por Claude, con copia previa `diarias/2026-10-16/cem-20261016T003156Z.dump.age` (15 tablas, 19 filas).
  - **Producción, 01:14–01:17 UTC:** `cd.ps1 -Confirmar` por el propietario, con copia previa `diarias/2026-10-16/cem-20261016T011453Z.dump.age` (15 tablas, 9 filas).
  - **Comprobado en los dos entornos:**
    - base en `0008`, con la RLS forzada en `factor_totp` y `codigo_recuperacion`;
    - `/api/v1/auth/totp` sin sesión y con una cookie inventada dan 401, y como control una ruta inexistente da 404;
    - el JavaScript servido contiene `/cuenta/segundo-factor`;
    - 0 reinicios y 0 errores.
  - **El propietario probó el ciclo entero en staging, con una aplicación real en el teléfono (01:08–01:13 UTC):** alta, confirmación (dos códigos rechazados antes de acertar), verificación y baja. La base quedó limpia.
  - Para entrar hubo que pedir el correo con `curl`: `/entrar` no tiene formulario para pedir el enlace (ver "Próximo paso").

- **Invitaciones (`3251caf`), 15-10.**
  - **CI:** `ci.ps1` con imágenes en verde.
  - **Staging, 21:38–21:41 UTC:** `cd.ps1` por Claude, con copia previa `diarias/2026-10-15/cem-20261015T213819Z.dump.age` (14 tablas, 19 filas).
  - **Producción, 22:12–22:14 UTC:** `cd.ps1 -Confirmar` por el propietario, con copia previa `diarias/2026-10-15/cem-20261015T221250Z.dump.age` (14 tablas, 9 filas).
  - **Comprobado en los dos entornos:**
    - base en `0007`;
    - las dos políticas de `nucleo.invitacion` y la RLS forzada;
    - un token inventado da 404 `invitacion_invalida`, y como control una ruta inexistente da 404 `no_encontrado`;
    - la lista sin sesión da 401;
    - `/invitacion` está en el JavaScript servido;
    - 0 reinicios y 0 errores en los registros.
  - **NO VERIFICADO:** una invitación de punta a punta. Staging no tiene consorcios ni unidades.

Antes, con `951114b` (identidad hasta el código de seis dígitos):

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
- **Trampa de las constancias, ya resuelta.** La app de Claude en Windows es un paquete MSIX: lo que sus procesos crean en `%LOCALAPPDATA%` queda en su carpeta privada (`%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local\`), y una terminal del propietario no lo ve.
  - Desde el 15-10, las constancias están en `C:\Users\holan\cem-despliegue`, que no se virtualiza. Las apunta la variable de usuario `CEM_DESPLIEGUE_ESTADO`.
  - Una terminal nueva del propietario la vio sin que él la creara: el registro de usuario no se virtualiza.
  - Así corrió el despliegue de producción de `3251caf`.

## Próximo paso

Orden pedido por el propietario el 15-10-2026: ordenar antes de sumar funciones.

1. **Hecho el 15-10:** `main` en staging y producción, primero `951114b` y después `3251caf` con las invitaciones. Las constancias están en `C:\Users\holan\cem-despliegue`, y una terminal nueva del propietario ve `CEM_DESPLIEGUE_ESTADO`.
   - Para probar una invitación de punta a punta en staging, primero hay que cargar un consorcio y una unidad de prueba.
2. Carril C: los prototipos P0 y el guion de prueba ya están en el repositorio CEM, en `doc/descubrimiento/` (CEM #26 y #27, 15-10-2026). Lo que sigue es la prueba de uso para la puerta G3.
3. Identidad, lo que falta de 2a (las invitaciones ya están en producción):
   - **Hecho el 16-10:** segundo factor TOTP con su página, en staging y producción, a la que se llega desde `/cuenta`;
   - **Hecho el 16-10:** el formulario para pedir el enlace en `/entrar` (PR #44), en staging y producción;
   - **Hecho el 16 y el 17-10:** contraseña Argon2id con sus pantallas (#48 y #50), página `/cuenta` (#46) y modo lectura ampliada (RF53, #47);
   - **falta:**
     - TOTP obligatorio para roles de dinero (con los mandatos, 2b);
     - la ayuda por persona autorizada (RF53, 2b);
     - la cuenta de solo carga;
     - borradores fuera de línea (PA16);
     - PA29.
   - Passkeys pueden ir en H6.
4. **Hecho el 16-10 (PR #40):** `ci.ps1` guarda la cobertura de PA en la constancia (campo `cobertura_pa`) y en el `.log`. Hoy hay 5 de 25 (PA31 desde el 17-10). Toda prueba nueva que cubra una PA lleva el prefijo `test_paNN_`.
5. Pendientes de las copias:
   - comprobar el primer latido de `cem-copia-diaria` (03:30 UTC del 17-10);
   - WAL continuo (H2c);
   - bloqueo de objeto del bucket;
   - el primer descifrado con la identidad real.
   - El inventario de copias de la VM y de la PC (16-10) se ordena aparte, para todos los proyectos.
6. TB-8: falta leer las reglas de OCI para cerrarlo con prueba firme (ver "Decisiones vigentes"); para eso hay que configurar el perfil del MCP de OCI.
7. Probar `/instalar` en un iPhone y en un Android reales. El cartel con el QR está en el repositorio CEM (#28); falta comprobar que el QR lleva a `/instalar`.
8. **Cloudflare Access de `cem-staging` (decidido por el propietario el 16-10, para más adelante):**
   - poner la marca en la pantalla de entrada (*Custom pages → Access login page*), después de confirmar que el plan gratuito la incluye;
   - alargar la sesión de Access a un mes.
   - Producción no lleva Access y queda como está: la sesión de la aplicación dura 30 días.
9. **RF02, corte 3 de 2b-5 (23-10-2026):** rama `rf02-importacion-corte3`, desde `94a2114`, empujada, **sin PR, sin fusionar y sin desplegar**.
   - **Qué trae:** migración `0025` (importación con informe, la invitación nombra su vínculo, anulación de un vínculo no iniciado), contrato 0.17.0 y `scripts.anular_vinculo`.
   - **Decisiones e informe:** D1 a D4, las inferencias y tres preguntas para el propietario están en `doc/rf02_corte3_informe.md`.
   - **Respuestas del propietario del 23-10** a las tres preguntas (`1e1b504`) y a la renovación de una invitación importada vencida, opción 3 (`2ceef5d`): aplicadas. Hallazgos de Codex en app #105 (un P1 y dos P2): corregidos en `1acb5c3`. Tanda única sobre `4b978a1` (dos P2 de Codex y un P2 más alcance y privacidad de la revisión independiente): corregida en `5f00c5b`. Dos P2 de Codex sobre `69f1f77` (encabezados repetidos en una pasada y surrogate suelto): corregidos en `2430829`. Dos P2 de Codex sobre `70e889c` (solapes acotados y orden de bloqueos persona → auditoría) y el P3 4 sobre `69f1f77` (anular frente a aceptar, que era real): corregidos en `65ee21f`, con pruebas de deadlock con dos conexiones reales. Dos P2 de Codex sobre `7e28cd5` y el barrido de bloqueos (orden único invitación → persona → vínculo → auditoría en toda la ruta) y de diagnósticos (lineales; contrato con los totales de coeficientes): corregidos en `7366be4` y `76a103a`. Los demás P3 de las revisiones independientes quedan pendientes, sin código. `ci.ps1` dio verde sobre `69f1f77`, `70e889c` y `7e28cd5`.
   - **Pruebas:** 1529 passed sobre el código final, en una imagen local equivalente: la oficial no se pudo construir en la nube.
   - **Revisión independiente final: PENDIENTE sobre el SHA final después de estas correcciones.** La de `129730f` no vale como final: sus P1 y P2 cambiaron el código.
   - **Falta:**
     - la revisión independiente final y Codex sobre el SHA final;
     - `ci.ps1` con la imagen oficial;
     - pasar las decisiones D1, D3, D4 y las del 23-10 al documento de decisiones de CEM.
