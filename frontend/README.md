# frontend (cem-frontend)

Aplicación web instalable (PWA) de Consorcio en Mano: React 18, TypeScript 5, Vite 5 y pnpm (documento 13, §5 y §14).

Los tipos de la API se **generan** desde `../contratos/openapi.json` con `openapi-typescript` en cada `dev`, `test`, `typecheck` y `build` (`src/api/esquema.d.ts`, ignorado por git). Un cambio incompatible del contrato rompe `tsc` (AD-06). El cliente es `openapi-fetch`.

```bash
pnpm install --frozen-lockfile
pnpm test        # Vitest
pnpm lint        # eslint, cero advertencias
pnpm build       # tsc + vite build -> dist/
```

## Imagen

`frontend/Dockerfile` se construye desde la raíz del repositorio: `docker build -f frontend/Dockerfile .`. Sirve `dist/` con nginx sin privilegios (uid 101) en el puerto 8080, con fallback de SPA a `index.html`, `index.html` sin caché y `/assets/` inmutable un año. `/api/` y `/health` responden 404 en este nginx: los enruta el proxy de Coolify directo a `cem-backend` en el mismo dominio.
