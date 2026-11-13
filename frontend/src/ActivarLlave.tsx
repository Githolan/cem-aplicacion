import { useState } from "react";

import { llaveDisponible, registrarLlave } from "./llaveAcceso";

// Activar la llave de acceso ahí mismo, sin ir a otra pantalla (informe de fricción, AC-21 en Mi
// cuenta y AC-34 en la hoja de confirmar un acto): el mismo alta que la página del segundo factor
// (`registrarLlave`), con los mismos controles del servidor (entrada reciente si es el primer medio;
// confirmar con el que ya haya si es otro). El alta deja la sesión verificada. Si llegan códigos de
// recuperación, los muestra quien usa el botón (`alActivar`), que no los suelta hasta que la persona
// dice que los guardó. Si el navegador no sabe usar llaves, no aparece. Mientras el alta está en
// curso, quien usa el botón lo sabe (`alOcupar`) y no deja cerrar ni salir: si se fuera con el
// pedido en camino, los códigos llegarían a una pantalla que ya no está (revisión de #237).

export const ACTIVAR_CON_LLAVE = "Activar con PIN, huella o cara";

interface Props {
  /** Quedó activada; trae los códigos de recuperación si son nuevos. */
  alActivar: (codigos: string[] | null) => void;
  /** Empieza (true) o termina (false) el alta. */
  alOcupar?: (ocupado: boolean) => void;
  /** Botón principal (por omisión) o secundario. */
  secundario?: boolean;
}

export function ActivarLlave({ alActivar, alOcupar, secundario = false }: Props) {
  const [enviando, setEnviando] = useState(false);
  const [falla, setFalla] = useState<string | null>(null);

  if (!llaveDisponible()) return null;

  function activar() {
    setEnviando(true);
    setFalla(null);
    alOcupar?.(true);
    void registrarLlave().then((r) => {
      // En el mismo paso: quien espera deja de estar ocupado y ya tiene los códigos a la vista.
      setEnviando(false);
      alOcupar?.(false);
      if (r.ok) alActivar(r.valor);
      else setFalla(r.mensaje);
    });
  }

  return (
    <>
      <button type="button" data-foco className={secundario ? "secundario" : undefined} onClick={activar} disabled={enviando}>
        {enviando ? "Esperando el PIN o la huella…" : ACTIVAR_CON_LLAVE}
      </button>
      {falla !== null && (
        <p className="aviso-error" role="alert">
          {falla}
        </p>
      )}
    </>
  );
}
