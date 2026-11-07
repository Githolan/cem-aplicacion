import { type DragEvent, type FormEvent, useEffect, useId, useState } from "react";
import { Link } from "react-router";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { type Aviso, ENCABEZADO_IDEMPOTENCIA, ESTADO, importe, mensajeDeError, sinLaFalla, sinPisarLoHecho } from "../egresos/comun";
import { ElegirConsorcio } from "../egresos/ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "../egresos/EstadoPantalla";
import { PedirCodigo } from "../egresos/PedirCodigo";
import { exigirSesion, SinSesion, useConsorcio } from "../egresos/useConsorcio";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { REQUISITOS } from "../permisos";
import { MONEDAS } from "./FormularioOrden";
import { RUTA_PAGOS, rutaFicha } from "./rutas";
import { EJEMPLO_EXTRACTO } from "./textos";
import { useActo } from "./useActo";

// `/pagos/cotejo`: cotejar con el banco, a mano o con el extracto (§10). Cotejar no cambia la orden
// ni mueve dinero. Quién coteja cada pago lo decide el servidor (`puede_cotejar`): el archivo se
// lee, se compara y se guarda allá, y acá solo se elige qué columna es cada dato.

type Elemento = components["schemas"]["PagoPorCotejar"];
type Cotejado = components["schemas"]["PagoCotejado"];
type Vista = components["schemas"]["VistaExtracto"];
type Columnas = components["schemas"]["ColumnasExtracto"];
type Resultado = components["schemas"]["ResultadoCotejo"];
type Signo = components["schemas"]["SignoExtracto"];

type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; elementos: Elemento[]; siguiente: string | null };
type Extracto =
  | { tipo: "sin_archivo" }
  | { tipo: "leyendo" }
  | { tipo: "columnas"; archivo: File; vista: Vista }
  | { tipo: "resultado"; archivo: File; vista: Vista; resultado: Resultado };

const TIPO_CSV = "text/csv";
const BYTES_POR_MB = 1024 * 1024;
const SIN_NUMERO = "";
/** Las respuestas que se resuelven eligiendo otras columnas. */
const VUELVE_A_ELEGIR = new Set(["columnas_no_encontradas", "columnas_sin_elegir", "extracto_sin_movimientos"]);
const SIGNOS: readonly { valor: Signo; texto: string }[] = [
  { valor: "con_signo", texto: "Con signo menos, en una columna de importes" },
  { valor: "sin_signo", texto: "Sin signo, en una columna solo de débitos" },
];

const cantidad = (n: number, una: string, varias: string) => (n === 1 ? una : `${n} ${varias}`);

/** «2026-11-05» a «05/11/2026». */
const fechaLegible = (iso: string) => iso.split("-").reverse().join("/");

const detalle = (numero: string | null, fecha: string, nombre: string) =>
  numero === null ? fechaLegible(fecha) : `${nombre} ${numero} · ${fechaLegible(fecha)}`;

/** La columna que parece ser ese dato, para no empezar de cero. */
const parecida = (columnas: string[], patron: RegExp) => columnas.find((c) => patron.test(c)) ?? "";

export function Cotejo() {
  const elegido = useConsorcio(REQUISITOS.cotejo);
  return <CotejoDelEdificio key={elegido.consorcio} {...elegido} />;
}

function CotejoDelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar }: ReturnType<typeof useConsorcio>) {
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [avisoLectura, setAvisoLectura] = useState<Aviso | null>(null);
  const [despues, setDespues] = useState<string | null>(null);
  const [vuelta, setVuelta] = useState(0);
  const [tope, setTope] = useState<number | null>(null);
  const [maximo, setMaximo] = useState<number | null>(null);
  /** La del extracto que se va a subir. El archivo no la dice; sin leer la lista, manda la guardada. */
  const [moneda, setMoneda] = useState<string | null>(null);
  const [tildados, setTildados] = useState<ReadonlySet<string>>(new Set());
  const [extracto, setExtracto] = useState<Extracto>({ tipo: "sin_archivo" });
  const [entrada, setEntrada] = useState(0);
  const acto = useActo(aEntrar);
  const { enviando, aviso, setAviso, pendiente, setPendiente, montado } = acto;

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    // Si falla «Ver más», lo ya cargado queda y el mismo botón vuelve a pedir esa página.
    const falla = (l: Lectura): Lectura => (despues !== null && l.tipo === "ok" ? l : { tipo: "sin_servicio" });
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/pagos/cotejo", {
        params: { path: { consorcio_id: consorcio }, query: despues === null ? {} : { despues } },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        const codigo = codigoDe(error);
        if (data !== undefined) {
          setAvisoLectura(sinLaFalla);
          setTope(data.tope_bytes);
          setMaximo(data.maximo_pagos);
          setMoneda(data.moneda_extracto);
          setLectura((l) => ({
            tipo: "ok",
            // Una página más se suma a lo que ya se ve; volver a empezar la reemplaza.
            elementos: despues !== null && l.tipo === "ok" ? [...l.elementos, ...data.elementos] : data.elementos,
            siguiente: data.siguiente,
          }));
          // Lo tildado que ya no está lo cotejó otra persona.
          if (despues === null) {
            const siguen = new Set(data.elementos.map((e) => e.pago_id));
            setTildados((t) => new Set([...t].filter((id) => siguen.has(id))));
          }
        } else if (response.status === ESTADO.sinPermiso && codigo === "sin_permiso") aInicio();
        else {
          setLectura(falla);
          setAvisoLectura(sinPisarLoHecho(mensajeDeError(response.status, codigo)));
        }
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else {
          setLectura(falla);
          setAvisoLectura(sinPisarLoHecho(SIN_CONEXION));
        }
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, despues, vuelta, aEntrar, aInicio]);

  /** Después de cotejar la lista cambió: se vuelve a leer desde el principio. */
  function releer() {
    setDespues(null);
    setVuelta((v) => v + 1);
  }

  function guardarTildados() {
    const pagos = [...tildados];
    void acto.hacer({
      quePide: pagos,
      nombre: "cotejar los pagos",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/pagos/cotejo", {
          headers: encabezadoCsrf(),
          params: { path: { consorcio_id: consorcio }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: { pagos },
        }),
      alTerminar: ({ cotejados }) => {
        setTildados(new Set());
        setAviso({ texto: cotejados === 0 ? "Esos pagos ya estaban cotejados." : `Listo: ${cantidad(cotejados, "quedó cotejado 1 pago", "pagos quedaron cotejados")}.`, ok: true });
        releer();
      },
      alFallar: (estado) => {
        if (estado === ESTADO.conflicto || estado === ESTADO.noEncontrado) releer();
      },
    });
  }

  function comparar(archivo: File, vista: Vista, columnas: Columnas | null): Promise<void> {
    return acto.hacer({
      quePide: [archivo.name, archivo.size, archivo.lastModified, columnas, moneda],
      nombre: "comparar el extracto",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/pagos/cotejo/extracto", {
          headers: { ...encabezadoCsrf(), "Content-Type": TIPO_CSV },
          params: {
            path: { consorcio_id: consorcio },
            header: { [ENCABEZADO_IDEMPOTENCIA]: clave },
            // Sin elegir, el servidor usa las columnas guardadas del edificio.
            query: columnas ?? (moneda === null ? {} : { moneda }),
          },
          // El archivo crudo, como en Documentos: `bodySerializer` lo deja pasar sin JSON.
          body: archivo as unknown as string,
          bodySerializer: (cuerpo) => cuerpo,
        }),
      alTerminar: (resultado) => {
        setExtracto({ tipo: "resultado", archivo, vista, resultado });
        releer();
      },
      alFallar: (_, codigo) => {
        setExtracto(codigo !== undefined && VUELVE_A_ELEGIR.has(codigo) ? { tipo: "columnas", archivo, vista } : { tipo: "sin_archivo" });
      },
    });
  }

  async function elegirArchivo(archivo: File) {
    setEntrada((v) => v + 1);
    if (tope !== null && archivo.size > tope) {
      setAviso({ texto: `El archivo pasa de ${tope / BYTES_POR_MB} MB, el máximo. Elegí uno más chico.`, ok: false });
      return;
    }
    setAviso(null);
    setExtracto({ tipo: "leyendo" });
    const falla = (texto: string) => {
      setExtracto({ tipo: "sin_archivo" });
      setAviso({ texto, ok: false });
    };
    try {
      const { data, error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/pagos/cotejo/columnas", {
        headers: { ...encabezadoCsrf(), "Content-Type": TIPO_CSV },
        params: { path: { consorcio_id: consorcio } },
        body: archivo as unknown as string,
        bodySerializer: (cuerpo) => cuerpo,
      });
      exigirSesion(response);
      if (!montado.current) return;
      if (data === undefined) falla(mensajeDeError(response.status, codigoDe(error)));
      else if (data.columnas_guardadas === null) setExtracto({ tipo: "columnas", archivo, vista: data });
      else {
        await comparar(archivo, data, null);
        // Sin respuesta, o esperando el código: lo que siga lo dice el aviso o el reintento.
        setExtracto((x) => (x.tipo === "leyendo" ? { tipo: "sin_archivo" } : x));
      }
    } catch (e: unknown) {
      if (!montado.current) return;
      if (e instanceof SinSesion) aEntrar();
      else falla(SIN_CONEXION);
    }
  }

  function alternar(id: string) {
    setTildados((t) => {
      const nuevos = new Set(t);
      if (!nuevos.delete(id) && (maximo === null || t.size < maximo)) nuevos.add(id);
      return nuevos;
    });
  }

  function deshacer(pago: string, motivo: string, alListo: () => void) {
    void acto.hacer({
      quePide: ["anular-cotejo", pago, motivo],
      nombre: "deshacer el cotejo",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/pagos/cotejo/{pago_id}/anulacion", {
          headers: encabezadoCsrf(),
          params: { path: { consorcio_id: consorcio, pago_id: pago }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: { motivo },
        }),
      alTerminar: () => {
        setAviso({ texto: "Listo: deshiciste el cotejo. Queda en la historia y el pago vuelve a lo que falta cotejar.", ok: true });
        alListo();
        releer();
      },
      alFallar: (estado) => {
        if (estado === ESTADO.conflicto || estado === ESTADO.noEncontrado) alListo();
      },
    });
  }

  const siguiente = lectura.tipo === "ok" ? lectura.siguiente : null;
  const conPantalla = pantalla.tipo === "lista" && consorcio !== "";

  return (
    <>
      <p>
        <Link to={RUTA_PAGOS}>Volver a Pagos</Link>
      </p>
      <h1>Cotejar con el banco</h1>
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={enviando} elegir={setConsorcio} />
      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">No tenés edificios en Consorcio en Mano.</p>}
      <ConsorcioSinServicio pantalla={pantalla} reintentar={reintentar} />
      <AvisoPantalla aviso={aviso} />
      <AvisoPantalla aviso={avisoLectura} />
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
      {/* Mientras se pide el código, lo tildado y el archivo siguen montados: el reintento no los pierde. */}
      {conPantalla && (
        <div className="cotejo" hidden={pendiente !== null}>
          <div>
            {extracto.tipo === "columnas" ? (
              <ElegirColumnas
                vista={extracto.vista}
                enviando={enviando}
                moneda={moneda}
                alComparar={(columnas) => void comparar(extracto.archivo, extracto.vista, columnas)}
                alVolver={() => setExtracto({ tipo: "sin_archivo" })}
              />
            ) : extracto.tipo === "resultado" ? (
              <ResultadoDelExtracto
                r={extracto.resultado}
                consorcio={consorcio}
                alElegirColumnas={() => setExtracto({ tipo: "columnas", archivo: extracto.archivo, vista: extracto.vista })}
                alOtroArchivo={() => setExtracto({ tipo: "sin_archivo" })}
              />
            ) : (
              <SubirExtracto
                key={entrada}
                tope={tope}
                ocupado={enviando || extracto.tipo === "leyendo"}
                moneda={moneda}
                alCambiarMoneda={setMoneda}
                alElegir={(archivo) => void elegirArchivo(archivo)}
              />
            )}
            {(extracto.tipo === "leyendo" || (enviando && extracto.tipo === "columnas")) && <p role="status">Leyendo el extracto…</p>}
          </div>
          <section className="tarjeta" aria-labelledby="cotejo-a-mano">
            <h2 id="cotejo-a-mano">O tildá a mano</h2>
            {lectura.tipo === "cargando" && <p role="status">Cargando los pagos…</p>}
            {lectura.tipo === "sin_servicio" && (
              <FallaDeLectura
                alReintentar={() => {
                  setAvisoLectura(sinLaFalla);
                  setLectura({ tipo: "cargando" });
                  setVuelta((v) => v + 1);
                }}
                avisar={setAvisoLectura}
              />
            )}
            {lectura.tipo === "ok" && lectura.elementos.length === 0 && <p>No falta cotejar ningún pago.</p>}
            {lectura.tipo === "ok" && lectura.elementos.length > 0 && (
              <ATildar
                elementos={lectura.elementos}
                tildados={tildados}
                maximo={maximo}
                enviando={enviando}
                alternar={alternar}
                alGuardar={guardarTildados}
              />
            )}
            {siguiente !== null && (
              <div className="formulario">
                <button
                  type="button"
                  className="secundario"
                  onClick={() => {
                    // La vuelta: si falló, `despues` no cambia y el mismo botón tiene que volver a pedir.
                    setDespues(siguiente);
                    setVuelta((v) => v + 1);
                  }}
                >
                  Ver más
                </button>
              </div>
            )}
          </section>
          <YaCotejados consorcio={consorcio} enviando={enviando} aEntrar={aEntrar} avisar={setAviso} deshacer={deshacer} />
        </div>
      )}
    </>
  );
}

