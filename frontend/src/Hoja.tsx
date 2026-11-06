import { type ReactNode, useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";

// Una hoja sobre la pantalla: desde abajo en el teléfono, al centro con más ancho. Lo de atrás queda
// montado pero inerte: sin foco y sin clics. Al cerrarse, el foco vuelve a donde estaba. Va en un
// portal para que una hoja pueda abrirse sobre otra (entrar encima de confirmar).

const ENFOCABLE = "[data-foco]";

type Props = {
  titulo: string;
  children: ReactNode;
  /** Qué hace Escape; sin esto, la hoja solo se cierra con lo que ofrece adentro. */
  alEscapar?: () => void;
};

export function Hoja({ titulo, children, alEscapar }: Props) {
  const id = useId();
  const fondo = useRef<HTMLDivElement>(null);
  const hoja = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previo = document.activeElement;
    const otros = Array.from(document.body.children).filter(
      (e): e is HTMLElement => e instanceof HTMLElement && e !== fondo.current && !e.inert,
    );
    otros.forEach((e) => {
      e.inert = true;
    });
    (hoja.current?.querySelector<HTMLElement>(ENFOCABLE) ?? hoja.current)?.focus();
    return () => {
      otros.forEach((e) => {
        e.inert = false;
      });
      if (previo instanceof HTMLElement) previo.focus();
    };
  }, []);

  return createPortal(
    <div ref={fondo} className="hoja-fondo" onKeyDown={(e) => e.key === "Escape" && alEscapar?.()}>
      <div ref={hoja} className="hoja" role="dialog" aria-modal="true" aria-labelledby={id} tabIndex={-1}>
        <h2 id={id}>{titulo}</h2>
        {children}
      </div>
    </div>,
    document.body,
  );
}
