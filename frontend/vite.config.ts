/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";
import { VitePWA } from "vite-plugin-pwa";

import { NAVEGACION_ENTRAR } from "./src/enlace";
import { manifiesto } from "./src/manifiesto";
import { ARCHIVO_SW_ENTRAR, codigoSwEntrar } from "./src/swEntrar";

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
        navigateFallback: "/index.html",
        // /entrar va siempre a la red: el enlace del correo carga la versión actual, nunca la
        // que haya quedado en caché, que puede ser anterior a la página (medido el 14-10-2026).
        navigateFallbackDenylist: [/^\/api\//, /^\/health/, NAVEGACION_ENTRAR],
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