type PropsSubir = { tope: number | null; ocupado: boolean; moneda: string | null; alCambiarMoneda: (moneda: string) => void; alElegir: (archivo: File) => void };

function SubirExtracto({ tope, ocupado, moneda, alCambiarMoneda, alElegir }: PropsSubir) {
  const id = useId();
  function soltar(evento: DragEvent) {
    evento.preventDefault();
    const archivo = evento.dataTransfer.files[0];
    if (archivo !== undefined && !ocupado) alElegir(archivo);
  }
  return (
    <section className="tarjeta" aria-labelledby={`${id}-titulo`}>
      <h2 id={`${id}-titulo`}>Subí el extracto</h2>
      {moneda !== null && (
        <div className="formulario">
          <label htmlFor={`${id}-moneda`}>Moneda del extracto</label>
          <select id={`${id}-moneda`} value={moneda} disabled={ocupado} onChange={(e) => alCambiarMoneda(e.target.value)}>
            {MONEDAS.map((m) => (
              <option key={m}>{m}</option>
            ))}
          </select>
          <p>El archivo del banco no la dice: un débito solo coincide con un pago en la misma moneda.</p>
        </div>
      )}
      <div className="zona-archivo formulario" onDragOver={(e) => e.preventDefault()} onDrop={soltar}>
        <p className="zona-archivo__soltar">Soltá acá el extracto</p>
        {/* El campo va antes que su rótulo: el foco del teclado se ve en el botón (estilos.css). */}
        <input
          id={`${id}-archivo`}
          className="solo-lector elegir-archivo"
          type="file"
          accept=".csv,text/csv"
          disabled={ocupado}
          onChange={(e) => {
            const elegido = e.target.files?.[0];
            if (elegido !== undefined) alElegir(elegido);
          }}
        />
        <label htmlFor={`${id}-archivo`} className="boton-archivo">
          Elegir el archivo
        </label>
        {tope !== null && <p className="zona-archivo__nota">CSV, hasta {tope / BYTES_POR_MB} MB</p>}
      </div>
      <details className="historial">
        <summary>Ver el ejemplo</summary>
        <p>La primera línea trae los nombres de las columnas y cada una de las demás, un movimiento:</p>
        <pre className="ejemplo-extracto">{EJEMPLO_EXTRACTO}</pre>
      </details>
    </section>
  );
}

