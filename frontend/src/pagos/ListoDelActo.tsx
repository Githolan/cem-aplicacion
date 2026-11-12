import { useEffect, useState } from "react";
import { useNavigate } from "react-router";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { importe, mensajeDeError } from "../egresos/comun";
import { exigirSesion, SinSesion } from "../egresos/useConsorcio";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { CaminoOrden } from "./CaminoOrden";
import { rutaFicha } from "./rutas";
import { TEXTO_LE_TOCA_A, TEXTO_SITUACION } from "./textos";

// En PC, terminado un acto, el lugar de la ficha muestra lo que pasó con ESA orden: en qué estado
// quedó y a quién le toca. No se abre ninguna otra orden sola: la siguiente se abre recién con
// «Seguir con lo pendiente» (decisión del propietario del 12-11-2026, P-01).

type FichaPago = components["schemas"]["FichaPago"];
type Lectura = { tipo: "cargando" } | { tipo: "ok"; ficha: FichaPago } | { tipo: "falla"; texto: string };

/** Lo que se dice cuando el acto dejó la orden sin nada más para hacer, para nadie. */
export const SIN_NADA_MAS = "No hay nada más que hacer con ella.";
export const SEGUIR = "Seguir con lo pendiente";
export const NADA_PENDIENTE = "No queda nada pendiente.";

type Props = {
  consorcio: string;
  orden: string;
  /** El «Listo» del acto, como lo dijo la ficha. */
  texto: string;
  /** La orden que abre «Seguir con lo pendiente»; nula si no queda nada para hacer. */
  siguiente: string | null;
  quedan: number;
  aEntrar: () => void;
};

export function ListoDelActo({ consorcio, orden, texto, siguiente, quedan, aEntrar }: Props) {
  const navegar = useNavigate();
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });

  useEffect(() => {
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/pagos/{orden_id}", { params: { path: { consorcio_id: consorcio, orden_id: orden } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        setLectura(data === undefined ? { tipo: "falla", texto: mensajeDeError(response.status, codigoDe(error)) } : { tipo: "ok", ficha: data });
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else setLectura({ tipo: "falla", texto: SIN_CONEXION });
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, orden, aEntrar]);

  return (
    <section className="tarjeta listo-del-acto" aria-label="Lo que pasó con la orden">
      <p className="aviso-ok" role="status">
        {texto}
      </p>
      {lectura.tipo === "cargando" && <p role="status">Cargando la orden…</p>}
      {lectura.tipo === "falla" && (
        <p className="aviso-error" role="alert">
          No se pudo leer en qué quedó la orden. {lectura.texto}
        </p>
      )}
      {lectura.tipo === "ok" && (
        <>
          <dl className="detalles">
            <dt>Orden</dt>
            <dd>
              {lectura.ficha.orden.razon_social}, {importe(lectura.ficha.orden.importe_centavos, lectura.ficha.orden.moneda)}
            </dd>
            <dt>Quedó</dt>
            <dd>{TEXTO_SITUACION[lectura.ficha.situacion]}</dd>
            <dt>Ahora</dt>
            <dd>{lectura.ficha.le_toca_a === null ? SIN_NADA_MAS : TEXTO_LE_TOCA_A[lectura.ficha.le_toca_a]}</dd>
          </dl>
          <CaminoOrden situacion={lectura.ficha.situacion} paso={lectura.ficha.paso} />
        </>
      )}
      {siguiente === null ? (
        <p>{NADA_PENDIENTE}</p>
      ) : (
        <div className="acciones">
          <button type="button" onClick={() => navegar(rutaFicha(siguiente), { state: { consorcio } })}>
            {SEGUIR} ({quedan})
          </button>
        </div>
      )}
    </section>
  );
}
