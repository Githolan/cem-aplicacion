import { type ReactNode, useId } from "react";

import type { Barra } from "./datos";

// Un gráfico de barras horizontales en SVG propio (sin librería): cada barra lleva su nombre y su
// valor escritos, así que nada depende del color (documento 08, §13.4). El dibujo se esconde del
// lector de pantalla, que lee la misma información en una tabla que no se ve (PAN-02: a la vista,
// la tabla repetía las barras). Sin barras, el bloque no se dibuja (PAN-03): lo decide quien lo usa.

/** Ancho del dibujo en unidades del SVG: la barra más larga ocupa todo. */
const ANCHO = 100;
/** La barra de un valor muy chico se ve igual, para que no parezca que no hay nada. */
const ANCHO_MINIMO = 1.5;

type Props = {
  titulo: string;
  /** Qué mide el gráfico y de dónde sale (RN33): plegado en «Cómo se calcula» (PAN-05). */
  base: string;
  /** Lo único de la base que evita una lectura equivocada («No incluye borradores.»): a la vista. */
  aviso?: string;
  barras: readonly Barra[];
  columna: string;
  valor: string;
  /** Algo para elegir antes del gráfico (el mes del gasto por rubro). */
  control?: ReactNode;
};

export function GraficoBarras({ titulo, base, aviso, barras, columna, valor, control }: Props) {
  const id = useId();
  const maximo = Math.max(...barras.map((b) => b.valor), 0);
  if (barras.length === 0) return null;
  return (
    <section className="tarjeta grafico" aria-labelledby={`${id}-titulo`}>
      <h2 id={`${id}-titulo`}>{titulo}</h2>
      {aviso !== undefined && <p className="grafico__base">{aviso}</p>}
      <details className="grafico__como">
        <summary>Cómo se calcula</summary>
        <p className="grafico__base">{base}</p>
      </details>
      {control}
      <ul className="grafico__barras" aria-hidden="true">
        {barras.map((b) => (
          <li key={b.clave} className="grafico__fila">
            <span className="grafico__etiqueta">{b.etiqueta}</span>
            <svg className="grafico__dibujo" viewBox={`0 0 ${ANCHO} 10`} preserveAspectRatio="none" focusable="false">
              <rect className="grafico__fondo" x="0" y="0" width={ANCHO} height="10" rx="2" />
              <rect
                className="grafico__barra"
                x="0"
                y="0"
                width={maximo === 0 ? 0 : Math.max((b.valor / maximo) * ANCHO, ANCHO_MINIMO)}
                height="10"
                rx="2"
              />
            </svg>
            <span className="grafico__valor">{b.texto}</span>
          </li>
        ))}
      </ul>
      {/* La tabla va dentro de un bloque oculto: una tabla no achica su ancho a 1 px y, sola, ensancharía
          la página en un teléfono (medido: 498 px en 360 px). */}
      <div className="solo-lector">
        <table aria-label={titulo}>
          <thead>
            <tr>
              <th scope="col">{columna}</th>
              <th scope="col">{valor}</th>
            </tr>
          </thead>
          <tbody>
            {barras.map((b) => (
              <tr key={b.clave}>
                <th scope="row">{b.etiqueta}</th>
                <td>{b.texto}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