type PropsColumnas = { vista: Vista; enviando: boolean; moneda: string | null; alComparar: (c: Columnas) => void; alVolver: () => void };

function ElegirColumnas({ vista, enviando, moneda: monedaSugerida, alComparar, alVolver }: PropsColumnas) {
  const id = useId();
  const { columnas, muestra, columnas_guardadas: guardadas, signo_sugerido: sugerido } = vista;
  const [fecha, setFecha] = useState(guardadas?.fecha ?? parecida(columnas, /fecha/i));
  const [monto, setMonto] = useState(guardadas?.importe ?? parecida(columnas, /importe|d[eé]bito|monto/i));
  const [numero, setNumero] = useState(guardadas?.numero_operacion ?? parecida(columnas, /operaci[oó]n|referencia|comprobante/i));
  const [signo, setSigno] = useState<Signo>(guardadas?.signo ?? sugerido);
  const [moneda, setMoneda] = useState(monedaSugerida ?? guardadas?.moneda ?? "");
  const ejemplo = (columna: string) => muestra[columnas.indexOf(columna)] ?? "";
  const primeraFila = [fecha, monto, numero].filter((c) => c !== "").map(ejemplo);

  function enviar(evento: FormEvent) {
    evento.preventDefault();
    alComparar({ fecha, importe: monto, numero_operacion: numero === SIN_NUMERO ? null : numero, signo, moneda });
  }

  const opciones = columnas.map((c) => (
    <option key={c} value={c}>
      {c}
    </option>
  ));
  return (
    <section className="tarjeta" aria-labelledby={`${id}-titulo`}>
      <h2 id={`${id}-titulo`}>¿Qué es cada columna?</h2>
      <p>Queda guardado para este edificio.</p>
      <form className="formulario" onSubmit={enviar}>
        <label htmlFor={`${id}-fecha`}>Fecha</label>
        <select id={`${id}-fecha`} required value={fecha} onChange={(e) => setFecha(e.target.value)}>
          <option value="">Elegí una columna</option>
          {opciones}
        </select>
        <label htmlFor={`${id}-importe`}>Importe</label>
        <select id={`${id}-importe`} required value={monto} onChange={(e) => setMonto(e.target.value)}>
          <option value="">Elegí una columna</option>
          {opciones}
        </select>
        <label htmlFor={`${id}-numero`}>Número de operación</label>
        <select id={`${id}-numero`} value={numero} onChange={(e) => setNumero(e.target.value)}>
          <option value={SIN_NUMERO}>No tiene: comparar por importe y fecha</option>
          {opciones}
        </select>
        <label htmlFor={`${id}-moneda`}>Moneda del extracto</label>
        <select id={`${id}-moneda`} required value={moneda} onChange={(e) => setMoneda(e.target.value)}>
          {MONEDAS.map((m) => (
            <option key={m}>{m}</option>
          ))}
        </select>
        <label htmlFor={`${id}-signo`}>Cómo figuran los pagos</label>
        <select id={`${id}-signo`} value={signo} onChange={(e) => setSigno(e.target.value as Signo)}>
          {SIGNOS.map((s) => (
            <option key={s.valor} value={s.valor}>
              {s.texto}
            </option>
          ))}
        </select>
        {primeraFila.length > 0 && <p>Primera fila: {primeraFila.join(" · ")}</p>}
        <div className="acciones">
          <button type="submit" disabled={enviando}>
            Comparar
          </button>
          <button type="button" className="secundario" disabled={enviando} onClick={alVolver}>
            Elegir otro archivo
          </button>
        </div>
      </form>
    </section>
  );
}

