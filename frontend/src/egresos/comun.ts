import type { MutableRefObject } from "react";

import type { components } from "../api/tipos";
import { LIMITE_DE_PEDIDOS, mensajeDeFalla } from "../fallas";

// Lo común a las pantallas de egresos (H3; `doc/diseno/H3_Proveedor_Beneficiario_Cuenta.md` del
// repositorio CEM, «Pantallas», aprobadas el 30-10-2026). Lo que la persona no puede hacer no se le
// ofrece (`permisos.tsx`) y una lectura negada lleva al inicio, pero eso es solo interfaz: la API
// decide cada pedido, la base vuelve a controlar y un 403 al actuar se dice en la pantalla, porque
// la separación de funciones depende del caso.

export type EstadoOrden = components["schemas"]["EstadoOrdenPago"];
export type Franja = components["schemas"]["Franja"];
export type Condicion = components["schemas"]["CondicionBeneficiario"];
export type EstadoProveedor = components["schemas"]["EstadoProveedor"];
type OrdenPago = components["schemas"]["OrdenPago"];

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
/** El tope del título de un documento (`LARGO_MAXIMO_TITULO` de los contratos). */
export const LARGO_TITULO = 200;

/** R10: falta verificar el segundo factor en esta sesión (se pide el código y se reintenta). */
export const SEGUNDO_FACTOR_REQUERIDO = "segundo_factor_requerido";
/** R10: la sesión tiene más de 12 horas (hay que volver a entrar). */
export const SESION_RECIENTE_REQUERIDA = "sesion_reciente_requerida";
const CAMBIO_LA_ORDEN = new Set(["version_desactualizada", "huella_distinta"]);
/** Lo que se anota ante R10 por sesión vieja. `AvisoPantalla` no lo muestra: lo pide la hoja encima. */
export const TEXTO_SESION_VENCIDA = "Tu sesión tiene más de 12 horas: volvé a entrar para seguir.";

/** El resultado de lo último que se hizo o se intentó en una pantalla. */
export type Aviso = { texto: string; ok: boolean };

/** Lo que se dice al repetir sola una lectura que falló. No es algo hecho: si vuelve a fallar, se va. */
export const VOLVIO_LA_RED = "Volvió la conexión. Actualizamos los datos.";

/** Si falla la recarga que sigue a un cambio, queda a la vista lo que sí se hizo. */
export function sinPisarLoHecho(falla: string) {
  return (actual: Aviso | null): Aviso =>
    actual?.ok === true && actual.texto !== VOLVIO_LA_RED ? actual : { texto: falla, ok: false };
}

/** Al volver a pedir se va el error de la lectura anterior; un «Listo» se queda. */
export const sinLaFalla = (actual: Aviso | null): Aviso | null => (actual?.ok === true ? actual : null);

/** Al volver la red se repite la lectura y se dice; un «Listo» que esté a la vista se queda. */
export const conLaRedDeVuelta = (actual: Aviso | null): Aviso =>
  actual?.ok === true ? actual : { texto: VOLVIO_LA_RED, ok: true };

/** La lectura que se repitió volvió a fallar y la pantalla ya lo dice: el aviso de la red se va. */
export const sinElAvisoDeLaRed = (actual: Aviso | null): Aviso | null => (actual?.texto === VOLVIO_LA_RED ? null : actual);

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

const TEXTO_FRANJA: Record<Franja, string> = {
  fondo_reserva: "Fondo de reserva: aprueba el Consejo",
  beneficiario_nuevo: "Proveedor nuevo: pide aprobación",
  cuenta_cambiada: "Cuenta cambiada: pide aprobación",
  rutinaria: "Rutinaria: la respalda un contrato confirmado",
  sin_umbral: "Sin umbral registrado: pide aprobación",
  sobre_umbral: "Supera el umbral: pide aprobación",
  bajo_umbral: "No supera el umbral",
};

const TEXTO_FRANJA_DECIDIDA: Record<Franja, string> = {
  ...TEXTO_FRANJA,
  fondo_reserva: "Fondo de reserva: pasó por el Consejo",
  beneficiario_nuevo: "Proveedor nuevo: pidió aprobación",
  cuenta_cambiada: "Cuenta cambiada: pidió aprobación",
  sin_umbral: "Sin umbral registrado: pidió aprobación",
  sobre_umbral: "Superó el umbral: pidió aprobación",
};

/** Si todavía falta decidir la orden: después, nada de ella queda por revisar. */
export const enDecision = (estado: EstadoOrden) => estado === "borrador" || estado === "en_revision";

/** La franja: con la orden decidida dice lo que pasó, para que no parezca un paso pendiente. */
export function textoFranja(franja: Franja, estado: EstadoOrden): string {
  return enDecision(estado) ? TEXTO_FRANJA[franja] : TEXTO_FRANJA_DECIDIDA[franja];
}

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

