import type { components } from "../api/tipos";
import { fechaCorta } from "../pagos/textos";

// Los textos de las reparaciones a cargo del consorcio (RF19, app 0124), los mismos en «Nueva
// gestión», en «Seguimiento» y en el expediente.

export type Estado = components["schemas"]["EstadoReparacion"];
export type Prioridad = components["schemas"]["Prioridad"];
export type TipoAdjunto = components["schemas"]["TipoAdjunto"];
export type ReparacionResumen = components["schemas"]["ReparacionResumen"];

export const RUTA_REPARACIONES = "/reparaciones";
export const TITULO_REPARACIONES = "Reparaciones";
export const NUEVA_GESTION = "Pedir una reparación";
export const CERRADAS = "Ver las terminadas";
export const SIN_REPARACIONES = "Todavía no hay pedidos de reparación.";
export const ESPACIO_COMUN = "Un espacio común";
export const AGREGAR_FOTO = "Agregar una foto";
export const REGISTRAR_GESTION = "Asignar el responsable";
export const CAMBIAR_GESTION = "Cambiar el responsable o la fecha";
export const PRESUPUESTAR = "Registrar el presupuesto";
export const APROBAR = "Registrar la aprobación";
export const TERMINAR = "Registrar que se hizo";
export const ENLAZAR_GASTO = "Enlazar el gasto y cerrar";
export const NO_REALIZADA = "No se pudo hacer";
export const RETIRAR = "Retirar el pedido";
export const REVISANDO = "Revisando el archivo con el antivirus…";
export const TODAVIA_REVISANDO =
  "El archivo todavía se está revisando con el antivirus. Se puede volver a intentar en un momento: no hace falta elegirlo de nuevo.";
export const RECHAZADO = "El antivirus rechazó el archivo: no se usó. Hay que elegir otro.";
export const GASTO_PENDIENTE =
  "Falta el gasto: llega con la próxima liquidación oficial. Cuando esté confirmada, se enlaza acá y la reparación se cierra.";
export const NO_ES_PAGO =
  "Es una constancia de quién aprobó el presupuesto: en Modo Consejo la administración paga fuera de la plataforma.";
/** El 422 `texto_con_cuenta` de una reparación (el de `MENSAJES` habla de comunicados). */
export const TEXTO_CON_CUENTA = "El texto no puede llevar el número de una cuenta: hay que sacarlo. Si no es una cuenta, separar los números con una coma.";
export const motivoDe = (codigo: string | undefined, general: string) => (codigo === "texto_con_cuenta" ? TEXTO_CON_CUENTA : general);

export const TEXTO_ESTADO: Record<Estado, string> = {
  pedida: "Pedida",
  en_gestion: "En gestión",
  presupuestada: "Presupuestada",
  aprobada: "Aprobada",
  terminada_gasto_pendiente: "Hecha, falta el gasto",
  cerrada: "Cerrada",
  no_realizada: "No se pudo hacer",
  retirada: "Retirada",
};

export const TEXTO_PRIORIDAD: Record<Prioridad, string> = {
  emergencia: "Emergencia",
  urgente: "Urgente",
  normal: "Normal",
};

export const TEXTO_ADJUNTO: Record<TipoAdjunto, string> = {
  foto: "Foto",
  presupuesto: "Presupuesto",
  aprobacion: "Constancia de aprobación",
  factura: "Factura",
};

export const ABIERTAS: readonly Estado[] = ["pedida", "en_gestion", "presupuestada", "aprobada", "terminada_gasto_pendiente"];

export const lugarDe = (r: { designacion?: string | null; lugar?: string | null }) =>
  [r.designacion == null ? ESPACIO_COMUN : `Unidad ${r.designacion}`, r.lugar].filter((x) => x != null && x !== "").join(" · ");

export const tituloDe = (r: { numero: number }) => `Reparación N.º ${r.numero}`;

export const resumenDe = (r: ReparacionResumen, conQuien: boolean) =>
  [lugarDe(r), `pedida el ${fechaCorta(r.pedida_en)}`, conQuien ? r.pedida_por_nombre : null].filter((x) => x !== null).join(" · ");

/** «09/2026». */
export const mesDe = (periodo: string) => `${periodo.slice(5, 7)}/${periodo.slice(0, 4)}`;
