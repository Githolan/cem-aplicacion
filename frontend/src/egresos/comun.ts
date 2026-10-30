import type { components } from "../api/tipos";
import { LIMITE_DE_PEDIDOS, mensajeDeFalla } from "../fallas";

// Lo común a las pantallas de egresos (H3; `doc/diseno/H3_Proveedor_Beneficiario_Cuenta.md` del
// repositorio CEM, «Pantallas», aprobadas el 30-10-2026). La interfaz nunca concede ni deduce
// permisos: la API decide y la base vuelve a controlar.

export type EstadoOrden = components["schemas"]["EstadoOrdenPago"];
export type Franja = components["schemas"]["Franja"];
export type Condicion = components["schemas"]["CondicionBeneficiario"];
export type EstadoProveedor = components["schemas"]["EstadoProveedor"];

export const ESTADO = {
  sinSesion: 401,
  sinPermiso: 403,
  noEncontrado: 404,
  conflicto: 409,
  invalido: 422,
  limite: 429,
  errorDelServidor: 500,
} as const;
export const ENCABEZADO_IDEMPOTENCIA = "Idempotency-Key" as const;

/** R10: falta verificar el segundo factor en esta sesión (se pide el código y se reintenta). */
export const SEGUNDO_FACTOR_REQUERIDO = "segundo_factor_requerido";
/** R10: la sesión tiene más de 12 horas (hay que volver a entrar). */
export const SESION_RECIENTE_REQUERIDA = "sesion_reciente_requerida";
const CAMBIO_LA_ORDEN = new Set(["version_desactualizada", "huella_distinta"]);

export const TEXTO_ESTADO_ORDEN: Record<EstadoOrden, string> = {
  borrador: "Borrador",
  en_revision: "En revisión",
  rechazada: "Rechazada",
  aprobada: "Aprobada",
  en_ejecucion: "En ejecución",
  en_verificacion: "En verificación",
  pagada: "Pagada",
  fallida: "Fallida",
  conciliada: "Conciliada",
};

export const TEXTO_FRANJA: Record<Franja, string> = {
  fondo_reserva: "Fondo de reserva: aprueba el Consejo",
  beneficiario_nuevo: "Proveedor nuevo: pide aprobación",
  cuenta_cambiada: "Cuenta cambiada: pide aprobación",
  rutinaria: "Rutinaria: la respalda un contrato confirmado",
  sin_umbral: "Sin umbral registrado: pide aprobación",
  sobre_umbral: "Supera el umbral: pide aprobación",
  bajo_umbral: "No supera el umbral",
};

export const TEXTO_CONDICION: Record<Condicion, string> = {
  nuevo: "Proveedor nuevo",
  cuenta_cambiada: "Cuenta cambiada",
  conocido: "Conocido",
};

export const TEXTO_ESTADO_PROVEEDOR: Record<EstadoProveedor, string> = {
  activo: "Activo",
  suspendido: "Suspendido",
  baja: "De baja",
};

/** Una cuenta en las listas: solo los últimos 4 dígitos (la completa, en la revisión). */
export function cuentaEnmascarada(cuenta: string | null): string {
  return cuenta === null ? "Sin cuenta" : `…${cuenta.slice(-4)}`;
}

/** Un importe en centavos, con la moneda. */
export function importe(centavos: number, moneda: string): string {
  return new Intl.NumberFormat("es-AR", { style: "currency", currency: moneda }).format(centavos / 100);
}

/** Pesos escritos por la persona («1.234,50» o «1234.5») a centavos; null si no es un importe. */
export function aCentavos(texto: string): number | null {
  const limpio = texto.trim().replace(/\s/g, "");
  if (!/^\d{1,3}(\.\d{3})*(,\d{1,2})?$|^\d+([.,]\d{1,2})?$/.test(limpio)) return null;
  const normal = /,/.test(limpio) ? limpio.replace(/\./g, "").replace(",", ".") : limpio;
  const centavos = Math.round(Number(normal) * 100);
  return Number.isSafeInteger(centavos) && centavos > 0 ? centavos : null;
}

/** Centavos a lo que se escribe en el campo («1234,50»). */
export function aTexto(centavos: number): string {
  const enteros = Math.trunc(centavos / 100);
  const resto = centavos % 100;
  return resto === 0 ? String(enteros) : `${enteros},${String(resto).padStart(2, "0")}`;
}

