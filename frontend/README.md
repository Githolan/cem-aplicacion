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
| `src/Entrar.tsx` | Página `/entrar` que abre el enlace del correo: lee el token del fragmento (`#token=`, nunca llega al servidor), lo guarda en `sessionStorage` de la pestaña (vence a los 15 minutos del enlace), lo borra de la URL y lo canjea con `POST /api/v1/auth/canje`; muestra "Entraste." o un error genérico. Una recarga o una actualización a mitad de camino usa el guardado; se borra apenas termina el canje, salga bien o mal. Diseño provisorio hasta G3 |
| `src/enlace.ts` | Ruta `/entrar`, parámetro `token` y vigencia del enlace: una sola fuente para la página, `vite.config.ts` y la extensión del service worker |
| `src/swEntrar.ts` | Extensión del service worker (`sw-entrar.js`, la carga `sw.js` con `importScripts`): si una ventana abre un enlace de entrada que nadie leyó (versión anterior en caché), la versión nueva se activa sin esperar el aviso y esa ventana vuelve a abrir su URL completa |
| `src/manifiesto.ts` | Manifiesto de la PWA; una prueba exige `standalone` e iconos 192, 512 y `maskable` |
| `src/AvisoVersion.tsx` | Aviso "hay una versión nueva": el service worker nuevo se activa solo si la persona acepta (§14.4) |
| `src/ProveedorVersion.tsx`, `src/version.ts` | Registra el service worker una vez y reparte el estado de versión; al activarse la versión nueva recarga solo la pestaña que la pidió, en su misma URL. Una ruta desconocida con una versión nueva esperando la aplica sola (la app vieja no conoce las rutas nuevas) |
| `vite.config.ts` | Manifiesto y service worker (Workbox): precaché del shell; `/api/`, `/health` y `/entrar` nunca salen de caché |
| `nginx/cem-frontend.conf.template` | Proxy de `/api/` y `/health` al backend con `resolver` y variable (AD-05); caché de activos y cabeceras de seguridad |
| (registro de acceso) | `log_format cem_sin_consulta`: ruta sin `?…` y sin `Referer`, así un token o un correo en la consulta no quedan en `docker logs`; lo prueba `scripts/probar-imagen.sh` |
| `nginx/05-exigir-backend.sh` | El contenedor no arranca si falta `CEM_BACKEND_DESTINO` |
| `Dockerfile` | Dos etapas: `node:22-alpine` (pnpm, tipos, `tsc`, `vite build`) y `nginxinc/nginx-unprivileged:1.27-alpine` (uid 101, puerto 8080) |
| `scripts/probar-imagen.sh` | Prueba la imagen construida sin backend: uid 101, cabeceras, caché, fallback de la SPA, 404 de `/assets/` y reenvío de `/health` |
| `e2e/`, `playwright.config.ts` | Pruebas de extremo a extremo (Playwright + `@axe-core/playwright`) contra la imagen real, en Chromium de escritorio y móvil (Pixel 7) |
| `scripts/probar-extremo-a-extremo.sh` | Construye `cem-frontend` y `cem-backend`, los levanta con Redis en una red propia y corre Playwright; borra todo al final |

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

## Pruebas de extremo a extremo

```bash
pnpm e2e                              # todo: construye, levanta, prueba y limpia
pnpm e2e --project movil -g instalar  # los argumentos pasan a "playwright test"
```

Necesita Docker y el Chromium de Playwright 1.57.0 (revisión 1200; si falta, `pnpm exec playwright install chromium`). Las pruebas corren contra el nginx de la imagen, no contra Vite: el script levanta `cem-backend` real (con Redis, sin PostgreSQL: `/health` no la usa), un `cem-frontend` que le reenvía `/api/` y `/health`, y otro cuyo backend no atiende, para el aviso de "no pudimos contactar al servicio". Los contenedores se llaman `cem-frontend-e2e-*`, publican en puertos libres de `127.0.0.1` que elige Docker y se borran al terminar, pase lo que pase. Las imágenes también llevan el sufijo de la corrida (`cem-frontend-e2e-<sufijo>`, `cem-backend-e2e-<sufijo>`), así otra corrida en paralelo no las reemplaza a mitad de camino, y sus etiquetas se borran al final. Para las pruebas de versiones construye además `cem-frontend-e2e-anterior-<sufijo>` desde git (commit 38aca25, anterior a `/entrar`; sin `.git`, el repositorio llega en `CEM_E2E_REPO`, como hace `ci.ps1`) y `cem-frontend-e2e-siguiente-<sufijo>` (la de la corrida con otros bytes en `sw.js`); cada prueba levanta los suyos como `cem-frontend-e2e-version-*` y reemplaza una versión por otra en el mismo puerto, que es el mismo origen para el service worker.

| Archivo | Qué prueba |
|---|---|
| `inicio.e2e.ts` | La página muestra la SHA que informa el backend real en `/health`; enlace a `/instalar`; fallback de la SPA; `/api/` llega al backend (401 `no_autenticado`); sin backend, aviso y "Reintentar" |
| `instalar.e2e.ts` | `beforeinstallprompt` real de Chromium (perfil persistente, Chromium completo) → botón propio; sin el evento, pasos del menú; iPhone (user agent de Safari iOS) → Compartir → Agregar a inicio; `display-mode: standalone` → "ya está instalada" y el inicio deja de enlazar |
| `pwa.e2e.ts` | Manifiesto enlazado, `standalone` e iconos servidos; service worker activo que controla la página; `Page.getInstallabilityErrors` vacío; `/entrar` siempre de la red; sin conexión el shell carga desde caché y `/health` no |
| `versiones.e2e.ts` | Con la versión desplegada antes de `/entrar` (38aca25) en caché, el enlace del correo llega al canje (primero responde la caché vieja, después la red); una ruta desconocida aplica sola la versión nueva en la misma URL y la otra pestaña sigue esperando el botón; una recarga a mitad del canje no pierde el token |
| `seguridad.e2e.ts` | Cabeceras de seguridad y CSP en página, rutas de la SPA, activos, manifiesto, `sw.js` y `/health`; nginx sin versión; sin errores de consola ni violaciones de la CSP |

Cada página pasa por `axe` con las reglas WCAG 2.0, 2.1 y 2.2 A y AA, contraste incluido (en el navegador real, no en jsdom).

Límites conocidos: Chromium no emula `display-mode` (medido con Chromium 143), así que el modo `standalone` se simula respondiendo solo esa consulta de `matchMedia`; el diálogo nativo de instalación no se puede aceptar en modo headless, así que el botón se prueba hasta que aparece; y una instalación real por CDP (`PWA.install`) crea accesos directos en el sistema operativo, por eso no se usa. Safari de verdad (WebKit) no se prueba: se emula su user agent en Chromium.

| Variable | Qué es | Producción | Staging |
|---|---|---|---|
| `CEM_BACKEND_DESTINO` | `host:puerto` del backend en la red de Docker; nginx lo vuelve a resolver cada 10 s | `cem-backend:8000` | `cem-backend-staging:8000` |

El nombre del backend es su **alias de red** en Coolify (`custom_network_aliases`), nunca el nombre del contenedor, que cambia en cada despliegue. Healthcheck propio en `/frontend-salud`, que no depende del backend.

## Pendiente

Rutas por rol y modo, borradores en IndexedDB, push, `/soporte` detrás de Access, `pnpm e2e` en la canalización (`cem-despliegue`) y `VITE_PUBLIC_BASE_URL` como secreto de construcción cuando haga falta (§6.3).
