import type { ReactNode } from "react";
import { Link } from "react-router";

// La fila compacta del Padrón (`FilaDeRegistro`, criterio 4 del informe de fricción) para las listas
// de Pagos, cuyas filas llevan a otra pantalla (la ficha de la orden, el cotejo): es un enlace y no
// un botón que abre una hoja. El mismo dibujo y las mismas partes: título, una etiqueta corta solo
// si cambia lo que hay que hacer y un resumen de un renglón. Va dentro de `ul.filas-registro > li`.

type Props = {
  a: string;
  /** Lo que viaja con la navegación (`consorcio`, `volver`). */
  estadoNavegacion?: unknown;
  titulo: ReactNode;
  resumen?: ReactNode;
  estado?: ReactNode;
};

export function FilaEnlace({ a, estadoNavegacion, titulo, resumen, estado }: Props) {
  return (
    <Link to={a} state={estadoNavegacion} className="fila-registro">
      <span className="fila-registro__titulo">{titulo}</span>
      {estado !== undefined && (
        <>
          {" "}
          <span className="fila-registro__estado">{estado}</span>
        </>
      )}
      {resumen !== undefined && (
        <>
          {" "}
          <span className="fila-registro__resumen">{resumen}</span>
        </>
      )}
    </Link>
  );
}

/** La misma fila para quien solo lee y no tiene nada que abrir (FL-CON-05, FL-PAN-02): sin botón ni
 * enlace, para no ofrecer una ficha que no agrega nada. */
export function FilaDeLectura({ titulo, resumen }: { titulo: ReactNode; resumen?: ReactNode }) {
  return (
    <div className="fila-registro fila-registro--lectura">
      <span className="fila-registro__titulo">{titulo}</span>
      {resumen !== undefined && (
        <>
          {" "}
          <span className="fila-registro__resumen">{resumen}</span>
        </>
      )}
    </div>
  );
}
