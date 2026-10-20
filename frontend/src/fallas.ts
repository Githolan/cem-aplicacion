/**
 * Qué decir cuando un pedido no salió y no hay un mensaje propio para su estado (E34).
 *
 * Un 5xx con el cuerpo uniforme de la aplicación (`codigo`), o un 503 del borde, es un problema del
 * servicio y no de la conexión de quien usa la página: decirle que revise su conexión la manda a
 * buscar donde no está. Un 429 es el límite de pedidos. Un 4xx de la aplicación sin mensaje propio
 * en la pantalla (p. ej. un 404 porque perdió el acceso al edificio con la página abierta) pide
 * recargar. Solo un pedido que no tuvo respuesta, o una respuesta sin cuerpo de la aplicación, se
 * explica como conexión.
 */
export const SIN_CONEXION = "No pudimos completar el pedido. Revisá la conexión y probá de nuevo.";
export const SERVICIO_NO_DISPONIBLE = "El servicio no está disponible en este momento. Probá de nuevo en unos minutos.";
export const LIMITE_DE_PEDIDOS = "Llegaste al límite de pedidos por ahora. Probá más tarde.";
export const RECARGAR = "No pudimos completar el pedido. Recargá la página y probá de nuevo.";

const ESTADO_LIMITE = 429;
const ESTADO_ERROR_DEL_SERVIDOR = 500;
const ESTADO_NO_DISPONIBLE = 503;

export function mensajeDeFalla(estado: number, codigo: string | undefined): string {
  if (estado === ESTADO_NO_DISPONIBLE) return SERVICIO_NO_DISPONIBLE;
  if (codigo === undefined) return SIN_CONEXION;
  if (estado === ESTADO_LIMITE) return LIMITE_DE_PEDIDOS;
  if (estado >= ESTADO_ERROR_DEL_SERVIDOR) return SERVICIO_NO_DISPONIBLE;
  return RECARGAR;
}

/**
 * Lo mismo para las páginas que se abren con el enlace de un correo (E52). El token sale de la
 * dirección al abrirla, así que recargar no sirve: hay que volver a abrir el enlace.
 */
export const SIN_CONEXION_ENLACE =
  "No pudimos completar el pedido. Revisá la conexión y volvé a abrir el enlace en un rato.";
export const REABRIR_ENLACE =
  "No pudimos completar el pedido. Volvé a abrir el enlace desde el correo y probá de nuevo.";

export function mensajeDeFallaConEnlace(estado: number, codigo: string | undefined): string {
  const mensaje = mensajeDeFalla(estado, codigo);
  if (mensaje === SIN_CONEXION) return SIN_CONEXION_ENLACE;
  if (mensaje === RECARGAR) return REABRIR_ENLACE;
  return mensaje;
}

/** Si la falla fue de la red (sin respuesta de la aplicación) y no del servicio. */
export function esDeConexion(estado: number, codigo: string | undefined): boolean {
  return mensajeDeFalla(estado, codigo) === SIN_CONEXION;
}

/** El `codigo` del cuerpo uniforme de error, si la respuesta lo trae. */
export function codigoDe(error: unknown): string | undefined {
  return (error as { codigo?: string } | undefined)?.codigo;
}
