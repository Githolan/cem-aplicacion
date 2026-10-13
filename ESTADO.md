# Estado de cem-aplicacion

Se lee al empezar cada sesión y se actualiza antes de terminarla (documento 14, regla 5).

**Actualizado:** 13-10-2026.

## Qué existe

- `contratos/`: modelos Pydantic (error uniforme, salud, consorcios, unidades), enumeraciones, `esquemas.json` y `openapi.json` comprometidos.
- `backend/`: FastAPI con `/health`, errores uniformes, `require_scope`, `GET /api/v1/me/consorcios`, `GET /api/v1/consorcios/{id}/unidades` y el motor de reparto.
- `frontend/`: PWA (React 18, Vite 5, TypeScript) con página de inicio que lee `/health`; tipos generados desde `contratos/openapi.json`; imagen `nginx:1.27-alpine` que reenvía `/api/` y `/health` al backend con `resolver` (AD-05). **Desplegado en staging** (`cem-frontend-staging`, rama `claude/project-thread-hytkur`, 13-10-2026 19:28 UTC); producción todavía no.
- Migración `0001`: esquemas, consorcio, persona, unidad, vínculo, auditoría encadenada, RLS forzada y privilegios de `cem_app`.
- 50 pruebas (42 del backend, 3 de contratos, 5 del frontend con `axe`, rutas y manifiesto) y `frontend/scripts/probar-imagen.sh` para la imagen, incluidas las de RLS contra PostgreSQL real.

## Cómo probar

```bash
cd backend
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
export CEM_TEST_ADMIN_URL=postgresql+psycopg://postgres:postgres@localhost:5432/postgres
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

## Próximo paso

1. **Validar staging y desplegar `cem-frontend` en producción** (orden del propietario). Staging quedó así el 13-10-2026: `cem-backend-staging` con alias de red `cem-backend-staging` (Coolify 4.4.6 sí acepta `custom_network_aliases` en aplicaciones; resuelve a IPv4) y sin dominio; `cem-frontend-staging` con `CEM_BACKEND_DESTINO=cem-backend-staging:8000`, 64 MB y 0,1 CPU, construido desde la raíz con `frontend/Dockerfile`, dueño del dominio `cem-staging.holancloud.com`. Comprobado por Traefik: `/` 200 HTML, `/health` 200 del backend, API 401, activos inmutables, host desconocido 503; desde afuera 302 a Access. AD-05 en real: redespliegue del backend con 120 consultas a `/health` cada 2 s, 119 en 200, ninguna 502 y una sin respuesta a los 3 s durante el cambio de contenedor; el frontend no se reinició. Corregido después y **desplegado en staging** (`431a88f`, 20:57 UTC): un 404 en `/assets/` ya no lleva `immutable`; nginx corre sin privilegios (`nginxinc/nginx-unprivileged`, uid 101) y escucha en **8080**, (antes el proceso maestro corría como root). Ojo: cambiar *Ports Exposes* por la API no regenera las etiquetas de Traefik y deja 502; en producción, crear `cem-frontend` con 8080 desde el principio o usar "Reset Labels to Defaults"; React Router con página de no encontrada. Para producción: alias `cem-backend` en `cem-backend`, `cem-frontend` en el puerto 8080 con `CEM_BACKEND_DESTINO=cem-backend:8000` y el dominio `cem.holancloud.com` pasa al frontend.
2. Hook `SessionStart` que instale PostgreSQL 18 y Redis 7 en las sesiones de Claude (hoy se probó con PostgreSQL 16 y Python 3.13, los del entorno).
3. Tablas de `plataforma`: `jobs`, `outbox`, `idempotencia` (migración 0002).
4. Módulo `identidad`: personas, sesiones, passkeys.
