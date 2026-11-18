import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "./api/cliente";
import { codigoDe, mensajeDeFalla } from "./fallas";
import { FallaDeLectura, SIN_CACHE } from "./permisos";
import { comprobarSesion, pedirEntrarDeNuevo } from "./sesionEncima";

// FL-CTA-01 (tanda 9, `SesionActual.alta_confirmacion_hasta`, app 0110): el alta de la confirmación (la
// llave o la aplicación de códigos) pide una sesión reciente. Si el plazo de esta sesión ya pasó, no se
// ofrece un botón que va a fallar: se dice que primero hay que entrar de nuevo, con ese único botón, y
// al entrar se ofrece activar.

export const ENTRAR_PARA_ACTIVAR = "Por seguridad, la confirmación se activa apenas se entra: primero hay que entrar de nuevo.";
export const BOTON_ENTRAR_PARA_ACTIVAR = "Entrar de nuevo para activar";

/** Hasta cuándo esta sesión puede activar la confirmación; null si el servidor no pone plazo. */
async function leerPlazo(): Promise<string | null> {
  const { data, error, response } = await api.GET("/api/v1/auth/sesion", { fetch: comprobarSesion });
  if (data === undefined) throw new FallaDeLectura(mensajeDeFalla(response.status, codigoDe(error)));
  return data.alta_confirmacion_hasta ?? null;
}

/** Si el plazo para activar ya pasó: `null` mientras no se sabe (o si no se pudo leer: entonces se
 * ofrece activar y, si el servidor pide entrar, lo pide la hoja de entrar encima). Al vencer el plazo
 * con la pantalla abierta, se dibuja de nuevo. */
export function useAltaVencida(activa: boolean) {
  const plazo = useQuery({ queryKey: ["alta-confirmacion-hasta"], queryFn: leerPlazo, enabled: activa, ...SIN_CACHE });
  const [ahora, setAhora] = useState(() => Date.now());
  const hasta = plazo.data === undefined || plazo.data === null ? null : Date.parse(plazo.data);
  // El plazo corre con la pantalla abierta: al llegar, se dibuja de nuevo.
  useEffect(() => {
    if (hasta === null || hasta <= ahora) return;
    const espera = setTimeout(() => setAhora(Date.now()), hasta - ahora);
    return () => clearTimeout(espera);
  }, [hasta, ahora]);
  const vencida = plazo.data === undefined ? null : hasta !== null && hasta <= ahora;
  return { vencida, plazo };
}

/** En lugar de los botones de activar, con el plazo vencido: entrar de nuevo, y al volver se ofrece activar. */
export function EntrarParaActivar({ plazo }: { plazo: ReturnType<typeof useAltaVencida>["plazo"] }) {
  const [entrando, setEntrando] = useState(false);
  return (
    <>
      <p>{ENTRAR_PARA_ACTIVAR}</p>
      <button
        type="button"
        disabled={entrando}
        onClick={() => {
          setEntrando(true);
          void pedirEntrarDeNuevo().then(() => {
            setEntrando(false);
            void plazo.refetch();
          });
        }}
      >
        {BOTON_ENTRAR_PARA_ACTIVAR}
      </button>
    </>
  );
}
