import { PASOS_DEL_CAMINO, type Paso, type Situacion } from "./textos";

// Por dónde va la orden. Sale de `situacion` y `paso`, que manda el servidor; acá no se deduce nada.
// La ficha no sabe si un pago se cotejó (eso vive en /pagos/cotejo): una orden pagada deja «Cotejar»
// sin marcar, ni hecho ni actual.

type Lugar = { hechos: number; actual: "ahora" | "alerta" | null };

const LUGAR: Record<Situacion, Lugar | null> = {
  borrador: { hechos: 0, actual: "ahora" },
  en_revision: { hechos: 0, actual: "ahora" },
  rechazada: { hechos: 0, actual: "alerta" },
  // La retiró quien la preparó antes de que nadie la aprobara: no sigue ningún camino.
  retirada: null,
  para_pagar: { hechos: 1, actual: "ahora" },
  pagando: { hechos: 1, actual: "ahora" },
  no_pago_declarado: { hechos: 1, actual: "alerta" },
  falta_conciliar: { hechos: 2, actual: "ahora" },
  observado: { hechos: 2, actual: "alerta" },
  con_diferencia: { hechos: 2, actual: "alerta" },
  reclamo: { hechos: 2, actual: "alerta" },
  pagada: { hechos: 3, actual: null },
  no_se_pago: null,
};

export function CaminoOrden({ situacion, paso }: { situacion: Situacion; paso: Paso | null }) {
  const lugar = LUGAR[situacion];
  if (lugar === null) return null;
  // Venció la aprobación: sigue en Pagar, pero hay que renovarla antes.
  const actual = paso === "renovar" ? "alerta" : lugar.actual;
  return (
    <ol className="camino-orden" aria-label="Por dónde va la orden">
      {PASOS_DEL_CAMINO.map((nombre, i) => {
        const hecho = i < lugar.hechos;
        const esActual = i === lugar.hechos && actual !== null;
        return (
          <li key={nombre} data-estado={hecho ? "hecho" : esActual ? actual : undefined} aria-current={esActual ? "step" : undefined}>
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
