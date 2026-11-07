import { useQuery } from "@tanstack/react-query";
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
// mismo que la bandeja muestra para hacer: lo que espera a otra persona no es para hacer.

const NADA_PARA_HACER = "No tenés nada para hacer.";
const DETALLE_SIN_DATO = "Lo que tenés para hacer";
const DETALLE_SIN_LECTURA = "No pudimos leer lo que tenés para hacer";

async function cuantoHayParaHacer(consorcio: string): Promise<number> {
  const { data, error, response } = await api.GET("/api/v1/consorcios/{consorcio_id}/pagos/bandeja", {
    params: { path: { consorcio_id: consorcio } },
  });
  if (data === undefined) throw new FallaDeLectura(mensajeDeFalla(response.status, codigoDe(error)));
  return data.elementos.filter((e) => e.grupo !== "esperando").length;
}

const cosas = (cantidad: number) => (cantidad === 1 ? "cosa" : "cosas");

/** El edificio del resumen, entre los que la persona ve Pagos, y cuánto tiene para hacer en él. */
export function useResumenPagos(porEdificio: ReadonlyMap<string, readonly string[]> | null) {
  const { edificios, consorcio, setConsorcio } = useEdificioElegido(useMisConsorcios(), REQUISITOS.pagos);
  const consulta = useQuery({
    queryKey: ["para-hacer", consorcio],
    queryFn: () => cuantoHayParaHacer(consorcio),
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
    consorcio,
    setConsorcio,
    /** Quien revisa y no prepara tiene Pagos como tarjeta; quien prepara, como fila entre las demás. */
    enTarjeta: porEdificio !== null && consorcio !== "" && !cumple(porEdificio.get(consorcio) ?? [], REQUISITOS.ordenes),
    prepara: porEdificio !== null && cumple(porEdificio.get(consorcio) ?? [], REQUISITOS.ordenes),
    detalle,
    cantidad,
    falla: consulta.isError ? mensajeDeLectura(consulta.error) : null,
    reintentar: () => void consulta.refetch(),
    reintentando: consulta.isFetching,
  };
}

type Props = Pick<ReturnType<typeof useResumenPagos>, "cantidad" | "falla" | "reintentar" | "reintentando"> & {
  edificio: string;
};

export function TarjetaPagos({ cantidad, falla, reintentar, reintentando, edificio }: Props) {
  return (
    <section className="resumen-inicio" aria-label="Pagos">
      <div className="resumen-inicio__arriba">
        <span>Pagos</span>
        {cantidad !== null && cantidad > 0 && <span className="resumen-inicio__estado">Para hacer</span>}
      </div>
      {cantidad === null && falla === null && <p role="status">Cargando…</p>}
      {falla !== null && <p role="alert" className="aviso-error">{falla}</p>}
      {falla !== null && (
        <button type="button" className="secundario" disabled={reintentando} onClick={reintentar}>
          Reintentar
        </button>
      )}
      {cantidad === 0 && <p>{NADA_PARA_HACER}</p>}
      {cantidad !== null && cantidad > 0 && (
        <p>
          <span className="resumen-inicio__cifra">{cantidad}</span> {cosas(cantidad)} para hacer
        </p>
      )}
      <p className="resumen-inicio__edificio">{edificio}</p>
      <Link to={RUTA_PAGOS} className="resumen-inicio__accion">
        Ver los pagos
      </Link>
    </section>
  );
}
