// El error junto al campo (tanda 3 del informe de fricción): todo formulario marca el campo con el
// error y dice qué hacer, en vez de un «Revisá los datos del formulario.» arriba. El campo lleva
// `aria-invalid` y `aria-describedby` hacia su mensaje; el resumen nombra los campos marcados.
//
// De dónde salen los mensajes: un 422 de la API trae `detalle.campos` (p. ej. `body.cuit`) y, desde
// la tanda 3, `detalle.mensajes` con qué hacer en cada uno; un error de negocio con campo trae solo
// `detalle.campos`, y su mensaje es el de su código. Las comprobaciones de la pantalla (el CUIT, la
// cuenta) escriben el suyo. Ningún mensaje repite lo que se escribió.

/** Por campo (el último tramo de `body.<campo>`), qué hacer con él. */
export type ErroresDeCampo = Readonly<Record<string, string>>;
export const SIN_ERRORES: ErroresDeCampo = {};

/** Si un 422 no trae el mensaje del campo (una API anterior a la tanda 3). */
export const REVISAR_DATO = "Revisar este dato.";
/** Un 422 que no nombra ningún campo de la pantalla. */
export const REVISAR_FORMULARIO = "Hay que revisar los datos del formulario.";

type Detalle = { campos?: unknown; mensajes?: unknown };

const detalleDe = (error: unknown): Detalle | undefined => (error as { detalle?: Detalle } | undefined)?.detalle;
const campoDe = (ubicacion: string) => ubicacion.split(".").pop() ?? "";

/**
 * Los campos que marcó la API, con su mensaje. `porDefecto` es el mensaje de un campo que viene sin
 * el suyo (el de un error de negocio: el texto de su código). `propios` reemplaza el de la API en
 * un campo cuya única causa posible la pantalla ya conoce (lo demás lo frena el propio campo).
 */
export function erroresDe(error: unknown, porDefecto = REVISAR_DATO, propios: Readonly<Record<string, string>> = {}): ErroresDeCampo {
  const detalle = detalleDe(error);
  const campos = Array.isArray(detalle?.campos) ? detalle.campos.filter((c): c is string => typeof c === "string") : [];
  const mensajes = typeof detalle?.mensajes === "object" && detalle.mensajes !== null ? (detalle.mensajes as Record<string, unknown>) : {};
  const errores: Record<string, string> = {};
  for (const ubicacion of campos) {
    if (!ubicacion.startsWith("body.")) continue;
    const campo = campoDe(ubicacion);
    const mensaje = propios[campo] ?? mensajes[ubicacion];
    errores[campo] ??= typeof mensaje === "string" ? mensaje : porDefecto;
  }
  return errores;
}

/** Solo los campos que la pantalla muestra: uno que no está no se puede marcar. */
export function soloDe(errores: ErroresDeCampo, nombres: Readonly<Record<string, string>>): ErroresDeCampo {
  return Object.fromEntries(Object.entries(errores).filter(([campo]) => campo in nombres));
}

/** El resumen del formulario: qué campos corregir, por su nombre en la pantalla. */
export function resumenDe(errores: ErroresDeCampo, nombres: Readonly<Record<string, string>>): string {
  const marcados = Object.keys(errores)
    .map((campo) => nombres[campo])
    .filter((n): n is string => n !== undefined);
  if (marcados.length === 0) return REVISAR_FORMULARIO;
  const lista = marcados.length === 1 ? marcados[0] : `${marcados.slice(0, -1).join(", ")} y ${marcados[marcados.length - 1]}`;
  return `Hay que corregir ${lista}: el motivo está junto a cada campo.`;
}

/** Sin el error de un campo: se corrige y el aviso de ese campo se va. */
export function sinElDe(errores: ErroresDeCampo, campo: string): ErroresDeCampo {
  if (!(campo in errores)) return errores;
  return Object.fromEntries(Object.entries(errores).filter(([nombre]) => nombre !== campo));
}

/** El id del mensaje de un campo, a partir del id del control. */
export const idDelError = (idCampo: string) => `${idCampo}-error`;

/** Lo que lleva el control: marcado y descrito por su mensaje (y por lo que ya lo describía). */
export function marcaDe(idCampo: string, mensaje: string | undefined, describe?: string) {
  const ids = [describe, mensaje === undefined ? undefined : idDelError(idCampo)].filter((i) => i !== undefined);
  return {
    "aria-invalid": mensaje === undefined ? undefined : true,
    "aria-describedby": ids.length === 0 ? undefined : ids.join(" "),
  } as const;
}

/** El mensaje junto al campo. */
export function ErrorDeCampo({ idCampo, mensaje }: { idCampo: string; mensaje: string | undefined }) {
  if (mensaje === undefined) return null;
  return (
    <p className="aviso-error error-de-campo" id={idDelError(idCampo)}>
      {mensaje}
    </p>
  );
}
