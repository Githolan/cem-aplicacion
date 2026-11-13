/**
 * Qué decir cuando un pedido no salió y no hay un mensaje propio para su estado (E34).
 *
 * Un 5xx, de la aplicación o del borde, es un problema del servicio y no de la conexión de quien usa
 * la página: decirle que revise su conexión la manda a buscar donde no está. Un 429 es el límite de
 * pedidos. Un 4xx sin mensaje propio en la pantalla (p. ej. un 404 porque perdió el acceso al
 * edificio con la página abierta) pide recargar. Solo un pedido que no tuvo respuesta (estado 0) se
 * explica como conexión.
 */
export const SIN_CONEXION = "No se pudo completar el pedido. Hay que revisar la conexión y reintentar.";
export const SERVICIO_NO_DISPONIBLE = "El servicio no está disponible en este momento. Reintentar en unos minutos.";
export const LIMITE_DE_PEDIDOS = "Se llegó al límite de pedidos por ahora. Reintentar más tarde.";
export const RECARGAR = "No se pudo completar el pedido. Hay que recargar la página y reintentar.";

const SIN_RESPUESTA = 0;
const ESTADO_LIMITE = 429;
const ESTADO_ERROR_DEL_SERVIDOR = 500;

/** Una acción sensible pide una entrada reciente. Lo resuelve la hoja de entrar encima. */
export const CODIGO_ENTRAR_DE_NUEVO = "reautenticacion_requerida";
export const ENTRAR_DE_NUEVO = "Por seguridad, hay que volver a entrar y hacerlo apenas se entra.";

export function mensajeDeFalla(estado: number, codigo: string | undefined): string {
  if (estado === SIN_RESPUESTA) return SIN_CONEXION;
  if (codigo === CODIGO_ENTRAR_DE_NUEVO) return ENTRAR_DE_NUEVO;
  if (estado === ESTADO_LIMITE) return LIMITE_DE_PEDIDOS;
  if (estado >= ESTADO_ERROR_DEL_SERVIDOR) return SERVICIO_NO_DISPONIBLE;
  return RECARGAR;
}

/**
 * Lo mismo para las páginas que se abren con el enlace de un correo (E52). El token sale de la
 * dirección al abrirla, así que recargar no sirve: hay que volver a abrir el enlace.
 */
export const SIN_CONEXION_ENLACE =
  "No se pudo completar el pedido. Hay que revisar la conexión y abrir el enlace de nuevo en un rato.";
export const REABRIR_ENLACE =
  "No se pudo completar el pedido. Hay que abrir de nuevo el enlace del correo.";

export function mensajeDeFallaConEnlace(estado: number, codigo: string | undefined): string {
  const mensaje = mensajeDeFalla(estado, codigo);
  if (mensaje === SIN_CONEXION) return SIN_CONEXION_ENLACE;
  if (mensaje === RECARGAR) return REABRIR_ENLACE;
  return mensaje;
}

/** Si la falla fue de la red (sin respuesta) y no del servicio. */
export function esDeConexion(estado: number, codigo: string | undefined): boolean {
  return mensajeDeFalla(estado, codigo) === SIN_CONEXION;
}

/** El `codigo` del cuerpo uniforme de error, si la respuesta lo trae. */
export function codigoDe(error: unknown): string | undefined {
  return (error as { codigo?: string } | undefined)?.codigo;
}

/** Cuando la separación de funciones frena un acto (`ya_participo`), el servidor dice el motivo
 * concreto a quien ya participa («La preparó esta cuenta: la aprueba otra persona del Consejo.»,
 * pregunta 10 del propietario, 13-11-2026) y se dice tal cual. Con otro error, nada. */
export function motivoDeSeparacion(error: unknown): string | undefined {
  return codigoDe(error) === "ya_participo" ? (error as { mensaje: string }).mensaje : undefined;
}
