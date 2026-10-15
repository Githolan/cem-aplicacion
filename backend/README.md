# cem-backend

API con las reglas del negocio de **Consorcio en Mano** (FastAPI, PostgreSQL con seguridad por fila). Diseño: documento 13 del repositorio `CEM`, §7 (backend), §8 (datos), §11 (API).

## Qué hay

| Ruta | Qué hace |
|---|---|
| `app/main.py` | Arma la aplicación: salud, errores uniformes, rutas |
| `app/acceso.py`, `app/soporte/` | JWT de Cloudflare Access en las rutas de soporte (`TB-CEM-5`) |
| `app/limite.py` | Límite de tasa por IP real con Redis (§10.6, cierra TB-8) |
| `app/db.py` | `transaccion()`: abre la transacción y fija persona y consorcio para la RLS (§7.2) |
| `app/contexto.py` | `obtener_identidad`: valida la cookie de sesión (y el `X-CSRF-Token` si la petición modifica estado); `require_scope`: vínculos vigentes de la persona en el consorcio de la ruta |
| `app/identidad/` | Enlace mágico, código por correo, sesiones y CSRF (§10.3, §10.4): `POST /api/v1/auth/enlace` (el correo lleva el enlace `<origen>/entrar#token=…` y un código de seis dígitos), `POST /api/v1/auth/canje` (token en el cuerpo), `POST /api/v1/auth/codigo` (correo y código, desde cualquier dispositivo; 10 minutos, cinco intentos, 10 por hora y 20 por día por correo), `GET /api/v1/auth/sesion`, `POST /api/v1/auth/salir`. Sesiones de la persona (RF04): `GET /api/v1/auth/sesiones`, `POST /api/v1/auth/sesiones/{id}/revocar` y `POST /api/v1/auth/sesiones/revocar-otras`. Necesita `CEM_ORIGEN_PUBLICO` (`https://<dominio>`) para armar el enlace y `CEM_CODIGO_CLAVE` (`secrets.token_urlsafe(32)`, distinta por entorno) para el HMAC de los códigos; sin ellas, pedir la entrada da 503 |
| `app/consorcios/` | `GET /api/v1/me/consorcios` y `GET /api/v1/consorcios/{id}/unidades` |
| `app/liquidacion/reparto.py` | Motor de reparto: función pura con `Decimal` y restos mayores (§7.3) |
| `app/plataforma/cola.py` | Cola de trabajos en PostgreSQL: `encolar()` dentro de la transacción de negocio, tipos con reintentos y retroceso (§12.1) |
| `app/plataforma/outbox.py` | `publicar()` de eventos en la misma transacción y despachador que los convierte en trabajos (§12.2) |
| `app/plataforma/trabajos.py` | Proceso `cem-trabajos`: toma trabajos con `SKIP LOCKED` y los ejecuta con el consorcio fijado |
| `app/plataforma/planificador.py` | Proceso `cem-planificador`: líder único con `pg_advisory_lock`, despacho del outbox y tareas periódicas con `UNIQUE(tipo, ventana)` (§12.3) |
| `app/plataforma/idempotencia.py` | `Idempotency-Key` (§7.4): `IdempotenciaDep` y `responder_una_vez()` guardan la respuesta en la misma transacción que el efecto; repetición → misma respuesta, otro cuerpo → 422, en curso → espera o 409 |
| `app/plataforma/tareas.py` | Tipos de trabajo y tareas periódicas registradas (hoy: purga diaria de la cola, de las respuestas de `Idempotency-Key` vencidas y de las sesiones y enlaces que dejaron de servir hace más de 90 días) |
| `migrations/versions/0001_*` | Esquemas, núcleo, auditoría encadenada, RLS y privilegios |
| `migrations/versions/0002_*` | `plataforma.jobs`, `outbox`, `ejecuciones_programadas`, sus políticas y las funciones del trabajador |
| `migrations/versions/0003_*` | `plataforma.idempotencia` (respuestas de `Idempotency-Key`, §7.4) con RLS por persona y consorcio, y su purga |
| `migrations/versions/0004_*` | `nucleo.sesion` y `nucleo.enlace_acceso` (solo hashes) con RLS por persona y funciones `security definer` para pedir, canjear y validar |
| `migrations/versions/0005_*` | `nucleo.revocar_sesiones_de(persona)` (cierre por cambio de mandato, RN22) y `nucleo.purgar_identidad(retencion)`, que la purga diaria llama con 90 días (§15.6) |
| `app/identidad/invitaciones.py` | Invitaciones y alta de personas (RF03): `POST/GET /api/v1/consorcios/{id}/invitaciones`, `POST …/invitaciones/{id}/revocar`, `POST /api/v1/auth/invitacion/consultar` y `POST /api/v1/auth/invitacion/aceptar`. Invita, por ahora, el propietario vigente de la unidad (los mandatos de H2 2b reemplazan esa regla en `puede_invitar`). El correo lleva `<origen>/invitacion#token=…`; 7 días, un solo uso. Correo sin cuenta: crea persona, vínculo y sesión; con cuenta: pide su sesión. 20 por hora y 50 por día por persona |
| `app/identidad/segundo_factor.py` | Segundo factor TOTP (RFC 6238) y verificación reciente (TB-CEM-3): `GET /api/v1/auth/totp`, `POST …/alta`, `…/confirmar` (devuelve diez códigos de recuperación), `…/verificar` (código o código de recuperación; la sesión queda verificada 10 minutos) y `…/baja` (pide verificación reciente). `SegundoFactorRecienteDep` para las acciones privilegiadas. Secreto cifrado con AES-256-GCM y códigos con HMAC, con claves derivadas de `CEM_CODIGO_CLAVE`. 5 intentos por minuto y 20 por día por persona |
| `app/auditoria.py` | `registrar()`: agrega un evento a la auditoría encadenada del consorcio del ámbito, sin datos de contacto |
| `migrations/versions/0006_*` | `nucleo.codigo_acceso` (HMAC del código, intentos, vencimiento; uno por pedido, se borra con su enlace) con RLS y sin privilegios para `cem_app`, y las funciones `registrar_codigo` y `canjear_codigo` |
| `migrations/versions/0007_*` | `nucleo.invitacion` (hash del token, 7 días como máximo, una pendiente por correo, unidad y vínculo) con RLS por consorcio que exige `invitada_por` = persona del ámbito, y las funciones `consultar_invitacion` y `aceptar_invitacion` (persona, vínculo desde hoy en la zona del consorcio, sesión y auditoría en una transacción) |
| `migrations/versions/0008_*` | `nucleo.factor_totp` (secreto cifrado, último paso aceptado), `nucleo.codigo_recuperacion` (HMAC, un solo uso) con RLS por persona, y `nucleo.sesion.segundo_factor_en` |
| `scripts/invitar.py` | Alta de las primeras personas de un consorcio por el equipo de la plataforma: `python -m scripts.invitar --consorcio … --unidad … --tipo … --correo … --nombre …` dentro del contenedor de `cem-backend` |
| `scripts/crear_roles.sql` | Roles `cem_migrate`, `cem_app`, `cem_ro` y la base (una vez por servidor, con superusuario) |

