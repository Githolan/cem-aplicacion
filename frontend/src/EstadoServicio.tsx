import { useQuery } from "@tanstack/react-query";

import { api, type Salud } from "./api/cliente";

// El estado del servicio (rediseño de pagos, §12): un punto en la barra de Inicio y un aviso chico,
// los dos solo si no funciona (comentario 19 del propietario, INI-09): mientras funciona no hay nada
// que decir. La versión va al pie de Cuenta, plegada (CTA-05). La consulta es una sola, compartida.

export const TEXTO_NO_FUNCIONA = "El servicio no funciona";
const LARGO_SHA_CORTA = 7;

async function consultarSalud(): Promise<Salud> {
  const { data, error } = await api.GET("/health");
  if (error !== undefined || data === undefined) throw new Error("sin respuesta");
  return data;
}

const useSalud = (enabled = true) => useQuery({ queryKey: ["salud"], queryFn: consultarSalud, enabled });

export function PuntoServicio() {
  const salud = useSalud();
  if (!salud.isError) return null;
  return <span role="img" aria-label={TEXTO_NO_FUNCIONA} className="punto-servicio punto-servicio--mal" />;
}

/** Si el servicio no responde. El Inicio lo dice junto con las otras lecturas que fallaron, con
 * un solo «Reintentar» para todas (AC-08). Solo con la sesión conocida: sin sesión, el inicio
 * lleva a entrar sin consultar el servicio. */
export function useFallaDelServicio(conSesion: boolean) {
  const salud = useSalud(conSesion);
  return {
    falla: salud.isError ? `${TEXTO_NO_FUNCIONA} en este momento.` : null,
    reintentar: () => void salud.refetch(),
    reintentando: salud.isFetching,
  };
}

/** Plegado detrás de «Acerca de la aplicación» (CTA-05): sirve para soporte, no para el día a día. */
export function PieDeVersion() {
  const { data } = useSalud();
  if (data === undefined) return null;
  return (
    <details className="pie-version">
      <summary>Acerca de la aplicación</summary>
      <p>
        Versión {data.version} · Contrato {data.version_contrato} · Compilación{" "}
        <code>{data.sha_imagen.slice(0, LARGO_SHA_CORTA)}</code>
      </p>
    </details>
  );
}
