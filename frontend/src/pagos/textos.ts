import type { components } from "../api/tipos";

// Lo que se le dice a la persona de cada `paso`, `grupo` y `situacion`. Los valores los decide el
// servidor; acá solo están las palabras. Con un valor nuevo en el contrato, esto deja de compilar.

export type Paso = components["schemas"]["PasoPago"];
export type Grupo = components["schemas"]["GrupoPago"];
export type Situacion = components["schemas"]["SituacionPago"];
export type LeTocaA = components["schemas"]["LeTocaA"];
export type Filtro = components["schemas"]["FiltroConsultaPagos"];

export const VERBO: Record<Paso, string> = {
  pagar: "Pagar",
  subir_comprobante: "Subir el comprobante",
  corregir: "Corregir el registro",
  enviar: "Enviar a aprobar",
  renovar: "Renovar la aprobación",
  volver_a_pagar: "Volver a pagar",
  rechazada: "Ver el motivo",
  aprobar: "Aprobar o rechazar",
  conciliar: "Conciliar",
  confirmar_no_pago: "Confirmar",
  declarar_no_pago: "Declarar que no se pagó",
  resolver_diferencia: "Resolver",
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
  esperando: "Esperando a otra persona",
};

export const TEXTO_SITUACION: Record<Situacion, string> = {
  borrador: "Borrador",
  en_revision: "Esperando aprobación",
  rechazada: "Rechazada",
  para_pagar: "Aprobada",
  pagando: "Para pagar",
  no_pago_declarado: "Falta confirmar",
  falta_conciliar: "Falta conciliar",
  observado: "Observado",
  con_diferencia: "Se pagó otra cosa",
  reclamo: "Reclamo abierto",
  pagada: "Pagada",
  no_se_pago: "No se pagó",
};

export const TEXTO_LE_TOCA_A: Record<LeTocaA, string> = {
  vos: "Te toca a vos.",
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
  { valor: "en_curso", texto: "En curso" },
];

export const AVISO_VER_LA_CUENTA =
  "Para pagar hay que ver la cuenta. Queda registrado quién la vio, y esa persona no puede conciliar este pago.";
export const AVISO_VERIFICAR_CUENTA =
  "Verificá que la cuenta mostrada coincida con la cuenta autorizada para esta versión.";
export const AVISO_SIN_CUENTA =
  "La cuenta ya no se muestra: venció la aprobación, cambió o se dio de baja el proveedor, o el contrato dejó de estar vigente. Si transferiste, registrá el pago con los datos del comprobante (si fue después del vencimiento, queda marcado fuera de término y el Consejo lo concilia con un motivo); si no, declará que no se pagó.";
export const AVISO_SIN_SALIDA =
  "Nadie vio la cuenta y ya no se puede abrir: venció la aprobación, cambió el proveedor o el contrato dejó de estar vigente. Declará que no se pagó y después se puede volver a pagar.";
export const AVISO_RETOMAR = "Retomar deja sin efecto lo que se declaró.";
export const AVISO_NO_TRANSFIERAS = "Si la plata ya salió, no transfieras de nuevo.";
export const AVISO_MODIFICAR =
  "Esta orden ya está en revisión o aprobada: modificarla crea una versión nueva, la vuelve a revisión y las aprobaciones anteriores dejan de contar.";
