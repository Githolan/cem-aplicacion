import { useEffect, useState } from "react";
import { Link } from "react-router";

import { CabeceraInterna } from "../CabeceraInterna";
import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { type Aviso, ESTADO, fechaLegible, importe, mensajeDeError, momento, sinLaFalla, sinPisarLoHecho } from "../egresos/comun";
import { ElegirConsorcio } from "../egresos/ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "../egresos/EstadoPantalla";
import { exigirSesion, SinSesion, useConsorcio } from "../egresos/useConsorcio";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { REQUISITOS, usePuede } from "../permisos";
import { CargandoPagos } from "./CargandoPagos";
import { RUTA_COTEJO, RUTA_PAGOS } from "./rutas";
import { TITULO_SIN_EXPLICAR } from "./textos";

// `/pagos/extracto-sin-explicar`: los débitos de los extractos ya subidos que ningún pago explica
// (pregunta 8 del propietario, 13-11-2026, opción c). Lo ven quien concilia y la administración; es
// solo para leer: acá no se cambia nada. Qué entra en la lista lo decide el servidor, con la misma
// comparación que al subir un extracto (`backend/tests/test_avisos_y_sin_explicar_0098.py`).

type Movimiento = components["schemas"]["MovimientoSinExplicar"];
type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; elementos: Movimiento[]; total: number };

export function SinExplicar() {
  const elegido = useConsorcio(REQUISITOS.sinExplicar);
  return <SinExplicarDelEdificio key={elegido.consorcio} {...elegido} />;
}

function SinExplicarDelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar }: ReturnType<typeof useConsorcio>) {
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [vuelta, setVuelta] = useState(0);
  const puede = usePuede(consorcio);
  const coteja = puede("egresos.conciliar_pago");

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/pagos/extracto/sin-explicar", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        const codigo = codigoDe(error);
        if (data !== undefined) {
          setAviso(sinLaFalla);
          setLectura({ tipo: "ok", elementos: data.elementos, total: data.total });
        } else if (response.status === ESTADO.sinPermiso && codigo === "sin_permiso") aInicio();
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
  }, [consorcio, vuelta, aEntrar, aInicio]);

  return (
    <>
      <CabeceraInterna titulo={TITULO_SIN_EXPLICAR} volver={RUTA_PAGOS} destino="Pagos" />
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={false} elegir={setConsorcio} />
      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">Esta cuenta no tiene edificios en Consorcio en Mano.</p>}
      <ConsorcioSinServicio pantalla={pantalla} reintentar={reintentar} />
      <AvisoPantalla aviso={aviso} />
      {pantalla.tipo === "lista" && consorcio !== "" && (
        <section className="tarjeta" aria-label={TITULO_SIN_EXPLICAR}>
          <p>
            Débitos de los extractos del banco que ningún pago de Consorcio en Mano explica. Los ven quien concilia y la administración, para
            saber qué falta registrar; desde acá no se cambia nada.
          </p>
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
          {lectura.tipo === "ok" && lectura.total === 0 && <p>El extracto no tiene nada sin explicar.</p>}
          {lectura.tipo === "ok" && lectura.total > 0 && (
            <>
              <ul className="filas-pago">
                {lectura.elementos.map((m) => (
                  <li key={m.movimiento_id} className="fila-pago fila-pago--con-borde">
                    <span className="fila-pago__arriba">
                      <strong>Débito sin explicar</strong>
                      <span className="fila-pago__importe">{importe(m.importe_centavos, m.moneda)}</span>
                    </span>
                    <span>{m.numero_operacion === null ? fechaLegible(m.fecha) : `Referencia ${m.numero_operacion} · ${fechaLegible(m.fecha)}`}</span>
                    <span className="fila-pago__nota">Extracto subido el {momento(m.visto_en)}.</span>
                  </li>
                ))}
              </ul>
              {lectura.total > lectura.elementos.length && <p>Se ven los primeros {lectura.elementos.length}.</p>}
            </>
          )}
          {/* Quien concilia sube el extracto que lo explique; la administración solo lee. */}
          {coteja && (
            <p className="salidas-pago">
              <Link to={RUTA_COTEJO} state={{ consorcio }} className="enlace-boton enlace-boton--borde">
                Cotejar con el banco
              </Link>
            </p>
          )}
        </section>
      )}
    </>
  );
}