/** La alerta de fraccionamiento (punto 9 del 30-10-2026): funcional, sin afirmar intención. */
export const TEXTO_ALERTA_FRACCIONAMIENTO =
  "Posible fraccionamiento: existen órdenes recientes relacionadas que en conjunto superan el umbral o el máximo del contrato.";

/** Si la aprobación de una orden aprobada ya venció (la base lo vuelve a controlar al emitir). */
export function vencida(o: OrdenPago, ahora = Date.now()): boolean {
  return o.estado === "aprobada" && o.aprobacion_vence_en !== null && Date.parse(o.aprobacion_vence_en) <= ahora;
}

/** Una cuenta en las listas: los últimos 4 dígitos, que es lo único que manda el backend fuera
 * de la revisión (decisión del 30-10-2026). */
export function cuentaEnmascarada(ultimos: string | null): string {
  return ultimos === null ? "Sin cuenta" : `…${ultimos}`;
}

/** Un importe en centavos, con la moneda. */
export function importe(centavos: number, moneda: string): string {
  return new Intl.NumberFormat("es-AR", { style: "currency", currency: moneda }).format(centavos / 100);
}

/** Pesos escritos por la persona («1.234,50» o «1234.5») a centavos; null si no es un importe. */
export function aCentavos(texto: string): number | null {
  const limpio = texto.trim().replace(/\s/g, "");
  // Con separador de miles («1.234», «1.234,5»): los puntos agrupan y la coma es decimal.
  // Sin separador de miles («1234», «1234,5», «1234.5»): un solo separador decimal.
  const partes = /^(\d{1,3}(?:\.\d{3})+)(?:,(\d{1,2}))?$/.exec(limpio) ?? /^(\d+)(?:[.,](\d{1,2}))?$/.exec(limpio);
  if (partes === null) return null;
  const [, enteros = "", decimales = ""] = partes;
  // Se arma con los dígitos: un importe no pasa por un número con decimales.
  const centavos = Number(enteros.replace(/\./g, "") + decimales.padEnd(2, "0"));
  return Number.isSafeInteger(centavos) && centavos > 0 ? centavos : null;
}

