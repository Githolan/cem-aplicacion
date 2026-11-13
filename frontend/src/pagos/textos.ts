import type { components } from "../api/tipos";

// Lo que se le dice a la persona de cada `paso`, `grupo` y `situacion`. Los valores los decide el
// servidor; acá solo están las palabras. Con un valor nuevo en el contrato, esto deja de compilar.

export type Paso = components["schemas"]["PasoPago"];
export type Grupo = components["schemas"]["GrupoPago"];
export type Situacion = components["schemas"]["SituacionPago"];
export type LeTocaA = components["schemas"]["LeTocaA"];
export type Filtro = components["schemas"]["FiltroConsultaPagos"];
export type EstadoVisible = components["schemas"]["EstadoVisible"];

export const VERBO: Record<Paso, string> = {
  pagar: "Ver la cuenta para pagar",
  tomar_pago: "Hacerse cargo de este pago",
  subir_comprobante: "Subir el comprobante",
  corregir: "Corregir el registro",
  enviar: "Enviar a aprobar",
  renovar: "Renovar la aprobación",
  modificar: "Modificar la orden",
  volver_a_pagar: "Enviar a aprobar de nuevo",
  rechazada: "Ver el motivo",
  preparar_faltante: "Preparar la orden por lo que falta",
  aprobar: "Aprobar la orden",
  conciliar: "Conciliar el pago",
  confirmar_no_pago: "Confirmar que no se pagó",
  declarar_no_pago: "Declarar que no se pagó",
  resolver_diferencia: "Resolver la diferencia",
  espera_aprobar: "Falta aprobar",
  espera_comprobante: "Falta el comprobante",
  espera_declarar: "Falta declarar que no se pagó",
  espera_conciliar: "Falta conciliar",
  espera_confirmar: "Falta confirmar",
  espera_resolver: "Falta resolver",
  espera_corregir: "Falta corregir",
};

export const TITULO_GRUPO: Record<Grupo, string> = {
  para_pagar: "Para pagar",
  para_corregir: "Para corregir",
  para_preparar: "Para preparar",
  para_aprobar: "Para aprobar",
  para_conciliar: "Para conciliar",
  para_confirmar: "Para confirmar",
  para_resolver: "Para resolver",
  para_cotejar: "Para cotejar",
  esperando: "Esperando a otra persona",
  borradores_viejos: "Borradores viejos",
};

/** La única palabra de cada estado, la misma en bandeja, ficha, consulta y panel (decisión 11 del
 * propietario, 12-11-2026). Qué estado tiene cada orden lo dice el servidor (`estado_visible`); la
 * palabra es la de `contratos/cem_contracts/pantallas_datos/pagos.json`, y una prueba compara las
 * dos. No hay otro mapa de nombres de estado en el frontend. */
export const NOMBRE_ESTADO: Record<EstadoVisible, string> = {
  borrador: "Borrador",
  esperando_aprobacion: "Esperando aprobación",
  rechazada: "Rechazada",
  retirada: "Retirada",
  lista_para_pagar: "Lista para pagar",
  pagando: "Pagando",
  falta_conciliar: "Falta conciliar",
  observada: "Observada",
  no_se_pago: "No se pagó",
  pagada: "Pagada",
  pagada_y_cotejada: "Pagada y cotejada",
};

/** El color que acompaña a la palabra de cada estado. */
export const TONO_ESTADO: Record<EstadoVisible, "pendiente" | "bien" | "mal" | "neutro"> = {
  borrador: "pendiente",
  esperando_aprobacion: "pendiente",
  rechazada: "mal",
  retirada: "neutro",
  lista_para_pagar: "bien",
  pagando: "pendiente",
  falta_conciliar: "pendiente",
  observada: "mal",
  no_se_pago: "neutro",
  pagada: "bien",
  pagada_y_cotejada: "bien",
};

