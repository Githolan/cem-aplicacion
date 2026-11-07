import { type FormEvent, useEffect, useId, useRef, useState } from "react";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { useAEntrar } from "../sesionEncima";
import { mensajeDeError as mensajeDeCarga, problemaDelArchivo } from "../Documentos";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { MONEDAS } from "../pagos/FormularioOrden";
import { CampoImporte } from "./CampoImporte";
import {
  aCentavos,
  aDecimal,
  type Aviso,
  ENCABEZADO_IDEMPOTENCIA,
  importe,
  mensajeDeActo,
  mensajeDeError,
  momentoAlFinal,
  renovarClave,
  SEGUNDO_FACTOR_REQUERIDO,
  sinLaFalla,
  sinPisarLoHecho,
  soltarClaveSiCambio,
} from "./comun";
import { AvisoPantalla, FallaDeLectura } from "./EstadoPantalla";
import { PedirCodigo } from "./PedirCodigo";
import { exigirSesion, SinSesion, useAlVolverLaRed, useEsperaAntivirus, useMontado } from "./useConsorcio";

// Página /pagos/por-aclarar (app 0067, P3 179): quien vio la cuenta en el acto de ejecución y
// ya no ejecuta pagos en ese edificio (renunció o lo reemplazaron) declara que no transfirió.
// Sin esa declaración la orden queda trabada: el Consejo no puede confirmar que no se pagó. Lo
// confirma otro miembro del Consejo. La lista viene de todos los edificios (sin el mandato, el
// edificio ya no aparece en la lista de la persona) y nunca trae la cuenta.
//
// Si transfirió (app 0068, P3 184), sube el comprobante y registra el pago desde acá; la cuenta
// la escribe como figura en el comprobante. Otro miembro del Consejo lo concilia.
//
// Si transfirió otra cosa (decisión 3 del 07-11-2026), el mismo formulario registra lo que salió del
// banco. La orden no queda pagada: lo resuelve el Consejo.

type PorAclarar = components["schemas"]["PagoPorAclarar"];

export const RUTA_POR_ACLARAR = "/pagos/por-aclarar";

type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; elementos: PorAclarar[] };
type Pendiente = { acto: string; reintentar: () => void };
type Registro = { numero: string; fecha: string; importe: string; moneda: string; cuenta: string; explicacion: string };

const REGISTRO_VACIO: Registro = { numero: "", fecha: "", importe: "", moneda: "", cuenta: "", explicacion: "" };
const hoy = () => new Date().toLocaleDateString("en-CA");
/** El título del comprobante cargado desde acá. */
const TITULO_COMPROBANTE = "Comprobante de la transferencia";

/** Qué pasa con el comprobante, según el estado de su último archivo. */
function textoDelComprobante(p: PorAclarar): string {
  if (p.comprobante_estado === "disponible") return "El comprobante pasó el antivirus: ya podés registrar el pago.";
  if (p.comprobante_estado === "en_cuarentena") return "El antivirus revisa el comprobante: esto se actualiza solo en unos segundos.";
  if (p.comprobante_estado === "rechazado") return "El antivirus rechazó el archivo: subí otro.";
  return "Primero subí el comprobante de la transferencia.";
}

