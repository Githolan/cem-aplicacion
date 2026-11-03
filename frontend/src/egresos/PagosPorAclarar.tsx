import { type FormEvent, useCallback, useEffect, useId, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { RUTA_ENTRAR } from "../enlace";
import { mensajeDeError as mensajeDeCarga, problemaDelArchivo } from "../Documentos";
import { codigoDe, SIN_CONEXION } from "../fallas";
import {
  aCentavos,
  ENCABEZADO_IDEMPOTENCIA,
  importe,
  LARGO_TITULO,
  mensajeDeError,
  momento,
  renovarClave,
  SEGUNDO_FACTOR_REQUERIDO,
} from "./comun";
import { PedirCodigo } from "./PedirCodigo";
import { exigirSesion, SinSesion, useMontado } from "./useConsorcio";

// Página /egresos/por-aclarar (app 0067, P3 179): quien vio la cuenta en el acto de ejecución y
// ya no ejecuta pagos en ese edificio (renunció o lo reemplazaron) declara que no transfirió.
// Sin esa declaración la orden queda trabada: el Consejo no puede confirmar que no se pagó. Lo
// confirma otro miembro del Consejo. La lista viene de todos los edificios (sin el mandato, el
// edificio ya no aparece en la lista de la persona) y nunca trae la cuenta.
//
// Si transfirió (app 0068, P3 184), sube el comprobante y registra el pago desde acá; la cuenta
// la escribe como figura en el comprobante. Otro miembro del Consejo lo concilia.

type PorAclarar = components["schemas"]["PagoPorAclarar"];

export const RUTA_POR_ACLARAR = "/egresos/por-aclarar";

type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; elementos: PorAclarar[] };
type Aviso = { texto: string; ok: boolean };
type Pendiente = { acto: string; reintentar: () => void };
type Registro = { numero: string; fecha: string; importe: string; cuenta: string };

const REGISTRO_VACIO: Registro = { numero: "", fecha: "", importe: "", cuenta: "" };
const hoy = () => new Date().toLocaleDateString("en-CA");

/** Qué pasa con el comprobante, según el estado de su último archivo. */
function textoDelComprobante(p: PorAclarar): string {
  if (p.comprobante_estado === "disponible") return "El comprobante pasó el antivirus: ya podés registrar el pago.";
  if (p.comprobante_estado === "en_cuarentena") return "El antivirus revisa el comprobante: tocá «Actualizar» en unos segundos.";
  if (p.comprobante_estado === "rechazado") return "El antivirus rechazó el archivo: subí otro.";
  return "Primero subí el comprobante de la transferencia.";
}

