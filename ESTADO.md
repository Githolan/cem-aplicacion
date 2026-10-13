# Estado de cem-aplicacion

Se lee al empezar cada sesión y se actualiza antes de terminarla (documento 14, regla 5).

**Actualizado:** 13-10-2026.

## Qué existe

- `contratos/`: modelos Pydantic (error uniforme, salud, consorcios, unidades), enumeraciones, `esquemas.json` y `openapi.json` comprometidos.
- `backend/`: FastAPI con `/health`, errores uniformes, `require_scope`, `GET /api/v1/me/consorcios`, `GET /api/v1/consorcios/{id}/unidades` y el motor de reparto.
- `frontend/`: PWA mínima (React 18, Vite 5, TypeScript 5, pnpm 10.26.2): pantalla de inicio, indicador de estado que lee `/health` con el cliente `openapi-fetch` tipado desde `contratos/openapi.json`, manifest e iconos, ruta de no encontrada; 6 pruebas de Vitest; `frontend/Dockerfile` (nginx sin privilegios, puerto 8080, fallback de SPA, HEALTHCHECK).
- Migración `0001`: esquemas, consorcio, persona, unidad, vínculo, auditoría encadenada, RLS forzada y privilegios de `cem_app`.
- 45 pruebas (42 del backend, 3 de contratos), incluidas las de RLS contra PostgreSQL real.

## Cómo probar

```bash
cd backend
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
export CEM_TEST_ADMIN_URL=postgresql+psycopg://postgres:postgres@localhost:5432/postgres
.venv/bin/pytest && .venv/bin/ruff check .. && .venv/bin/ruff format --check ..
cd ../contratos && ../backend/.venv/bin/pytest
cd ../frontend && pnpm install --frozen-lockfile && pnpm lint && pnpm test && pnpm build
```

## Decisiones vigentes

- Monorrepositorio (AD-23); contratos como fuente única y tipos TypeScript generados desde `contratos/openapi.json` (AD-06).
- Migraciones en SQL explícito con Alembic, aplicadas con `cem_migrate` (AD-09). Los roles los crea una vez `backend/scripts/crear_roles.sql` con un superusuario, porque `cem_migrate` no tiene `CREATEROLE`.
- El ámbito se fija con `set_config(..., true)` (equivale a `SET LOCAL`); si falta, las políticas no devuelven filas.
- En staging, el dominio se reparte por ruta en el proxy de Coolify: `/api` y `/health` van a `cem-backend-staging` y el resto a `cem-frontend-staging`. Difiere del nginx del mismo origen del documento 13 (§14.5, AD-05): el nginx del frontend solo sirve estáticos y responde 404 en `/api/` y `/health` si el enrutamiento falla. Producción todavía no tiene frontend.
- No hay atajo de desarrollo para la autenticación: hasta que exista `identidad`, toda ruta protegida responde 401.

## Próximo paso

1. Hook `SessionStart` que instale PostgreSQL 18 y Redis 7 en las sesiones de Claude (hoy se probó con PostgreSQL 16 y Python 3.13, los del entorno).
2. Tablas de `plataforma`: `jobs`, `outbox`, `idempotencia` (migración 0002).
3. Módulo `identidad`: personas, sesiones, passkeys.
4. Frontend: Workbox (precaché del shell), `axe` y Playwright (documento 14, H1 semana 3); decidir si producción usa el reparto por ruta de Coolify o el nginx del mismo origen de AD-05.
