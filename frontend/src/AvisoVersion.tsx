import { useEffect, useRef } from "react";
import { useLocation } from "react-router";

import { useVersion } from "./version";

// Documento 13, §14.4: la versión nueva no se aplica sola mientras la persona trabaja en una pantalla
// (podría tener algo escrito). Informe de fricción, MEN-07: se aplica sola en el próximo cambio de
// pantalla, cuando lo de la pantalla anterior ya quedó atrás; mientras tanto queda el aviso con
// «Actualizar», para quien quiera aplicarla antes.
export function AvisoVersion() {
  const { hayVersionNueva, actualizar } = useVersion();

  if (!hayVersionNueva) return null;
  return (
    <div role="alert" className="aviso-version">
      <span>Hay una versión nueva de la aplicación.</span>
      <button type="button" onClick={actualizar}>
        Actualizar
      </button>
    </div>
  );
}

/** Si la navegación trae el resultado de un acto (`state.aviso`): recargar ahora lo perdería. */
function traeAviso(estado: unknown): boolean {
  return typeof estado === "object" && estado !== null && "aviso" in estado;
}

/** Aplica la versión nueva al pasar a otra pantalla (MEN-07). Va dentro del router. */
export function VersionAlCambiarDePantalla() {
  const { hayVersionNueva, actualizar } = useVersion();
  const { pathname, state } = useLocation();
  const anterior = useRef(pathname);
  useEffect(() => {
    if (anterior.current === pathname) return;
    anterior.current = pathname;
    // Con un «Listo» en camino se espera al cambio siguiente: la recarga lo borraría.
    if (hayVersionNueva && !traeAviso(state)) actualizar();
  }, [pathname, state, hayVersionNueva, actualizar]);
  return null;
}
