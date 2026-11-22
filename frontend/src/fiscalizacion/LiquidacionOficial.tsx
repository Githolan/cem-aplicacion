import { useEffect, useId, useState } from "react";

import { CabeceraInterna } from "../CabeceraInterna";
import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { RUTA_INICIO } from "../enlace";
import { type Aviso, ESTADO, mensajeDeError, sinLaFalla, sinPisarLoHecho } from "../egresos/comun";
import { ElegirConsorcio } from "../egresos/ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "../egresos/EstadoPantalla";
import { periodo as mesLargo } from "../egresos/Liquidacion";
import { exigirSesion, SinSesion, useConsorcio } from "../egresos/useConsorcio";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { FilaDeRegistro } from "../FichaDeRegistro";
import { fechaCorta } from "../pagos/textos";
import { REQUISITOS } from "../permisos";
import { enPesos } from "./LiquidacionDelAdministrador";

// Página /liquidacion-oficial (H5 §6, D3-A; app 0119), en solo lectura: para el Consejo y la
// administración (`liquidacion.ver_importada`, la facultad del panel), la liquidación oficial de cada
// período —la que la administración mandó y el Consejo confirmó— con sus rubros, sus gastos, las
// unidades y lo que no cierra (RN33: dice la diferencia, no la califica). Las reemplazadas quedan
// como historia, con el motivo del reemplazo. Nunca una importación sin confirmar (RN30).

type Oficial = components["schemas"]["LiquidacionOficialResumen"];
type Detalle = components["schemas"]["LiquidacionOficial"];
type Campo = components["schemas"]["CampoOficial"];

export const RUTA_LIQUIDACION_OFICIAL = "/liquidacion-oficial";
export const TITULO_LIQUIDACION_OFICIAL = "Liquidación oficial";
export const SIN_OFICIALES = "Todavía no hay ninguna liquidación de la administración confirmada.";
export const VIGENTE = "Vigente";
export const REEMPLAZADA = "Reemplazada";
const VOLVER = "Volver a la lista";

const importe = (v: unknown) => (typeof v === "string" ? enPesos(v) : "—");
const textoDe = (v: unknown) => (typeof v === "string" && v !== "" ? v : typeof v === "number" ? String(v) : "—");

/** La historia de una liquidación: a quién reemplazó o quién la reemplazó, con el motivo. */
export function historiaDe(o: Pick<Oficial, "id" | "reemplaza_a" | "motivo_reemplazo" | "reemplazada_por">, todas: readonly Oficial[]): string[] {
  const lineas: string[] = [];
  if (o.reemplazada_por !== null) {
    const por = todas.find((x) => x.id === o.reemplazada_por);
    lineas.push(
      por === undefined
        ? "Reemplazada por otra liquidación del mismo período."
        : `Reemplazada por la confirmada el ${fechaCorta(por.decidida_en)}. Motivo: ${por.motivo_reemplazo ?? ""}`,
    );
  }
  if (o.reemplaza_a !== null) {
    const anterior = todas.find((x) => x.id === o.reemplaza_a);
    const cuando = anterior === undefined ? "" : ` confirmada el ${fechaCorta(anterior.decidida_en)}`;
    lineas.push(`Reemplazó a la liquidación${cuando}. Motivo: ${o.motivo_reemplazo ?? ""}`);
  }
  return lineas;
}

type Lista = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; oficiales: Oficial[] };
type Abierta = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; detalle: Detalle };

export function LiquidacionOficial() {
  const elegido = useConsorcio(REQUISITOS.liquidacionOficial);
  return <DelEdificio key={elegido.consorcio} {...elegido} />;
}

function DelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar }: ReturnType<typeof useConsorcio>) {
  const id = useId();
  const [lista, setLista] = useState<Lista>({ tipo: "cargando" });
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [vuelta, setVuelta] = useState(0);
  const [elegida, setElegida] = useState<string | null>(null);
  const [abierta, setAbierta] = useState<Abierta>({ tipo: "cargando" });
  const [vueltaDetalle, setVueltaDetalle] = useState(0);

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/liquidacion-oficial", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        const codigo = codigoDe(error);
        if (data !== undefined) {
          setAviso(sinLaFalla);
          setLista({ tipo: "ok", oficiales: data });
        } else if (response.status === ESTADO.sinPermiso && codigo === "sin_permiso") aInicio();
        else {
          setLista({ tipo: "sin_servicio" });
          setAviso(sinPisarLoHecho(mensajeDeError(response.status, codigo)));
        }
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else {
          setLista({ tipo: "sin_servicio" });
          setAviso(sinPisarLoHecho(SIN_CONEXION));
        }
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, vuelta, aEntrar, aInicio]);

  useEffect(() => {
    if (consorcio === "" || elegida === null) return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/liquidacion-oficial/{importacion_id}", {
        params: { path: { consorcio_id: consorcio, importacion_id: elegida } },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        if (data !== undefined) setAbierta({ tipo: "ok", detalle: data });
        else {
          setAbierta({ tipo: "sin_servicio" });
          setAviso(sinPisarLoHecho(mensajeDeError(response.status, codigoDe(error))));
        }
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else {
          setAbierta({ tipo: "sin_servicio" });
          setAviso(sinPisarLoHecho(SIN_CONEXION));
        }
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, elegida, vueltaDetalle, aEntrar]);

  const oficiales = lista.tipo === "ok" ? lista.oficiales : [];
  const detalle = abierta.tipo === "ok" && elegida !== null ? abierta.detalle : null;

  return (
    <>
      <CabeceraInterna titulo={TITULO_LIQUIDACION_OFICIAL} volver={RUTA_INICIO} destino="Inicio" />
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={false} elegir={setConsorcio} />
      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">Esta cuenta no tiene edificios en Consorcio en Mano.</p>}
      <ConsorcioSinServicio pantalla={pantalla} reintentar={reintentar} />
      <AvisoPantalla aviso={aviso} />

      {pantalla.tipo === "lista" && consorcio !== "" && elegida === null && (
        <>
          {lista.tipo === "cargando" && <p role="status">Cargando las liquidaciones…</p>}
          {lista.tipo === "sin_servicio" && (
            <FallaDeLectura
              alReintentar={() => {
                setAviso(sinLaFalla);
                setLista({ tipo: "cargando" });
                setVuelta((v) => v + 1);
              }}
              avisar={setAviso}
            />
          )}
          {lista.tipo === "ok" &&
            (oficiales.length === 0 ? (
              <p className="tarjeta">{SIN_OFICIALES}</p>
            ) : (
              <ul className="filas-registro" aria-label="Liquidaciones oficiales">
                {oficiales.map((o) => (
                  <li key={o.id}>
                    <FilaDeRegistro
                      titulo={`Período ${mesLargo(o.periodo)}`}
                      resumen={`Confirmada el ${fechaCorta(o.decidida_en)}${o.diferencias > 0 ? ` · ${o.diferencias} sin cerrar` : ""}`}
                      estado={o.vigente ? VIGENTE : REEMPLAZADA}
                      alAbrir={() => {
                        setAbierta({ tipo: "cargando" });
                        setElegida(o.id);
                      }}
                      deshabilitada={false}
                    />
                  </li>
                ))}
              </ul>
            ))}
        </>
      )}

      {pantalla.tipo === "lista" && elegida !== null && (
        <section aria-labelledby={`${id}-titulo`}>
          <p>
            <button
              type="button"
              className="secundario"
              onClick={() => {
                setElegida(null);
                setVuelta((v) => v + 1);
              }}
            >
              {VOLVER}
            </button>
          </p>
          {abierta.tipo === "cargando" && <p role="status">Cargando la liquidación…</p>}
          {abierta.tipo === "sin_servicio" && (
            <FallaDeLectura
              alReintentar={() => {
                setAviso(sinLaFalla);
                setAbierta({ tipo: "cargando" });
                setVueltaDetalle((v) => v + 1);
              }}
              avisar={setAviso}
            />
          )}
          {detalle !== null && <DeUnPeriodo detalle={detalle} oficiales={oficiales} idTitulo={`${id}-titulo`} />}
        </section>
      )}
    </>
  );
}

function deTipo(campos: readonly Campo[], tipo: Campo["tipo"]): Campo[] {
  return campos.filter((c) => c.tipo === tipo);
}

type FilaUnidad = { clave: string; nombre: string; saldo?: Record<string, unknown>; importe?: Record<string, unknown> };

