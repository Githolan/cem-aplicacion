import { Link } from "react-router";

import { Icono } from "./Iconos";

/** Lo que encabeza una pantalla que cuelga de otra: el círculo de volver y su título. `estado`
 * viaja con la vuelta: lo que la pantalla de destino mostraba al salir de ella. */
export function CabeceraInterna({ titulo, volver, destino, estado }: { titulo: string; volver: string; destino: string; estado?: unknown }) {
  return (
    <div className="cabecera-interna">
      <Link to={volver} state={estado} className="cabecera-interna__volver" aria-label={`Volver a ${destino}`}>
        <Icono nombre="volver" />
      </Link>
      <h1>{titulo}</h1>
    </div>
  );
}