export function PagosPorAclarar() {
  const id = useId();
  const navegar = useNavigate();
  const aEntrar = useCallback(() => navegar(RUTA_ENTRAR, { replace: true }), [navegar]);
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [motivos, setMotivos] = useState<Record<string, string>>({});
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [pendiente, setPendiente] = useState<Pendiente | null>(null);
  const [transfirio, setTransfirio] = useState<string | null>(null);
  const [registro, setRegistro] = useState<Registro>(REGISTRO_VACIO);
  const [archivo, setArchivo] = useState<File | null>(null);
  /** Cambia para vaciar el campo del archivo después de subirlo. */
  const [entrada, setEntrada] = useState(0);
  const claveCarga = useRef<string | null>(null);
  const claveArchivo = useRef<string | null>(null);
  /** El comprobante ya creado cuyo archivo todavía no entró: el reintento sube solo el archivo. */
  const sinArchivo = useRef<string | null>(null);
  const clave = useRef<string | null>(null);
  const montado = useMontado();

  useEffect(() => {
    let vigente = true;
    void api
      .GET("/api/v1/pagos-por-aclarar")
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        if (data !== undefined) setLectura({ tipo: "ok", elementos: data.elementos });
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso({ texto: mensajeDeError(response.status, codigoDe(error)), ok: false });
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
  }, [vuelta, aEntrar]);

  function declarar(p: PorAclarar, reintento = false) {
    const motivo = (motivos[p.orden_id] ?? "").trim();
    if (motivo === "" || !montado.current) return;
    if (!reintento) clave.current = null;
    clave.current ??= crypto.randomUUID();
    setEnviando(true);
    setAviso(null);
    void api
      .POST("/api/v1/consorcios/{consorcio_id}/pagos-por-aclarar/{orden_id}/no-realizado", {
        headers: encabezadoCsrf(),
        params: {
          path: { consorcio_id: p.consorcio_id, orden_id: p.orden_id },
          header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current },
        },
        body: { version: p.version, motivo },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data !== undefined) {
          setAviso({ texto: "Listo: declaraste que no transferiste. Ahora lo confirma otro miembro del Consejo.", ok: true });
          setVuelta((v) => v + 1);
          return;
        }
        const codigo = codigoDe(error);
        if (codigo === SEGUNDO_FACTOR_REQUERIDO) {
          const actual = clave.current;
          setPendiente({ acto: "declarar que no transferiste", reintentar: () => {
            clave.current = actual;
            declarar(p, true);
          } });
          return;
        }
        if (renovarClave(response.status, codigo)) clave.current = null;
        setAviso({ texto: mensajeDeError(response.status, codigo), ok: false });
        setVuelta((v) => v + 1);
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setAviso({ texto: SIN_CONEXION, ok: false })))
      .finally(() => setEnviando(false));
  }

  function alEnviar(evento: FormEvent, p: PorAclarar) {
    evento.preventDefault();
    declarar(p);
  }

  function abrirTransferi(p: PorAclarar) {
    setTransfirio(p.orden_id);
    setRegistro(REGISTRO_VACIO);
    setArchivo(null);
    claveCarga.current = null;
    claveArchivo.current = null;
    sinArchivo.current = null;
  }

  /** Un 403 por el segundo factor pide el código y reintenta; lo demás se avisa. */
  function fallo(status: number, codigo: string | undefined, acto: string, reintentar: () => void): void {
    if (codigo === SEGUNDO_FACTOR_REQUERIDO) {
      setPendiente({ acto, reintentar });
      return;
    }
    setAviso({ texto: mensajeDeError(status, codigo), ok: false });
    setVuelta((v) => v + 1);
  }

  /** Crea el comprobante (si todavía no hay uno propio de la orden) y sube el archivo. */
  async function subirComprobante(p: PorAclarar) {
    if (archivo === null || !montado.current) return;
    const problema = problemaDelArchivo(archivo);
    if (problema !== null) {
      setAviso({ texto: problema, ok: false });
      return;
    }
    const ruta = { consorcio_id: p.consorcio_id, orden_id: p.orden_id };
    setEnviando(true);
    setAviso(null);
    try {
      sinArchivo.current ??= p.comprobante_id ?? null;
      if (sinArchivo.current === null) {
        claveCarga.current ??= crypto.randomUUID();
        const creado = await api.POST("/api/v1/consorcios/{consorcio_id}/pagos-por-aclarar/{orden_id}/comprobante", {
          headers: encabezadoCsrf(),
          params: { path: ruta, header: { [ENCABEZADO_IDEMPOTENCIA]: claveCarga.current } },
          body: { titulo: `Transferencia: ${p.concepto}`.slice(0, LARGO_TITULO), fecha: registro.fecha || hoy() },
        });
        exigirSesion(creado.response);
        if (creado.data === undefined) {
          const codigo = codigoDe(creado.error);
          if (renovarClave(creado.response.status, codigo)) claveCarga.current = null;
          fallo(creado.response.status, codigo, "cargar el comprobante", () => void subirComprobante(p));
          return;
        }
        claveCarga.current = null;
        sinArchivo.current = creado.data.id;
      }
      claveArchivo.current ??= crypto.randomUUID();
      const subido = await api.POST(
        "/api/v1/consorcios/{consorcio_id}/pagos-por-aclarar/{orden_id}/comprobante/{documento_id}/versiones",
        {
          headers: { ...encabezadoCsrf(), "Content-Type": archivo.type },
          params: {
            path: { ...ruta, documento_id: sinArchivo.current },
            header: { [ENCABEZADO_IDEMPOTENCIA]: claveArchivo.current },
          },
          // El archivo crudo, como en Documentos: `bodySerializer` lo deja pasar sin JSON.
          body: archivo as unknown as string,
          bodySerializer: (cuerpo) => cuerpo,
        },
      );
      exigirSesion(subido.response);
      if (subido.data === undefined) {
        const codigo = codigoDe(subido.error);
        if (codigo === SEGUNDO_FACTOR_REQUERIDO) {
          fallo(subido.response.status, codigo, "subir el comprobante", () => void subirComprobante(p));
          return;
        }
        if (renovarClave(subido.response.status, codigo)) claveArchivo.current = null;
        const texto = mensajeDeCarga(subido.response.status, codigo, "subir", [], subido.response.headers.has("Retry-After"));
        setAviso({ texto: `El archivo no se subió. ${texto}`, ok: false });
        return;
      }
      claveArchivo.current = null;
      sinArchivo.current = null;
      setArchivo(null);
      setEntrada((n) => n + 1);
      setAviso({ texto: "Listo: subimos el comprobante. El antivirus lo revisa en unos segundos; tocá «Actualizar».", ok: true });
      setVuelta((v) => v + 1);
    } catch (e: unknown) {
      if (e instanceof SinSesion) aEntrar();
      else setAviso({ texto: SIN_CONEXION, ok: false });
    } finally {
      setEnviando(false);
    }
  }

  function registrar(evento: FormEvent, p: PorAclarar, reintento = false) {
    evento.preventDefault();
    if (p.comprobante_id == null || !montado.current) return;
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
      .POST("/api/v1/consorcios/{consorcio_id}/pagos-por-aclarar/{orden_id}/pago", {
        headers: encabezadoCsrf(),
        params: {
          path: { consorcio_id: p.consorcio_id, orden_id: p.orden_id },
          header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current },
        },
        body: {
          version: p.version,
          huella: p.huella,
          numero_operacion: registro.numero.trim(),
          fecha_operacion: registro.fecha,
          importe_centavos: centavos,
          moneda: p.moneda,
          cuenta_destino: registro.cuenta.replace(/\D/g, ""),
          documento_id: p.comprobante_id,
        },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data !== undefined) {
          setTransfirio(null);
          setAviso({ texto: "Listo: registraste el pago. Ahora lo concilia otro miembro del Consejo.", ok: true });
          setVuelta((v) => v + 1);
          return;
        }
        const codigo = codigoDe(error);
        if (codigo !== SEGUNDO_FACTOR_REQUERIDO && renovarClave(response.status, codigo)) clave.current = null;
        const actual = clave.current;
        fallo(response.status, codigo, "registrar el pago", () => {
          clave.current = actual;
          registrar(evento, p, true);
        });
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setAviso({ texto: SIN_CONEXION, ok: false })))
      .finally(() => setEnviando(false));
  }

  const cambiar = (campo: keyof Registro, valor: string) => setRegistro((r) => ({ ...r, [campo]: valor }));

  return (
    <>
      <h1>Pagos para aclarar</h1>
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
      {pendiente === null && lectura.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pendiente === null &&
        lectura.tipo === "ok" &&
        (lectura.elementos.length === 0 ? (
          <p className="tarjeta">No tenés pagos para aclarar.</p>
        ) : (
          <>
            <p>
              Viste la cuenta para pagar estas órdenes y ya no ejecutás pagos en ese edificio. Si no transferiste, declaralo:
              sin eso, la orden no puede volver a pagarse. Si transferiste, subí el comprobante y registrá el pago: lo
              concilia otro miembro del Consejo.
            </p>
            <ul className="versiones" aria-label="Pagos para aclarar">
              {lectura.elementos.map((p) => (
                <li key={p.orden_id} className="tarjeta">
                  <h2>
                    {p.consorcio}: {importe(p.importe_centavos, p.moneda)}
                  </h2>
                  <p>{p.concepto}</p>
                  <p>Viste la cuenta el {momento(p.cuenta_vista_en)}.</p>
                  {transfirio !== p.orden_id && (
                  <form className="formulario" onSubmit={(e) => alEnviar(e, p)}>
                    <label htmlFor={`${id}-${p.orden_id}`}>Por qué no se transfirió</label>
                    <input id={`${id}-${p.orden_id}`} required maxLength={200} disabled={enviando}
                      value={motivos[p.orden_id] ?? ""}
                      onChange={(e) => setMotivos((m) => ({ ...m, [p.orden_id]: e.target.value }))} />
                    <div className="acciones">
                      <button type="submit" disabled={enviando || (motivos[p.orden_id] ?? "").trim() === ""}>
                        No transferí
                      </button>
                      <button type="button" className="secundario" disabled={enviando} onClick={() => abrirTransferi(p)}>
                        Sí transferí
                      </button>
                    </div>
                  </form>
                  )}
                  {transfirio === p.orden_id && (
                    <form className="formulario" onSubmit={(e) => registrar(e, p)}>
                      <h3>Registrá la transferencia</h3>
                      <p role="status">{textoDelComprobante(p)}</p>
                      <label htmlFor={`${id}-archivo`}>Archivo del comprobante (PDF, JPEG o PNG, hasta 20 MB)</label>
                      <input key={entrada} id={`${id}-archivo`} type="file" accept="application/pdf,image/jpeg,image/png"
                        disabled={enviando}
                        onChange={(e) => {
                          claveArchivo.current = null;
                          setArchivo(e.target.files?.[0] ?? null);
                        }} />
                      <div className="acciones">
                        <button type="button" disabled={enviando || archivo === null} onClick={() => void subirComprobante(p)}>
                          Subir el comprobante
                        </button>
                        <button type="button" className="secundario" disabled={enviando} onClick={() => setVuelta((v) => v + 1)}>
                          Actualizar
                        </button>
                      </div>
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
                      <input id={`${id}-cuenta`} required inputMode="numeric" maxLength={30} value={registro.cuenta}
                        disabled={enviando} onChange={(e) => cambiar("cuenta", e.target.value)} />
                      <div className="acciones">
                        <button type="submit" disabled={enviando || p.comprobante_estado !== "disponible"}>
                          Registrar el pago
                        </button>
                        <button type="button" className="secundario" disabled={enviando} onClick={() => setTransfirio(null)}>
                          Volver
                        </button>
                      </div>
                    </form>
                  )}
                </li>
              ))}
            </ul>
          </>
        ))}
    </>
  );
}
