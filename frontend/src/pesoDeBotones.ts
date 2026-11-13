// Solo para las pruebas (configuracionPruebas.ts): la regla de colores y del botón principal
// (pregunta 13 del propietario, 13-11-2026; tanda 5), la misma idea que los tipos de acción de
// `pantallas_datos/pagos.json` (principal, secundaria, negativa) llevada a todas las pantallas:
//
// - un solo botón principal lleno por pantalla o por diálogo (azul para avanzar, o rojo si el
//   principal es el acto negativo);
// - «Cancelar», «No» y «Volver» con borde y sin relleno, nunca en rojo;
// - el acto negativo (retirar, rechazar, quitar, finalizar, anular…) en rojo: lleno si es el
//   principal (`peligro`), con borde y texto rojos si solo lo ofrece (`secundario peligro`).
//
// Se mira el DOM que dibuja cada prueba, en cada cambio y no solo al final: cualquier pantalla que
// una prueba alcance a mostrar queda revisada. Las clases son las de `estilos.css`: lo que no tiene
// relleno de color es `secundario` y las formas propias de una fila, un filtro o un ícono.

/** Botones que el CSS dibuja sin relleno de color (`estilos.css`). */
const SIN_RELLENO = ".secundario, .sutil, .fila-registro, .boton-copiar, .boton-modo-lectura, .boton-ver, .filtros-pago button";
/** Cada uno es otra «pantalla»: un diálogo encima, o el aviso fijo de versión nueva. */
const ALCANCES = '[role="dialog"], .aviso-version';
/** Lo que deja de lado un formulario o una pregunta: nunca en rojo. */
const DEJAR_DE_LADO = /^(cancelar|no|no, .+|no retirar|volver( .+)?)$/i;
/** El acto negativo: en rojo, lleno o con borde. */
const NEGATIVO = /^(sí, )?(retirar|rechazar|quitar|descartar|finalizar|anular|borrar|eliminar|suprimir|desactivar|dar de baja|cerrar sin|declarar que no|confirmar que no|no coincide)/i;

function texto(el: Element): string {
  return (el.textContent ?? "").replace(/\s+/g, " ").trim();
}

function visible(el: Element): boolean {
  for (let a: Element | null = el; a !== null; a = a.parentElement) {
    if (a.hasAttribute("hidden") || (a instanceof HTMLElement && a.style.display === "none")) return false;
    const padre = a.parentElement;
    if (padre instanceof HTMLDetailsElement && !padre.open && a.tagName !== "SUMMARY") return false;
  }
  return true;
}

function alcanceDe(el: Element): Element | null {
  return el.closest(ALCANCES);
}

function lleno(el: Element): boolean {
  if (el instanceof HTMLButtonElement) return !el.disabled && !el.matches(SIN_RELLENO);
  return el.matches(".enlace-boton--principal, .enlace-boton.peligro");
}

/** Las faltas a la regla en lo que hay dibujado debajo de `raiz`, cada una con el texto del botón. */
export function faltasDeBotones(raiz: Element): string[] {
  const faltas: string[] = [];
  const botones = [...raiz.querySelectorAll("button, a.enlace-boton")].filter(visible);
  const porAlcance = new Map<Element | null, string[]>();
  for (const b of botones) {
    const t = texto(b) || (b.getAttribute("aria-label") ?? b.outerHTML.slice(0, 120));
    if (lleno(b)) porAlcance.set(alcanceDe(b), [...(porAlcance.get(alcanceDe(b)) ?? []), t]);
    if (DEJAR_DE_LADO.test(t) && b.classList.contains("peligro")) faltas.push(`«${t}» deja de lado y está en rojo`);
    if (b instanceof HTMLButtonElement && NEGATIVO.test(t) && !b.classList.contains("peligro"))
      faltas.push(`«${t}» es un acto negativo y no está en rojo`);
  }
  for (const llenos of porAlcance.values())
    if (llenos.length > 1) faltas.push(`más de un botón principal a la vez: ${llenos.map((t) => `«${t}»`).join(", ")}`);
  return faltas;
}

const vistas: string[] = [];
let observador: MutationObserver | null = null;

function mirar() {
  for (const f of faltasDeBotones(document.body)) if (!vistas.includes(f)) vistas.push(f);
}

/** Empieza a mirar cada cambio del documento (una prueba nueva). */
export function vigilarBotones() {
  vistas.length = 0;
  observador ??= new MutationObserver(mirar);
  observador.observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });
}

/** Lo que se vio desde `vigilarBotones`, y deja de mirar. */
export function faltasVistas(): string[] {
  if (observador !== null) mirar();
  observador?.disconnect();
  return vistas.splice(0);
}
