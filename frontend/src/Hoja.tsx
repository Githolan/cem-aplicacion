import { type ReactNode, useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";

import { abrirHoja } from "./pilaDeHojas";

// Una hoja sobre la pantalla: desde abajo en el teléfono, al centro con más ancho. Lo de atrás queda
// montado pero inerte: sin foco y sin clics. Al cerrarse, el foco vuelve a donde estaba. Va en un
// portal para que una hoja pueda abrirse sobre otra (entrar encima de confirmar): quién queda arriba
// lo decide `pilaDeHojas.ts`.

const ENFOCABLE = "[data-foco]";

type Props = {
  titulo: string;
  children: ReactNode;
  /** Qué hace Escape; sin esto, la hoja solo se cierra con lo que ofrece adentro. */
  alEscapar?: () => void;
  /** Queda arriba de las demás aunque otra se monte después: sin resolverla no se puede seguir. */
  encima?: boolean;
};

export function Hoja({ titulo, children, alEscapar, encima = false }: Props) {
  const id = useId();
  const fondo = useRef<HTMLDivElement>(null);
  const hoja = useRef<HTMLDivElement>(null);

  useEffect(
    () =>
      abrirHoja({
        fondo: fondo.current!,
        encima,
        enfocar: () => (hoja.current?.querySelector<HTMLElement>(ENFOCABLE) ?? hoja.current)?.focus(),
      }),
    [encima],
  );

  return createPortal(
    <div ref={fondo} className={encima ? "hoja-fondo hoja-fondo-encima" : "hoja-fondo"} onKeyDown={(e) => e.key === "Escape" && alEscapar?.()}>
      <div ref={hoja} className="hoja" role="dialog" aria-modal="true" aria-labelledby={id} tabIndex={-1}>
        <h2 id={id}>{titulo}</h2>
        {children}
      </div>
    </div>,
    document.body,
  );
}
