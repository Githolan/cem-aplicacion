import { useEffect, useState } from "react";
import { useLocation, useNavigate, useNavigationType } from "react-router";

// El resultado de la acción que acaba de terminar, en la pantalla a la que llevó (regla de
// fricción, decisión del propietario del 05-11-2026). Viaja en `state.aviso` de la navegación. Se
// borra del historial apenas se lee, para que una recarga no lo repita, y sigue a la vista si una
// redirección (`replace`) lleva a otra pantalla; se va con la navegación siguiente.

function avisoDe(estado: unknown): string | null {
  if (typeof estado !== "object" || estado === null || !("aviso" in estado)) return null;
  return typeof estado.aviso === "string" ? estado.aviso : null;
}

export function AvisoLlegada() {
  const lugar = useLocation();
  const tipo = useNavigationType();
  const navegar = useNavigate();
  const [aviso, setAviso] = useState<string | null>(null);

  useEffect(() => {
    const nuevo = avisoDe(lugar.state);
    if (nuevo !== null) {
      setAviso(nuevo);
      const { pathname, search, hash } = lugar;
      navegar({ pathname, search, hash }, { replace: true, state: null });
    } else if (tipo !== "REPLACE") setAviso(null);
  }, [lugar, tipo, navegar]);

  return (
    <div role="status" aria-live="polite">
      {aviso !== null && <p className="aviso-ok">{aviso}</p>}
    </div>
  );
}
