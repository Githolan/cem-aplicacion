import { type FormEvent, useEffect, useId, useState } from "react";
import { useLocation, useSearchParams } from "react-router";

import { CabeceraInterna } from "../CabeceraInterna";
import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { type Aviso, ESTADO, importe, mensajeDeError, sinLaFalla, sinPisarLoHecho } from "../egresos/comun";
import { ElegirConsorcio } from "../egresos/ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "../egresos/EstadoPantalla";
import { exigirSesion, SinSesion, useConsorcio } from "../egresos/useConsorcio";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { REQUISITOS } from "../permisos";
import { CargandoPagos } from "./CargandoPagos";
import { FilaEnlace } from "./FilaEnlace";
import { RUTA_CONSULTA, RUTA_PAGOS, rutaFicha } from "./rutas";
import { fechaCorta, FILTROS, type Filtro, NOMBRE_ESTADO } from "./textos";

// `/pagos/consulta`: lo cerrado y lo que se busca (§11). Abre la misma ficha, sola, y su «volver»
// regresa acá con lo buscado: la búsqueda y el filtro van en la dirección (CNS-01). Cada pago es
// una fila compacta, como el Padrón: proveedor; estado, fecha e importe (CNS-02). El concepto y el
// número de operación están en la ficha.

type Elemento = components["schemas"]["ElementoConsultaPagos"];
type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; elementos: Elemento[]; siguiente: number | null };

const PARAM_BUSQUEDA = "busqueda";
const PARAM_FILTRO = "filtro";
const FILTRO_INICIAL: Filtro = "todos";

const filtroDe = (valor: string | null): Filtro => FILTROS.find((f) => f.valor === valor)?.valor ?? FILTRO_INICIAL;

export function ConsultaPagos() {
  const elegido = useConsorcio(REQUISITOS.pagos);
  return <ConsultaDelEdificio key={elegido.consorcio} {...elegido} />;
}

function ConsultaDelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar }: ReturnType<typeof useConsorcio>) {
  const id = useId();
  const { search } = useLocation();
  const [parametros, setParametros] = useSearchParams();
  const busqueda = parametros.get(PARAM_BUSQUEDA) ?? "";
  const filtro = filtroDe(parametros.get(PARAM_FILTRO));
  const [escrito, setEscrito] = useState(busqueda);
  /** Cuántos saltear: «Ver más» pide la página siguiente; una consulta nueva vuelve a cero. */
  const [saltar, setSaltar] = useState(0);
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [vuelta, setVuelta] = useState(0);

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    // Si falla «Ver más», lo ya cargado queda y el mismo botón vuelve a pedir esa página.
    const falla = (l: Lectura): Lectura => (saltar > 0 && l.tipo === "ok" ? l : { tipo: "sin_servicio" });
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/pagos/consulta", {
        params: {
          path: { consorcio_id: consorcio },
          query: { filtro, saltar, ...(busqueda === "" ? {} : { busqueda }) },
        },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        const codigo = codigoDe(error);
        if (data !== undefined) {
          setAviso(sinLaFalla);
          setLectura((l) => ({
            tipo: "ok",
            // Una página más se suma a lo que ya se ve; una búsqueda nueva empieza de cero.
            elementos: saltar > 0 && l.tipo === "ok" ? [...l.elementos, ...data.elementos] : data.elementos,
            siguiente: data.siguiente,
          }));
        } else if (response.status === ESTADO.sinPermiso && codigo === "sin_permiso") aInicio();
        else {
          setLectura(falla);
          setAviso(sinPisarLoHecho(mensajeDeError(response.status, codigo)));
        }
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else {
          setLectura(falla);
          setAviso(sinPisarLoHecho(SIN_CONEXION));
        }
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, busqueda, filtro, saltar, vuelta, aEntrar, aInicio]);

  /** Otra consulta empieza de cero: lo cargado y su «Ver más» eran de la anterior. Queda en la
   * dirección, sin sumar pasos al historial, para volver a ella desde la ficha. */
  function consultar(cambio: { busqueda?: string; filtro?: Filtro }) {
    const nueva = { busqueda: cambio.busqueda ?? busqueda, filtro: cambio.filtro ?? filtro };
    setLectura({ tipo: "cargando" });
    setSaltar(0);
    setVuelta((v) => v + 1);
    setParametros(
      {
        ...(nueva.busqueda === "" ? {} : { [PARAM_BUSQUEDA]: nueva.busqueda }),
        ...(nueva.filtro === FILTRO_INICIAL ? {} : { [PARAM_FILTRO]: nueva.filtro }),
      },
      { replace: true },
    );
  }

  function buscar(evento: FormEvent) {
    evento.preventDefault();
    consultar({ busqueda: escrito.trim() });
  }

  const siguiente = lectura.tipo === "ok" ? lectura.siguiente : null;
  /** Adonde vuelve la ficha abierta desde acá: esta consulta, con lo buscado. */
  const volver = `${RUTA_CONSULTA}${search}`;

  return (
    <>
      <CabeceraInterna titulo="Consultar pagos" volver={RUTA_PAGOS} destino="Pagos" />
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={false} elegir={setConsorcio} />
      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">Esta cuenta no tiene edificios en Consorcio en Mano.</p>}
      <ConsorcioSinServicio pantalla={pantalla} reintentar={reintentar} />
      <AvisoPantalla aviso={aviso} />
      {pantalla.tipo === "lista" && consorcio !== "" && (
        <>
          <form className="formulario" role="search" onSubmit={buscar}>
            <label htmlFor={`${id}-buscar`}>Proveedor, concepto o número de operación</label>
            <input id={`${id}-buscar`} type="search" maxLength={100} value={escrito} onChange={(e) => setEscrito(e.target.value)} />
            <button type="submit">Buscar</button>
          </form>
          <div className="filtros-pago" role="group" aria-label="Filtrar">
            {FILTROS.map((f) => (
              <button key={f.valor} type="button" className="secundario" aria-pressed={filtro === f.valor}
                onClick={() => consultar({ filtro: f.valor })}>
                {f.texto}
              </button>
            ))}
          </div>
          {lectura.tipo === "cargando" && <CargandoPagos />}
          {lectura.tipo === "sin_servicio" && (
            <FallaDeLectura
              alReintentar={() => {
                setAviso(sinLaFalla);
                setLectura({ tipo: "cargando" });
                setVuelta((v) => v + 1);
              }}
              avisar={setAviso}
            />
          )}
          {lectura.tipo === "ok" && lectura.elementos.length === 0 && <p className="tarjeta">No se encontraron pagos.</p>}
          {lectura.tipo === "ok" && lectura.elementos.length > 0 && (
            <ul className="filas-registro" aria-label="Pagos">
              {lectura.elementos.map((e) => (
                <li key={e.orden_id}>
                  <FilaEnlace
                    a={rutaFicha(e.orden_id)}
                    estadoNavegacion={{ consorcio, volver }}
                    titulo={e.proveedor}
                    resumen={
                      <>
                        {NOMBRE_ESTADO[e.estado_visible]} · <time dateTime={e.creada_en}>{fechaCorta(e.creada_en)}</time> ·{" "}
                        {importe(e.importe_centavos, e.moneda)}
                        {e.importe_pagado_centavos !== null && <> · Se pagó {importe(e.importe_pagado_centavos, e.moneda)}</>}
                      </>
                    }
                  />
                </li>
              ))}
            </ul>
          )}
          {siguiente !== null && (
            <div className="formulario">
              <button
                type="button"
                className="secundario"
                onClick={() => {
                  setSaltar(siguiente);
                  // Si falló, `saltar` no cambia y el mismo botón tiene que volver a pedir.
                  setVuelta((v) => v + 1);
                }}
              >
                Ver más
              </button>
            </div>
          )}
        </>
      )}
    </>
  );
}
