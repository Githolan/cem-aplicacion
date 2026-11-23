# Accesibilidad (RNF07, PA28) y Lighthouse (RNF02)

| Dato | Valor |
|---|---|
| Fecha | 23-11-2026 |
| Código medido | `c7c200a` (rama `h6/restauracion-carga-accesibilidad`, sin cambios de la aplicación), el mismo que staging |
| Umbrales | RNF07: WCAG 2.2 AA y modo lectura ampliada verificado con personas mayores. RNF02: pantalla útil en 3 s en un móvil de referencia, «con dispositivo y red registrados» (documento 08 y documento 13, §20) |

## 1. axe en la suite de extremo a extremo

`pnpm -C frontend e2e` corre Playwright contra las imágenes reales (nginx del frontend y backend en Docker), en tres proyectos: `escritorio` (Desktop Chrome), `movil` (Pixel 7) y `tablet` (Galaxy Tab S4). `sinFaltasDeAccesibilidad` (`e2e/entorno.ts`) corre axe-core 4.14.0 con las etiquetas `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa` y `wcag22aa`, y la prueba falla con la primera falta.

| Medición | Resultado |
|---|---|
| Corrida | 23-11-2026, ~04:12 a 04:18 UTC, en la PC; candado de CPU libre al empezar (`CI_CORRIENDO` ausente; control: la misma prueba encuentra un archivo que existe) |
| Total | **487 pasaron, 0 fallaron, 2 omitidas**, 5,0 min. Por proyecto: 162 escritorio, 163 móvil, 162 tablet |
| Omitidas | `instalar.e2e.ts:169` «página /instalar en iPhone» en escritorio y tablet (solo corre en el proyecto móvil) |
| Llamadas a axe | 120 en 26 archivos de prueba (algunas dentro de bucles por página y esquema); todas en pruebas que pasaron: **0 faltas de WCAG 2.2 AA** en las pantallas que recorren |
| Pantallas nuevas del P0 en Modo Consejo | `liquidacion-oficial`, `reclamos`, `reparaciones`, `comunicados`, `presupuesto`, `ayuda`, `bandeja-consejo` y `panel`: cada archivo mide a 360 px y corre axe AA; todas pasaron |
| Lectura ampliada (`e2e/lectura-ampliada.e2e.ts`) | **102 de 102** (34 por proyecto): prendido, 15 rutas sin desbordar 360 px y con contraste AAA (7:1) en claro y oscuro: `/`, `/entrar`, `/instalar`, `/invitacion`, `/invitacion-mandato`, `/invitaciones`, `/cuenta`, `/cuenta/segundo-factor`, `/contactos`, `/contactos/gestion`, `/documentos`, `/mandatos`, `/padron`, `/panel` y una ruta inexistente; apagado AA y prendido AAA con 125 % de letra; la elección se recuerda y `prefers-contrast: more` lo prende |

**Hueco de cobertura (no es un defecto medido):** la lectura ampliada con datos no recorre todavía las pantallas nuevas del P0 fuera de `/panel`: liquidación oficial, reclamos, reparaciones, comunicados, presupuesto y ayuda tienen axe AA a 360 px, pero no la pasada AAA del modo lectura ampliada. Queda **SIN VERIFICAR** en ese modo.

**Lo que axe no cubre:** RNF07 pide además el modo verificado con personas mayores y lector de pantalla (PA28). Eso lo hace el propietario con el manual de pruebas con personas (documento 14, §5.1): **SIN VERIFICAR**.

## 2. Lighthouse

**Staging no se puede medir sin credenciales:** `curl https://cem-staging.holancloud.com/` responde `302` a `holancloud.cloudflareaccess.com/cdn-cgi/access/login/…` (Cloudflare Access). Control: desde dentro de la VM, por `https://coolify-proxy` con `Host: cem-staging.holancloud.com`, la misma página da `200` con `<title>Consorcio en Mano</title>`. Lighthouse desde la PC mediría la pantalla de Access.

**Lo que se midió:** la compilación de producción del frontend (`pnpm -C frontend build`, el mismo código `c7c200a`) servida en la PC con `vite preview` en el puerto 4410, **sin backend** (`/api/*` responde 500). Lighthouse 12.8.2 por `npx` (no estaba instalado; no se agregó a `package.json`), Chromium 1200 de Playwright sin pantalla, emulación móvil por defecto: 412 × 823, 4G lenta simulada (150 ms de RTT, 1,6 Mbps) y CPU 4 veces más lenta. Una corrida por pantalla, 23-11-2026 ~05:15 UTC, con el candado de CPU libre. La compilación (`pnpm -C frontend build`, ~1 min) corrió a las ~04:35 UTC con el candado `CI_CORRIENDO` puesto (se comprobó después de lanzarla): no afecta el contenido de `dist/`, sí pudo restarle CPU a la corrida de CI de ese momento.

| Pantalla | Rendimiento | Accesibilidad | Buenas prácticas | FCP | LCP | TBT | CLS |
|---|---|---|---|---|---|---|---|
| `/entrar` | 89 | 100 | 96 | 2,8 s | 3,1 s | 20 ms | 0 |
| `/instalar` | 74 | 100 | 100 | 2,8 s | 3,1 s | 540 ms | 0,063 |
| `/` (sin sesión) | 64 | 100 | 96 | 2,9 s | 3,1 s | 1150 ms | 0,036 |

