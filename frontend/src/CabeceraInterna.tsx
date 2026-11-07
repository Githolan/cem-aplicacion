import { Link } from "react-router";

import { Icono } from "./Iconos";

/** Lo que encabeza una pantalla que cuelga de otra: el círculo de volver y su título. */
export function CabeceraInterna({ titulo, volver, destino }: { titulo: string; volver: string; destino: string }) {
  return (
    <div className="cabecera-interna">
      <Link to={volver} className="cabecera-interna__volver" aria-label={`Volver a ${destino}`}>
        <Icono nombre="volver" />
      </Link>
      <h1>{titulo}</h1>
    </div>
  );
}
