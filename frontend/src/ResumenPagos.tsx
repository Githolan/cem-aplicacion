import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Link } from "react-router";

import { api } from "./api/cliente";
import { codigoDe, mensajeDeFalla } from "./fallas";
import { RUTA_PAGOS } from "./pagos/rutas";
import {
  cumple,
  FallaDeLectura,
  mensajeDeLectura,
  REQUISITOS,
  SIN_CACHE,
  useEdificioElegido,
  useMisConsorcios,
} from "./permisos";

// Lo que el inicio dice de Pagos, de un edificio por vez: la bandeja es de un edificio. Cuenta lo
// que el servidor marca como pendiente (la tabla de la pantalla): lo que espera a otra persona, los
// borradores viejos y los avisos de rechazo no son para hacer. Los pagos por cotejar, sí, y para
// quien coteja también lo que el extracto no explica (pregunta 8): para la administración es solo
// una consulta, como en la bandeja.

const NADA_PARA_HACER = "Nada pendiente.";
const DETALLE_SIN_DATO = "Pendientes";
const DETALLE_SIN_LECTURA = "No se pudieron leer los pendientes";

async function cuantoHayParaHacer(consorcio: string, coteja: boolean): Promise<number> {
  const { data, error, response } = await api.GET("/api/v1/consorcios/{consorcio_id}/pagos/bandeja", {
    params: { path: { consorcio_id: consorcio } },
  });
  if (data === undefined) throw new FallaDeLectura(mensajeDeFalla(response.status, codigoDe(error)));
  return data.elementos.filter((e) => e.pendiente).length + data.por_cotejar + (coteja ? data.sin_explicar : 0);
}

const cosas = (cantidad: number) => (cantidad === 1 ? "cosa" : "cosas");

/** El edificio del resumen, entre los que la persona ve Pagos, y cuánto tiene para hacer en él. */
export function useResumenPagos(porEdificio: ReadonlyMap<string, readonly string[]> | null) {
  const { edificios, consorcio, setConsorcio } = useEdificioElegido(useMisConsorcios(), REQUISITOS.pagos);
  const coteja = cumple(porEdificio?.get(consorcio) ?? [], REQUISITOS.cotejo);
  const consulta = useQuery({
    queryKey: ["para-hacer", consorcio, coteja],
    queryFn: () => cuantoHayParaHacer(consorcio, coteja),
    enabled: porEdificio !== null && consorcio !== "",
    ...SIN_CACHE,
  });
  const cantidad = consulta.data ?? null;
  const detalle =
    cantidad === null
      ? consulta.isError
        ? DETALLE_SIN_LECTURA
        : DETALLE_SIN_DATO
      : cantidad === 0
        ? NADA_PARA_HACER
        : `${cantidad} ${cosas(cantidad)} para hacer`;
  return {
    edificios,
    /** Con dos o más edificios el inicio muestra el selector «Edificio» (`ElegirConsorcio`). */
    conSelector: edificios.length > 1,
    consorcio,
    setConsorcio,
    /** Quien revisa y no prepara tiene Pagos como tarjeta; quien prepara, como fila entre las demás.
     * Sin nada pendiente no hay tarjeta: Pagos queda como una fila más, «Nada pendiente.» (AC-01). */
    enTarjeta:
      porEdificio !== null && consorcio !== "" && cantidad !== 0 && !cumple(porEdificio.get(consorcio) ?? [], REQUISITOS.ordenes),
    detalle,
    cantidad,
    falla: consulta.isError ? mensajeDeLectura(consulta.error) : null,
    reintentar: () => void consulta.refetch(),
    reintentando: consulta.isFetching,
  };
}

type Props = Pick<ReturnType<typeof useResumenPagos>, "cantidad" | "falla"> & {
  /** El único «Reintentar» del Inicio, cuando lo único que falló es esta tarjeta (AC-08). */
  reintentar: ReactNode;
  /** El edificio, solo si no está a la vista el selector «Edificio», que ya lo dice (INI-06). */
  edificio: string | null;
};

/** «Pagos — N cosas para hacer»: la tarjeta entera lleva a la bandeja, sin una marca «Para hacer»
 * que repita la cifra ni un botón aparte (BAN-09, INI-06). */
export function TarjetaPagos({ cantidad, falla, reintentar, edificio }: Props) {
  return (
    <section className="resumen-inicio" aria-label="Pagos">
      <Link to={RUTA_PAGOS} className="resumen-inicio__enlace">
        <span className="resumen-inicio__arriba">Pagos</span>
        {cantidad !== null && cantidad > 0 && (
          <>
            {" "}
            <span className="resumen-inicio__cuenta">
              <span className="resumen-inicio__cifra">{cantidad}</span> {cosas(cantidad)} para hacer
            </span>
          </>
        )}
        {edificio !== null && (
          <>
            {" "}
            <span className="resumen-inicio__edificio">{edificio}</span>
          </>
        )}
      </Link>
      {cantidad === null && falla === null && <p role="status">Cargando…</p>}
      {falla !== null && <p role="alert" className="aviso-error">{falla}</p>}
      {falla !== null && reintentar}
    </section>
  );
}
