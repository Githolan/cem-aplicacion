import { Link } from "react-router";

import { RUTA_INICIO } from "./enlace";
import { Icono } from "./Iconos";

/** Lo que encabeza una pantalla que cuelga de otra: el círculo de volver y su título. El que vuelve
 * al inicio es solo para el teléfono y la tablet: en la PC la barra lateral ya tiene «Inicio» y la
 * pantalla misma (FL-SOL-07), así que desde 1025 px queda el título solo. `estado` viaja con la
 * vuelta: lo que la pantalla de destino mostraba al salir de ella. */
export function CabeceraInterna({ titulo, volver, destino, estado }: { titulo: string; volver: string; destino: string; estado?: unknown }) {
  const clase = volver === RUTA_INICIO ? "cabecera-interna__volver cabecera-interna__volver--angosto" : "cabecera-interna__volver";
  return (
    <div className="cabecera-interna">
      <Link to={volver} state={estado} className={clase} aria-label={`Volver a ${destino}`}>
        <Icono nombre="volver" />
      </Link>
      <h1>{titulo}</h1>
    </div>
  );
}