- **PWA:** Lighthouse 12 ya no tiene la categoría PWA. La instalabilidad la prueba `e2e/pwa.e2e.ts` contra la imagen (manifiesto, iconos, service worker que controla la página y Chromium que la da por instalable): pasó en los tres proyectos.
- **Buenas prácticas:** `errors-in-console` en `/entrar` y `/` son los 500 de `/api/*` sin backend (propios de esta medición); `valid-source-maps` falla en las tres (la compilación no publica mapas de fuente).
- **RNF02** («pantalla útil en 3 s» en un móvil de referencia con dispositivo y red registrados): con la emulación de Lighthouse el LCP da **3,1 s** en las tres pantallas, apenas por encima de 3 s. No es la medición que pide RNF02 (dispositivo y red reales registrados), y `vite preview` no es el nginx de la imagen (sin sus cabeceras de caché): **RNF02 queda SIN VERIFICAR**; el dato sirve de referencia.
- **Pantallas con sesión** (inicio con datos, Mis expensas, panel, liquidación oficial, comunicados): no se midieron con Lighthouse (necesitan backend y sesión): **SIN VERIFICAR**. Su accesibilidad automática la cubre axe en la suite (sección 1).

## Procedimiento (tarjeta de tarea)

### Tarea A11Y-1: axe y lectura ampliada en la suite de extremo a extremo

| Campo | Valor |
|---|---|
| Efectividad | Rama con el código a medir; verificado con `c7c200a` el 23-11-2026 |
| Quién | Equipo, en la PC con Docker, pnpm y Chromium de Playwright |

#### Precondiciones

| Condición | Cómo se comprueba |
|---|---|
| Candado de CPU libre | `test -e <scratchpad>/CI_CORRIENDO` da código 1 |
| Docker en marcha | `docker info` responde |
| Dependencias instaladas | `pnpm -C frontend install --frozen-lockfile` termina con `Done` |

> **CAUTION:** con `ci.ps1` corriendo, las pruebas pasan sus plazos y fallan por tiempo: esperar el candado.

#### Pasos

1. Correr la suite:
   `pnpm -C "C:\tmp\app-h6b\frontend" e2e --reporter=list`
   - Resultado esperado: `487 passed` (o la cantidad vigente), `0 failed`, `2 skipped`.
   - Si no: anotar el nombre de la prueba y la falta de axe (`id: selector`); detenerse.
2. Contar la lectura ampliada en la salida:
   `grep -c "lectura-ampliada.*ok\|ok.*lectura-ampliada"` sobre la salida guardada.
   - Resultado esperado: 102 (34 por proyecto).
   - Si no: anotar las que faltan.

#### Cierre

- Anotar total, omitidas, fallidas y la cuenta de lectura ampliada en ESTADO.md.

### Tarea A11Y-2: Lighthouse sobre la compilación de producción servida en la PC

| Campo | Valor |
|---|---|
| Efectividad | Igual que A11Y-1 |
| Quién | Equipo, en la PC, con red para `npx` |

#### Precondiciones

| Condición | Cómo se comprueba |
|---|---|
| Candado de CPU libre | Igual que A11Y-1 |
| Puerto libre fuera de 4321 y de 8137–9036 | `netstat -ano \| findstr :4410` no devuelve nada |
| Chromium de Playwright | `C:\Users\holan\AppData\Local\ms-playwright\chromium-1200\chrome-win64\chrome.exe` existe (SIN VERIFICAR en otra PC: la carpeta cambia con la versión) |

#### Advertencias

> **CAUTION:** staging está detrás de Cloudflare Access: Lighthouse sin credenciales mide la pantalla de Access, no CEM. Medir solo la compilación local.

> **CAUTION:** `vite preview` no es el nginx de la imagen (sin las cabeceras de caché y de seguridad de producción) y sin backend la aplicación no tiene sesión: solo valen las pantallas públicas.

#### Pasos

1. Compilar: `pnpm -C "C:\tmp\app-h6b\frontend" build`.
   - Resultado esperado: termina con `dist/sw.js` generado.
2. Servir: `pnpm -C "C:\tmp\app-h6b\frontend" preview --port 4410 --strictPort`.
   - Resultado esperado: `Local: http://localhost:4410/`.
3. Medir cada pantalla (otra terminal):
   `$env:CHROME_PATH = "C:\Users\holan\AppData\Local\ms-playwright\chromium-1200\chrome-win64\chrome.exe"; npx --yes lighthouse@12.8.2 http://localhost:4410/entrar --only-categories=performance,accessibility,best-practices --output=json --output-path=C:\tmp\lh-entrar.json --chrome-flags="--headless=new" --quiet`
   - Resultado esperado: el JSON existe y `categories.*.score` tiene valores entre 0 y 1.
   - Si no: anotar el error de Lighthouse; detenerse.
4. Detener `vite preview` (Ctrl+C en su terminal).
   - Resultado esperado: `netstat -ano | findstr :4410` no devuelve nada.

#### Cierre

- Anotar la versión de Lighthouse, la emulación (móvil, red y CPU de Lighthouse por defecto) y las cuatro cifras por pantalla.
- Borrar los JSON de `C:\tmp\lh-*.json` si no se guardan.
