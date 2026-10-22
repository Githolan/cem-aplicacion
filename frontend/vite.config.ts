/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";
import { VitePWA } from "vite-plugin-pwa";

import { manifiesto } from "./src/manifiesto";
import { ARCHIVO_SW_ENTRAR, codigoSwEntrar } from "./src/swEntrar";
import { esNavegacionDeLaApp } from "./src/swNavegacion";

// La extensión del service worker (src/swEntrar.ts) sale como archivo propio en dist/; el sw.js
// de Workbox la carga con importScripts. nginx la sirve sin caché, como sw.js.
function extensionSwEntrar(): Plugin {
  return {
    name: "cem-sw-entrar",
    apply: "build",
    generateBundle() {
      this.emitFile({ type: "asset", fileName: ARCHIVO_SW_ENTRAR, source: codigoSwEntrar() });
    },
  };
}

// Documento 13, §14: PWA instalable. El service worker solo precachea el shell; nunca
// sirve /api ni /health desde caché (RN25: nada de dinero ni votos fuera de línea).
export default defineConfig({
  plugins: [
    react(),
    extensionSwEntrar(),
    VitePWA({
      // "prompt": la versión nueva espera a que la persona acepte el aviso (§14.4), así no se
      // corta un formulario a medio completar.
      registerType: "prompt",
      injectRegister: false,
      includeAssets: ["icono.svg"],
      manifest: manifiesto,
      workbox: {
        globPatterns: ["**/*.{js,css,html,svg,png,webmanifest}"],
        // La extensión se carga con importScripts; no hace falta además en la precaché.
        globIgnores: [ARCHIVO_SW_ENTRAR],
        importScripts: [`/${ARCHIVO_SW_ENTRAR}`],
        // Las navegaciones van a la red (src/swNavegacion.ts: por qué, y qué rutas quedan afuera).
        // Si la red falla, cualquier ruta recibe el index.html de la precaché, que es el de la
        // versión instalada. No se guarda ninguna navegación: una caché propia sobreviviría a la
        // actualización y, sin conexión, podría servir el HTML de una versión anterior (Codex en
        // #103). Sin plazo de espera: una red lenta espera a la red. Una redirección de
        // Cloudflare Access pasa al navegador.
        navigateFallback: null,
        // La precaché traduce "/" a "/index.html" y lo responde de la caché antes de llegar a la
        // regla de navegación: "/" (la entrada más común) quedaba igual que antes (medido con la
        // prueba de Access de e2e/pwa.e2e.ts). Sin esa traducción, "/" va por la regla de abajo.
        directoryIndex: null,
        runtimeCaching: [
          {
            urlPattern: esNavegacionDeLaApp,
            handler: "NetworkOnly",
            options: { precacheFallback: { fallbackURL: "/index.html" } },
          },
        ],
        cleanupOutdatedCaches: true,
      },
    }),
  ],
  server: {
    // En desarrollo, el backend local responde en el 8000 (README del backend).
    proxy: { "/api": "http://127.0.0.1:8000", "/health": "http://127.0.0.1:8000" },
  },
  test: {
    environment: "jsdom",
  },
});
