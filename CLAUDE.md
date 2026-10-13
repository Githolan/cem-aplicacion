# CLAUDE.md — cem-aplicacion

Código de Consorcio en Mano. El diseño vive en el repositorio `Githolan/CEM` (documentos 13 y 14); sus reglas de `CLAUDE.md` (nombres por función, `cem-<función>`) valen también acá.

- Leer `ESTADO.md` al empezar y actualizarlo antes de terminar; todo queda comprometido y publicado en la rama de trabajo.
- Los comandos se corren desde `backend/`, `contratos/` o `frontend/` (este último solo con `pnpm`, nunca npm ni yarn) (`requirements.txt` instala `../contratos` relativo a la carpeta).
- Un cambio de forma de la API se hace en `contratos/`; se regenera `contratos/openapi.json` con `python -m scripts.exportar_openapi` desde `backend/` y la diferencia se revisa en el PR. Nunca se escriben tipos TypeScript a mano.
- Toda tabla con `consorcio_id` lleva RLS habilitada y forzada, y una prueba contra PostgreSQL real que demuestre que otro consorcio no la ve.
- Importes en `NUMERIC(18,2)` y `Decimal`; nunca `float`.
- Requisitos de producción a versión exacta en `backend/requirements.prod.txt`.
- Sin GitHub Actions: CI/CD con los scripts de `cem-despliegue`.
