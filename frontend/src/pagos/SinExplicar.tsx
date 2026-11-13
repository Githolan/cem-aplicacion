import { type FormEvent, useEffect, useId, useState } from "react";
import { Link } from "react-router";

import { CabeceraInterna } from "../CabeceraInterna";
import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { ENCABEZADO_IDEMPOTENCIA, ESTADO, fechaLegible, importe, mensajeDeActo, mensajeDeError, momento, sinLaFalla, sinPisarLoHecho } from "../egresos/comun";
import { ElegirConsorcio } from "../egresos/ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "../egresos/EstadoPantalla";
import { PedirCodigo } from "../egresos/PedirCodigo";
import { exigirSesion, SinSesion, useConsorcio } from "../egresos/useConsorcio";
import { ErrorDeCampo, erroresDe, type ErroresDeCampo, marcaDe, SIN_ERRORES, sinElDe, soloDe } from "../ErrorDeCampo";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { REQUISITOS, usePuede } from "../permisos";
import { CargandoPagos } from "./CargandoPagos";
import { RUTA_COTEJO, RUTA_PAGOS } from "./rutas";
import { NO_ES_PAGO, TITULO_SIN_EXPLICAR } from "./textos";
import { useActo } from "./useActo";

// `/pagos/extracto-sin-explicar`: los débitos de los extractos ya subidos que ningún pago explica
// (pregunta 8 del propietario, 13-11-2026, opción c). Lo ven quien concilia y la administración, que
// solo lee. Quien concilia puede decir que un débito no es el pago de una orden (una comisión, un
// impuesto), con motivo y el gesto: no se borra, sale de la lista (respuesta del 13-11, opción a).
// Qué entra en la lista y quién marca lo decide el servidor
// (`backend/tests/test_avisos_y_sin_explicar_0098.py`).

type Movimiento = components["schemas"]["MovimientoSinExplicar"];
type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; elementos: Movimiento[]; total: number };

const CAMPOS_MOTIVO = { motivo: "el motivo" } as const;
const LARGO_MOTIVO = 300;

export function SinExplicar() {
  const elegido = useConsorcio(REQUISITOS.sinExplicar);
  return <SinExplicarDelEdificio key={elegido.consorcio} {...elegido} />;
}

function SinExplicarDelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar }: ReturnType<typeof useConsorcio>) {
  const id = useId();
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [elegido, setElegido] = useState<string | null>(null);
  const [motivo, setMotivo] = useState("");
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  const puede = usePuede(consorcio);
  const coteja = puede("egresos.conciliar_pago");
  const acto = useActo(aEntrar);
  const { enviando, aviso, setAviso, pendiente, setPendiente } = acto;

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
  }, [consorcio, vuelta, aEntrar, aInicio, setAviso]);

  function cerrar() {
    setElegido(null);
    setMotivo("");
    setErrores(SIN_ERRORES);
  }

  function marcar(evento: FormEvent, movimiento: string) {
    evento.preventDefault();
    const texto = motivo.trim();
    void acto.hacer({
      quePide: ["no-es-pago", movimiento, texto],
      nombre: "marcar que no es el pago de una orden",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/pagos/extracto/sin-explicar/{movimiento_id}/no-es-pago", {
          headers: encabezadoCsrf(),
          params: { path: { consorcio_id: consorcio, movimiento_id: movimiento }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: { motivo: texto },
        }),
      alTerminar: () => {
        setAviso({ texto: "Listo: el débito salió de la lista. No se borra: queda quién lo marcó, cuándo y por qué.", ok: true });
        cerrar();
        setVuelta((v) => v + 1);
      },
      alFallar: (estado, codigo, error) => {
        setErrores(soloDe(erroresDe(error, mensajeDeActo(estado, error)), CAMPOS_MOTIVO));
        if (estado === ESTADO.conflicto || estado === ESTADO.noEncontrado || codigo === "movimiento_ya_explicado") {
          cerrar();
          setVuelta((v) => v + 1);
        }
      },
    });
  }

  return (
    <>
      <CabeceraInterna titulo={TITULO_SIN_EXPLICAR} volver={RUTA_PAGOS} destino="Pagos" />
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={enviando} elegir={setConsorcio} />
      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">Esta cuenta no tiene edificios en Consorcio en Mano.</p>}
      <ConsorcioSinServicio pantalla={pantalla} reintentar={reintentar} />
      <AvisoPantalla aviso={aviso} />
      {pendiente !== null && (
        <PedirCodigo
          acto={pendiente.acto}
          alVerificar={() => {
            const { reintentar: repetir } = pendiente;
            setPendiente(null);
            repetir();
          }}
          alCancelar={() => setPendiente(null)}
        />
      )}
      {pantalla.tipo === "lista" && consorcio !== "" && (
        <section className="tarjeta" aria-label={TITULO_SIN_EXPLICAR} hidden={pendiente !== null}>
          <p>
            {coteja
              ? "Débitos de los extractos del banco que ningún pago de Consorcio en Mano explica. La administración también los ve. Si uno no es el pago de una orden (una comisión, un impuesto), se marca con el motivo y sale de la lista."
              : "Débitos de los extractos del banco que ningún pago de Consorcio en Mano explica. Los ven quien concilia y la administración, para saber qué falta registrar; desde acá no se cambia nada."}
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
                    {coteja && elegido !== m.movimiento_id && (
                      <div className="formulario">
                        <button type="button" className="secundario" disabled={enviando} onClick={() => (cerrar(), setElegido(m.movimiento_id))}>
                          {NO_ES_PAGO}
                        </button>
                      </div>
                    )}
                    {coteja && elegido === m.movimiento_id && (
                      <form className="formulario" onSubmit={(evento) => marcar(evento, m.movimiento_id)}>
                        <label htmlFor={`${id}-motivo`}>Qué es este débito</label>
                        <textarea
                          id={`${id}-motivo`}
                          required
                          maxLength={LARGO_MOTIVO}
                          rows={3}
                          value={motivo}
                          disabled={enviando}
                          {...marcaDe(`${id}-motivo`, errores.motivo)}
                          onChange={(evento) => {
                            setMotivo(evento.target.value);
                            setErrores((e) => sinElDe(e, "motivo"));
                          }}
                        />
                        <ErrorDeCampo idCampo={`${id}-motivo`} mensaje={errores.motivo} />
                        <p>No se borra: queda quién lo marcó, cuándo y por qué.</p>
                        <div className="acciones">
                          <button type="submit" disabled={enviando || motivo.trim() === ""}>
                            {NO_ES_PAGO}
                          </button>
                          <button type="button" className="secundario" disabled={enviando} onClick={cerrar}>
                            Cancelar
                          </button>
                        </div>
                      </form>
                    )}
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
