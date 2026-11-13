import type { components } from "../api/tipos";
import { PASOS_DEL_CAMINO } from "./textos";

// Por dónde va la orden: Preparar, Aprobar, Pagar, Conciliar y Cotejar (decisiones del propietario
// del 12-11-2026). La barra la manda el servidor, de la tabla de la pantalla: depende solo de la
// orden, no de quien mira, y acá no se deduce nada. «Cotejar» queda hecho cuando el pago se cotejó
// con el banco (P-05).

type BarraPago = components["schemas"]["BarraPago"];

export function Barra({ barra }: { barra: BarraPago | null }) {
  if (barra === null) return null;
  return (
    <ol className="camino-orden" aria-label="Por dónde va la orden">
      {PASOS_DEL_CAMINO.map((nombre, i) => {
        const hecho = i < barra.hechos;
        const esActual = i === barra.hechos && barra.actual !== null;
        return (
          <li key={nombre} data-estado={hecho ? "hecho" : esActual ? (barra.actual ?? undefined) : undefined} aria-current={esActual ? "step" : undefined}>
            <i />
            <span>
              {nombre}
              {hecho && <span className="solo-lector"> (hecho)</span>}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
