import { useEffect, useId, useState } from "react";

import { CabeceraInterna } from "../CabeceraInterna";
import { api } from "../api/cliente";
import { type Aviso, ESTADO, mensajeDeError, sinLaFalla, sinPisarLoHecho } from "../egresos/comun";
import { ElegirConsorcio } from "../egresos/ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "../egresos/EstadoPantalla";
import { exigirSesion, SinSesion, useConsorcio } from "../egresos/useConsorcio";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { REQUISITOS } from "../permisos";
import { type Cotejo, fecha, mesDe, RUTA_PAGOS_INFORMADOS, TEXTO_RESULTADO, textoCotejo, TITULO_COTEJO, TITULO_PAGOS_INFORMADOS } from "./textos";

export { RUTA_COTEJO_PAGOS_INFORMADOS, TITULO_COTEJO } from "./textos";

// Página /pagos-informados/cotejo (PI-7 y PI-8, app 0131): lo informado y confirmado de cada unidad
// al lado de la columna «Pagos» de la liquidación oficial vigente del mes (que suma lo pagado el mes
// anterior, Q2). Se calcula al leer. La pantalla dice los dos números, nunca que alguien se
// equivocó. Quien ve el panel ve todas las unidades; el propietario, las suyas.

type Lectura =
  | { tipo: "cargando" }
  | { tipo: "sin_servicio" }
  | { tipo: "sin_liquidacion_del_mes"; periodo: string }
  | { tipo: "ok"; cotejo: Cotejo };

export function CotejoPagos() {
  const elegido = useConsorcio(REQUISITOS.pagosInformados);
  return <DelEdificio key={elegido.consorcio} {...elegido} />;
}

function DelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar }: ReturnType<typeof useConsorcio>) {
  const id = useId();
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  /** «AAAA-MM» del campo; vacío: la última liquidación vigente. */
  const [mes, setMes] = useState("");
  const [pedido, setPedido] = useState("");

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    const periodo = pedido === "" ? undefined : `${pedido}-01`;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/pagos-informados/cotejo", {
        params: { path: { consorcio_id: consorcio }, query: periodo === undefined ? {} : { periodo } },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        const codigo = codigoDe(error);
        if (data !== undefined) setLectura({ tipo: "ok", cotejo: data });
        else if (response.status === ESTADO.sinPermiso && codigo === "sin_permiso") aInicio();
        else if (response.status === ESTADO.noEncontrado && periodo !== undefined) setLectura({ tipo: "sin_liquidacion_del_mes", periodo });
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso(sinPisarLoHecho(mensajeDeError(response.status, codigo)));
        }
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso(sinPisarLoHecho(SIN_CONEXION));
        }
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, pedido, vuelta, aEntrar, aInicio]);

  const liquidacion = lectura.tipo === "ok" ? lectura.cotejo.liquidacion : undefined;
  const unidades = liquidacion?.unidades ?? [];
  const idMes = `${id}-mes`;
  return (
    <>
      <CabeceraInterna titulo={TITULO_COTEJO} volver={RUTA_PAGOS_INFORMADOS} destino={TITULO_PAGOS_INFORMADOS} />
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={false} elegir={setConsorcio} />
      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      <ConsorcioSinServicio pantalla={pantalla} reintentar={reintentar} />
      <AvisoPantalla aviso={aviso} />
      {pantalla.tipo === "lista" && (
        <>
          <form
            className="formulario"
            noValidate
            onSubmit={(ev) => {
              ev.preventDefault();
              setLectura({ tipo: "cargando" });
              setPedido(mes);
              // El mismo mes otra vez también se vuelve a leer: sin esto quedaba «Cargando» para siempre.
              setVuelta((v) => v + 1);
            }}
          >
            <label htmlFor={idMes}>Mes de la liquidación (vacío: la última)</label>
            <input id={idMes} type="month" value={mes} onChange={(ev) => setMes(ev.target.value)} />
            <div className="acciones">
              <button type="submit" className="secundario">
                Ver ese mes
              </button>
            </div>
          </form>
          {lectura.tipo === "cargando" && <p role="status">Cargando el cotejo…</p>}
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
          {lectura.tipo === "sin_liquidacion_del_mes" && (
            <p className="tarjeta">No hay una liquidación oficial confirmada de {mesDe(lectura.periodo)}.</p>
          )}
          {lectura.tipo === "ok" && liquidacion === null && (
            <p className="tarjeta">Todavía no hay una liquidación oficial confirmada para comparar.</p>
          )}
          {liquidacion != null && (
            <>
              <p>
                Liquidación de {mesDe(liquidacion.periodo)}: su columna «Pagos» suma lo pagado del {fecha(liquidacion.pagos_desde)} al{" "}
                {fecha(liquidacion.pagos_hasta)}.
                {liquidacion.para_revisar > 0 && ` ${liquidacion.para_revisar === 1 ? "Una unidad para revisar" : `${liquidacion.para_revisar} unidades para revisar`}.`}
              </p>
              {unidades.length === 0 ? (
                <p className="tarjeta">No hay unidades de esta cuenta en la liquidación.</p>
              ) : (
                <ul className="lista-cotejo" aria-label={TITULO_COTEJO}>
                  {unidades.map((u) => (
                    <li key={u.unidad_id} className="tarjeta">
                      <strong>Unidad {u.unidad_designacion}</strong> · {TEXTO_RESULTADO[u.resultado]}
                      <br />
                      <span>{textoCotejo(u)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </>
      )}
    </>
  );
}
