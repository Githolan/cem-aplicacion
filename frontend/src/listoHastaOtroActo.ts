import { type RefObject, useEffect, useRef } from "react";

// Un «Listo» dice lo último que se hizo en su tarjeta. Si la persona empieza otro acto en otra
// tarjeta de la misma página (toca un botón o un enlace fuera de ella), ese «Listo» ya no habla de
// lo que está haciendo y se va (AC-41 del informe de fricción).

/** Llama a `quitar` cada vez que se toca un botón o un enlace fuera de `zona`. */
export function useListoHastaOtroActo(zona: RefObject<HTMLElement | null>, quitar: () => void) {
  const ultimo = useRef(quitar);
  useEffect(() => {
    ultimo.current = quitar;
  });
  useEffect(() => {
    const alTocar = (evento: MouseEvent) => {
      const tocado = evento.target instanceof Element ? evento.target.closest("button, a") : null;
      if (tocado === null || zona.current?.contains(tocado) === true) return;
      ultimo.current();
    };
    document.addEventListener("click", alTocar, true);
    return () => document.removeEventListener("click", alTocar, true);
  }, [zona]);
}
