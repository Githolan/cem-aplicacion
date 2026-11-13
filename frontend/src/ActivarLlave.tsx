import { useState } from "react";

import { llaveDisponible, registrarLlave } from "./llaveAcceso";

// Activar la llave de acceso ahí mismo, sin ir a otra pantalla (informe de fricción, AC-21 en Mi
// cuenta y AC-34 en la hoja de confirmar un acto): el mismo alta que la página del segundo factor
// (`registrarLlave`), con los mismos controles del servidor (entrada reciente si es el primer medio;
// confirmar con el que ya haya si es otro). El alta deja la sesión verificada. Si llegan códigos de
// recuperación, los muestra quien usa el botón (`alActivar`), que no los suelta hasta que la persona
// dice que los guardó. Si el navegador no sabe usar llaves, no aparece.

export const ACTIVAR_CON_LLAVE = "Activar con PIN, huella o cara";

interface Props {
  /** Quedó activada; trae los códigos de recuperación si son nuevos. */
  alActivar: (codigos: string[] | null) => void;
  /** Botón principal (por omisión) o secundario. */
  secundario?: boolean;
}

export function ActivarLlave({ alActivar, secundario = false }: Props) {
  const [enviando, setEnviando] = useState(false);
  const [falla, setFalla] = useState<string | null>(null);

  if (!llaveDisponible()) return null;

  function activar() {
    setEnviando(true);
    setFalla(null);
    void registrarLlave()
      .then((r) => {
        if (r.ok) alActivar(r.valor);
        else setFalla(r.mensaje);
      })
      .finally(() => setEnviando(false));
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
