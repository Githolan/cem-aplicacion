import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { RUTA_DOCUMENTOS } from "../Documentos";
import { codigoDe, SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "../fallas";
import {
  aCentavos,
  cambioLaOrden,
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

// Página /egresos/pagos: el pago manual del piloto (app 0055; propietario, 31-10-2026). Sin riel:
// quien está autorizado transfiere por el home banking del consorcio y registra acá la evidencia;
// otro miembro del Consejo la concilia (/egresos/conciliacion).
//
// La cuenta completa NO viaja con las listas: el backend la manda solamente cuando se abre el acto
// de ejecución de una orden aprobada («Ejecutar pago manual»), que vuelve a evaluar todas las
// barreras (quien aprobó no ejecuta, aprobación vigente, proveedor sin cambios, segundo factor
// reciente). Vive solo en el estado de esa ficha y se descarta al cerrarla.
//
// Si la aprobación venció, cambió el proveedor o el contrato dejó de estar vigente, con la orden
// emitida esperando su pago (quizás se transfirió antes y falta registrar, o hay que corregir un
// registro observado), la ficha se abre sin la cuenta: queda registrar con los datos del
// comprobante o declarar que no se pagó. El backend sigue exigiendo que registre quien vio la
// cuenta en el acto.

type Orden = components["schemas"]["OrdenPago"];
type ConPago = components["schemas"]["OrdenConPagoManual"];
type Cuenta = components["schemas"]["CuentaParaPagoManual"];
type DocumentoEnLista = components["schemas"]["DocumentoEnLista"];
type Version = components["schemas"]["VersionOrdenPago"];
/** La ficha del acto; sin la cuenta ni el plazo cuando se abre con la aprobación vencida. */
type Ficha = Omit<Cuenta, "cuenta_destino" | "aprobada_en" | "aprobacion_vence_en"> & {
  cuenta_destino: string | null;
  aprobacion_vence_en: string | null;
};

export const RUTA_PAGOS = "/egresos/pagos";
export const AVISO_VERIFICAR_CUENTA =
  "Verificá que la cuenta mostrada coincida con la cuenta autorizada para esta versión.";
export const AVISO_SIN_CUENTA =
  "La cuenta ya no se muestra: venció la aprobación, cambió el proveedor o el contrato dejó de estar vigente. Si transferiste antes, registrá el pago con los datos del comprobante; si no, declará que no se pagó.";
/** Las barreras del acto que, con la orden ya emitida, dejan registrar o declarar sin la cuenta. */
const SIN_CUENTA = new Set(["aprobacion_vencida", "proveedor_cambiado", "contrato_no_vigente"]);
const CATEGORIA_COMPROBANTE = "facturas_y_comprobantes";

type Lectura =
  | { tipo: "cargando" }
  | { tipo: "sin_permiso" }
  | { tipo: "sin_servicio" }
  | { tipo: "ok"; aprobadas: Orden[]; manuales: ConPago[]; comprobantes: DocumentoEnLista[] };
type Pendiente = { acto: string; reintentar: () => void };
type Aviso = { texto: string; ok: boolean };
type Registro = { numero: string; fecha: string; importe: string; cuenta: string; documento: string };

const REGISTRO_VACIO: Registro = { numero: "", fecha: "", importe: "", cuenta: "", documento: "" };

/** El estado de una orden con ejecución manual, en palabras del piloto. */
export function situacion(c: ConPago): string {
  const { orden, ejecucion } = c;
  if (orden.estado === "pagada") return `Pagada (operación ${ejecucion.referencia ?? "sin número"})`;
  if (orden.estado === "fallida") return "No se pagó (confirmado por el Consejo)";
  const ultimo = ejecucion.pagos.at(-1);
  if (ultimo === undefined) return "Lista para pagar";
  if (ultimo.conciliacion === "observado") return "Pago observado: registrá de nuevo con el comprobante correcto";
  return "Pago registrado: pendiente de conciliación";
}

/** Si hay que pagarla (o volver a registrar): sin un pago vigente y sin resultado. */
export function porPagar(c: ConPago): boolean {
  if (c.orden.estado !== "en_ejecucion" && c.orden.estado !== "en_verificacion") return false;
  const ultimo = c.ejecucion.pagos.at(-1);
  return ultimo === undefined || ultimo.conciliacion === "observado";
}

/** Los comprobantes que pueden respaldar un pago: de la categoría y con un archivo que pasó el antivirus. */
export function comprobantesDisponibles(documentos: DocumentoEnLista[]): DocumentoEnLista[] {
  return documentos.filter((d) => d.categoria === CATEGORIA_COMPROBANTE && d.versiones.some((v) => v.estado === "disponible"));
}

const hoy = () => new Date().toLocaleDateString("en-CA");

export function PagosManuales() {
  const elegido = useConsorcio();
  return <PagosManualesDelEdificio key={elegido.consorcio} {...elegido} />;
}

function PagosManualesDelEdificio({ pantalla, consorcio, setConsorcio, aEntrar }: ReturnType<typeof useConsorcio>) {
  const id = useId();
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [ficha, setFicha] = useState<Ficha | null>(null);
  const [fichaOrden, setFichaOrden] = useState<string | null>(null);
  const [registro, setRegistro] = useState<Registro>(REGISTRO_VACIO);
  const [declarando, setDeclarando] = useState(false);
  const [motivo, setMotivo] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [pendiente, setPendiente] = useState<Pendiente | null>(null);
  const clave = useRef<string | null>(null);
  const montado = useMontado();

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    const ruta = { params: { path: { consorcio_id: consorcio } } };
    void Promise.all([
      api.GET("/api/v1/consorcios/{consorcio_id}/ordenes-pago", ruta),
      api.GET("/api/v1/consorcios/{consorcio_id}/pagos-manuales", ruta),
      api.GET("/api/v1/consorcios/{consorcio_id}/documentos", ruta),
    ])
      .then(([ordenes, manuales, documentos]) => {
        for (const r of [ordenes, manuales, documentos]) exigirSesion(r.response);
        if (!vigente) return;
        const falla = [ordenes, manuales].find((r) => r.data === undefined);
        if (falla === undefined && ordenes.data !== undefined && manuales.data !== undefined) {
          setLectura({
            tipo: "ok",
            aprobadas: ordenes.data.elementos.filter((o) => o.estado === "aprobada"),
            manuales: manuales.data.elementos,
            // Sin acceso a los documentos, la lista de comprobantes queda vacía y se dice.
            comprobantes: comprobantesDisponibles(documentos.data?.documentos ?? []),
          });
          return;
        }
        const codigo = codigoDe(falla?.error);
        if (codigo === SEGUNDO_FACTOR_REQUERIDO)
          setPendiente({ acto: "ver los pagos", reintentar: () => setVuelta((v) => v + 1) });
        else if (falla?.response.status === ESTADO.sinPermiso && codigo === "sin_permiso") setLectura({ tipo: "sin_permiso" });
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso({ texto: mensajeDeError(falla?.response.status ?? 0, codigo), ok: false });
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

  function cerrar() {
    setFicha(null);
    setFichaOrden(null);
    setRegistro(REGISTRO_VACIO);
    setDeclarando(false);
    setMotivo("");
    clave.current = null;
  }

  function fallo(status: number, codigo: string | undefined, acto: string, reintentar: () => void) {
    if (codigo === SEGUNDO_FACTOR_REQUERIDO) {
      setPendiente({ acto, reintentar });
      return;
    }
    if (renovarClave(status, codigo)) clave.current = null;
    const texto = mensajeDeError(status, codigo);
    setAviso({ texto, ok: false });
    if (cambioLaOrden(codigo) || cambioLoVisto(status, codigo)) {
      cerrar();
      setVuelta((v) => v + 1);
    }
  }

  /** La versión vigente y su huella, como las ve quien prepara (sin la cuenta completa). */
  async function vista(ordenId: string): Promise<{ version: number; huella: string; estado: Orden["estado"]; detalle: Version } | null> {
    const { data, error, response } = await api.GET("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}", {
      params: { path: { consorcio_id: consorcio, orden_id: ordenId } },
    });
    exigirSesion(response);
    const v = data?.versiones.find((x) => x.numero === data.orden.version_vigente);
    if (data === undefined || v === undefined) {
      fallo(response.status, codigoDe(error), "ver la orden", () => void ejecutar(ordenId));
      return null;
    }
    return { version: v.numero, huella: v.huella, estado: data.orden.estado, detalle: v };
  }

  /** «Ejecutar pago manual»: emite si todavía está aprobada y abre el acto de ejecución. */
  async function ejecutar(ordenId: string, reintento = false) {
    if (!montado.current) return;
    if (!reintento) clave.current = null;
    setEnviando(true);
    setAviso(null);
    try {
      const v = await vista(ordenId);
      if (v === null) return;
      if (v.estado === "aprobada") {
        clave.current ??= crypto.randomUUID();
        const emision = await api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/emitir", {
          headers: encabezadoCsrf(),
          params: { path: { consorcio_id: consorcio, orden_id: ordenId }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current } },
          body: { version: v.version, huella: v.huella },
        });
        exigirSesion(emision.response);
        if (emision.data === undefined) {
          const actual = clave.current;
          fallo(emision.response.status, codigoDe(emision.error), "emitir la orden", () => {
            clave.current = actual;
            void ejecutar(ordenId, true);
          });
          return;
        }
        clave.current = null;
      }
      const { data, error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/cuenta", {
        headers: encabezadoCsrf(),
        params: { path: { consorcio_id: consorcio, orden_id: ordenId } },
        body: { version: v.version, huella: v.huella },
      });
      exigirSesion(response);
      const previo = lectura.tipo === "ok" ? lectura.manuales.find((c) => c.orden.id === ordenId) : undefined;
      if (data === undefined && SIN_CUENTA.has(codigoDe(error) ?? "") && previo !== undefined && porPagar(previo)) {
        const d = v.detalle;
        setRegistro({ ...REGISTRO_VACIO, fecha: hoy() });
        setFichaOrden(ordenId);
        setFicha({
          ejecucion_id: previo.ejecucion.id,
          version: v.version,
          huella: v.huella,
          proveedor_id: d.proveedor_id,
          proveedor_version: d.proveedor_version,
          razon_social: d.razon_social,
          cuit: d.cuit,
          cuenta_destino: null,
          importe_centavos: d.importe_centavos,
          moneda: d.moneda,
          concepto: d.concepto,
          comprobante: d.comprobante,
          aprobacion_vence_en: null,
        });
        return;
      }
      if (data === undefined) {
        fallo(response.status, codigoDe(error), "ver la cuenta para pagar", () => void ejecutar(ordenId, true));
        setVuelta((n) => n + 1);
        return;
      }
      setRegistro({ ...REGISTRO_VACIO, fecha: hoy() });
      setFichaOrden(ordenId);
      setFicha(data);
    } catch (e: unknown) {
      if (e instanceof SinSesion) aEntrar();
      else setAviso({ texto: SIN_CONEXION, ok: false });
    } finally {
      setEnviando(false);
    }
  }

  function registrar(evento: FormEvent, reintento = false) {
    evento.preventDefault();
    if (ficha === null || fichaOrden === null || !montado.current) return;
    const centavos = aCentavos(registro.importe);
    if (centavos === null) {
      setAviso({ texto: "Escribí el importe tal como figura en el comprobante.", ok: false });
      return;
    }
    if (!reintento) clave.current = null;
    clave.current ??= crypto.randomUUID();
    setEnviando(true);
    setAviso(null);
    void api
      .POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual", {
        headers: encabezadoCsrf(),
        params: { path: { consorcio_id: consorcio, orden_id: fichaOrden }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current } },
        body: {
          version: ficha.version,
          huella: ficha.huella,
          numero_operacion: registro.numero.trim(),
          fecha_operacion: registro.fecha,
          importe_centavos: centavos,
          moneda: ficha.moneda,
          cuenta_destino: registro.cuenta.replace(/\D/g, ""),
          documento_id: registro.documento,
        },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data !== undefined) {
          cerrar();
          setAviso({ texto: "Listo: registraste el pago. Falta que otro miembro del Consejo lo concilie.", ok: true });
          setVuelta((v) => v + 1);
          return;
        }
        const actual = clave.current;
        fallo(response.status, codigoDe(error), "registrar el pago", () => {
          clave.current = actual;
          registrar(evento, true);
        });
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setAviso({ texto: SIN_CONEXION, ok: false })))
      .finally(() => setEnviando(false));
  }

  function declarar(evento: FormEvent, reintento = false) {
    evento.preventDefault();
    if (ficha === null || fichaOrden === null || motivo.trim() === "" || !montado.current) return;
    if (!reintento) clave.current = null;
    clave.current ??= crypto.randomUUID();
    setEnviando(true);
    setAviso(null);
    void api
      .POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/no-realizado", {
        headers: encabezadoCsrf(),
        params: { path: { consorcio_id: consorcio, orden_id: fichaOrden }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current } },
        body: { version: ficha.version, motivo: motivo.trim() },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data !== undefined) {
          cerrar();
          setAviso({ texto: "Listo: declaraste que no se pagó. Lo confirma otro miembro del Consejo.", ok: true });
          setVuelta((v) => v + 1);
          return;
        }
        const actual = clave.current;
        fallo(response.status, codigoDe(error), "declarar que no se pagó", () => {
          clave.current = actual;
          declarar(evento, true);
        });
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setAviso({ texto: SIN_CONEXION, ok: false })))
      .finally(() => setEnviando(false));
  }

  function cambiar<K extends keyof Registro>(campo: K, valor: Registro[K]) {
    clave.current = null;
    setRegistro((r) => ({ ...r, [campo]: valor }));
  }

  return (
    <>
      <h1>Pagos a realizar</h1>
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
            <p className="tarjeta">Los pagos los ve quien prepara y ejecuta las órdenes de este edificio.</p>
          )}

          {lectura.tipo === "ok" && ficha === null && (
            <>
              <p>
                El pago se hace por el home banking del consorcio. Después registrás acá el número de operación y el
                comprobante, y otro miembro del Consejo lo concilia. Registrar no es pagar: la orden queda pagada recién
                con la conciliación.
              </p>
              {lectura.aprobadas.length + lectura.manuales.filter(porPagar).length === 0 ? (
                <p className="tarjeta">No hay órdenes aprobadas esperando su pago.</p>
              ) : (
                <ul className="versiones" aria-label="Órdenes para pagar">
                  {lectura.aprobadas.map((o) => (
                    <li key={o.id} className="tarjeta">
                      <h2>
                        {o.razon_social}: {importe(o.importe_centavos, o.moneda)}
                      </h2>
                      <p>{o.concepto}</p>
                      <button type="button" disabled={enviando} onClick={() => void ejecutar(o.id)}>
                        Ejecutar pago manual de {o.razon_social}
                      </button>
                    </li>
                  ))}
                  {lectura.manuales.filter(porPagar).map((c) => (
                    <li key={c.orden.id} className="tarjeta">
                      <h2>
                        {c.orden.razon_social}: {importe(c.orden.importe_centavos, c.orden.moneda)}
                      </h2>
                      <p>{situacion(c)}</p>
                      <button type="button" disabled={enviando} onClick={() => void ejecutar(c.orden.id)}>
                        Ejecutar pago manual de {c.orden.razon_social}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {lectura.manuales.some((c) => !porPagar(c)) && (
                <details className="historial">
                  <summary>Pagos registrados y resueltos</summary>
                  <ul className="versiones">
                    {lectura.manuales
                      .filter((c) => !porPagar(c))
                      .map((c) => (
                        <li key={c.orden.id}>
                          {c.orden.razon_social}, {importe(c.orden.importe_centavos, c.orden.moneda)}: {situacion(c)}
                        </li>
                      ))}
                  </ul>
                </details>
              )}
            </>
          )}

          {lectura.tipo === "ok" && ficha !== null && (
            <section className="tarjeta" aria-labelledby={`${id}-ficha`}>
              <h2 id={`${id}-ficha`}>Ejecutar el pago a {ficha.razon_social}</h2>
              <p className="aviso-error" role="note">
                {ficha.cuenta_destino === null ? AVISO_SIN_CUENTA : AVISO_VERIFICAR_CUENTA}
              </p>
              <dl className="detalles">
                <dt>Proveedor</dt>
                <dd>{ficha.razon_social}</dd>
                <dt>CUIT</dt>
                <dd>{ficha.cuit ?? "Sin CUIT"}</dd>
                {ficha.cuenta_destino !== null && (
                  <>
                    <dt>CBU/CVU</dt>
                    <dd>
                      <code>{ficha.cuenta_destino}</code>
                    </dd>
                  </>
                )}
                <dt>Importe</dt>
                <dd>{importe(ficha.importe_centavos, ficha.moneda)}</dd>
                <dt>Concepto</dt>
                <dd>{ficha.concepto}</dd>
                <dt>Comprobante de la orden</dt>
                <dd>{ficha.comprobante ?? "Sin comprobante"}</dd>
                <dt>Versión de la orden</dt>
                <dd>{ficha.version}</dd>
                <dt>Versión del proveedor</dt>
                <dd>{ficha.proveedor_version}</dd>
                {ficha.aprobacion_vence_en !== null && (
                  <>
                    <dt>Pagar antes de</dt>
                    <dd>{momento(ficha.aprobacion_vence_en)}</dd>
                  </>
                )}
              </dl>

              {!declarando && (
                <form className="formulario" onSubmit={(e) => registrar(e)}>
                  <h3>Después de transferir, registrá la evidencia</h3>
                  <label htmlFor={`${id}-numero`}>Número de operación</label>
                  <input id={`${id}-numero`} required maxLength={60} value={registro.numero} disabled={enviando}
                    onChange={(e) => cambiar("numero", e.target.value)} />
                  <label htmlFor={`${id}-fecha`}>Fecha de la transferencia</label>
                  <input id={`${id}-fecha`} type="date" required value={registro.fecha} disabled={enviando}
                    onChange={(e) => cambiar("fecha", e.target.value)} />
                  <label htmlFor={`${id}-importe`}>Importe transferido (como figura en el comprobante)</label>
                  <input id={`${id}-importe`} required inputMode="decimal" value={registro.importe} disabled={enviando}
                    onChange={(e) => cambiar("importe", e.target.value)} />
                  <label htmlFor={`${id}-cuenta`}>CBU/CVU de destino (como figura en el comprobante)</label>
                  <input id={`${id}-cuenta`} required inputMode="numeric" maxLength={30} value={registro.cuenta} disabled={enviando}
                    onChange={(e) => cambiar("cuenta", e.target.value)} />
                  <label htmlFor={`${id}-comprobante`}>Comprobante de la transferencia</label>
                  <select id={`${id}-comprobante`} required value={registro.documento} disabled={enviando}
                    onChange={(e) => cambiar("documento", e.target.value)}>
                    <option value="">Elegí el comprobante</option>
                    {lectura.comprobantes.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.titulo} ({d.fecha})
                      </option>
                    ))}
                  </select>
                  <p>
                    Subilo antes en <Link to={RUTA_DOCUMENTOS}>Documentos</Link>, en «Facturas y comprobantes». Aparece acá
                    cuando el antivirus lo aprueba.
                  </p>
                  <div className="acciones">
                    <button type="submit" disabled={enviando || registro.documento === ""}>
                      Registrar el pago
                    </button>
                    <button type="button" className="secundario" disabled={enviando} onClick={() => setDeclarando(true)}>
                      No se pudo pagar
                    </button>
                  </div>
                </form>
              )}
              {declarando && (
                <form className="formulario" onSubmit={(e) => declarar(e)}>
                  <label htmlFor={`${id}-motivo`}>Por qué no se pagó</label>
                  <input id={`${id}-motivo`} required maxLength={200} value={motivo} disabled={enviando}
                    onChange={(e) => setMotivo(e.target.value)} />
                  <div className="acciones">
                    <button type="submit" disabled={enviando || motivo.trim() === ""}>
                      Declarar que no se pagó
                    </button>
                    <button type="button" className="secundario" disabled={enviando} onClick={() => setDeclarando(false)}>
                      Cancelar
                    </button>
                  </div>
                </form>
              )}
              <p>
                <button type="button" className="secundario" disabled={enviando} onClick={cerrar}>
                  Cerrar sin registrar
                </button>
              </p>
            </section>
          )}
        </>
      )}
    </>
  );
}
