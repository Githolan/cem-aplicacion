// Parámetros de la interfaz. La API es del mismo origen (documento 13, §6.3): no hay URL del backend.

/** Cada cuánto se vuelve a consultar /health mientras la pantalla está abierta. */
export const INTERVALO_CONSULTA_SALUD_MS = 30_000;

/** Reintentos de una consulta fallida antes de mostrar el error. */
export const REINTENTOS_CONSULTA = 1;
