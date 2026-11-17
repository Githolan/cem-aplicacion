// Las hojas abiertas, de todas las pantallas. Solo la de arriba se usa: el resto de la página y las
// otras hojas quedan inertes. La pila es una sola porque una hoja puede cambiar debajo de otra: la de
// confirmar pasa a «No se pudo confirmar» mientras la de entrar encima sigue abierta. Una hoja `encima`
// no pierde ese lugar por más que otra se monte después.

export type HojaAbierta = {
  fondo: HTMLElement;
  encima: boolean;
  enfocar: () => void;
};

const pila: HojaAbierta[] = [];
/** Lo que esta pila dejó inerte: lo que ya venía así no es suyo y no lo suelta. */
const inertes = new Set<HTMLElement>();
/** El foco antes de la primera hoja: adonde vuelve cuando se cierra la última. */
let origen: Element | null = null;

const deArriba = () => pila.filter((hoja) => hoja.encima).at(-1) ?? pila.at(-1);

function repartirInerte() {
  const arriba = deArriba();
  inertes.forEach((elemento) => {
    if (arriba !== undefined && elemento !== arriba.fondo) return;
    elemento.inert = false;
    inertes.delete(elemento);
  });
  if (arriba === undefined) return;
  Array.from(document.body.children).forEach((elemento) => {
    if (!(elemento instanceof HTMLElement) || elemento === arriba.fondo || elemento.inert) return;
    elemento.inert = true;
    inertes.add(elemento);
  });
}

/**
 * Suma la hoja y devuelve cómo sacarla. El foco solo se mueve si cambia la hoja de arriba. Al sacar
 * la de arriba, el foco vuelve a lo que lo tenía en la de abajo (el botón de la acción que abrió una
 * ventana sobre su ficha), si sigue ahí.
 */
export function abrirHoja(hoja: HojaAbierta): () => void {
  const previo = document.activeElement;
  if (pila.length === 0) origen = previo;
  pila.push(hoja);
  repartirInerte();
  if (deArriba() === hoja) hoja.enfocar();

  return () => {
    const estabaArriba = deArriba() === hoja;
    pila.splice(pila.indexOf(hoja), 1);
    inertes.delete(hoja.fondo);
    repartirInerte();
    const siguiente = deArriba();
    if (siguiente === undefined) {
      if (origen instanceof HTMLElement) origen.focus();
      origen = null;
    } else if (estabaArriba) {
      if (previo instanceof HTMLElement && previo.isConnected && siguiente.fondo.contains(previo)) previo.focus();
      else siguiente.enfocar();
    }
  };
}
