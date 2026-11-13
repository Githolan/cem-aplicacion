import type { components } from "../api/tipos";

// Las palabras de cada acción que el servidor ofrece en una orden (informe de fricción, parte 4).
// Qué se ofrece, en qué orden y con qué peso lo dice el servidor (`acciones: [{codigo, tipo}]`),
// que lo saca de la tabla de la pantalla y de lo que la base acepta. Acá solo están las palabras,
// en un solo lugar: el botón de la ficha y el verbo de la fila de la bandeja dicen lo mismo (P-09).

export type AccionPago = components["schemas"]["AccionPago"];
export type TipoAccion = components["schemas"]["TipoAccion"];
export type AccionOfrecida = components["schemas"]["AccionOfrecida"];

export const TEXTO_ACCION: Record<AccionPago, string> = {
  enviar: "Enviar a aprobar",
  modificar: "Modificar la orden",
  retirar: "Retirar la orden",
  aprobar: "Aprobar la orden",
  rechazar: "Rechazar",
  renovar: "Renovar la aprobación",
  pagar: "Ver la cuenta para pagar",
  ver_cuenta_de_nuevo: "Ver la cuenta de nuevo",
  retomar_pago: "Retomar el pago",
  tomar_pago: "Tomar el pago",
  subir_comprobante: "Subir el comprobante",
  corregir: "Corregir el registro",
  declarar_no_pago: "Declarar que no se pagó",
  conciliar: "Conciliar el pago",
  observar: "Observar",
  confirmar_no_pago: "Confirmar que no se pagó",
  resolver_diferencia: "Resolver la diferencia",
  recuperar: "Registrar que se recuperó la plata",
  cerrar_reclamo: "Cerrar sin recuperar",
  corregir_diferencia: "Corregir lo registrado",
  preparar_faltante: "Preparar la orden por lo que falta",
  volver_a_pagar: "Enviar a aprobar de nuevo",
  preparar_otra: "Preparar otra orden",
  cerrar_sin_pagar: "Cerrar sin pagar",
  cotejar: "Cotejar con el banco",
};

/** Las que se hacen con un formulario de la ficha y no con un botón suelto: el formulario aparece
 * cuando el servidor las ofrece. */
export const CON_FORMULARIO: ReadonlySet<AccionPago> = new Set(["subir_comprobante", "corregir", "resolver_diferencia"]);

/** Las que llevan a preparar otra orden: un enlace, no un acto sobre esta. */
export const CON_ENLACE: ReadonlySet<AccionPago> = new Set(["preparar_otra", "preparar_faltante"]);

/** Principales que cierran o niegan algo: van llenas y en rojo, como hasta ahora. Los colores de
 * cada acción son de la tanda 5 (pregunta 13): acá no cambian. */
const PRINCIPAL_EN_ROJO: ReadonlySet<AccionPago> = new Set(["declarar_no_pago", "confirmar_no_pago", "observar"]);

/** La clase del botón según el tipo que mandó el servidor: una sola principal, llena. */
export function claseDe({ codigo, tipo }: AccionOfrecida): string | undefined {
  if (tipo === "principal") return PRINCIPAL_EN_ROJO.has(codigo) ? "peligro" : undefined;
  return tipo === "negativa" ? "secundario peligro" : "secundario";
}
