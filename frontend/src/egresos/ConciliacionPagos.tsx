import { type FormEvent, useEffect, useId, useRef, useState } from "react";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { codigoDe, SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "../fallas";
import {
  cambioLoVisto,
  ENCABEZADO_IDEMPOTENCIA,
  ESTADO,
  importe,
  mensajeDeError,
  momento,
  renovarClave,
  SEGUNDO_FACTOR_REQUERIDO,
} from "./comun";
import { ElegirConsorcio } from "./ElegirConsorcio";
import { PedirCodigo } from "./PedirCodigo";
import { exigirSesion, SinSesion, useConsorcio, useMontado } from "./useConsorcio";

// Página /egresos/conciliacion (app 0055): el Consejo concilia los pagos manuales. Quien concilia
// compara el comprobante con la orden (número de operación, fecha, importe y proveedor) y marca
// «Conforme» u «Observado». Recién con «Conforme» la orden queda Pagada. Quien registró el pago, o
// preparó la orden, no lo concilia (la API y la base lo niegan, también con otra cuenta suya).
// Acá no viaja la cuenta completa.

type ConPago = components["schemas"]["OrdenConPagoManual"];
type Pago = components["schemas"]["PagoManual"];
type Declaracion = components["schemas"]["PagoNoRealizado"];

export const RUTA_CONCILIACION = "/egresos/conciliacion";

type Lectura = { tipo: "cargando" } | { tipo: "sin_permiso" } | { tipo: "sin_servicio" } | { tipo: "ok"; elementos: ConPago[] };
type Pendiente = { acto: string; reintentar: () => void };
type Aviso = { texto: string; ok: boolean };
type Acto =
  | { tipo: "conforme"; orden: string; pago: string }
  | { tipo: "observado"; orden: string; pago: string; motivo: string }
  | { tipo: "no_realizado"; orden: string; declaracion: string };

/** El registro que espera conciliación: el último, si nadie lo concilió. */
export function porConciliar(c: ConPago): Pago | undefined {
  const ultimo = c.ejecucion.pagos.at(-1);
  return c.orden.estado === "en_verificacion" && ultimo !== undefined && ultimo.conciliacion === null ? ultimo : undefined;
}

/** La última declaración de «no se pagó» sin confirmar, si no hay un pago vigente y nadie registró
 * un pago después de ella (un registro posterior la deja superada; el backend también lo niega si
 * después alguien vio la cuenta). */
export function porConfirmar(c: ConPago): Declaracion | undefined {
  if (c.orden.estado !== "en_ejecucion" && c.orden.estado !== "en_verificacion") return undefined;
  if (porConciliar(c) !== undefined) return undefined;
  const ultima = c.ejecucion.declaraciones.at(-1);
  if (ultima === undefined || ultima.confirmado_por !== null) return undefined;
  const registro = c.ejecucion.pagos.at(-1);
  return registro === undefined || Date.parse(registro.registrado_en) < Date.parse(ultima.declarado_en) ? ultima : undefined;
}

function descarga(consorcio: string, pago: Pago): string {
  return `/api/v1/consorcios/${consorcio}/documentos/${pago.documento_id}/versiones/${pago.version_documento_id}/descargar`;
}

export function ConciliacionPagos() {
  const elegido = useConsorcio();
  return <ConciliacionDelEdificio key={elegido.consorcio} {...elegido} />;
}

function ConciliacionDelEdificio({ pantalla, consorcio, setConsorcio, aEntrar }: ReturnType<typeof useConsorcio>) {
  const id = useId();
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [observando, setObservando] = useState<string | null>(null);
  const [motivo, setMotivo] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [pendiente, setPendiente] = useState<Pendiente | null>(null);
  const clave = useRef<string | null>(null);
  const montado = useMontado();

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/pagos-manuales/revision", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        const codigo = codigoDe(error);
        if (data !== undefined) setLectura({ tipo: "ok", elementos: data.elementos });
        else if (codigo === SEGUNDO_FACTOR_REQUERIDO)
          setPendiente({ acto: "ver los pagos", reintentar: () => setVuelta((v) => v + 1) });
        else if (response.status === ESTADO.sinPermiso && codigo === "sin_permiso") setLectura({ tipo: "sin_permiso" });
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso({ texto: mensajeDeError(response.status, codigo), ok: false });
        }
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso({ texto: SIN_CONEXION, ok: false });
        }
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, vuelta, aEntrar]);

  function hacer(acto: Acto, reintento = false) {
    if (!montado.current) return;
    if (!reintento) clave.current = null;
    clave.current ??= crypto.randomUUID();
    const header = { [ENCABEZADO_IDEMPOTENCIA]: clave.current };
    setEnviando(true);
    setAviso(null);
    const pedido =
      acto.tipo === "no_realizado"
        ? api.POST(
            "/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/no-realizado/{declaracion_id}/confirmacion",
            {
              headers: encabezadoCsrf(),
              params: { path: { consorcio_id: consorcio, orden_id: acto.orden, declaracion_id: acto.declaracion }, header },
            },
          )
        : api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/{pago_id}/conciliacion", {
            headers: encabezadoCsrf(),
            params: { path: { consorcio_id: consorcio, orden_id: acto.orden, pago_id: acto.pago }, header },
            body: acto.tipo === "conforme" ? { resultado: "conforme" } : { resultado: "observado", motivo: acto.motivo },
          });
    void pedido
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data !== undefined) {
          setObservando(null);
          setMotivo("");
          const texto = {
            conforme: "Listo: conciliaste el pago. La orden quedó pagada.",
            observado: "Listo: observaste el pago. Quien lo registró tiene que registrarlo de nuevo.",
            no_realizado: "Listo: confirmaste que no se pagó. La orden quedó fallida.",
          }[acto.tipo];
          setAviso({ texto, ok: true });
          setVuelta((v) => v + 1);
          return;
        }
        const codigo = codigoDe(error);
        if (codigo === SEGUNDO_FACTOR_REQUERIDO) {
          const actual = clave.current;
          setPendiente({
            acto: acto.tipo === "no_realizado" ? "confirmar que no se pagó" : "conciliar el pago",
            reintentar: () => {
              clave.current = actual;
              hacer(acto, true);
            },
          });
          return;
        }
        if (renovarClave(response.status, codigo)) clave.current = null;
        setAviso({ texto: mensajeDeError(response.status, codigo), ok: false });
        if (cambioLoVisto(response.status, codigo)) setVuelta((v) => v + 1);
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setAviso({ texto: SIN_CONEXION, ok: false })))
      .finally(() => setEnviando(false));
  }

  function observar(evento: FormEvent, orden: string, pago: string) {
    evento.preventDefault();
    if (motivo.trim() === "") return;
    hacer({ tipo: "observado", orden, pago, motivo: motivo.trim() });
  }

  const elementos = lectura.tipo === "ok" ? lectura.elementos : [];
  const pendientes = elementos.filter((c) => porConciliar(c) !== undefined || porConfirmar(c) !== undefined);
  const pagadas = elementos.filter((c) => c.orden.estado === "pagada");

  return (
    <>
      <h1>Conciliación de pagos</h1>
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={enviando} elegir={setConsorcio} />

      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">No tenés edificios en Consorcio en Mano.</p>}
      {pantalla.tipo === "sin_servicio" && (
        <p className="aviso-error" role="alert">
          {pantalla.deConexion ? SIN_CONEXION : SERVICIO_NO_DISPONIBLE}
        </p>
      )}
      {aviso !== null && (
        <p className={aviso.ok ? "aviso-ok" : "aviso-error"} role={aviso.ok ? "status" : "alert"}>
          {aviso.texto}
        </p>
      )}
      {pendiente !== null && (
        <PedirCodigo
          acto={pendiente.acto}
          alVerificar={() => {
            const { reintentar } = pendiente;
            setPendiente(null);
            reintentar();
          }}
          alCancelar={() => setPendiente(null)}
        />
      )}

      {pantalla.tipo === "lista" && pendiente === null && (
        <>
          {lectura.tipo === "cargando" && <p role="status">Cargando los pagos…</p>}
          {lectura.tipo === "sin_permiso" && (
            <p className="tarjeta">La conciliación de pagos es del Consejo: hace falta un mandato de Consejo en este edificio.</p>
          )}
          {lectura.tipo === "ok" &&
            (pendientes.length === 0 ? (
              <p className="tarjeta">No hay pagos esperando conciliación.</p>
            ) : (
              <ul className="versiones" aria-label="Pagos para conciliar">
                {pendientes.map((c) => {
                  const pago = porConciliar(c);
                  const declaracion = porConfirmar(c);
                  return (
                    <li key={c.orden.id} className="tarjeta">
                      <h2>
                        {c.orden.razon_social}: {importe(c.orden.importe_centavos, c.orden.moneda)}
                      </h2>
                      <p>{c.orden.concepto}</p>
                      {pago !== undefined && (
                        <>
                          <dl className="detalles">
                            <dt>Número de operación</dt>
                            <dd>{pago.numero_operacion}</dd>
                            <dt>Fecha de la transferencia</dt>
                            <dd>{pago.fecha_operacion}</dd>
                            <dt>Importe registrado</dt>
                            <dd>{importe(pago.importe_centavos, pago.moneda)}</dd>
                            <dt>Registrado</dt>
                            <dd>{momento(pago.registrado_en)}</dd>
                            <dt>Comprobante</dt>
                            <dd>
                              <a href={descarga(consorcio, pago)}>Ver el comprobante</a>
                            </dd>
                          </dl>
                          {observando !== pago.id ? (
                            <div className="acciones">
                              <button type="button" disabled={enviando}
                                onClick={() => hacer({ tipo: "conforme", orden: c.orden.id, pago: pago.id })}>
                                Conforme
                              </button>
                              <button type="button" className="secundario" disabled={enviando}
                                onClick={() => {
                                  setObservando(pago.id);
                                  setMotivo("");
                                }}>
                                Observar
                              </button>
                            </div>
                          ) : (
                            <form className="formulario" onSubmit={(e) => observar(e, c.orden.id, pago.id)}>
                              <label htmlFor={`${id}-motivo`}>Qué no coincide</label>
                              <input id={`${id}-motivo`} required maxLength={300} value={motivo} disabled={enviando}
                                onChange={(e) => setMotivo(e.target.value)} />
                              <div className="acciones">
                                <button type="submit" disabled={enviando || motivo.trim() === ""}>
                                  Observar el pago
                                </button>
                                <button type="button" className="secundario" disabled={enviando} onClick={() => setObservando(null)}>
                                  Cancelar
                                </button>
                              </div>
                            </form>
                          )}
                        </>
                      )}
                      {declaracion !== undefined && (
                        <>
                          <p>
                            Declarado como no pagado el {momento(declaracion.declarado_en)}: {declaracion.motivo}
                          </p>
                          <button type="button" disabled={enviando}
                            onClick={() => hacer({ tipo: "no_realizado", orden: c.orden.id, declaracion: declaracion.id })}>
                            Confirmar que no se pagó
                          </button>
                        </>
                      )}
                    </li>
                  );
                })}
              </ul>
            ))}
          {lectura.tipo === "ok" && pagadas.length > 0 && (
            <details className="historial">
              <summary>Pagos conciliados (para cotejar con el extracto del banco)</summary>
              <ul className="versiones">
                {pagadas.map((c) => {
                  const conforme = c.ejecucion.pagos.find((p) => p.conciliacion === "conforme");
                  return (
                    <li key={c.orden.id}>
                      {c.orden.razon_social}, {importe(c.orden.importe_centavos, c.orden.moneda)}: operación{" "}
                      {c.ejecucion.referencia ?? "sin número"}
                      {conforme !== undefined && `, transferida el ${conforme.fecha_operacion}`}
                    </li>
                  );
                })}
              </ul>
            </details>
          )}
        </>
      )}
    </>
  );
}