/** La fecha de una orden, corta para no ensanchar la fila: «9 nov». Con otro año, «9 nov 2025». */
export function fechaCorta(iso: string, ahora = new Date()): string {
  const fecha = new Date(iso);
  const conAnio = fecha.getFullYear() !== ahora.getFullYear();
  return new Intl.DateTimeFormat("es-AR", { day: "numeric", month: "short", ...(conAnio ? { year: "numeric" } : {}) })
    .format(fecha)
    .replace(/\./g, "")
    .replace(/ de /g, " ");
}

/** El aviso de orden repetida (decisión 5): no bloquea, dice cuál es la otra. */
export const textoRepetida = (fecha: string, estado: EstadoVisible) =>
  `Hay otra orden del mismo proveedor y el mismo importe, del ${fecha} (${NOMBRE_ESTADO[estado]}). Revisar que no sea la misma factura.`;

/** El aviso arriba de la bandeja de quien preparó una orden rechazada (decisión 3). */
/** Para quien coteja, sin pagos por cotejar: el cotejo sigue a mano (lo ya cotejado, deshacer). */
export const VER_LO_COTEJADO = "Ver lo cotejado";
/** El aviso antes de guardar una orden parecida a otra (decisión 4 del 13-11-2026). */
export const textoParecidaAntes = (fecha: string) => `Ya hay una orden parecida del ${fecha}.`;
export const GUARDAR_IGUAL = "Guardar igual";
export const VER_LA_OTRA = "Ver la otra";
export const ENTENDIDO = "Entendido";
export const PREPARAR_OTRA = "Preparar otra orden";
export const textoRechazada = (proveedor: string, importe: string, fecha: string) =>
  `Se rechazó la orden de ${proveedor} por ${importe}, del ${fecha}.`;

export const TEXTO_LE_TOCA_A: Record<LeTocaA, string> = {
  vos: "Le toca a esta cuenta.",
  consejo: "Le toca al Consejo.",
  quien_registro: "Le toca a quien registró el pago.",
  quien_vio_la_cuenta: "Le toca a quien vio la cuenta.",
  quien_ejecuta: "Le toca a quien ejecuta los pagos.",
};

export const FILTROS: readonly { valor: Filtro; texto: string }[] = [
  { valor: "todos", texto: "Todos" },
  { valor: "pagados", texto: "Pagados" },
  { valor: "no_se_pagaron", texto: "No se pagaron" },
  { valor: "rechazados", texto: "Rechazados" },
  { valor: "retirados", texto: "Retirados" },
  { valor: "en_curso", texto: "En curso" },
];

// Retirar una orden que nadie aprobó (comentario 23, 09-11-2026): queda, con quién y cuándo.
export const RETIRAR = "Retirar la orden";
export const CONFIRMAR_RETIRAR = "Sí, retirar la orden";
export const NO_RETIRAR = "No, dejarla";
export const PREGUNTA_RETIRAR =
  "¿Retirar esta orden? Sale de la bandeja de todos y ya no se aprueba ni se paga. Queda registrado quién la retiró y cuándo, y se ve en la consulta.";
export const TEXTO_RETIRADA = "Listo: la orden se retiró. Ya no está en la bandeja.";
export const AVISO_SE_PUEDE_RETIRAR = "Mientras nadie la apruebe, se puede modificar o retirar.";
export const textoRetiro = (nombre: string, cuando: string) => `La retiró ${nombre} el ${cuando}. No se aprueba ni se paga.`;

export const PASOS_DEL_CAMINO = ["Preparar", "Aprobar", "Pagar", "Conciliar", "Cotejar"] as const;

/** Cerrar sin pagar una orden que no se pagó (decisión 12). */
export const PREGUNTA_CERRAR_SIN_PAGAR =
  "Cerrar la orden sin pagar: queda como «No se pagó», sale de la bandeja y ya no se vuelve a pagar. Queda registrado quién la cerró, cuándo y por qué.";
