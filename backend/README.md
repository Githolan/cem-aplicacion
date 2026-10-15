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
| `app/identidad/` | Enlace mágico, sesiones y CSRF (§10.3, §10.4): `POST /api/v1/auth/enlace`, `POST /api/v1/auth/canje` (token en el cuerpo; el correo lleva `<origen>/entrar#token=…`), `GET /api/v1/auth/sesion`, `POST /api/v1/auth/salir`. Sesiones de la persona (RF04): `GET /api/v1/auth/sesiones`, `POST /api/v1/auth/sesiones/{id}/revocar` y `POST /api/v1/auth/sesiones/revocar-otras`. Necesita `CEM_ORIGEN_PUBLICO` (`https://<dominio>`) para armar el enlace |
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

De `identidad`: revocaciones en Redis (hoy cada petición valida en la base, así que una revocación corta el pedido siguiente), 12 h para roles de dinero y la llamada a `nucleo.revocar_sesiones_de` desde el módulo de mandatos (H2 2b), código por correo, TOTP y passkeys. Además: partición mensual de auditoría por el planificador, imagen Docker. Producción usa PostgreSQL 18 y Python 3.12; la migración también corre en PostgreSQL 16.