const FORMATO_MOMENTO = new Intl.DateTimeFormat("es-AR", { dateStyle: "medium", timeStyle: "short" });
export const momento = (iso: string) => FORMATO_MOMENTO.format(new Date(iso));

/** Si el próximo intento lleva otra clave. Ante R10 se conserva: se reintenta el mismo acto. */
export function renovarClave(estado: number, codigo: string | undefined): boolean {
  if (codigo === SEGUNDO_FACTOR_REQUERIDO || codigo === SESION_RECIENTE_REQUERIDA) return false;
  if (codigo === "idempotencia_reutilizada") return true;
  return estado < ESTADO.errorDelServidor && codigo !== "idempotencia_en_curso";
}

/** Una respuesta que dice que lo que se ve ya no es lo vigente (se vuelve a pedir). */
export function cambioLoVisto(estado: number, codigo: string | undefined): boolean {
  return estado === ESTADO.noEncontrado || (estado === ESTADO.conflicto && codigo !== "idempotencia_en_curso");
}

export function cambioLaOrden(codigo: string | undefined): boolean {
  return codigo !== undefined && CAMBIO_LA_ORDEN.has(codigo);
}

const MENSAJES: Record<string, string> = {
  csrf_invalido: "No pudimos verificar el pedido. Recargá la página.",
  sesion_reciente_requerida: "Tu sesión tiene más de 12 horas: volvé a entrar para seguir.",
  identidad_no_verificada: "Falta verificar una identidad en el consorcio. Soporte puede ayudarte.",
  version_desactualizada: "La orden cambió mientras la mirabas. Revisá la versión nueva.",
  huella_distinta: "La orden cambió mientras la mirabas. Revisá la versión nueva antes de aprobar.",
  sin_cambios: "No hay ningún cambio para guardar.",
  orden_no_admite: "La orden ya no admite eso en su estado. Revisá la lista actualizada.",
  aprobacion_repetida: "Ya aprobaste esta versión.",
  no_es_quien_preparo: "Quien preparó la orden no la aprueba ni la rechaza.",
  no_es_quien_cargo_la_cuenta: "Quien cargó la cuenta del proveedor no aprueba un pago a esa cuenta.",
  no_es_quien_aprobo: "Quien aprobó la orden no la emite.",
  proveedor_inexistente: "Ese proveedor no existe en este edificio.",
  proveedor_inactivo: "El proveedor está suspendido o de baja: no recibe órdenes nuevas.",
  proveedor_cambiado: "El proveedor cambió después de preparar la orden: modificala para que vuelva a revisión.",
  proveedor_sin_cuenta: "El proveedor no tiene cuenta destino: cargala antes de emitir.",
  cuit_invalido: "El CUIT no es válido.",
  cuit_repetido: "Ya hay un proveedor con ese CUIT en este edificio.",
  documento_invalido: "El documento no existe en este edificio o no está vigente.",
  aprobacion_vencida: "La aprobación tiene más de 7 días: renová la orden para que vuelva a revisión.",
  aprobacion_vigente: "La aprobación todavía rige: no hay que renovarla.",
  aprobaciones_insuficientes: "La versión no tiene las aprobaciones que pide.",
  contrato_no_vigente: "El contrato que respaldaba la orden ya no rige: renovala; la versión nueva va sin contrato y pide aprobación.",
  contrato_no_aplica: "El contrato elegido no cubre esta orden (no rige, es de otro proveedor o moneda, o el importe supera su máximo). Elegí otro o ninguno.",
  idempotencia_en_curso: "El pedido se está procesando. Probá de nuevo en unos segundos.",
  idempotencia_reutilizada: "Cambiaste los datos de un intento anterior. Volvé a enviar el formulario.",
};

/** Qué decir ante una respuesta de error de una acción o una lectura de egresos. */
export function mensajeDeError(estado: number, codigo: string | undefined): string {
  const conocido = codigo === undefined ? undefined : MENSAJES[codigo];
  if (conocido !== undefined) return conocido;
  if (estado === ESTADO.sinPermiso) return "No tenés la facultad para esto en este edificio.";
  if (estado === ESTADO.noEncontrado) return "Eso ya no está en este edificio. Revisá la lista actualizada.";
  if (estado === ESTADO.invalido) return "Revisá los datos del formulario.";
  if (estado === ESTADO.limite) return LIMITE_DE_PEDIDOS;
  return mensajeDeFalla(estado, codigo);
}