export const TEXTO_CERRADA_SIN_PAGO = "Listo: la orden quedó cerrada sin pagar. Ya no está en la bandeja.";
export const textoCierreSinPago = (nombre: string, cuando: string, motivo: string) =>
  `La cerró sin pagar ${nombre} el ${cuando}: ${motivo}.`;

/** Inventado: muestra la forma del archivo, que el servidor lee (`extracto.py`). */
export const EJEMPLO_EXTRACTO = "Fecha;Débito;Número de operación\n05/11/2026;-184.350,00;0048213377";
/** Se dice una vez, donde se paga, y no en cada visita al inicio (P-36, AC-04, 12-11-2026). */
export const AVISO_NO_TRANSFIERE = "CEM no transfiere: la transferencia se hace en el banco.";
export const AVISO_VER_LA_CUENTA =
  "Para pagar hay que ver la cuenta. Queda registrado quién la vio, y esa persona no puede conciliar este pago.";
export const AVISO_ANTIVIRUS_TARDA =
  "El antivirus está tardando. Se puede salir: la orden queda en Pagos como «Subir el comprobante», con el archivo ya cargado.";
export const AVISO_LECTURA_FALLO = "El comprobante no se pudo leer. Hay que completar los datos a mano.";
export const AVISO_ARCHIVO_RECHAZADO = "Este archivo no se puede usar: no pasó el antivirus. Hay que subir otro.";
export const AVISO_SIN_CUENTA =
  "La cuenta ya no se muestra: venció la aprobación, cambió el proveedor o terminó el contrato. Si se transfirió, registrar el pago con el comprobante; si no, declarar que no se pagó.";
export const AVISO_SIN_SALIDA =
  "Nadie vio la cuenta y ya no se puede abrir: venció la aprobación, cambió el proveedor o el contrato dejó de estar vigente. Hay que declarar que no se pagó, y después se puede volver a pagar.";
export const AVISO_DIFERENCIA_ESPERA = "No hay que transferir de nuevo hasta que el Consejo decida.";
export const AVISO_OTRA_COSA = "La orden no queda pagada: lo resuelve el Consejo.";
export const AVISO_TOMAR =
  "Antes de hacerse cargo, hay que confirmar con quien vio la cuenta que no transfirió. Si la plata ya salió y se paga otra vez, la orden se paga dos veces.";
export const AVISO_RETOMAR = "«Anular la declaración y pagar» deja sin efecto lo que se declaró.";
export const AVISO_NO_TRANSFIERAS = "Si la plata ya salió, no hay que transferir de nuevo.";
export const AVISO_LECTURA_COINCIDE = "La lectura del comprobante coincide con lo registrado.";
export const AVISO_LECTURA_INCOMPLETA = "La lectura del comprobante quedó incompleta: conviene comparar los datos con el archivo.";
export const AVISO_LECTURA_DISPUTADA = "Dijo que la lectura estaba mal";
export const AVISO_VUELVE_A_APROBACION = "Para volver a pagarla, hay que enviarla a aprobar de nuevo.";
export const AVISO_PASA_SOLA = "No pide aprobación: se puede dejar lista para pagar de nuevo.";
/** El botón de `volver_a_pagar` cuando la orden no pasa por el Consejo: dice lo que ocurre al tocarlo. */
export const VERBO_PASA_SOLA = "Dejar lista para pagar";
/** En la ficha, conciliar dice qué pasa al tocarlo (P-22); en la bandeja la fila sigue diciendo «Conciliar el pago». */
export const VERBO_CONCILIAR = "Está bien: conciliar";
/** Debajo del motivo de observar: qué pasa después (P-22). */
export const AVISO_OBSERVAR = "Vuelve a quien lo registró para que lo corrija.";
export const AVISO_POR_EL_CONSEJO = "Hubo una diferencia: para volver a pagarla, la aprueba el Consejo, sea cual sea el importe.";
export const AVISO_MODIFICAR = "Si se cambia algo, el Consejo tiene que aprobarla de nuevo.";
