import type { components } from "../api/tipos";
import { fechaCorta } from "../pagos/textos";
import type { CampoCitado } from "./borradorReclamo";

// Los textos de los reclamos sobre la liquidación (RF34, app 0123), los mismos en «Mis expensas»
// (quien presenta) y en «Reclamos» (quien atiende).

export type Estado = components["schemas"]["EstadoReclamo"];
export type ReclamoResumen = components["schemas"]["ReclamoResumen"];

export const HACER_RECLAMO = "Hacer un reclamo";
export const RECLAMAR = "Reclamar";
export const MIS_RECLAMOS = "Mis reclamos";
export const RETIRAR_RECLAMO = "Retirar el reclamo";
export const RESPONDER_RECLAMO = "Responder";
export const PASAR_A_SOLICITUD = "Pasar a solicitud formal";
export const BORRADOR_SIN_ENVIAR = "Borrador sin enviar";
export const POR_ENVIAR = "Sale al volver la conexión";
export const NO_SE_ENVIO = "No se pudo enviar";
export const LIQUIDACION_REEMPLAZADA = "Liquidación reemplazada";
export const SEGUIR_ESCRIBIENDO = "Seguir escribiendo";
export const ENVIAR_AHORA = "Enviar ahora";
export const DESCARTAR = "Descartar el borrador";
export const CITAR_LA_VIGENTE = "Citar la vigente";
export const SOBRE_LA_REEMPLAZADA = "Reclamar sobre la reemplazada";
export const NO_CAMBIA_IMPORTES = "Un reclamo no cambia ningún importe: queda registrado para que el Consejo o la administración lo atiendan.";
/** PA16: el servidor revalidó el borrador y el vínculo ya no alcanza. */
export const PERMISO_VENCIDO =
  "Esta cuenta ya no figura como propietaria confirmada de esa unidad en el edificio: el reclamo no se envió. El borrador queda guardado en este dispositivo para descartarlo.";
export const CONFLICTO_REEMPLAZADA =
  "La liquidación que cita el borrador fue reemplazada por otra del mismo período. Se puede citar la vigente o reclamar igual sobre la reemplazada.";
export const SIN_LA_VIGENTE = "La liquidación vigente de ese período no tiene esta unidad entre las suyas: se puede reclamar sobre la reemplazada o descartar el borrador.";
export const SIN_GUARDAR = "Este navegador no deja guardar el borrador en el dispositivo: si se cierra la ventana sin enviarlo, lo escrito se pierde.";
export const SIN_BORRADORES = "Este navegador no deja leer los borradores guardados en el dispositivo.";
export const SIN_CONEXION_BORRADOR = "Sin conexión: el reclamo quedó como borrador sin enviar y sale solo cuando vuelva la conexión.";
export const SALIO = "Listo: el reclamo quedó presentado. El Consejo o la administración lo van a atender.";
export const SALIERON = (n: number) => (n === 1 ? "Listo: salió el reclamo que estaba sin enviar." : `Listo: salieron ${n} reclamos que estaban sin enviar.`);
export const SIN_RECLAMOS = "Todavía no hay reclamos.";
/** El 422 `texto_con_cuenta` de un reclamo (el de `MENSAJES` habla de comunicados). */
export const TEXTO_CON_CUENTA = "El texto no puede llevar el número de una cuenta: hay que sacarlo. Si no es una cuenta, separar los números con una coma.";

/** El mensaje de un rechazo de los reclamos: el de su código, salvo el de la cuenta. */
export const motivoDe = (codigo: string | undefined, general: string) => (codigo === "texto_con_cuenta" ? TEXTO_CON_CUENTA : general);

export const TEXTO_ESTADO: Record<Estado, string> = {
  presentado: "Presentado",
  respondido: "Respondido",
  derivado_a_solicitud: "Pasó a solicitud formal",
  retirado: "Retirado",
};

export const TEXTO_CAMPO: Record<CampoCitado, string> = {
  importe_unidad: "Del período: el total y los grupos",
  saldo_unidad: "El saldo: saldo anterior, pagos, deuda e interés",
};

/** «09/2026», como lo dicen los correos. */
export const mesDe = (periodo: string) => `${periodo.slice(5, 7)}/${periodo.slice(0, 4)}`;

export const tituloDe = (r: { designacion: string; periodo: string }) => `Unidad ${r.designacion} · período ${mesDe(r.periodo)}`;

export const resumenDe = (r: ReclamoResumen, conQuien: boolean) =>
  [`Presentado el ${fechaCorta(r.presentado_en)}`, conQuien ? r.presentado_por_nombre : null, r.liquidacion_vigente ? null : LIQUIDACION_REEMPLAZADA]
    .filter((x) => x !== null)
    .join(" · ");
