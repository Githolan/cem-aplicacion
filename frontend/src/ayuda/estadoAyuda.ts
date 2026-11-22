import { borrarDeLaPestana, guardarEnLaPestana, leerDeLaPestana } from "../almacenDeLaPestana";
import { RUTA_INICIO } from "../enlace";

// Ayuda por una persona autorizada (RF53, app 0127). Quien ayuda entra con su propia cuenta y elige
// «Ayudar a …» en /ayuda. Desde ahí, y solo en esta pestaña, cada pedido al edificio de la ayuda
// lleva el encabezado con la autorización, y el servidor responde como si fuera la persona asistida,
// con lo que la autorización alcanza: el menú, las pantallas y los botones salen de lo que el servidor
// dice que se puede (`mis-acciones`), así que lo decisorio no se ofrece. «Dejar de ayudar» lo quita.
// Empezar y dejar de ayudar recargan la aplicación en el inicio: nada leído como una persona queda en
// la pantalla de la otra.

export const ENCABEZADO_AYUDA = "X-CEM-Ayuda";
/** La página de la ayuda: autorizar, revocar y empezar a ayudar (`Ayuda.tsx`). */
export const RUTA_AYUDA = "/ayuda";
const CLAVE = "cem:ayuda";
/** A quién se ayudaba cuando la autorización se revocó: el aviso lo dice una vez, al volver al inicio. */
const CLAVE_TERMINADA = "cem:ayuda-terminada";
export const AYUDA_REVOCADA = "ayuda_revocada";
const SIN_PERMISO = 403;
const SELECTOR = "/api/v1/me/consorcios";

export type Ayuda = {
  autorizacion: string;
  consorcio: string;
  edificio: string;
  asistida: string;
};

function esAyuda(x: unknown): x is Ayuda {
  if (typeof x !== "object" || x === null) return false;
  const a = x as Record<string, unknown>;
  return ["autorizacion", "consorcio", "edificio", "asistida"].every((k) => typeof a[k] === "string");
}

/** La ayuda en curso en esta pestaña, o null. Lo guardado que no tiene la forma se descarta. */
export function ayudaEnCurso(): Ayuda | null {
  const texto = leerDeLaPestana(CLAVE);
  if (texto === null) return null;
  let valor: unknown;
  try {
    valor = JSON.parse(texto);
  } catch {
    borrarDeLaPestana(CLAVE);
    return null;
  }
  if (esAyuda(valor)) return valor;
  borrarDeLaPestana(CLAVE);
  return null;
}

/** Las salidas que recargan la página, aparte para poder observarlas en las pruebas. */
export const recargas = {
  aInicio: () => window.location.replace(RUTA_INICIO),
};

/** Empieza a ayudar. false si el navegador no deja guardar en la pestaña: entonces no se puede. */
export function empezarAAyudar(ayuda: Ayuda): boolean {
  guardarEnLaPestana(CLAVE, JSON.stringify(ayuda));
  if (ayudaEnCurso()?.autorizacion !== ayuda.autorizacion) return false;
  recargas.aInicio();
  return true;
}

export function dejarDeAyudar(): void {
  borrarDeLaPestana(CLAVE);
  recargas.aInicio();
}

/** Sin recargar: al salir de la cuenta o cuando entra otra persona. */
export function olvidarAyuda(): void {
  borrarDeLaPestana(CLAVE);
  borrarDeLaPestana(CLAVE_TERMINADA);
}

/** El mismo pedido con el encabezado de la ayuda, si va al edificio de la ayuda o al selector. */
export function conAyuda(pedido: Request): Request {
  const ayuda = ayudaEnCurso();
  if (ayuda === null) return pedido;
  const ruta = new URL(pedido.url).pathname;
  if (ruta !== SELECTOR && !ruta.startsWith(`/api/v1/consorcios/${ayuda.consorcio}/`)) return pedido;
  const encabezados = new Headers(pedido.headers);
  encabezados.set(ENCABEZADO_AYUDA, ayuda.autorizacion);
  return new Request(pedido, { headers: encabezados });
}

/** De quién son los borradores de reclamos (RF38): ayudando, de quien ayuda para esa autorización. */
export function autorDeBorradores(persona: string): string {
  const ayuda = ayudaEnCurso();
  return ayuda === null ? persona : `${persona}:ayuda:${ayuda.autorizacion}`;
}

/** Si la respuesta dice que la persona asistida revocó la autorización (`ayuda_revocada`), la
 * pestaña sale sola de la ayuda: se olvida, queda a quién se ayudaba para el aviso, y vuelve al
 * inicio con la cuenta propia. Cualquier otra respuesta sigue igual. */
export async function vigilarRevocacion(pedido: Request, respuesta: Response): Promise<Response> {
  if (respuesta.status !== SIN_PERMISO || !pedido.headers.has(ENCABEZADO_AYUDA)) return respuesta;
  if (!(respuesta.headers.get("content-type") ?? "").includes("application/json")) return respuesta;
  const cuerpo = (await respuesta.clone().json()) as { codigo?: unknown };
  const ayuda = ayudaEnCurso();
  if (cuerpo.codigo !== AYUDA_REVOCADA || ayuda === null) return respuesta;
  borrarDeLaPestana(CLAVE);
  guardarEnLaPestana(CLAVE_TERMINADA, ayuda.asistida);
  recargas.aInicio();
  return respuesta;
}

/** A quién se ayudaba si la autorización se acaba de revocar, o null. */
export function ayudaTerminada(): string | null {
  return leerDeLaPestana(CLAVE_TERMINADA);
}

/** El aviso de la revocación se dice una vez: ya mostrado, se olvida. */
export function olvidarAyudaTerminada(): void {
  borrarDeLaPestana(CLAVE_TERMINADA);
}
