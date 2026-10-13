# cem-frontend

Aplicación web instalable (PWA) de **Consorcio en Mano**, servida por nginx del mismo origen. Diseño: documento 13 del repositorio `CEM`, §6.3 (imagen), §14 (PWA y nginx) y AD-05/AD-06.

Hoy es el esqueleto de H1: una página de inicio que muestra el estado del backend leído de `/health`, instalable y con el shell disponible sin conexión.

## Qué hay

| Ruta | Qué hace |
|---|---|
| `src/api/cliente.ts` | Cliente tipado (`openapi-fetch`) del mismo origen; sin URL de backend |
| `src/api/tipos.ts` | **Generado** desde `../contratos/openapi.json` en cada `dev`, `test`, `tipos` y `build`; no se compromete ni se edita (AD-06) |
| `src/EstadoServicio.tsx` | Consulta `/health` con TanStack Query y muestra versión, contrato y SHA |
| `src/App.tsx` | Rutas (React Router 7): inicio y "No encontramos esa página" para cualquier otra |
| `src/manifiesto.ts` | Manifiesto de la PWA; una prueba exige `standalone` e iconos 192, 512 y `maskable` |
| `src/AvisoVersion.tsx` | Aviso "hay una versión nueva": el service worker nuevo se activa solo si la persona acepta (§14.4) |
| `vite.config.ts` | Manifiesto y service worker (Workbox): precaché del shell; `/api/` y `/health` nunca salen de caché |
| `nginx/cem-frontend.conf.template` | Proxy de `/api/` y `/health` al backend con `resolver` y variable (AD-05); caché de activos y cabeceras de seguridad |
| `nginx/05-exigir-backend.sh` | El contenedor no arranca si falta `CEM_BACKEND_DESTINO` |
| `Dockerfile` | Dos etapas: `node:22-alpine` (pnpm, tipos, `tsc`, `vite build`) y `nginxinc/nginx-unprivileged:1.27-alpine` (uid 101, puerto 8080) |
| `scripts/probar-imagen.sh` | Prueba la imagen construida sin backend: uid 101, cabeceras, caché, fallback de la SPA, 404 de `/assets/` y reenvío de `/health` |

## Desarrollo

Node 22 y pnpm 10.26.2 (`corepack enable` lo toma de `packageManager`).

```bash
pnpm install
pnpm lint        # eslint, cero advertencias
pnpm tipos       # genera los tipos y corre tsc
pnpm test        # Vitest + axe sobre la página
pnpm build       # genera, compila y empaqueta en dist/
pnpm dev         # Vite en el 5173; reenvía /api y /health al backend local del 8000
```

## Imagen y despliegue

Se construye desde la **raíz** del repositorio, igual que el backend:

```bash
docker build -f frontend/Dockerfile -t cem-frontend .
docker run -p 8080:8080 -e CEM_BACKEND_DESTINO=cem-backend:8000 cem-frontend
frontend/scripts/probar-imagen.sh cem-frontend
```

El contenedor escucha en **8080** (nginx sin privilegios): en Coolify, *Ports Exposes* = `8080`.

| Variable | Qué es | Producción | Staging |
|---|---|---|---|
| `CEM_BACKEND_DESTINO` | `host:puerto` del backend en la red de Docker; nginx lo vuelve a resolver cada 10 s | `cem-backend:8000` | `cem-backend-staging:8000` |

El nombre del backend es su **alias de red** en Coolify (`custom_network_aliases`), nunca el nombre del contenedor, que cambia en cada despliegue. Healthcheck propio en `/frontend-salud`, que no depende del backend.

## Pendiente

Rutas por rol y modo, borradores en IndexedDB, push, `/soporte` detrás de Access, Playwright en la canalización y `VITE_PUBLIC_BASE_URL` como secreto de construcción cuando haga falta (§6.3).