## Desarrollo

Requiere el paquete de contratos de `../contratos` (mismo repositorio) y un PostgreSQL local con un superusuario.

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
export CEM_TEST_ADMIN_URL=postgresql+psycopg://postgres:postgres@localhost:5432/postgres
.venv/bin/pytest            # todo, incluidas las pruebas de RLS contra la base real
.venv/bin/pytest -m "not db and not redis"  # solo lo que no necesita servicios
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

La misma imagen corre los tres procesos; `CEM_PROCESO` elige cuál (`arranque.sh`): `api` (por defecto, migra y levanta la API), `trabajos` o `planificador`. El `HEALTHCHECK` (`python -m app.plataforma.latido`) comprueba la API por HTTP y los otros dos por la edad del archivo de latido. `CEM_TRABAJOS_LOTE` (por defecto 1) dice cuántos trabajos reserva cada toma de `trabajos`.

Los tres procesos escriben registros JSON a stdout, una línea por registro, con la configuración de `app/registros.json` (documento 13, §16.1): `momento`, `nivel`, `proceso`, `modulo`, `mensaje` y, cuando existen, `id_correlacion` (el mismo del cuerpo de un error; en un trabajo, su id), `consorcio_id`, `persona_id` y `duracion_ms`. Correos, tokens, claves (también dentro de una URL), teléfonos e importes salen redactados, y el error de un trabajo fallido se guarda redactado en `plataforma.jobs`. La API apaga el registro de acceso de uvicorn y escribe una línea por petición con ruta (sin la consulta), estado, tamaño y duración.

Las pruebas `db` crean y borran la base `cem_prueba`, aplican la migración (ida, vuelta e ida) como `cem_migrate` y consultan como `cem_app`, que no tiene `BYPASSRLS`.

Un cambio de la API que modifique el OpenAPI hace fallar `test_contrato_openapi.py`; si es intencional se regenera con `python -m scripts.exportar_openapi` y la diferencia se revisa en el PR.

## Pendiente

De `identidad`: revocaciones en Redis (hoy cada petición valida en la base, así que una revocación corta el pedido siguiente), 12 h para roles de dinero y la llamada a `nucleo.revocar_sesiones_de` desde el módulo de mandatos (H2 2b), TOTP y passkeys; el código por WhatsApp (con opt-in) espera a la integración de WhatsApp. Además: partición mensual de auditoría por el planificador, imagen Docker. Producción usa PostgreSQL 18 y Python 3.12; la migración también corre en PostgreSQL 16.