/** Las filas de las unidades, una por unidad de la liquidación (su UF y su departamento). */
function unidadesDe(campos: readonly Campo[]): FilaUnidad[] {
  const filas = new Map<string, FilaUnidad>();
  for (const c of campos) {
    if (c.tipo !== "unidad" && c.tipo !== "saldo_unidad" && c.tipo !== "importe_unidad") continue;
    const v = c.valor;
    const clave = `${textoDe(v.uf)}|${textoDe(v.depto)}`;
    const nombre = c.designacion ?? [v.uf ? `UF ${textoDe(v.uf)}` : "", v.depto ? textoDe(v.depto) : ""].filter(Boolean).join(" ");
    const fila = filas.get(clave) ?? { clave, nombre };
    if (c.designacion !== null) fila.nombre = c.designacion;
    if (c.tipo === "saldo_unidad") fila.saldo = v;
    if (c.tipo === "importe_unidad") fila.importe = v;
    filas.set(clave, fila);
  }
  return [...filas.values()];
}

function DeUnPeriodo({ detalle, oficiales, idTitulo }: { detalle: Detalle; oficiales: readonly Oficial[]; idTitulo: string }) {
  const total = detalle.campos.find((c) => c.clave === "total:gastos")?.valor;
  const vencimientos = Array.isArray(total?.vencimientos) ? (total.vencimientos as unknown[]).map(String) : [];
  const rubros = deTipo(detalle.campos, "rubro");
  const gastos = deTipo(detalle.campos, "gasto");
  const unidades = unidadesDe(detalle.campos);
  const nombreDelRubro = new Map(rubros.map((r) => [String(r.valor.numero), textoDe(r.valor.nombre)]));
  return (
    <>
      <h2 id={idTitulo}>Período {mesLargo(detalle.periodo)}</h2>
      <p>
        {detalle.vigente ? VIGENTE : REEMPLAZADA} · Confirmada el {fechaCorta(detalle.decidida_en)}
      </p>
      {historiaDe(detalle, oficiales).map((linea) => (
        <p key={linea} className="tarjeta">
          {linea}
        </p>
      ))}
      {total !== undefined && typeof total.total === "string" && <p>Total del gasto: {enPesos(total.total)}</p>}
      {vencimientos.length > 0 && <p>Vencimientos: {vencimientos.join(", ")}</p>}

      {rubros.length > 0 && (
        <div className="tabla-desplazable tabla-tarjetas">
          <table>
            <caption>Rubros</caption>
            <thead>
              <tr>
                <th scope="col">Rubro</th>
                <th scope="col">Total</th>
              </tr>
            </thead>
            <tbody>
              {rubros.map((r) => (
                <tr key={r.clave}>
                  <th scope="row">{`${textoDe(r.valor.numero)}. ${textoDe(r.valor.nombre)}`}</th>
                  <td data-etiqueta="Total">{importe(r.valor.total)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {gastos.length > 0 && (
        <div className="tabla-desplazable tabla-tarjetas">
          <table>
            <caption>Gastos</caption>
            <thead>
              <tr>
                <th scope="col">Concepto</th>
                <th scope="col">Rubro</th>
                <th scope="col">Total</th>
              </tr>
            </thead>
            <tbody>
              {gastos.map((g) => (
                <tr key={g.clave}>
                  <th scope="row">{textoDe(g.valor.concepto)}</th>
                  <td data-etiqueta="Rubro">{nombreDelRubro.get(String(g.valor.rubro)) ?? textoDe(g.valor.rubro)}</td>
                  <td data-etiqueta="Total">{importe(g.valor.total)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {unidades.length > 0 && (
        <div className="tabla-desplazable tabla-tarjetas">
          <table>
            <caption>Unidades</caption>
            <thead>
              <tr>
                <th scope="col">Unidad</th>
                <th scope="col">Saldo anterior</th>
                <th scope="col">Pagos</th>
                <th scope="col">Total del período</th>
              </tr>
            </thead>
            <tbody>
              {unidades.map((u) => (
                <tr key={u.clave}>
                  <th scope="row">{u.nombre}</th>
                  <td data-etiqueta="Saldo anterior">{importe(u.saldo?.saldo_anterior)}</td>
                  <td data-etiqueta="Pagos">{importe(u.saldo?.pagos)}</td>
                  <td data-etiqueta="Total del período">{importe(u.importe?.total)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h3>Lo que no cierra</h3>
      {detalle.diferencias.length === 0 ? (
        <p>Las cuentas de la liquidación cierran.</p>
      ) : (
        <ul aria-label="Lo que no cierra">
          {detalle.diferencias.map((d, i) => {
            const { esperado, informado } = d as Record<string, unknown>;
            return (
              <li key={`${d.regla}-${i}`}>
                {d.detalle} (página {d.pagina})
                {typeof esperado === "string" && typeof informado === "string" ? `: suma ${enPesos(esperado)}, dice ${enPesos(informado)}` : ""}
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}
