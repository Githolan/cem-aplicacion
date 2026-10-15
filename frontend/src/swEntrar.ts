import { PARAMETRO_TOKEN, RUTA_ENTRAR } from "./enlace";

// Extensión del service worker que genera Workbox (vite.config.ts la agrega con importScripts).
//
// El problema (medido en staging el 14-10-2026): un navegador con una versión anterior en caché
// abre /entrar#token=… del correo; el service worker viejo responde con el index.html de su
// caché, la app vieja no conoce /entrar y la persona queda en "No encontramos esa página" con el
// token sin canjear. Ese service worker viejo no tiene este código, pero en esa misma visita el
// navegador baja el sw.js nuevo (nginx lo sirve sin caché) y lo instala. Esta extensión hace que,
// si alguna ventana está abriendo un enlace de entrada que nadie leyó todavía (el fragmento con
// el token sigue en la URL), la versión nueva se active sin esperar el aviso y esa ventana vuelva
// a abrir su misma URL, ya con la versión nueva.
//
// Documento 13, §14.4: la versión nueva espera a que la persona la acepte para no cortar un
// formulario a medio completar. Acá no hay formulario: la ventana está parada en un enlace sin
// canjear. Las demás pestañas pasan a estar controladas por el service worker nuevo, pero no se
// recargan: con este código (ProveedorVersion), solo recarga la pestaña que pidió la versión;
// las de versiones anteriores recargan si ya mostraban el aviso (comportamiento de
// vite-plugin-pwa al cambiar de controlador). La página /entrar nueva saca el token de la URL
// apenas lo lee, en su primer efecto, así que con ella esto solo se dispara si una versión espera
// justo en ese instante; entonces la recarga vuelve a leer el token del fragmento.

export const ARCHIVO_SW_ENTRAR = "sw-entrar.js";

/** Cada cuánto la versión en espera vuelve a mirar si alguna ventana abre un enlace sin leer. */
const INTERVALO_MS = 250;

/** El código del archivo, con las constantes de enlace.ts (una sola fuente). */
export function codigoSwEntrar(): string {
  return `// Generado al construir desde src/swEntrar.ts: no editar.
"use strict";
const RUTA_ENTRAR = ${JSON.stringify(RUTA_ENTRAR)};
const PARAMETRO_TOKEN = ${JSON.stringify(PARAMETRO_TOKEN)};
const INTERVALO_MS = ${INTERVALO_MS};

// Una URL que no se puede leer no es un enlace de entrada.
function abreEnlaceSinLeer(url) {
  if (!URL.canParse(url)) return false;
  const u = new URL(url);
  return u.pathname === RUTA_ENTRAR && new URLSearchParams(u.hash.slice(1)).has(PARAMETRO_TOKEN);
}

function ventanasConEnlace(incluirSinControlar) {
  return self.clients
    .matchAll({ type: "window", includeUncontrolled: incluirSinControlar })
    .then((ventanas) => ventanas.filter((ventana) => abreEnlaceSinLeer(ventana.url)));
}

// El navegador busca la versión nueva en paralelo con la navegación al enlace: cuando empieza a
// instalarla, la ventana del enlace puede no existir todavía (medido con Chromium: a veces sí, a
// veces no). Por eso no se mira una sola vez: mientras esta versión no esté activa, se vuelve a
// mirar cada INTERVALO_MS. No hace falta un tope: el navegador detiene un service worker ocioso
// (unos 30 s en Chromium) y con él los temporizadores.
// Si una revisión falla (por ejemplo, una ventana a mitad de crearse con una URL que no se puede
// leer), se informa en la consola del service worker y se sigue vigilando: cortar la vigilancia
// por una ventana ajena dejaría a la del enlace sin la versión nueva.
let activo = false;
function vigilarEnlaces() {
  if (activo) return;
  ventanasConEnlace(true).then(
    (ventanas) => {
      if (ventanas.length > 0) return self.skipWaiting();
      setTimeout(vigilarEnlaces, INTERVALO_MS);
    },
    (error) => {
      console.error("sw-entrar: no pude revisar las ventanas abiertas", error);
      setTimeout(vigilarEnlaces, INTERVALO_MS);
    },
  );
}

self.addEventListener("install", vigilarEnlaces);

// Al activarse, las ventanas de este alcance ya están controladas por este service worker: la que
// abre el enlace vuelve a navegar a su URL completa (ruta y fragmento), ahora con la versión nueva.
// No se espera la navegación dentro de waitUntil: esa navegación pasa por este mismo service
// worker, que recién la atiende cuando termina de activarse.
self.addEventListener("activate", () => {
  activo = true;
  ventanasConEnlace(false).then((ventanas) =>
    Promise.all(ventanas.map((ventana) => ventana.navigate(ventana.url))),
  );
});
`;
}
