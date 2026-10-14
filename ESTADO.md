# Estado de cem-aplicacion

Se lee al empezar cada sesión y se actualiza antes de terminarla (documento 14, regla 5).

**Actualizado:** 14-10-2026.

## Qué existe

- `contratos/`: modelos Pydantic (error uniforme, salud, consorcios, unidades), enumeraciones, `esquemas.json` y `openapi.json` comprometidos.
- `backend/`: FastAPI con `/health`, errores uniformes, `require_scope`, `GET /api/v1/me/consorcios`, `GET /api/v1/consorcios/{id}/unidades` y el motor de reparto.
- `GET /api/v1/health/deps`: PostgreSQL (`select 1` con `cem_app`) y Redis (`PING`); 200 con `{"estado":"ok","db":"ok","redis":"ok"}` o 503 con la que falla, sin detalles (van a los registros). Kuma puede buscar `"db":"ok"`.
- **Límite de tasa (cierra TB-8):** middleware propio `app/limite.py` sobre `limits` con Redis (`cem:rl:*`), 300 pedidos por minuto por IP para toda la API; `/health` y `/api/v1/health/deps` exentas. Sin Redis la API responde 503 (falla cerrada) y la app no arranca sin `CEM_REDIS_URL`. La IP real: el nginx del frontend escribe `X-Forwarded-For` con `CF-Connecting-IP` y `arranque.sh` exige `FORWARDED_ALLOW_IPS` (uvicorn solo acepta ese encabezado de esa red).
- `frontend/`: PWA (React 18, Vite 5, TypeScript) con página de inicio que lee `/health`; tipos generados desde `contratos/openapi.json`; imagen `nginxinc/nginx-unprivileged:1.27-alpine` (uid 101, puerto 8080) que reenvía `/api/` y `/health` al backend con `resolver` (AD-05). **Desplegado en staging y en producción** desde `main`, hoy en `38aca25`: `cem-frontend-staging` el 14-10-2026 a las 02:14 UTC y `cem-frontend` a las 02:16 UTC, después de validar staging. El frontend es dueño del dominio de cada entorno y el backend no tiene dominio propio: el frontend le llega por los alias de red `cem-backend-staging` y `cem-backend`. El detalle y las comprobaciones están en `doc/infra/REGISTRO_RECURSOS.md` del repositorio CEM.
- Página `/instalar` y enlace "Instalá la aplicación en tu teléfono" en el inicio (pedido del propietario del 14-10-2026): botón propio donde el navegador ofrece `beforeinstallprompt` (Chrome y Edge), pasos de Safari → Compartir → Agregar a inicio en iPhone y iPad, y aviso de "ya está instalada" en modo `standalone`. Es el enlace para compartir por WhatsApp o en un QR de cartelera. En producción desde el 14-10-2026 (`38aca25`): `https://cem.holancloud.com/instalar` responde, y el JavaScript servido contiene la ruta. Ojo: un 200 no alcanza como prueba, porque la aplicación devuelve `index.html` para cualquier ruta.
- Cola de trabajos, outbox y planificador (documento 13, §12; plan H1, semana 2), en revisión (PR #8; decisiones de diseño aprobadas por el propietario el 14-10-2026): migración `0002` con `plataforma.jobs`, `outbox` y `ejecuciones_programadas` con RLS forzada; funciones `security definer` para tomar, terminar y fallar trabajos; procesos `cem-trabajos` y `cem-planificador` en la misma imagen, elegidos con `CEM_PROCESO`; `GET /api/v1/health/cola` para Kuma (contrato 0.2.0). **No desplegado**: crear `cem-trabajos` y `cem-planificador` en Coolify necesita la aprobación escrita del propietario en el hilo de la plataforma H0.
- Migración `0001`: esquemas, consorcio, persona, unidad, vínculo, auditoría encadenada, RLS forzada y privilegios de `cem_app`.
- 87 pruebas (73 del backend, 3 de contratos, 11 del frontend con `axe`, rutas, manifiesto e instalación), incluidas las de RLS contra PostgreSQL real, y `frontend/scripts/probar-imagen.sh` para la imagen (con un backend de eco que comprueba el `X-Forwarded-For`).

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

**Frontend comprobado el 13-10-2026** (imágenes de ambos construidas en local, red Docker con el backend bajo el alias `cem-backend`): `/` y rutas de la SPA → `200` con `Cache-Control: no-cache` y CSP; activos → caché de un año; `/health` → JSON del backend; `/api/v1/me/consorcios` → `401 no_autenticado`; sin `CEM_BACKEND_DESTINO` el contenedor sale con código 1. **AD-05:** se levantó un backend nuevo con otra IP (`172.18.0.2` → `.5`) y se borró el anterior sin reiniciar nginx; `/health` siguió en `200` con la SHA nueva. En Chromium: página sin errores de consola (la CSP bloquea scripts en línea, comprobado), service worker activo, `axe` sin faltas (contraste incluido) y, sin conexión, el shell carga desde caché y avisa que no hay servicio.

## Decisiones vigentes

- Monorrepositorio (AD-23); contratos como fuente única y tipos TypeScript generados desde `contratos/openapi.json` (AD-06).
- Migraciones en SQL explícito con Alembic, aplicadas con `cem_migrate` (AD-09). Los roles los crea una vez `backend/scripts/crear_roles.sql` con un superusuario, porque `cem_migrate` no tiene `CREATEROLE`.
- El ámbito se fija con `set_config(..., true)` (equivale a `SET LOCAL`); si falta, las políticas no devuelven filas.
- No hay atajo de desarrollo para la autenticación: hasta que exista `identidad`, toda ruta protegida responde 401.
- **slowapi descartado (14-10-2026).** slowapi 0.1.10 busca el manejador solo entre las rutas de primer nivel; FastAPI 0.141 deja las de `include_router` anidadas (`_IncludedRouter`), así que eximía toda la API sin avisar: las 301 peticiones daban 401 y ninguna 429. El middleware propio usa el mismo motor (`limits`) y lo prueban `test_limite_de_tasa.py` (con el middleware quitado fallan 5 de 7).
- **NO VERIFICADO: que Traefik solo sea alcanzable por el túnel.** Si el puerto 443 de la VM respondiera desde Internet sin pasar por Cloudflare, `CF-Connecting-IP` se podría inventar y rotar cupos del límite de tasa. Comprobarlo (lista de seguridad de OCI o `curl` a la IP pública) antes de dar TB-8 por cerrado del todo.
- Sin Redis no se atiende: el límite de tasa es un control de seguridad, no una caché. Si el propietario prefiere que la API siga sin límite cuando Redis cae, es una decisión suya y hay que registrarla.

## Antes de desplegar este corte (propietario, staging primero)

En `cem-backend-staging` y después en `cem-backend`, variables de ejecución (no de construcción):

- `CEM_REDIS_URL=redis://default:<CEM_REDIS_CLAVE>@<UUID de cem-redis(-staging)>:6379/0` (el nombre interno de la base está en `REGISTRO_RECURSOS.md`; la clave, codificada para URL si tiene caracteres especiales).
- `FORWARDED_ALLOW_IPS=10.0.2.0/24` (red `coolify`, IPv4: nginx llega al backend por IPv4).

Sin ellas el contenedor nuevo no arranca y Coolify conserva el anterior. Después del despliegue: `/api/v1/health/deps` → `{"estado":"ok","db":"ok","redis":"ok"}` desde dentro del contenedor; un pedido real por Cloudflare deja en `cem-redis-staging` una clave `cem:rl:*` con la IP pública de quien llamó, no `10.0.2.1` (`redis-cli --scan --pattern 'cem:rl:*'`); y el frontend redesplegado (cambió su nginx).

## Próximo paso

1. Hook `SessionStart` que instale PostgreSQL 18 y Redis 7 en las sesiones de Claude (hoy se probó con PostgreSQL 16 y Python 3.13, los del entorno).
2. Revisar y fusionar el PR de la cola; crear `cem-trabajos` y `cem-planificador` en staging y después en producción, y el monitor de Kuma sobre `/api/v1/health/cola`.
3. Tabla `plataforma.idempotencia` (encabezado `Idempotency-Key`, §7.4).
4. Módulo `identidad`: personas, sesiones, passkeys.
