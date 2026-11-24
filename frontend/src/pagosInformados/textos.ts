import type { components } from "../api/tipos";
import { aCentavos, importe, mensajeDeActo, momento } from "../egresos/comun";
import { codigoDe, motivoDeSeparacion } from "../fallas";

// Los textos de los pagos informados (PI-S6 del diseño «Pagos informados» del repositorio CEM, app
// 0129 a 0134): la misma palabra en la lista, en la ficha, en las ventanas y en el inicio.

export type PagoInformado = components["schemas"]["PagoInformado"];
export type ListaPagos = components["schemas"]["ListaPagosInformados"];
export type PagoPorCorreo = components["schemas"]["PagoPorCorreo"];
export type Medio = components["schemas"]["MedioPago"];
export type EstadoPago = components["schemas"]["EstadoPagoInformado"];
export type Cotejo = components["schemas"]["CotejoPagosInformados"];
export type UnidadCotejada = components["schemas"]["UnidadCotejada"];

export const RUTA_PAGOS_INFORMADOS = "/pagos-informados";
export const RUTA_COTEJO_PAGOS_INFORMADOS = "/pagos-informados/cotejo";
export const TITULO_PAGOS_INFORMADOS = "Pagos informados";
export const TITULO_COTEJO = "Cotejo con la liquidación";

export const INFORMAR = "Informar un pago";
export const CORREGIR = "Corregir el pago";
export const COMPLETAR = "Completar e informar";
export const CONFIRMAR = "Confirmar el pago";
export const RECHAZAR = "Rechazar el pago";
export const ANULAR = "Anular la confirmación";
export const DESCARTAR = "Descartar el archivo";
export const VER_COMPROBANTE = "Ver el comprobante";
export const VER_ARCHIVO = "Ver el archivo";
export const VER_COTEJO = "Comparar con la liquidación";
export const POR_CONFIRMAR = "Por confirmar";
export const POR_CORREO = "Recibidos por correo, para completar";
export const DECIDIDOS = "Ver los ya decididos";
export const SIN_PAGOS = "Todavía no hay pagos informados.";

export const TEXTO_ESTADO: Record<EstadoPago, string> = {
  informado: "Por confirmar",
  confirmado: "Confirmado",
  rechazado: "Rechazado",
  anulado: "Anulado",
};

export const TEXTO_MEDIO: Record<Medio, string> = {
  transferencia: "Transferencia",
  deposito: "Depósito",
  efectivo: "Efectivo",
  mercado_pago: "Mercado Pago",
  otro: "Otro",
};

export const MEDIOS: readonly Medio[] = ["transferencia", "deposito", "mercado_pago", "efectivo", "otro"];
/** El único medio que se informa sin comprobante ni número de operación (contrato). */
export const SIN_COMPROBANTE: Medio = "efectivo";

/** La moneda de lo que se informa: el pago y la liquidación están en pesos. */
export const MONEDA_DEL_PAGO = "ARS";
const CODIGO_DE_MONEDA = /^[A-Za-z]{3}$/;

/**
 * El importe leído de un correo, con la moneda que leyó la lectura (puede ser otra que pesos).
 * Sin moneda leída, el número solo: no se supone que son pesos.
 */
export function importeLeido(r: { importe: string; moneda: string | null }): string {
  const centavos = aCentavos(r.importe);
  if (centavos === null || r.moneda === null || !CODIGO_DE_MONEDA.test(r.moneda)) return `${r.importe} (moneda sin leer)`;
  return importe(centavos, r.moneda.toUpperCase());
}

/** El importe leído entra en el campo en pesos solo si la lectura dijo que son pesos. */
export const leidoEnPesos = (r: { importe: string | null; moneda: string | null }) => r.importe !== null && r.moneda?.toUpperCase() === MONEDA_DEL_PAGO;

/** Aviso cuando hay un importe leído que no entra como pesos. */
export const AVISO_MONEDA = "El importe leído no está en pesos o no se leyó la moneda: al completar hay que escribir lo pagado en pesos.";

/** «$ 45.000,00» desde el decimal de la API («45000.00»). */
export function enPesos(decimal: string): string {
  const centavos = aCentavos(decimal);
  return centavos === null ? decimal : importe(centavos, "ARS");
}

