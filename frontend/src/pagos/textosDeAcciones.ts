import type { components } from "../api/tipos";
import { AVISO_RETOMAR } from "./textos";

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
  retomar_pago: "Anular la declaración y pagar",
  tomar_pago: "Hacerse cargo de este pago",
  subir_comprobante: "Subir el comprobante",
  corregir: "Corregir el registro",
  declarar_no_pago: "Declarar que no se pagó",
  conciliar: "Conciliar el pago",
  observar: "No coincide: devolverlo a corregir",
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

/** Principales que cierran o niegan algo: van llenas y en rojo (pregunta 13 del propietario,
 * 13-11-2026). Las negativas que no son la principal van con borde y texto rojos, sin relleno, y las
 * secundarias con borde: las mismas clases que el resto de las pantallas (estilos.css). */
const PRINCIPAL_EN_ROJO: ReadonlySet<AccionPago> = new Set(["declarar_no_pago", "confirmar_no_pago", "observar"]);

/** El título del plegable de lo que deshace algo ya hecho (D-13, decisión 5 del propietario del
 * 14-11-2026; texto impersonal, respuesta del 14-11): la acción no queda a la vista, para que nada invite a deshacer lo hecho sin querer. */
export const TITULO_PLEGADAS = "Hubo un error";

/** Lo que se dice dentro del plegable, antes del botón: qué deja sin efecto. */
export const AVISO_PLEGADA: Partial<Record<AccionPago, string>> = { retomar_pago: AVISO_RETOMAR };

/** La clase del botón según el tipo que mandó el servidor: una sola principal, llena. Las negativas
 * y las plegadas (deshacen algo ya hecho, D-13) van con borde rojo. */
export function claseDe({ codigo, tipo }: AccionOfrecida): string | undefined {
  if (tipo === "principal") return PRINCIPAL_EN_ROJO.has(codigo) ? "peligro" : undefined;
  return tipo === "negativa" || tipo === "plegada" ? "secundario peligro" : "secundario";
}
