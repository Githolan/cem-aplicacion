import { type FormEvent, useEffect, useId, useState } from "react";
import { Link } from "react-router";

import { CabeceraInterna } from "../CabeceraInterna";
import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { type Aviso, ESTADO, importe, mensajeDeError, sinLaFalla, sinPisarLoHecho } from "../egresos/comun";
import { ElegirConsorcio } from "../egresos/ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "../egresos/EstadoPantalla";
import { exigirSesion, SinSesion, useConsorcio } from "../egresos/useConsorcio";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { REQUISITOS } from "../permisos";
import { RUTA_PAGOS, rutaFicha } from "./rutas";
import { FILTROS, type Filtro, TEXTO_SITUACION, TONO_SITUACION } from "./textos";

// `/pagos/consulta`: lo cerrado y lo que se busca (§11). Abre la misma ficha.

type Elemento = components["schemas"]["ElementoConsultaPagos"];
type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; elementos: Elemento[]; siguiente: number | null };
type Pedido = { busqueda: string; filtro: Filtro; saltar: number };

export function ConsultaPagos() {
  const elegido = useConsorcio(REQUISITOS.pagos);
  return <ConsultaDelEdificio key={elegido.consorcio} {...elegido} />;
}

function ConsultaDelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar }: ReturnType<typeof useConsorcio>) {
  const id = useId();
  const [escrito, setEscrito] = useState("");
  const [pedido, setPedido] = useState<Pedido>({ busqueda: "", filtro: "todos", saltar: 0 });
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [vuelta, setVuelta] = useState(0);

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    const { busqueda, filtro, saltar } = pedido;
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
  }, [consorcio, pedido, vuelta, aEntrar, aInicio]);

  /** Otra consulta empieza de cero: lo cargado y su «Ver más» eran de la anterior. */
  function consultar(cambio: Partial<Pedido>) {
    setLectura({ tipo: "cargando" });
    setPedido((p) => ({ ...p, ...cambio, saltar: 0 }));
  }

  function buscar(evento: FormEvent) {
    evento.preventDefault();
    consultar({ busqueda: escrito.trim() });
  }

  const siguiente = lectura.tipo === "ok" ? lectura.siguiente : null;

  return (
    <>
      <CabeceraInterna titulo="Consultar pagos" volver={RUTA_PAGOS} destino="Pagos" />
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={false} elegir={setConsorcio} />
      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">No tenés edificios en Consorcio en Mano.</p>}
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
              <button key={f.valor} type="button" className="secundario" aria-pressed={pedido.filtro === f.valor}
                onClick={() => consultar({ filtro: f.valor })}>
                {f.texto}
              </button>
            ))}
          </div>
          {lectura.tipo === "cargando" && <p role="status">Cargando los pagos…</p>}
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
          {lectura.tipo === "ok" && lectura.elementos.length === 0 && <p className="tarjeta">No encontramos pagos.</p>}
          {lectura.tipo === "ok" && lectura.elementos.length > 0 && (
            <div className="tabla-lista tabla-tarjetas">
              <table aria-label="Pagos">
                <thead>
                  <tr>
                    <th scope="col">Proveedor</th>
                    <th scope="col">Estado</th>
                    <th scope="col">Operación</th>
                    <th scope="col">Importe</th>
                  </tr>
                </thead>
                <tbody>
                  {lectura.elementos.map((e) => (
                    <tr key={e.orden_id}>
                      <th scope="row">
                        <Link to={rutaFicha(e.orden_id)} state={{ consorcio }} className="enlace-fila">
                          {e.proveedor}
                        </Link>
                        <p className="tabla-lista__dato">{e.concepto}</p>
                      </th>
                      <td data-etiqueta="Estado">
                        <span className="fila-pago__verbo" data-tono={TONO_SITUACION[e.situacion]}>
                          {TEXTO_SITUACION[e.situacion]}
                        </span>
                      </td>
                      <td data-etiqueta="Operación">{e.numero_operacion}</td>
                      <td data-etiqueta="Importe">
                        {importe(e.importe_centavos, e.moneda)}
                        {e.importe_pagado_centavos !== null && <p className="tabla-lista__dato">Se pagó {importe(e.importe_pagado_centavos, e.moneda)}</p>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {siguiente !== null && (
            <div className="formulario">
              <button type="button" className="secundario"
                onClick={() => setPedido((p) => ({ ...p, saltar: siguiente }))}>
                Ver más
              </button>
            </div>
          )}
        </>
      )}
    </>
  );
}
