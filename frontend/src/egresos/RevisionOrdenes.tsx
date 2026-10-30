import { type FormEvent, useEffect, useId, useRef, useState } from "react";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { codigoDe, SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "../fallas";
import {
  cambioLaOrden,
  cambioLoVisto,
  cuentaEnmascarada,
  ENCABEZADO_IDEMPOTENCIA,
  ESTADO,
  importe,
  mensajeDeError,
  momento,
  renovarClave,
  SEGUNDO_FACTOR_REQUERIDO,
  TEXTO_CONDICION,
  TEXTO_ESTADO_ORDEN,
  TEXTO_FRANJA,
} from "./comun";
import { ElegirConsorcio } from "./ElegirConsorcio";
import { PedirCodigo } from "./PedirCodigo";
import { exigirSesion, SinSesion, useConsorcio } from "./useConsorcio";

// Página /egresos/revision (pantalla 3, aprobada el 30-10-2026; app 0049 y 0050): las órdenes ya
// enviadas, para el Consejo. La ficha muestra la versión vigente con el proveedor exacto que fija,
// y la cuenta COMPLETA: quien aprueba tiene que ver lo que aprueba. En la lista va enmascarada.
// «Aprobar esta versión» manda la versión y la huella que se vieron; si la orden cambió mientras
// tanto (409), la ficha se vuelve a cargar y hay que revisar la versión nueva: una aprobación
// nunca pasa sola a otra versión.

type Orden = components["schemas"]["OrdenPago"];
type Detalle = components["schemas"]["OrdenPagoConVersiones"];

export const RUTA_REVISION = "/egresos/revision";

type Lectura = { tipo: "cargando" } | { tipo: "sin_permiso" } | { tipo: "sin_servicio" } | { tipo: "ok"; ordenes: Orden[] };
type Acto = { tipo: "aprobar"; detalle: Detalle } | { tipo: "rechazar"; detalle: Detalle; motivo: string };
type Pendiente = { acto: string; reintentar: () => void };
type Aviso = { texto: string; ok: boolean };

/** Lo pendiente primero; después, el resto por fecha (la API ya las ordena por fecha). */
function ordenadas(ordenes: Orden[]): Orden[] {
  return [...ordenes.filter((o) => o.estado === "en_revision"), ...ordenes.filter((o) => o.estado !== "en_revision")];
}

// Cada edificio monta la pantalla de nuevo: nada de un edificio (estado, respuestas tardías,
// el reintento pendiente después del código) llega a la pantalla de otro.
export function RevisionOrdenes() {
  const elegido = useConsorcio();
  return <RevisionOrdenesDelEdificio key={elegido.consorcio} {...elegido} />;
}

function RevisionOrdenesDelEdificio({ pantalla, consorcio, setConsorcio, aEntrar }: ReturnType<typeof useConsorcio>) {
  const id = useId();
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [detalle, setDetalle] = useState<Detalle | null>(null);
  const [rechazando, setRechazando] = useState(false);
  const [motivo, setMotivo] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [pendiente, setPendiente] = useState<Pendiente | null>(null);
  const clave = useRef<string | null>(null);

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/ordenes-pago/revision", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        const codigo = codigoDe(error);
        if (data !== undefined) setLectura({ tipo: "ok", ordenes: ordenadas(data.elementos) });
        else if (codigo === SEGUNDO_FACTOR_REQUERIDO)
          setPendiente({ acto: "ver las órdenes", reintentar: () => setVuelta((v) => v + 1) });
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

  function abrir(ordenId: string, avisoAlAbrir: Aviso | null = null) {
    setAviso(avisoAlAbrir);
    setRechazando(false);
    setMotivo("");
    clave.current = null;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/revision", {
        params: { path: { consorcio_id: consorcio, orden_id: ordenId } },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data !== undefined) setDetalle(data);
        else if (codigoDe(error) === SEGUNDO_FACTOR_REQUERIDO)
          setPendiente({ acto: "ver la orden", reintentar: () => abrir(ordenId, avisoAlAbrir) });
        else {
          setDetalle(null);
          setAviso({ texto: mensajeDeError(response.status, codigoDe(error)), ok: false });
          setVuelta((v) => v + 1);
        }
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setAviso({ texto: SIN_CONEXION, ok: false })));
  }

  function hacer(acto: Acto, reintento = false) {
    if (!reintento) clave.current = null;
    clave.current ??= crypto.randomUUID();
    const encabezado = { [ENCABEZADO_IDEMPOTENCIA]: clave.current };
    const orden = acto.detalle.orden;
    const vista = acto.detalle.versiones.find((v) => v.numero === orden.version_vigente);
    if (vista === undefined) return;
    const ruta = { path: { consorcio_id: consorcio, orden_id: orden.id }, header: encabezado };
    setEnviando(true);
    setAviso(null);
    const pedido =
      acto.tipo === "aprobar"
        ? api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/aprobaciones", {
            headers: encabezadoCsrf(),
            params: ruta,
            body: { version: vista.numero, huella: vista.huella },
          })
        : api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/rechazar", {
            headers: encabezadoCsrf(),
            params: ruta,
            body: { version: vista.numero, motivo: acto.motivo },
          });
    void pedido
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data !== undefined) {
          setDetalle(null);
          setRechazando(false);
          setAviso({
            texto: acto.tipo === "aprobar" ? `Listo: aprobaste la versión ${vista.numero}.` : "Listo: rechazaste la orden.",
            ok: true,
          });
          setVuelta((v) => v + 1);
          return;
        }
        const codigo = codigoDe(error);
        if (codigo === SEGUNDO_FACTOR_REQUERIDO) {
          const actual = clave.current;
          setPendiente({
            acto: acto.tipo === "aprobar" ? "aprobar la orden" : "rechazar la orden",
            reintentar: () => {
              clave.current = actual;
              hacer(acto, true);
            },
          });
          return;
        }
        if (renovarClave(response.status, codigo)) clave.current = null;
        const texto = mensajeDeError(response.status, codigo);
        // La orden cambió (otra versión, otro estado o ya no está): se vuelve a cargar la ficha y
        // hay que revisar lo vigente.
        if (cambioLaOrden(codigo) || cambioLoVisto(response.status, codigo)) abrir(orden.id, { texto, ok: false });
        else setAviso({ texto, ok: false });
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setAviso({ texto: SIN_CONEXION, ok: false })))
      .finally(() => setEnviando(false));
  }

  function rechazar(evento: FormEvent) {
    evento.preventDefault();
    if (detalle === null || motivo.trim() === "") return;
    hacer({ tipo: "rechazar", detalle, motivo: motivo.trim() });
  }

  const vigente = detalle?.versiones.find((v) => v.numero === detalle.orden.version_vigente);

  return (
    <>
      <h1>Revisión de órdenes de pago</h1>
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={enviando}
        elegir={setConsorcio} />

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
          {lectura.tipo === "cargando" && <p role="status">Cargando las órdenes…</p>}
          {lectura.tipo === "sin_permiso" && (
            <p className="tarjeta">La revisión de órdenes es del Consejo: hace falta un mandato de Consejo en este edificio.</p>
          )}
          {lectura.tipo === "ok" && detalle === null && (
            lectura.ordenes.length === 0 ? (
              <p className="tarjeta">No hay órdenes enviadas a revisión.</p>
            ) : (
              <ul className="versiones" aria-label="Órdenes enviadas">
                {lectura.ordenes.map((o) => (
                  <li key={o.id} className="tarjeta">
                    <h2>
                      {o.razon_social}: {importe(o.importe_centavos, o.moneda)}
                    </h2>
                    <dl className="detalles">
                      <dt>Estado</dt>
                      <dd>{TEXTO_ESTADO_ORDEN[o.estado]}</dd>
                      <dt>Franja</dt>
                      <dd>{TEXTO_FRANJA[o.franja]}</dd>
                      {o.alerta_fraccionamiento && (
                        <>
                          <dt>Alerta</dt>
                          <dd>Posible fraccionamiento: sumada a las demás del mes, supera el umbral o el máximo del contrato.</dd>
                        </>
                      )}
                      <dt>Aprobaciones</dt>
                      <dd>
                        {o.aprobaciones} de {o.aprobaciones_requeridas}
                      </dd>
                    </dl>
                    <button type="button" onClick={() => abrir(o.id)}>
                      Revisar la orden de {o.razon_social}
                    </button>
                  </li>
                ))}
              </ul>
            )
          )}

          {detalle !== null && vigente !== undefined && (
            <section className="tarjeta" aria-labelledby={`${id}-ficha`}>
              <h2 id={`${id}-ficha`}>
                Versión {vigente.numero}: {importe(vigente.importe_centavos, vigente.moneda)}
              </h2>
              <dl className="detalles">
                <dt>Estado</dt>
                <dd>{TEXTO_ESTADO_ORDEN[detalle.orden.estado]}</dd>
                <dt>Concepto</dt>
                <dd>{vigente.concepto}</dd>
                <dt>Comprobante</dt>
                <dd>{vigente.comprobante ?? "Sin comprobante"}</dd>
                <dt>Proveedor</dt>
                <dd>{vigente.razon_social}</dd>
                <dt>CUIT</dt>
                <dd>{vigente.cuit ?? "Sin CUIT"}</dd>
                <dt>Cuenta destino</dt>
                <dd>{vigente.cuenta_destino ?? "Sin cuenta"}</dd>
                <dt>Beneficiario</dt>
                <dd>{TEXTO_CONDICION[vigente.condicion_beneficiario]}</dd>
                <dt>Franja</dt>
                <dd>{TEXTO_FRANJA[vigente.franja]}</dd>
                {vigente.alerta_fraccionamiento && (
                  <>
                    <dt>Alerta</dt>
                    <dd>Posible fraccionamiento: sumada a las demás del mes, supera el umbral o el máximo del contrato.</dd>
                  </>
                )}
                <dt>Fondo de reserva</dt>
                <dd>{vigente.usa_fondo_reserva ? "Sí" : "No"}</dd>
                <dt>Aprobaciones</dt>
                <dd>
                  {vigente.aprobada_por.length} de {vigente.aprobaciones_requeridas}
                </dd>
                <dt>Preparada</dt>
                <dd>{momento(vigente.creada_en)}</dd>
              </dl>
              {detalle.versiones.length > 1 && (
                <details className="historial">
                  <summary>Versiones anteriores</summary>
                  <ol className="versiones">
                    {detalle.versiones
                      .filter((v) => v.numero !== vigente.numero)
                      .map((v) => (
                        <li key={v.numero}>
                          Versión {v.numero}: {importe(v.importe_centavos, v.moneda)}, {v.razon_social}, cuenta{" "}
                          {cuentaEnmascarada(v.cuenta_destino)}
                        </li>
                      ))}
                  </ol>
                </details>
              )}
              {detalle.orden.estado === "en_revision" && !rechazando && (
                <div className="formulario">
                  <div className="acciones">
                    <button type="button" disabled={enviando} onClick={() => hacer({ tipo: "aprobar", detalle })}>
                      Aprobar esta versión
                    </button>
                    <button type="button" className="secundario" disabled={enviando} onClick={() => setRechazando(true)}>
                      Rechazar
                    </button>
                  </div>
                </div>
              )}
              {rechazando && (
                <form className="formulario" onSubmit={rechazar}>
                  <label htmlFor={`${id}-motivo`}>Motivo del rechazo</label>
                  <input id={`${id}-motivo`} required maxLength={500} value={motivo} disabled={enviando}
                    onChange={(e) => setMotivo(e.target.value)} />
                  <div className="acciones">
                    <button type="submit" disabled={enviando || motivo.trim() === ""}>
                      Rechazar la orden
                    </button>
                    <button type="button" className="secundario" disabled={enviando} onClick={() => setRechazando(false)}>
                      Cancelar
                    </button>
                  </div>
                </form>
              )}
              <p>
                <button type="button" className="secundario" onClick={() => setDetalle(null)}>
                  Volver a la lista
                </button>
              </p>
            </section>
          )}
        </>
      )}
    </>
  );
}
