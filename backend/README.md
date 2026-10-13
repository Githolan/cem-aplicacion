# cem-backend

API con las reglas del negocio de **Consorcio en Mano** (FastAPI, PostgreSQL con seguridad por fila). Diseño: documento 13 del repositorio `CEM`, §7 (backend), §8 (datos), §11 (API).

## Qué hay

| Ruta | Qué hace |
|---|---|
| `app/main.py` | Arma la aplicación: salud, errores uniformes, rutas |
| `app/db.py` | `transaccion()`: abre la transacción y fija persona y consorcio para la RLS (§7.2) |
| `app/contexto.py` | `require_scope`: vínculos vigentes de la persona en el consorcio de la ruta. La autenticación aún no existe y rechaza todo con 401 |
| `app/consorcios/` | `GET /api/v1/me/consorcios` y `GET /api/v1/consorcios/{id}/unidades` |
| `app/liquidacion/reparto.py` | Motor de reparto: función pura con `Decimal` y restos mayores (§7.3) |
| `migrations/versions/0001_*` | Esquemas, núcleo, auditoría encadenada, RLS y privilegios |
| `scripts/crear_roles.sql` | Roles `cem_migrate`, `cem_app`, `cem_ro` y la base (una vez por servidor, con superusuario) |

## Desarrollo

Requiere el paquete de contratos de `../contratos` (mismo repositorio) y un PostgreSQL local con un superusuario.

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
export CEM_TEST_ADMIN_URL=postgresql+psycopg://postgres:postgres@localhost:5432/postgres
.venv/bin/pytest            # todo, incluidas las pruebas de RLS contra la base real
.venv/bin/pytest -m "not db"  # solo lo que no necesita base
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Las pruebas `db` crean y borran la base `cem_prueba`, aplican la migración (ida, vuelta e ida) como `cem_migrate` y consultan como `cem_app`, que no tiene `BYPASSRLS`.

Un cambio de la API que modifique el OpenAPI hace fallar `test_contrato_openapi.py`; si es intencional se regenera con `python -m scripts.exportar_openapi` y la diferencia se revisa en el PR.

## Pendiente

Autenticación y sesiones (`identidad`), tablas de `plataforma` (`jobs`, `outbox`, `idempotencia`), rol `cem_backup`, partición mensual de auditoría por el planificador, límite de tasa, imagen Docker. Producción usa PostgreSQL 18 y Python 3.12; la migración también corre en PostgreSQL 16.
