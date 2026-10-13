import type { ManifestOptions } from "vite-plugin-pwa";

// Manifiesto de la PWA (documento 13, §14.2). Vive aparte para que la prueba lo valide.
export const manifiesto: Partial<ManifestOptions> = {
  name: "Consorcio en Mano",
  short_name: "Consorcio",
  description: "Expensas, documentos y avisos de tu consorcio.",
  lang: "es-AR",
  start_url: "/",
  scope: "/",
  display: "standalone",
  background_color: "#ffffff",
  theme_color: "#1d4e89",
  icons: [
    { src: "icono-192.png", sizes: "192x192", type: "image/png" },
    { src: "icono-512.png", sizes: "512x512", type: "image/png" },
    { src: "icono-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    { src: "icono.svg", sizes: "any", type: "image/svg+xml" },
  ],
};
