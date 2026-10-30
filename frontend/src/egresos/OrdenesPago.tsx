import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { codigoDe, SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "../fallas";
import {
  aCentavos,
  aTexto,
  cambioLoVisto,
  ENCABEZADO_IDEMPOTENCIA,
  ESTADO,
  importe,
  mensajeDeError,
  momento,
  renovarClave,
  SEGUNDO_FACTOR_REQUERIDO,
  TEXTO_ESTADO_ORDEN,
  TEXTO_FRANJA,
} from "./comun";
import { ElegirConsorcio } from "./ElegirConsorcio";
import { PedirCodigo } from "./PedirCodigo";
import { RUTA_PROVEEDORES } from "./Proveedores";
import { exigirSesion, SinSesion, useConsorcio } from "./useConsorcio";

// Página /egresos/ordenes (pantalla 2, aprobada el 30-10-2026; app 0045 a 0050): la lista de
// quien prepara, con estado, importe, proveedor, fecha y franja; preparar y modificar con el
// proveedor elegido de la lista (nunca escrito a mano); enviar a revisión, renovar una aprobación
// vencida y emitir. Emitir deja la orden en ejecución: no mueve dinero.

type Orden = components["schemas"]["OrdenPago"];
type Proveedor = components["schemas"]["Proveedor"];
type Contrato = components["schemas"]["Contrato"];

export const RUTA_ORDENES = "/egresos/ordenes";
const MONEDAS = ["ARS", "USD"] as const;
/** Estados en que la orden se puede modificar (`modificar_orden_pago`, 0045). */
const MODIFICABLES = new Set<Orden["estado"]>(["borrador", "en_revision", "aprobada", "fallida"]);

type Lectura =
  | { tipo: "cargando" }
  | { tipo: "sin_permiso" }
  | { tipo: "sin_servicio" }
  | { tipo: "ok"; ordenes: Orden[]; proveedores: Proveedor[]; contratos: Contrato[] };

type Datos = {
  proveedor: string;
  importe: string;
  moneda: string;
  concepto: string;
  comprobante: string;
  reserva: boolean;
  /** El contrato o abono que la respalda ("" = ninguno): solo así es rutinaria. */
  contrato: string;
};
type Formulario = { modo: "preparar" } | { modo: "modificar"; orden: Orden };
type Acto =
  | { tipo: "guardar"; formulario: Formulario }
  | { tipo: "enviar"; orden: Orden }
  | { tipo: "renovar"; orden: Orden }
  | { tipo: "emitir"; orden: Orden; huella: string };
type Pendiente = { acto: string; reintentar: () => void };
type Aviso = { texto: string; ok: boolean };

const opcional = (v: string) => (v.trim() === "" ? null : v.trim());

/** Los contratos que puede declarar una orden: vigentes, del proveedor y de la moneda elegidos.
 * La base vuelve a verificar que lo cubra (alcance, importe máximo, fechas) al evaluarla. */
export function contratosAplicables(contratos: Contrato[], proveedor: string, moneda: string): Contrato[] {
  return contratos.filter((k) => k.estado === "vigente" && k.proveedor_id === proveedor && k.moneda === moneda);
}

/** Si la aprobación de una orden aprobada ya venció (la base lo vuelve a controlar al emitir). */
export function vencida(o: Orden, ahora = Date.now()): boolean {
  return o.estado === "aprobada" && o.aprobacion_vence_en !== null && Date.parse(o.aprobacion_vence_en) <= ahora;
}

export const AVISO_MODIFICAR =
  "Esta orden ya está en revisión o aprobada: modificarla crea una versión nueva, la vuelve a revisión y las aprobaciones anteriores dejan de contar.";

// Cada edificio monta la pantalla de nuevo: nada de un edificio (estado, respuestas tardías,
// el reintento pendiente después del código) llega a la pantalla de otro.
export function OrdenesPago() {
  const elegido = useConsorcio();
  return <OrdenesPagoDelEdificio key={elegido.consorcio} {...elegido} />;
}

function OrdenesPagoDelEdificio({ pantalla, consorcio, setConsorcio, aEntrar }: ReturnType<typeof useConsorcio>) {
  const id = useId();
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [formulario, setFormulario] = useState<Formulario | null>(null);
  const [datos, setDatos] = useState<Datos | null>(null);
  const [confirmar, setConfirmar] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [pendiente, setPendiente] = useState<Pendiente | null>(null);
  const clave = useRef<string | null>(null);
  /** Órdenes cuyo contrato dejó de regir (lo dijo la API al emitir): se ofrece renovarlas. */
  const [sinContrato, setSinContrato] = useState<ReadonlySet<string>>(new Set());

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    const ruta = { params: { path: { consorcio_id: consorcio } } };
    void Promise.all([
      api.GET("/api/v1/consorcios/{consorcio_id}/ordenes-pago", ruta),
      api.GET("/api/v1/consorcios/{consorcio_id}/proveedores", ruta),
      api.GET("/api/v1/consorcios/{consorcio_id}/contratos", ruta),
    ])
      .then(([ordenes, proveedores, contratos]) => {
        exigirSesion(ordenes.response);
        exigirSesion(proveedores.response);
        exigirSesion(contratos.response);
        if (!vigente) return;
        if (ordenes.data !== undefined && proveedores.data !== undefined && contratos.data !== undefined) {
          setLectura({
            tipo: "ok",
            ordenes: ordenes.data.elementos,
            proveedores: proveedores.data.elementos,
            contratos: contratos.data.elementos,
          });
          return;
        }
        const falla = ordenes.data === undefined ? ordenes : proveedores.data === undefined ? proveedores : contratos;
        const codigo = codigoDe(falla.error);
        if (codigo === SEGUNDO_FACTOR_REQUERIDO)
          setPendiente({ acto: "ver las órdenes", reintentar: () => setVuelta((v) => v + 1) });
        else if (falla.response.status === ESTADO.sinPermiso && codigo === "sin_permiso") setLectura({ tipo: "sin_permiso" });
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso({ texto: mensajeDeError(falla.response.status, codigo), ok: false });
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

  function volverAPedir() {
    setLectura({ tipo: "cargando" });
    setVuelta((v) => v + 1);
  }

  function abrirPreparar(proveedores: Proveedor[]) {
    clave.current = null;
    setConfirmar(false);
    setAviso(null);
    const activo = proveedores.find((p) => p.estado === "activo");
    setDatos({
      proveedor: activo?.id ?? "",
      importe: "",
      moneda: "ARS",
      concepto: "",
      comprobante: "",
      reserva: false,
      contrato: "",
    });
    setFormulario({ modo: "preparar" });
  }

  /** Modificar parte de la versión vigente: el comprobante solo está en la ficha. */
  function abrirModificar(o: Orden) {
    setAviso(null);
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}", {
        params: { path: { consorcio_id: consorcio, orden_id: o.id } },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        const v = data?.versiones.find((x) => x.numero === data.orden.version_vigente);
        if (data !== undefined && v !== undefined) {
          clave.current = null;
          setConfirmar(false);
          const aplica =
            lectura.tipo === "ok" &&
            contratosAplicables(lectura.contratos, v.proveedor_id, v.moneda).some((k) => k.id === v.contrato_id);
          if (v.contrato_id !== null && !aplica)
            setAviso({ texto: "El contrato de esta orden ya no rige: se quitó del formulario. Si guardás, pide aprobación.", ok: false });
          setDatos({
            proveedor: v.proveedor_id,
            importe: aTexto(v.importe_centavos),
            moneda: v.moneda,
            concepto: v.concepto,
            comprobante: v.comprobante ?? "",
            reserva: v.usa_fondo_reserva,
            contrato: aplica && v.contrato_id !== null ? v.contrato_id : "",
          });
          setFormulario({ modo: "modificar", orden: data.orden });
        } else if (codigoDe(error) === SEGUNDO_FACTOR_REQUERIDO)
          setPendiente({ acto: "modificar la orden", reintentar: () => abrirModificar(o) });
        else setAviso({ texto: mensajeDeError(response.status, codigoDe(error)), ok: false });
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setAviso({ texto: SIN_CONEXION, ok: false })));
  }

  /** Emitir manda la versión y la huella vistas: si la orden cambió, la API responde 409. */
  function emitir(o: Orden) {
    setAviso(null);
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}", {
        params: { path: { consorcio_id: consorcio, orden_id: o.id } },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        const v = data?.versiones.find((x) => x.numero === o.version_vigente);
        if (data !== undefined && v !== undefined && data.orden.version_vigente === o.version_vigente)
          hacer({ tipo: "emitir", orden: o, huella: v.huella });
        else if (data !== undefined) {
          setAviso({ texto: mensajeDeError(ESTADO.conflicto, "version_desactualizada"), ok: false });
          volverAPedir();
        } else if (codigoDe(error) === SEGUNDO_FACTOR_REQUERIDO)
          setPendiente({ acto: "emitir la orden", reintentar: () => emitir(o) });
        else setAviso({ texto: mensajeDeError(response.status, codigoDe(error)), ok: false });
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setAviso({ texto: SIN_CONEXION, ok: false })));
  }

  function cerrar() {
    clave.current = null;
    setConfirmar(false);
    setFormulario(null);
    setDatos(null);
  }

  function cambiar<K extends keyof Datos>(campo: K, valor: Datos[K]) {
    clave.current = null;
    setConfirmar(false);
    setDatos((d) => {
      if (d === null) return d;
      const nuevo = { ...d, [campo]: valor };
      if (campo === "proveedor" || campo === "moneda") nuevo.contrato = "";
      return nuevo;
    });
  }

  function enviar(evento: FormEvent) {
    evento.preventDefault();
    if (formulario === null || datos === null) return;
    if (aCentavos(datos.importe) === null) {
      setAviso({ texto: "Escribí el importe en pesos, por ejemplo 12345,50.", ok: false });
      return;
    }
    const conAprobaciones = formulario.modo === "modificar" && ["en_revision", "aprobada"].includes(formulario.orden.estado);
    if (conAprobaciones && !confirmar) {
      setConfirmar(true);
      return;
    }
    hacer({ tipo: "guardar", formulario });
  }

  /** `reintento`: el mismo acto después de verificar el segundo factor, con la misma clave. */
  function hacer(acto: Acto, reintento = false) {
    // Cada botón de la lista es un intento nuevo; el formulario conserva su clave mientras no
    // cambien los datos.
    if (!reintento && acto.tipo !== "guardar") clave.current = null;
    clave.current ??= crypto.randomUUID();
    const encabezado = { [ENCABEZADO_IDEMPOTENCIA]: clave.current };
    const opciones = { headers: encabezadoCsrf() };
    setEnviando(true);
    setAviso(null);
    let pedido;
    let texto: string;
    if (acto.tipo === "guardar") {
      const d = datos as Datos;
      const esencial = {
        importe_centavos: aCentavos(d.importe) as number,
        moneda: d.moneda,
        concepto: d.concepto.trim(),
        proveedor_id: d.proveedor,
        comprobante: opcional(d.comprobante),
        usa_fondo_reserva: d.reserva,
        contrato_id: d.contrato === "" ? null : d.contrato,
      };
      const f = acto.formulario;
      pedido =
        f.modo === "preparar"
          ? api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago", {
              ...opciones,
              params: { path: { consorcio_id: consorcio }, header: encabezado },
              body: esencial,
            })
          : api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/versiones", {
              ...opciones,
              params: { path: { consorcio_id: consorcio, orden_id: f.orden.id }, header: encabezado },
              body: { ...esencial, version_esperada: f.orden.version_vigente },
            });
      texto = f.modo === "preparar" ? "Listo: la orden quedó en borrador." : "Listo: guardamos la versión nueva.";
    } else {
      const ruta = { path: { consorcio_id: consorcio, orden_id: acto.orden.id }, header: encabezado };
      const version = acto.orden.version_vigente;
      if (acto.tipo === "enviar") {
        pedido = api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/enviar-a-revision", {
          ...opciones, params: ruta, body: { version_esperada: version },
        });
        texto = "Listo: la orden se envió. Si su franja no pide aprobación, ya quedó aprobada.";
      } else if (acto.tipo === "renovar") {
        pedido = api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/renovar", {
          ...opciones, params: ruta, body: { version_esperada: version },
        });
        texto = "Listo: la orden tiene una versión nueva y volvió a revisión.";
      } else {
        pedido = api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/emitir", {
          ...opciones, params: ruta, body: { version, huella: acto.huella },
        });
        texto = "Listo: la orden quedó en ejecución. No se movió dinero.";
      }
    }
    void pedido
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data !== undefined) {
          cerrar();
          setAviso({ texto, ok: true });
          if (acto.tipo === "renovar")
            setSinContrato((s) => new Set([...s].filter((x) => x !== acto.orden.id)));
          volverAPedir();
          return;
        }
        const codigo = codigoDe(error);
        if (codigo === SEGUNDO_FACTOR_REQUERIDO) {
          // Se reintenta el mismo acto con la misma clave.
          const actual = clave.current;
          setPendiente({
            acto: "seguir con la orden",
            reintentar: () => {
              clave.current = actual;
              hacer(acto, true);
            },
          });
          return;
        }
        if (renovarClave(response.status, codigo)) clave.current = null;
        setAviso({ texto: mensajeDeError(response.status, codigo), ok: false });
        if (codigo === "contrato_no_vigente" && acto.tipo === "emitir")
          setSinContrato((s) => new Set(s).add(acto.orden.id));
        if (cambioLoVisto(response.status, codigo) && codigo !== "sin_cambios") {
          cerrar();
          volverAPedir();
        }
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setAviso({ texto: SIN_CONEXION, ok: false })))
      .finally(() => setEnviando(false));
  }

  return (
    <>
      <h1>Órdenes de pago</h1>
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={enviando}
        elegir={setConsorcio} />
      <p>
        <Link to={RUTA_PROVEEDORES}>Proveedores</Link>
      </p>

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
            <p className="tarjeta">No tenés la facultad de preparar órdenes de pago en este edificio.</p>
          )}
          {lectura.tipo === "ok" && formulario === null && (
            <>
              <button type="button" disabled={enviando} onClick={() => abrirPreparar(lectura.proveedores)}>
                Preparar una orden
              </button>
              {lectura.ordenes.length === 0 ? (
                <p className="tarjeta">El edificio no tiene órdenes de pago.</p>
              ) : (
                <ul className="versiones" aria-label="Órdenes de pago">
                  {lectura.ordenes.map((o) => (
                    <li key={o.id} className="tarjeta">
                      <h2>
                        {o.razon_social}: {importe(o.importe_centavos, o.moneda)}
                      </h2>
                      <dl className="detalles">
                        <dt>Estado</dt>
                        <dd>{TEXTO_ESTADO_ORDEN[o.estado]}</dd>
                        <dt>Concepto</dt>
                        <dd>{o.concepto}</dd>
                        <dt>Preparada</dt>
                        <dd>{momento(o.creada_en)}</dd>
                        <dt>Franja</dt>
                        <dd>{TEXTO_FRANJA[o.franja]}</dd>
                        {o.alerta_fraccionamiento && (
                          <>
                            <dt>Alerta</dt>
                            <dd>Sumada a las demás del mes, supera el umbral o el máximo del contrato: revisá si se fraccionó un gasto.</dd>
                          </>
                        )}
                        {o.aprobacion_vence_en !== null && (
                          <>
                            <dt>Se emite hasta</dt>
                            <dd>{vencida(o) ? "La aprobación venció: renovala" : momento(o.aprobacion_vence_en)}</dd>
                          </>
                        )}
                        {o.motivo_rechazo !== null && (
                          <>
                            <dt>Motivo del rechazo</dt>
                            <dd>{o.motivo_rechazo}</dd>
                          </>
                        )}
                      </dl>
                      <div className="formulario">
                        <div className="acciones">
                          {o.estado === "borrador" && (
                            <button type="button" disabled={enviando} onClick={() => hacer({ tipo: "enviar", orden: o })}>
                              Enviar a revisión
                            </button>
                          )}
                          {o.estado === "aprobada" && !vencida(o) && !sinContrato.has(o.id) && (
                            <button type="button" disabled={enviando} onClick={() => emitir(o)}>
                              Emitir
                            </button>
                          )}
                          {(vencida(o) || (o.estado === "aprobada" && sinContrato.has(o.id))) && (
                            <button type="button" disabled={enviando} onClick={() => hacer({ tipo: "renovar", orden: o })}>
                              Renovar
                            </button>
                          )}
                          {MODIFICABLES.has(o.estado) && (
                            <button type="button" className="secundario" disabled={enviando} onClick={() => abrirModificar(o)}>
                              Modificar
                            </button>
                          )}
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}

          {lectura.tipo === "ok" && formulario !== null && datos !== null && (
            <form className="formulario tarjeta" onSubmit={enviar} aria-labelledby={`${id}-form`}>
              <h2 id={`${id}-form`}>{formulario.modo === "preparar" ? "Preparar una orden" : "Modificar la orden"}</h2>
              <label htmlFor={`${id}-proveedor`}>Proveedor</label>
              <select id={`${id}-proveedor`} required value={datos.proveedor} disabled={enviando}
                onChange={(e) => cambiar("proveedor", e.target.value)}>
                <option value="" disabled>
                  Elegí un proveedor
                </option>
                {lectura.proveedores
                  .filter((p) => p.estado === "activo" || p.id === datos.proveedor)
                  .map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.razon_social}
                    </option>
                  ))}
              </select>
              <label htmlFor={`${id}-importe`}>Importe</label>
              <input id={`${id}-importe`} required inputMode="decimal" value={datos.importe} disabled={enviando}
                onChange={(e) => cambiar("importe", e.target.value)} />
              <label htmlFor={`${id}-moneda`}>Moneda</label>
              <select id={`${id}-moneda`} value={datos.moneda} disabled={enviando} onChange={(e) => cambiar("moneda", e.target.value)}>
                {MONEDAS.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
              <label htmlFor={`${id}-contrato`}>Contrato o abono (opcional)</label>
              <select id={`${id}-contrato`} value={datos.contrato} disabled={enviando}
                onChange={(e) => cambiar("contrato", e.target.value)}>
                <option value="">Ninguno: no es un gasto rutinario</option>
                {contratosAplicables(lectura.contratos, datos.proveedor, datos.moneda).map((k) => (
                  <option key={k.id} value={k.id}>
                    {k.alcance} (hasta {importe(k.importe_maximo_centavos, k.moneda)})
                  </option>
                ))}
              </select>
              <label htmlFor={`${id}-concepto`}>Concepto</label>
              <input id={`${id}-concepto`} required maxLength={300} value={datos.concepto} disabled={enviando}
                onChange={(e) => cambiar("concepto", e.target.value)} />
              <label htmlFor={`${id}-comprobante`}>Comprobante (opcional)</label>
              <input id={`${id}-comprobante`} maxLength={200} value={datos.comprobante} disabled={enviando}
                onChange={(e) => cambiar("comprobante", e.target.value)} />
              <label>
                <input type="checkbox" checked={datos.reserva} disabled={enviando}
                  onChange={(e) => cambiar("reserva", e.target.checked)} />{" "}
                Usa el fondo de reserva (siempre lo aprueba el Consejo)
              </label>
              {confirmar && (
                <p className="aviso-error" role="alert">
                  {AVISO_MODIFICAR}
                </p>
              )}
              <div className="acciones">
                <button type="submit" disabled={enviando || datos.proveedor === ""}>
                  {confirmar ? "Sí, crear la versión nueva" : "Guardar"}
                </button>
                <button type="button" className="secundario" disabled={enviando} onClick={cerrar}>
                  Cancelar
                </button>
              </div>
            </form>
          )}
        </>
      )}
    </>
  );
}