/** «12/11/2026» desde «2026-11-12»: la fecha sin hora, sin pasar por la zona del dispositivo. */
export function fecha(dia: string): string {
  const [anio = "", mes = "", d = ""] = dia.split("-");
  return `${d}/${mes}/${anio}`;
}

export const tituloDe = (p: { unidad_designacion: string; importe: string }) => `Unidad ${p.unidad_designacion} · ${enPesos(p.importe)}`;

export const resumenDe = (p: PagoInformado) =>
  [`pagado el ${fecha(p.fecha_pago)}`, TEXTO_MEDIO[p.medio], p.propio ? null : p.informado_por_nombre].filter((x) => x !== null).join(" · ");

export const TEXTO_COMPROBANTE: Record<NonNullable<PagoInformado["comprobante_estado"]>, string> = {
  en_cuarentena: "Lo revisa el antivirus",
  disponible: "Revisado",
  rechazado: "El antivirus lo rechazó",
};

export const cuandoInformado = (p: PagoInformado) => `${p.informado_por_nombre}, ${momento(p.informado_en)}`;

export const tituloCorreo = (r: PagoPorCorreo) => r.asunto ?? "Correo sin asunto";
export const resumenCorreo = (r: PagoPorCorreo) => `llegó ${momento(r.recibido_en)}`;

export const pagoInformado = (ya: boolean) =>
  ya
    ? "Ese pago ya estaba informado: es el mismo, no se registró otro."
    : "Listo: el pago quedó informado. El Consejo lo confirma después de revisar el comprobante.";
export const PAGO_CONFIRMADO = "Listo: el pago quedó confirmado.";
export const PAGO_RECHAZADO = "Listo: el pago quedó rechazado. Quien lo informó ve el motivo y puede corregirlo.";
export const PAGO_ANULADO = "Listo: la confirmación quedó anulada. Quien lo informó ve el motivo y puede corregirlo.";
export const ARCHIVO_DESCARTADO = "Listo: el archivo quedó descartado. No se informó ningún pago.";

/** Los códigos de los pagos informados cuyo `mensaje` del servidor ya dice qué hacer. */
const CON_MENSAJE_DEL_SERVIDOR = new Set([
  "fecha_futura",
  "texto_con_cuenta",
  "comprobante_invalido",
  "comprobante_de_otra_unidad",
  "pago_ya_corregido",
  "pago_informado_decidido",
  "comprobante_en_revision",
  "comprobante_rechazado",
  "correccion_de_pago_invalida",
  "motivo_requerido",
  "motivo_con_cuenta",
  "pago_por_correo_descartado",
  "pago_por_correo_informado",
]);

/** Qué decir ante el rechazo de un acto de pagos informados. */
export function mensajeDePago(estado: number, error: unknown): string {
  const codigo = codigoDe(error);
  if (codigo !== undefined && CON_MENSAJE_DEL_SERVIDOR.has(codigo) && typeof error === "object" && error !== null && "mensaje" in error) {
    const { mensaje } = error;
    if (typeof mensaje === "string" && mensaje !== "") return mensaje;
  }
  return motivoDeSeparacion(error) ?? mensajeDeActo(estado, error);
}

/** El resultado del cotejo de una unidad, en palabras (PI-8: nadie se equivocó, los números difieren). */
export function textoCotejo(u: UnidadCotejada): string {
  const informado = `Lo informado y confirmado suma ${enPesos(u.informado_confirmado)}`;
  if (u.liquidacion == null) return `${informado}; la liquidación no trae el dato de esta unidad.`;
  if (u.resultado === "coincide") return `${informado}; la liquidación dice lo mismo.`;
  if (u.sin_pagos_informados) return `La liquidación dice ${enPesos(u.liquidacion)} y no hay pagos informados y confirmados de esta unidad.`;
  return `${informado}; la liquidación dice ${enPesos(u.liquidacion)}.`;
}

export const TEXTO_RESULTADO: Record<UnidadCotejada["resultado"], string> = {
  coincide: "Coincide",
  difiere: "Para revisar",
  sin_datos: "Sin el dato",
};

/** «11/2026» desde «2026-11-01». */
export const mesDe = (periodo: string) => `${periodo.slice(5, 7)}/${periodo.slice(0, 4)}`;