type PropsCotejados = {
  consorcio: string;
  enviando: boolean;
  aEntrar: () => void;
  avisar: (aviso: Aviso) => void;
  deshacer: (pago: string, motivo: string, alListo: () => void) => void;
};

/** Se lee cuando alguien lo pide: deshacer es la excepción, no la tarea de la pantalla. */
function YaCotejados({ consorcio, enviando, aEntrar, avisar, deshacer }: PropsCotejados) {
  const id = useId();
  const [elementos, setElementos] = useState<Cotejado[] | null>(null);
  const [siguiente, setSiguiente] = useState<string | null>(null);
  const [elegido, setElegido] = useState<string | null>(null);
  const [motivo, setMotivo] = useState("");

  function leer(despues: string | null) {
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/pagos/cotejo/cotejados", {
        params: { path: { consorcio_id: consorcio }, query: despues === null ? {} : { despues } },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data === undefined) {
          avisar({ texto: mensajeDeError(response.status, codigoDe(error)), ok: false });
          return;
        }
        setElementos((actuales) => (despues === null || actuales === null ? data.elementos : [...actuales, ...data.elementos]));
        setSiguiente(data.siguiente);
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : avisar({ texto: SIN_CONEXION, ok: false })));
  }

  function listo() {
    setElegido(null);
    setMotivo("");
    leer(null);
  }

  function enviar(evento: FormEvent, pago: string) {
    evento.preventDefault();
    deshacer(pago, motivo.trim(), listo);
  }

  return (
    <section className="tarjeta" aria-labelledby={`${id}-titulo`}>
      <h2 id={`${id}-titulo`}>Lo ya cotejado</h2>
      {elementos === null && (
        <div className="formulario">
          <button type="button" className="secundario" onClick={() => leer(null)}>
            Ver lo ya cotejado
          </button>
        </div>
      )}
      {elementos !== null && elementos.length === 0 && <p>Todavía no hay pagos cotejados.</p>}
      {elementos !== null && elementos.length > 0 && (
        <ul className="tildes-pago" aria-label="Lo ya cotejado">
          {elementos.map((e) => (
            <li key={e.pago_id}>
              <div className="tilde-pago">
                <span className="tilde-pago__datos">
                  <span className="fila-pago__arriba">
                    <strong>{e.proveedor}</strong>
                    <span className="fila-pago__importe">{importe(e.importe_centavos, e.moneda)}</span>
                  </span>
                  <span>{detalle(e.numero_operacion, e.fecha_operacion, "Operación")}</span>
                  {!e.puede_cotejar && <span>Lo deshace otra persona del Consejo: participaste de este pago.</span>}
                </span>
              </div>
              {e.puede_cotejar && elegido !== e.pago_id && (
                <div className="formulario">
                  <button type="button" className="secundario" disabled={enviando} onClick={() => setElegido(e.pago_id)}>
                    Deshacer el cotejo de {e.proveedor}
                  </button>
                </div>
              )}
              {elegido === e.pago_id && (
                <form className="formulario" onSubmit={(evento) => enviar(evento, e.pago_id)}>
                  <label htmlFor={`${id}-motivo`}>Por qué se deshace</label>
                  <textarea id={`${id}-motivo`} required maxLength={300} rows={3} value={motivo} disabled={enviando} onChange={(evento) => setMotivo(evento.target.value)} />
                  <p>No se borra: queda en la historia. Al subir de nuevo el extracto no se vuelve a tildar solo.</p>
                  <div className="acciones">
                    <button type="submit" disabled={enviando || motivo.trim() === ""}>
                      Deshacer el cotejo
                    </button>
                    <button type="button" className="secundario" disabled={enviando} onClick={() => setElegido(null)}>
                      Cancelar
                    </button>
                  </div>
                </form>
              )}
            </li>
          ))}
        </ul>
      )}
      {siguiente !== null && (
        <div className="formulario">
          <button type="button" className="secundario" onClick={() => leer(siguiente)}>
            Ver más cotejados
          </button>
        </div>
      )}
    </section>
  );
}