export function PagosPorAclarar() {
  const id = useId();
  const aEntrar = useAEntrar();
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [motivos, setMotivos] = useState<Record<string, string>>({});
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [pendiente, setPendiente] = useState<Pendiente | null>(null);
  const [transfirio, setTransfirio] = useState<string | null>(null);
  const [registro, setRegistro] = useState<Registro>(REGISTRO_VACIO);
  const [otraCosa, setOtraCosa] = useState(false);
  const [archivo, setArchivo] = useState<File | null>(null);
  /** Cambia para vaciar el campo del archivo después de subirlo. */
  const [entrada, setEntrada] = useState(0);
  /** Lo que no se pudo actualizar con el formulario abierto: sigue a la vista con lo escrito. */
  const [fallaLista, setFallaLista] = useState<string | null>(null);
  /** El antivirus agotó la espera: deja de mirar solo hasta que la persona lo pida. */
  const [esperaAgotada, setEsperaAgotada] = useState(false);
  const conFormulario = useRef(false);
  const claveCarga = useRef<string | null>(null);
  const claveArchivo = useRef<string | null>(null);
  /** El comprobante ya creado cuyo archivo todavía no entró: el reintento sube solo el archivo. */
  const sinArchivo = useRef<string | null>(null);
  const clave = useRef<string | null>(null);
  const ultimoActo = useRef<string | null>(null);
  const montado = useMontado();

  useEffect(() => {
    let vigente = true;
    function noLlego(texto: string) {
      if (conFormulario.current) setFallaLista(`No pudimos actualizar la lista. ${texto}`);
      else {
        setLectura({ tipo: "sin_servicio" });
        setAviso(sinPisarLoHecho(texto));
      }
    }
    void api
      .GET("/api/v1/pagos-por-aclarar")
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        if (data !== undefined) {
          setFallaLista(null);
          setLectura({ tipo: "ok", elementos: data.elementos });
        } else noLlego(mensajeDeError(response.status, codigoDe(error)));
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else noLlego(SIN_CONEXION);
      });
    return () => {
      vigente = false;
    };
  }, [vuelta, aEntrar]);

  function reintentarLectura() {
    setAviso(sinLaFalla);
    setLectura({ tipo: "cargando" });
    setVuelta((v) => v + 1);
  }

  const abierto = lectura.tipo === "ok" ? lectura.elementos.find((p) => p.orden_id === transfirio) : undefined;
  conFormulario.current = abierto !== undefined;
  useEsperaAntivirus(
    abierto?.comprobante_estado === "en_cuarentena" && !esperaAgotada && pendiente === null,
    () => setVuelta((v) => v + 1),
    () => {
      setEsperaAgotada(true);
      setFallaLista("El antivirus sigue revisando el comprobante. Tocá «Actualizar» en un rato.");
    },
  );
  useAlVolverLaRed(fallaLista !== null, () => setVuelta((v) => v + 1));

  function declarar(p: PorAclarar, reintento = false) {
    const motivo = (motivos[p.orden_id] ?? "").trim();
    if (motivo === "" || !montado.current) return;
    if (!reintento) soltarClaveSiCambio(clave, ultimoActo, JSON.stringify(["declarar", p.orden_id, p.version, motivo]));
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
          clave.current = null;
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
        setAviso({ texto: mensajeDeActo(response.status, codigo), ok: false });
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
    setRegistro({ ...REGISTRO_VACIO, moneda: p.moneda });
    setArchivo(null);
    setEsperaAgotada(false);
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
    setAviso({ texto: mensajeDeActo(status, codigo), ok: false });
    setVuelta((v) => v + 1);
  }

  /** Crea el comprobante (si todavía no hay uno propio de la orden) y sube el archivo. */
  async function subirComprobante(p: PorAclarar, elegido: File) {
    if (!montado.current) return;
    const problema = problemaDelArchivo(elegido);
    if (problema !== null) {
      // Ese archivo no va a entrar: el campo queda vacío para elegir otro.
      setArchivo(null);
      setEntrada((n) => n + 1);
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
          // Un título fijo: el concepto de la orden puede no pasar el filtro de contactos del título.
          body: { titulo: TITULO_COMPROBANTE, fecha: registro.fecha || hoy() },
        });
        exigirSesion(creado.response);
        if (creado.data === undefined) {
          const codigo = codigoDe(creado.error);
          if (renovarClave(creado.response.status, codigo)) claveCarga.current = null;
          fallo(creado.response.status, codigo, "cargar el comprobante", () => void subirComprobante(p, elegido));
          return;
        }
        claveCarga.current = null;
        sinArchivo.current = creado.data.id;
      }
      claveArchivo.current ??= crypto.randomUUID();
      const subido = await api.POST(
        "/api/v1/consorcios/{consorcio_id}/pagos-por-aclarar/{orden_id}/comprobante/{documento_id}/versiones",
        {
          headers: { ...encabezadoCsrf(), "Content-Type": elegido.type },
          params: {
            path: { ...ruta, documento_id: sinArchivo.current },
            header: { [ENCABEZADO_IDEMPOTENCIA]: claveArchivo.current },
          },
          // El archivo crudo, como en Documentos: `bodySerializer` lo deja pasar sin JSON.
          body: elegido as unknown as string,
          bodySerializer: (cuerpo) => cuerpo,
        },
      );
      exigirSesion(subido.response);
      if (subido.data === undefined) {
        const codigo = codigoDe(subido.error);
        if (codigo === SEGUNDO_FACTOR_REQUERIDO) {
          fallo(subido.response.status, codigo, "subir el comprobante", () => void subirComprobante(p, elegido));
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
      setEsperaAgotada(false);
      setAviso({ texto: "Listo: subimos el comprobante. El antivirus lo revisa en unos segundos y esto se actualiza solo.", ok: true });
      setVuelta((v) => v + 1);
    } catch (e: unknown) {
      if (e instanceof SinSesion) aEntrar();
      else setAviso({ texto: SIN_CONEXION, ok: false });
    } finally {
      setEnviando(false);
    }
  }

  function registrarOtraCosa(evento: FormEvent, p: PorAclarar, reintento = false) {
    evento.preventDefault();
    if (p.comprobante_id == null || !montado.current) return;
    const centavos = aCentavos(registro.importe);
    if (centavos === null) {
      setAviso({ texto: "Escribí el importe que salió del banco.", ok: false });
      return;
    }
    if (!reintento) soltarClaveSiCambio(clave, ultimoActo, JSON.stringify(["otra-cosa", p.orden_id, p.version, p.huella, p.comprobante_id, registro]));
    clave.current ??= crypto.randomUUID();
    setEnviando(true);
    setAviso(null);
    void api
      .POST("/api/v1/consorcios/{consorcio_id}/pagos-por-aclarar/{orden_id}/diferencia", {
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
          importe_real: aDecimal(centavos),
          moneda: registro.moneda,
          cuenta_real: registro.cuenta.replace(/\D/g, ""),
          documento_id: p.comprobante_id,
          explicacion: registro.explicacion.trim(),
        },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data !== undefined) {
          clave.current = null;
          setTransfirio(null);
          setOtraCosa(false);
          setAviso({ texto: "Listo: registraste lo que pasó. La orden no queda pagada: lo resuelve el Consejo.", ok: true });
          setVuelta((v) => v + 1);
          return;
        }
        const codigo = codigoDe(error);
        if (codigo !== SEGUNDO_FACTOR_REQUERIDO && renovarClave(response.status, codigo)) clave.current = null;
        const actual = clave.current;
        fallo(response.status, codigo, "registrar lo que pasó", () => {
          clave.current = actual;
          registrarOtraCosa(evento, p, true);
        });
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setAviso({ texto: SIN_CONEXION, ok: false })))
      .finally(() => setEnviando(false));
  }

  function registrar(evento: FormEvent, p: PorAclarar, reintento = false) {
    evento.preventDefault();
    if (p.comprobante_id == null || !montado.current) return;
    const centavos = aCentavos(registro.importe);
    if (centavos === null) {
      setAviso({ texto: "Escribí el importe tal como figura en el comprobante.", ok: false });
      return;
    }
    if (!reintento) soltarClaveSiCambio(clave, ultimoActo, JSON.stringify(["registrar", p.orden_id, p.version, p.huella, p.comprobante_id, registro]));
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
          lectura_mal: false,
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
          clave.current = null;
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
      <AvisoPantalla aviso={aviso} />
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
      {pendiente === null && lectura.tipo === "sin_servicio" && <FallaDeLectura alReintentar={reintentarLectura} avisar={setAviso} />}
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
                  <p>Viste la cuenta el {momentoAlFinal(p.cuenta_vista_en)}</p>
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
                    <form className="formulario" onSubmit={(e) => (otraCosa ? registrarOtraCosa(e, p) : registrar(e, p))}>
                      <h3>{otraCosa ? "Pagué otra cosa" : "Registrá la transferencia"}</h3>
                      <p role="status">{textoDelComprobante(p)}</p>
                      {fallaLista !== null && (
                        <p className="aviso-error" role="alert">
                          {fallaLista}
                        </p>
                      )}
                      <label htmlFor={`${id}-archivo`}>Archivo del comprobante (PDF, JPEG o PNG, hasta 20 MB)</label>
                      <input key={entrada} id={`${id}-archivo`} type="file" accept="application/pdf,image/jpeg,image/png"
                        disabled={enviando}
                        onChange={(e) => {
                          const elegido = e.target.files?.[0] ?? null;
                          claveArchivo.current = null;
                          setArchivo(elegido);
                          if (elegido !== null) void subirComprobante(p, elegido);
                        }} />
                      {((archivo !== null && !enviando) || fallaLista !== null) && (
                        <div className="acciones">
                          {/* Sin esto, reintentar sería volver a elegir el mismo archivo, y el navegador no avisa. */}
                          {archivo !== null && !enviando && (
                            <button type="button" onClick={() => void subirComprobante(p, archivo)}>
                              Reintentar la subida
                            </button>
                          )}
                          {fallaLista !== null && (
                            <button type="button" className="secundario" disabled={enviando}
                              onClick={() => {
                                setEsperaAgotada(false);
                                setVuelta((v) => v + 1);
                              }}>
                              Actualizar
                            </button>
                          )}
                        </div>
                      )}
                      <label htmlFor={`${id}-numero`}>Número de operación</label>
                      <input id={`${id}-numero`} required maxLength={60} value={registro.numero} disabled={enviando}
                        onChange={(e) => cambiar("numero", e.target.value)} />
                      <label htmlFor={`${id}-fecha`}>Fecha de la transferencia</label>
                      <input id={`${id}-fecha`} type="date" required value={registro.fecha} disabled={enviando}
                        onChange={(e) => cambiar("fecha", e.target.value)} />
                      <label htmlFor={`${id}-importe`}>{otraCosa ? "Importe que salió del banco" : "Importe transferido (como figura en el comprobante)"}</label>
                      <CampoImporte id={`${id}-importe`} valor={registro.importe} deshabilitado={enviando}
                        alCambiar={(texto) => cambiar("importe", texto)} />
                      {otraCosa && (
                        <>
                          <label htmlFor={`${id}-moneda`}>Moneda que salió del banco</label>
                          <input id={`${id}-moneda`} required list={`${id}-monedas`} pattern="[A-Z]{3}" maxLength={3} autoCapitalize="characters"
                            aria-describedby={`${id}-moneda-ayuda`} value={registro.moneda} disabled={enviando}
                            onChange={(e) => cambiar("moneda", e.target.value.toUpperCase())} />
                          <datalist id={`${id}-monedas`}>
                            {MONEDAS.map((m) => (
                              <option key={m} value={m} />
                            ))}
                          </datalist>
                          <p id={`${id}-moneda-ayuda`}>El código de tres letras, como {MONEDAS.join(" o ")}. Si no es la de la orden, solo se puede reclamar.</p>
                        </>
                      )}
                      <label htmlFor={`${id}-cuenta`}>CBU/CVU de destino (como figura en el comprobante)</label>
                      <input id={`${id}-cuenta`} required inputMode="numeric" maxLength={30} value={registro.cuenta}
                        disabled={enviando} onChange={(e) => cambiar("cuenta", e.target.value)} />
                      {otraCosa && (
                        <>
                          <label htmlFor={`${id}-explicacion`}>Qué pasó</label>
                          <textarea id={`${id}-explicacion`} required maxLength={300} rows={3} value={registro.explicacion} disabled={enviando}
                            onChange={(e) => cambiar("explicacion", e.target.value)} />
                          <p>Hasta 300 caracteres. No incluyas el número de una cuenta. La orden no queda pagada: lo resuelve el Consejo.</p>
                        </>
                      )}
                      <div className="acciones">
                        <button type="submit" disabled={enviando || p.comprobante_estado !== "disponible"}>
                          {otraCosa ? "Registrar lo que pasó" : "Registrar el pago"}
                        </button>
                        <button type="button" className="secundario" disabled={enviando} onClick={() => setOtraCosa((v) => !v)}>
                          {otraCosa ? "Volver al registro" : "Pagué otra cosa"}
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
