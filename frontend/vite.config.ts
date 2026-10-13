/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

import { manifiesto } from "./src/manifiesto";

// Documento 13, §14: PWA instalable. El service worker solo precachea el shell; nunca
// sirve /api ni /health desde caché (RN25: nada de dinero ni votos fuera de línea).
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      // "prompt": la versión nueva espera a que la persona acepte el aviso (§14.4), así no se
      // corta un formulario a medio completar.
      registerType: "prompt",
      injectRegister: false,
      includeAssets: ["icono.svg"],
      manifest: manifiesto,
      workbox: {
        globPatterns: ["**/*.{js,css,html,svg,png,webmanifest}"],
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [/^\/api\//, /^\/health/],
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