function Resumen({ r }: { r: Resultado }) {
  const noLeidas = r.filas_no_leidas.join(", ");
  return (
    <>
      <p className="aviso-ok" role="status">
        {r.cotejados === 0
          ? "Ningún pago nuevo quedó cotejado."
          : `${cantidad(r.cotejados, "1 pago coincide con el banco y quedó cotejado", "pagos coinciden con el banco y quedaron cotejados")}.`}
      </p>
      {r.ya_cotejados > 0 && <p>{cantidad(r.ya_cotejados, "1 ya estaba cotejado", "ya estaban cotejados")}.</p>}
      {r.esperan_conciliar > 0 && <p>{cantidad(r.esperan_conciliar, "1 coincide, pero falta conciliarlo", "coinciden, pero falta conciliarlos")}.</p>}
      {r.para_otra_persona > 0 && (
        <p>
          {cantidad(r.para_otra_persona, "1 coincide, pero lo tiene que cotejar", "coinciden, pero los tiene que cotejar")} otra persona del Consejo: participaste de ese pago.
        </p>
      )}
      {r.anulados > 0 && <p>{cantidad(r.anulados, "1 coincide con un cotejo que se deshizo: si corresponde, tildalo a mano", "coinciden con cotejos que se deshicieron: si corresponde, tildalos a mano")}.</p>}
      {r.lineas_no_leidas > 0 && (
        <p>
          {cantidad(r.lineas_no_leidas, "No pudimos leer 1 línea", "líneas no se pudieron leer")} del archivo (línea {noLeidas}).
        </p>
      )}
    </>
  );
}

function TituloConTotal({ texto, total }: { texto: string; total: number }) {
  return (
    <h3 className="titulo-grupo">
      {texto} <span className="cantidad">{total}</span>
    </h3>
  );
}

type PropsResultado = { r: Resultado; consorcio: string; alElegirColumnas: () => void; alOtroArchivo: () => void };

