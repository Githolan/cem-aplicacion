import { useEffect, useRef } from "react";

import { useListoHastaOtroActo } from "./listoHastaOtroActo";

// Un solo lugar para el resultado (informe de fricción, tanda 1, punto 7). La regla:
//
// 1. El resultado de un acto («Listo: …» o por qué no se hizo) sale DONDE SE ACTUÓ: en la tarjeta,
//    la fila o la sección del botón que se tocó; nunca arriba o al pie de la página, lejos de ahí.
//    Si el acto se hizo en una `VentanaDeFormulario`, el rechazo queda en la ventana y el «Listo»
//    sale, al cerrarse, junto a lo que la abrió.
// 2. Sale UNA vez: no se repite en otro aviso de la página ni en la ficha.
// 3. Se va con el ACTO SIGUIENTE: al tocar cualquier otro botón o enlace, ese resultado ya no habla
//    de lo que la persona está haciendo. No hay dos «Listo» a la vista a la vez.
//
// Uso: la pantalla guarda el último resultado y dibuja esta pieza donde se actuó.
//
//   const [resultado, setResultado] = useState<Resultado | null>(null);
//   …al terminar el pedido: setResultado({ texto: "Listo: el contacto quedó corregido.", ok: true });
//   <ResultadoDelActo resultado={resultado} alOtroActo={() => setResultado(null)} />
//
// Al aparecer toma el foco (el lector de pantalla lo lee) y se anuncia como estado.

export type Resultado = { texto: string; ok: boolean };

type Props = {
  resultado: Resultado | null;
  /** Se llama al tocar otro botón o enlace: la pantalla borra el resultado. */
  alOtroActo: () => void;
};

export function ResultadoDelActo({ resultado, alOtroActo }: Props) {
  const zona = useRef<HTMLDivElement>(null);
  const texto = useRef<HTMLParagraphElement>(null);
  useListoHastaOtroActo(zona, () => {
    if (resultado !== null) alOtroActo();
  });

  useEffect(() => {
    if (resultado !== null) texto.current?.focus();
  }, [resultado]);

  return (
    <div ref={zona} role="status" aria-live="polite" className="resultado-del-acto">
      {resultado !== null && (
        <p ref={texto} tabIndex={-1} className={resultado.ok ? "aviso-ok" : "aviso-error"}>
          {resultado.texto}
        </p>
      )}
    </div>
  );
}
