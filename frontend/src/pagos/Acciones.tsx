import type { ReactNode } from "react";
import { Link } from "react-router";

import { type AccionOfrecida, type AccionPago, CON_ENLACE, CON_FORMULARIO, claseDe, TEXTO_ACCION } from "./textosDeAcciones";

// Los botones de una orden: exactamente los que mandó el servidor, en su orden y con su peso (una
// sola principal, llena; las secundarias con borde; las negativas en rojo). La ficha no escribe
// ningún botón de acto a mano (informe de fricción, §4.3). Lo único que decide la pantalla es lo que
// ya está a la vista: un formulario abierto, una pregunta de confirmación o la cuenta ya mostrada
// esconden el botón que los abrió.

export type Enlace = { a: string; estado: unknown };

type Props = {
  acciones: readonly AccionOfrecida[];
  enviando: boolean;
  alElegir: (codigo: AccionPago) => void;
  /** Lo que la pantalla ya tiene abierto por esa acción. */
  ocultas?: ReadonlySet<AccionPago>;
  /** Visibles, pero todavía no se pueden tocar (aprobar sin haber visto la cuenta). */
  deshabilitadas?: ReadonlySet<AccionPago>;
  /** Cuando el botón dice lo que ocurre al tocarlo, y eso depende de la orden. */
  textos?: Partial<Record<AccionPago, string>>;
  enlaces?: Partial<Record<AccionPago, Enlace>>;
  /** Lo que no es un acto sobre la orden (volver a pedir la cuenta), después de los actos. */
  children?: ReactNode;
};

/** Lo que se dibuja como botón o enlace: sin las que son un formulario ni las que ya están abiertas. */
export function aDibujar(acciones: readonly AccionOfrecida[], ocultas: ReadonlySet<AccionPago> = new Set()) {
  return acciones.filter((a) => !CON_FORMULARIO.has(a.codigo) && !ocultas.has(a.codigo));
}

export function Acciones({ acciones, enviando, alElegir, ocultas, deshabilitadas, textos, enlaces, children }: Props) {
  const visibles = aDibujar(acciones, ocultas);
  if (visibles.length === 0 && children === undefined) return null;
  return (
    <div className="acciones">
      {visibles.map((a) => {
        const texto = textos?.[a.codigo] ?? TEXTO_ACCION[a.codigo];
        const enlace = enlaces?.[a.codigo];
        if (CON_ENLACE.has(a.codigo)) {
          if (enlace === undefined) return null;
          const clase = a.tipo === "principal" ? "enlace-boton enlace-boton--principal" : "enlace-boton enlace-boton--borde";
          return (
            <Link key={a.codigo} to={enlace.a} state={enlace.estado} className={clase}>
              {texto}
            </Link>
          );
        }
        return (
          <button key={a.codigo} type="button" className={claseDe(a)} data-accion={a.codigo} data-tipo={a.tipo}
            disabled={enviando || deshabilitadas?.has(a.codigo) === true} onClick={() => alElegir(a.codigo)}>
            {texto}
          </button>
        );
      })}
      {children}
    </div>
  );
}
