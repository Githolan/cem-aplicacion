import { useQuery } from "@tanstack/react-query";

import { api, type Salud } from "./api/cliente";

// El estado del servicio (rediseño de pagos, §12): un punto en la barra de Inicio y un aviso chico
// solo si no funciona. La versión va al pie de Mi cuenta. La consulta es una sola, compartida.

export const TEXTO_FUNCIONA = "El servicio funciona";
export const TEXTO_NO_FUNCIONA = "El servicio no funciona";
const TEXTO_CONSULTANDO = "Consultando el servicio";
const LARGO_SHA_CORTA = 7;

async function consultarSalud(): Promise<Salud> {
  const { data, error } = await api.GET("/health");
  if (error !== undefined || data === undefined) throw new Error("sin respuesta");
  return data;
}

const useSalud = (enabled = true) => useQuery({ queryKey: ["salud"], queryFn: consultarSalud, enabled });

export function PuntoServicio() {
  const salud = useSalud();
  const [clase, nombre] = salud.isError
    ? ["punto-servicio--mal", TEXTO_NO_FUNCIONA]
    : salud.isSuccess
      ? ["punto-servicio--bien", TEXTO_FUNCIONA]
      : ["", TEXTO_CONSULTANDO];
  return <span role="img" aria-label={nombre} className={`punto-servicio ${clase}`} />;
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

export function PieDeVersion() {
  const { data } = useSalud();
  if (data === undefined) return null;
  return (
    <p className="pie-version">
      Versión {data.version} · Contrato {data.version_contrato} · Compilación{" "}
      <code>{data.sha_imagen.slice(0, LARGO_SHA_CORTA)}</code>
    </p>
  );
}
