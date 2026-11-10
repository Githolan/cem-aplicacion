import { type ReactNode, useId } from "react";

import type { Barra } from "./datos";

// Un gráfico de barras horizontales en SVG propio (sin librería): cada barra lleva su nombre y su
// valor escritos, así que nada depende del color (documento 08, §13.4), y debajo va la misma
// información en una tabla. El dibujo se esconde del lector de pantalla, que lee la tabla.

/** Ancho del dibujo en unidades del SVG: la barra más larga ocupa todo. */
const ANCHO = 100;
/** La barra de un valor muy chico se ve igual, para que no parezca que no hay nada. */
const ANCHO_MINIMO = 1.5;

type Props = {
  titulo: string;
  /** Qué mide el gráfico y de dónde sale (RN33): se lee antes del dibujo. */
  base: ReactNode;
  barras: readonly Barra[];
  columna: string;
  valor: string;
  /** Lo que se ve si no hay barras. */
  vacio: string;
  /** El enlace a los registros que originan el gráfico, si la persona puede abrirlos. */
  registros?: ReactNode;
  /** Algo para elegir antes del gráfico (el mes del gasto por rubro). */
  control?: ReactNode;
};

export function GraficoBarras({ titulo, base, barras, columna, valor, vacio, registros, control }: Props) {
  const id = useId();
  const maximo = Math.max(...barras.map((b) => b.valor), 0);
  return (
    <section className="tarjeta grafico" aria-labelledby={`${id}-titulo`}>
      <h2 id={`${id}-titulo`}>{titulo}</h2>
      <p className="grafico__base">{base}</p>
      {control}
      {barras.length === 0 ? (
        <p>{vacio}</p>
      ) : (
        <>
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
          <details className="grafico__tabla">
            <summary>Ver los datos en una tabla</summary>
            <div className="tabla-lista tabla-tarjetas">
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
                      <td data-etiqueta={valor}>{b.texto}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </>
      )}
      {registros}
    </section>
  );
}
