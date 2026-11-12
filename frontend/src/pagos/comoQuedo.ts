import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { exigirSesion } from "../egresos/useConsorcio";
import { TEXTO_LE_TOCA_A } from "./textos";

// En qué quedó una orden recién enviada a aprobar, desde la ficha o desde «Guardar y enviar a
// aprobar» (P-10, P-12, 12-11-2026). No se adivina por la franja: se lee la orden, porque la base
// decide si pide aprobación.

type FichaPago = components["schemas"]["FichaPago"];

/** Lo que se sabe si la orden no se pudo volver a leer: que se envió. */
export const TEXTO_ENVIADA = "Listo: la orden se envió a aprobar.";

export function textoComoQuedo(f: FichaPago): string {
  if (f.orden.estado === "aprobada") return "Listo: la orden no pide aprobación y quedó lista para pagar.";
  const quienSigue = f.le_toca_a === null || f.le_toca_a === "vos" ? "" : ` ${TEXTO_LE_TOCA_A[f.le_toca_a]}`;
  return `Listo: la orden quedó esperando aprobación.${quienSigue}`;
}

/** Lee la orden y dice en qué quedó; sin la lectura, solo que se envió. `SinSesion` sube. */
export async function leerComoQuedo(consorcio: string, orden: string): Promise<string> {
  const { data, response } = await api.GET("/api/v1/consorcios/{consorcio_id}/pagos/{orden_id}", {
    params: { path: { consorcio_id: consorcio, orden_id: orden } },
  });
  exigirSesion(response);
  return data === undefined ? TEXTO_ENVIADA : textoComoQuedo(data);
}