/** Centavos en el formato del idioma, siempre con los dos decimales («120.000,00»). */
export function importeEnCampo(centavos: number): string {
  return new Intl.NumberFormat("es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(centavos / 100);
}

/** Centavos a lo que se pega en el banco: sin miles y con coma decimal («184350,00»). */
export function importeParaCopiar(centavos: number): string {
  return `${Math.trunc(centavos / 100)},${String(centavos % 100).padStart(2, "0")}`;
}

/** Centavos al importe decimal de la API («1234.50»): sin pasar por un número con decimales. */
export function aDecimal(centavos: number): string {
  return `${Math.trunc(centavos / 100)}.${String(centavos % 100).padStart(2, "0")}`;
}

/** Centavos a lo que se escribe en el campo («1234,50»). */
export function aTexto(centavos: number): string {
  const enteros = Math.trunc(centavos / 100);
  const resto = centavos % 100;
  return resto === 0 ? String(enteros) : `${enteros},${String(resto).padStart(2, "0")}`;
}

const FORMATO_MOMENTO = new Intl.DateTimeFormat("es-AR", { dateStyle: "medium", timeStyle: "short" });
export const momento = (iso: string) => FORMATO_MOMENTO.format(new Date(iso));

/** Otro acto u otros datos llevan otra clave. Repetir el mismo (después de un 5xx, una caída de la
 * red o `idempotencia_en_curso`) es el mismo pedido: conserva la suya para recibir lo guardado, en
 * vez de un error por algo que salió bien. La clave se suelta acá y cuando el intento terminó. */
export function soltarClaveSiCambio(
  clave: MutableRefObject<string | null>,
  ultimoActo: MutableRefObject<string | null>,
  acto: string,
) {
  if (ultimoActo.current !== acto) clave.current = null;
  ultimoActo.current = acto;
}

/** Sobre una versión nueva: lo que la persona cambió queda y lo que no tocó toma el valor nuevo,
 * para que reintentar no deshaga en silencio lo que otro cambió mientras tanto. */
export function sobreLoNuevo<T extends object>(escrito: T, visto: T, nuevo: T): T {
  const resultado = { ...nuevo };
  for (const campo of Object.keys(nuevo) as (keyof T)[]) if (escrito[campo] !== visto[campo]) resultado[campo] = escrito[campo];
  return resultado;
}

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
  sesion_reciente_requerida: TEXTO_SESION_VENCIDA,
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
  cuit_ya_cargado: "Ese proveedor ya tiene CUIT, y un CUIT no cambia.",
  documento_invalido: "El documento no existe en este edificio o no está vigente.",
  aprobacion_vencida: "La aprobación tiene más de 7 días: renová la orden para que vuelva a revisión.",
  aprobacion_vigente: "La aprobación todavía rige: no hay que renovarla.",
  aprobaciones_insuficientes: "La versión no tiene las aprobaciones que pide.",
  contrato_no_vigente: "El contrato que respaldaba la orden ya no rige: renovala; la versión nueva va sin contrato y pide aprobación.",
  contrato_no_aplica: "El contrato elegido no cubre esta orden (no rige, es de otro proveedor o moneda, o el importe supera su máximo). Elegí otro o ninguno.",
  modalidad_riel: "Esta orden se paga por el riel: no se registra a mano.",
  pago_ya_registrado: "El pago de esta orden ya está registrado: falta que el Consejo lo concilie.",
  pago_en_curso: "Otra persona ya vio la cuenta de este pago: esperá su comprobante, para no pagar dos veces.",
  toma_antes_de_tiempo: "Todavía no pasó el plazo para tomar el pago: quien vio la cuenta puede estar pagando.",
  numero_operacion_invalido: "El número de operación no es válido: copialo tal como figura en el comprobante.",
  no_coincide_con_la_orden: "El importe, la moneda o la cuenta no son los de la orden aprobada: revisá el comprobante o usá «Pagué otra cosa».",
  sin_diferencia: "Lo que escribiste es lo mismo que la orden: registrá el pago, no una diferencia.",
  diferencia_ya_resuelta: "El Consejo ya resolvió esa diferencia.",
  solo_reclamo: "Un pago a otra cuenta o en otra moneda no se da por pagado: solo se puede reclamar.",
  fecha_operacion_invalida: "La fecha no puede ser anterior a la emisión ni futura.",
  comprobante_invalido: "El comprobante tiene que cargarse desde el pago de esta orden, después de ver la cuenta para pagar, seguir en «Facturas y comprobantes» y su archivo tiene que haber pasado el antivirus.",
  comprobante_de_otra_orden: "Ese comprobante ya respalda otro pago.",
  operacion_de_otra_orden: "Ese número de operación ya respalda otra orden: una transferencia paga una sola orden.",
  conciliacion_repetida: "Ese pago ya está conciliado.",
  pago_sin_conciliar: "Ese pago todavía no está conciliado: se coteja después de conciliarlo.",
  extracto_invalido: "No pudimos leer el archivo como un extracto en CSV: revisá que sea el que bajaste del banco.",
  extracto_demasiadas_lineas: "El archivo tiene demasiadas líneas: bajá del banco un período más corto.",
  extracto_sin_movimientos: "No pudimos leer ninguna línea con esas columnas: revisá cuál es cada dato.",
  columnas_no_encontradas: "El archivo no tiene las columnas elegidas: elegilas de nuevo.",
  columnas_sin_elegir: "Falta elegir qué columna es cada dato.",
  archivo_demasiado_grande: "El archivo pasa del tamaño máximo. Elegí uno más chico.",
  no_es_quien_ejecuto:
    "Carga el comprobante y registra el pago (o declara que no se hizo, si alguien vio la cuenta) quien vio la cuenta en el acto de ejecución. Quien vio la cuenta o registró un pago no lo concilia ni confirma que no se pagó, y quien declaró no confirma su propia declaración.",
  motivo_con_cuenta: "El motivo no puede llevar el número de una cuenta: describí el problema sin copiarla. Si no es una cuenta, separá los números con una coma.",
  liquidacion_no_admite: "La liquidación no admite eso: ya hay un borrador de ese período, ya no es un borrador o el gasto ya se quitó.",
  orden_no_liquidable:
    "Esa orden no se puede liquidar: no está pagada, ya está en una liquidación, es de otra moneda o se pagó después del período.",
  sin_esquema: "Ese grupo no tiene un esquema de coeficientes vigente en el período.",
  version_no_disponible: "Este archivo todavía se está revisando o fue rechazado: no se puede descargar.",
  archivo_vencido: "Este archivo ya no está disponible.",
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

/** Al actuar sobre una orden o su pago la API responde `sin_permiso` también cuando frena la
 * separación de funciones (el motivo no sale, por diseño): decir solo que falta la facultad sería falso. */
const SIN_PERMISO_EN_LA_ORDEN =
  "No podés hacer esto en esta orden. Puede ser que tu mandato no lo incluya o que ya hayas participado de ella: quien prepara, aprueba, ejecuta y concilia tienen que ser personas distintas.";

/** Qué decir ante el rechazo de un acto sobre una orden o su pago. */
export function mensajeDeActo(estado: number, codigo: string | undefined): string {
  return estado === ESTADO.sinPermiso && codigo === "sin_permiso" ? SIN_PERMISO_EN_LA_ORDEN : mensajeDeError(estado, codigo);
}
