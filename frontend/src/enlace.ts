// El enlace de entrada del correo (documento 13, §10.3): /entrar#token=…. Lo usan la página
// /entrar, la configuración del service worker (vite.config.ts) y su extensión (swEntrar.ts), así
// que vive en un módulo sin React, que también puede importar Node al construir.

export const RUTA_ENTRAR = "/entrar";
export const PARAMETRO_TOKEN = "token";

/** Vigencia del enlace del correo: 15 minutos (backend, `sesiones.VIGENCIA_ENLACE`). */
export const VIGENCIA_ENLACE_MS = 15 * 60 * 1000;

/** El token del fragmento, o null si no hay. */
export function tokenDelFragmento(fragmento: string): string | null {
  return new URLSearchParams(fragmento.replace(/^#/, "")).get(PARAMETRO_TOKEN);
}

/**
 * Navegaciones a /entrar (con o sin consulta), que el service worker nunca responde desde su caché:
 * van a la red, donde nginx sirve el index.html actual sin caché, con sus activos.
 * Workbox compara contra la ruta más la consulta, sin el fragmento.
 */
export const NAVEGACION_ENTRAR = new RegExp(`^${RUTA_ENTRAR}(?:[/?]|$)`);