function ResultadoDelExtracto({ r, consorcio, alElegirColumnas, alOtroArchivo }: PropsResultado) {
  const id = useId();
  const nadaParaMirar = r.total_aca_no_banco === 0 && r.total_banco_no_aca === 0;
  return (
    <section className="tarjeta" aria-labelledby={`${id}-titulo`}>
      <h2 id={`${id}-titulo`}>
        Extracto del {fechaLegible(r.desde)} al {fechaLegible(r.hasta)}
      </h2>
      <Resumen r={r} />
      {nadaParaMirar && <p>No hay nada para mirar.</p>}
      {r.total_aca_no_banco > 0 && (
        <>
          <TituloConTotal texto="Están acá y no en el banco" total={r.total_aca_no_banco} />
          <ul className="filas-pago">
            {r.aca_no_banco.map((p) => (
              <li key={p.pago_id}>
                <Link to={rutaFicha(p.orden_id)} state={{ consorcio }} className="fila-pago fila-pago--con-borde">
                  <span className="fila-pago__arriba">
                    <strong>{p.proveedor}</strong>
                    <span className="fila-pago__importe">{importe(p.importe_centavos, p.moneda)}</span>
                  </span>
                  <span>{detalle(p.numero_operacion, p.fecha_operacion, "Operación")}</span>
                  <span className="fila-pago__verbo">Ver el pago</span>
                </Link>
              </li>
            ))}
          </ul>
          {r.total_aca_no_banco > r.aca_no_banco.length && <p>Se ven los primeros {r.aca_no_banco.length}.</p>}
        </>
      )}
      {r.total_banco_no_aca > 0 && (
        <>
          <TituloConTotal texto="Están en el banco y no acá" total={r.total_banco_no_aca} />
          <ul className="filas-pago">
            {r.banco_no_aca.map((m, i) => (
              <li key={`${m.fecha}-${m.importe_centavos}-${m.numero_operacion}-${i}`} className="fila-pago fila-pago--con-borde">
                <span className="fila-pago__arriba">
                  <strong>Débito sin registrar</strong>
                  <span className="fila-pago__importe">{importe(m.importe_centavos, m.moneda)}</span>
                </span>
                <span>{detalle(m.numero_operacion, m.fecha, "Referencia")}</span>
                <span>Avisale a la administración.</span>
              </li>
            ))}
          </ul>
          {r.total_banco_no_aca > r.banco_no_aca.length && <p>Se ven los primeros {r.banco_no_aca.length}.</p>}
        </>
      )}
      <div className="formulario">
        <div className="acciones">
          <button type="button" onClick={alOtroArchivo}>
            Subir otro extracto
          </button>
          <button type="button" className="secundario" onClick={alElegirColumnas}>
            Elegir otras columnas
          </button>
        </div>
      </div>
    </section>
  );
}

type PropsTildar = {
  elementos: Elemento[];
  tildados: ReadonlySet<string>;
  /** Cuántos entran en un pedido: más, la API los rechaza. */
  maximo: number | null;
  enviando: boolean;
  alternar: (id: string) => void;
  alGuardar: () => void;
};

function ATildar({ elementos, tildados, maximo, enviando, alternar, alGuardar }: PropsTildar) {
  const lleno = maximo !== null && tildados.size >= maximo;
  const hayQuienPuede = elementos.some((e) => e.puede_cotejar);
  function enviar(evento: FormEvent) {
    evento.preventDefault();
    alGuardar();
  }
  return (
    <form onSubmit={enviar}>
      <ul className="tildes-pago" aria-label="Lo que falta cotejar">
        {elementos.map((e) => {
          const fila = (
            <>
              <span className="fila-pago__arriba">
                <strong>{e.proveedor}</strong>
                <span className="fila-pago__importe">{importe(e.importe_centavos, e.moneda)}</span>
              </span>
              <span>{detalle(e.numero_operacion, e.fecha_operacion, "Operación")}</span>
            </>
          );
          return (
            <li key={e.pago_id}>
              {e.puede_cotejar ? (
                <label className="tilde-pago">
                  <input type="checkbox" checked={tildados.has(e.pago_id)} disabled={enviando || (lleno && !tildados.has(e.pago_id))} onChange={() => alternar(e.pago_id)} />
                  <span className="tilde-pago__datos">{fila}</span>
                </label>
              ) : (
                <div className="tilde-pago">
                  <span className="tilde-pago__datos">
                    {fila}
                    <span>Lo coteja otra persona del Consejo: participaste de este pago.</span>
                  </span>
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {lleno && <p role="status">Se guardan hasta {maximo} por vez: guardá estos y seguí con los demás.</p>}
      {hayQuienPuede && (
        <div className="formulario">
          <button type="submit" disabled={enviando || tildados.size === 0}>
            Guardar lo tildado
          </button>
        </div>
      )}
    </form>
  );
}
