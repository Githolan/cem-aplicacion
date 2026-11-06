import { NavLink } from "react-router";

import { RUTA_INICIO } from "../enlace";
import { cumpleEnAlguno, REQUISITOS, useAccionesPorEdificio } from "../permisos";
import { RUTA_PAGOS } from "./rutas";

// Las pestañas del teléfono, para quien tiene algo que ver con pagos (§1). Se ven solo en un ancho
// de teléfono (`estilos.css`); las montan las pantallas que ya saben que hay sesión.

export function Pestanas() {
  const porEdificio = useAccionesPorEdificio();
  if (porEdificio === null || !cumpleEnAlguno(porEdificio, REQUISITOS.pagos)) return null;
  return (
    <nav className="pestanas" aria-label="Secciones">
      <NavLink to={RUTA_INICIO} end>
        Inicio
      </NavLink>
      <NavLink to={RUTA_PAGOS}>Pagos</NavLink>
    </nav>
  );
}
