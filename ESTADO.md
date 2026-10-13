# Estado de cem-aplicacion

Se lee al empezar cada sesión y se actualiza antes de terminarla (documento 14, regla 5).

**Actualizado:** 13-10-2026.

## Qué existe

- `contratos/`: modelos Pydantic (error uniforme, salud, consorcios, unidades), enumeraciones, `esquemas.json` y `openapi.json` comprometidos.
- `backend/`: FastAPI con `/health`, errores uniformes, `require_scope`, `GET /api/v1/me/consorcios`, `GET /api/v1/consorcios/{id}/unidades` y el motor de reparto.
- `frontend/`: PWA (React 18, Vite 5, TypeScript) con página de inicio que lee `/health`; tipos generados desde `contratos/openapi.json`; imagen `nginx:1.27-alpine` que reenvía `/api/` y `/health` al backend con `resolver` (AD-05). Sin desplegar todavía.
- Migración `0001`: esquemas, consorcio, persona, unidad, vínculo, auditoría encadenada, RLS forzada y privilegios de `cem_app`.
- 48 pruebas (42 del backend, 3 de contratos, 3 del frontend con `axe`), incluidas las de RLS contra PostgreSQL real.

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

1. **Desplegar `cem-frontend-staging`** (necesita la orden del propietario; se hace desde su PC): alias de red `cem-backend-staging` en el backend de staging, aplicación nueva con `frontend/Dockerfile` desde la raíz, `CEM_BACKEND_DESTINO=cem-backend-staging:8000`, 64 MB y 0,1 CPU, y el dominio `cem-staging.holancloud.com` pasa del backend al frontend. Validado allí, lo mismo en producción con `cem-backend` y `cem.holancloud.com`. **NO VERIFICADO:** que la API de Coolify 4.4.6 acepte `custom_network_aliases` en aplicaciones (en bases no lo acepta) y que el DNS de Docker devuelva una IPv4 para el alias (nginx usa `ipv6=off`).
2. Hook `SessionStart` que instale PostgreSQL 18 y Redis 7 en las sesiones de Claude (hoy se probó con PostgreSQL 16 y Python 3.13, los del entorno).
3. Tablas de `plataforma`: `jobs`, `outbox`, `idempotencia` (migración 0002).
4. Módulo `identidad`: personas, sesiones, passkeys.
