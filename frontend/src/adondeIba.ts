import { RUTA_ENTRAR, RUTA_INSTALAR, RUTA_INVITACION, RUTA_INVITACION_MANDATO } from "./enlace";

// Adónde iba la persona cuando un 401 la mandó a /entrar (decisión 7-11-2026, punto 13). Pasa con una
// sesión que este navegador nunca leyó: un enlace abierto con la sesión vencida, por ejemplo. Sin
// esto, entrar siempre la dejaba en el inicio.
//
// Va en sessionStorage: es de esta pestaña y se usa una sola vez. Lo guardado se vuelve a mirar al
// leerlo, porque se navega hacia ahí: solo una ruta de este origen, nunca una dirección de otro.
// El fragmento no se guarda: en los enlaces de la aplicación es donde viaja un token.

const CLAVE = "cem.entrar.adonde_iba";
/** Las que no se retoman: no son trabajo, son la entrada a la aplicación. */
const SIN_VOLVER: ReadonlySet<string> = new Set([RUTA_ENTRAR, RUTA_INSTALAR, RUTA_INVITACION, RUTA_INVITACION_MANDATO]);

/** La ruta con su consulta, o null si no es una ruta de la aplicación en este origen. */
export function rutaPropia(ruta: string, origen: string): string | null {
  // `//otro` y `/\otro` son direcciones de otro sitio para el navegador, aunque empiecen con «/».
  if (!ruta.startsWith("/") || ruta.startsWith("//") || ruta.includes("\\")) return null;
  const url = new URL(ruta, origen);
  if (url.origin !== origen || SIN_VOLVER.has(url.pathname)) return null;
  return `${url.pathname}${url.search}`;
}

export function guardarAdondeIba() {
  const { pathname, search, origin } = window.location;
  const ruta = rutaPropia(`${pathname}${search}`, origin);
  if (ruta !== null) window.sessionStorage.setItem(CLAVE, ruta);
}

export function olvidarAdondeIba() {
  window.sessionStorage.removeItem(CLAVE);
}

/** Lo guardado, una sola vez. */
export function tomarAdondeIba(): string | null {
  const guardada = window.sessionStorage.getItem(CLAVE);
  olvidarAdondeIba();
  return guardada === null ? null : rutaPropia(guardada, window.location.origin);
}
