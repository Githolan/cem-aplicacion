/**
 * Qué decir cuando un pedido no salió y no hay un mensaje propio para su estado (E34).
 *
 * Si respondió la aplicación (el cuerpo uniforme trae `codigo`) o el borde avisó 503, el problema
 * es del servicio y no de la conexión de quien usa la página: decirle que revise su conexión la
 * manda a buscar donde no está. Solo un pedido que no tuvo respuesta, o una respuesta sin cuerpo
 * de la aplicación, se explica como conexión.
 */
export const SIN_CONEXION = "No pudimos completar el pedido. Revisá la conexión y probá de nuevo.";
export const SERVICIO_NO_DISPONIBLE = "El servicio no está disponible en este momento. Probá de nuevo en unos minutos.";

const ESTADO_NO_DISPONIBLE = 503;

export function mensajeDeFalla(estado: number, codigo: string | undefined): string {
  return estado === ESTADO_NO_DISPONIBLE || codigo !== undefined ? SERVICIO_NO_DISPONIBLE : SIN_CONEXION;
}

/** El `codigo` del cuerpo uniforme de error, si la respuesta lo trae. */
export function codigoDe(error: unknown): string | undefined {
  return (error as { codigo?: string } | undefined)?.codigo;
}
