import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router";

import { CSV } from "../archivos";
import { CabeceraInterna } from "../CabeceraInterna";
import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import {
  type Aviso,
  ENCABEZADO_IDEMPOTENCIA,
  ESTADO,
  fechaLegible,
  importe,
  mensajeDeActo,
  mensajeDeError,
  sinLaFalla,
  sinPisarLoHecho,
} from "../egresos/comun";
import { ElegirConsorcio } from "../egresos/ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "../egresos/EstadoPantalla";
import { PedirCodigo } from "../egresos/PedirCodigo";
import { exigirSesion, monedasDe, SinSesion, useConsorcio } from "../egresos/useConsorcio";
import { ErrorDeCampo, type ErroresDeCampo, faltaCompletar, hayErrores, marcaDe, SIN_ERRORES, sinElDe } from "../ErrorDeCampo";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { FichaDeRegistro, FilaDeRegistro, useFichas } from "../FichaDeRegistro";
import { Hoja } from "../Hoja";
import { REQUISITOS } from "../permisos";
import { SubirArchivo } from "../SubirArchivo";
import { type FinDeEnvio, VentanaDeFormulario } from "../VentanaDeFormulario";
import { CargandoPagos } from "./CargandoPagos";
import { FilaEnlace } from "./FilaEnlace";
import { MONEDAS } from "./FormularioOrden";
import { RUTA_PAGOS, RUTA_SIN_EXPLICAR, rutaFicha } from "./rutas";
import {
  COMPARAR,
  DESHACER_COTEJO,
  EJEMPLO_EXTRACTO,
  FALTA_ARCHIVO_EXTRACTO,
  FALTA_MOTIVO_DESHACER,
  NOMBRE_MONEDA,
  SUBIR_EXTRACTO,
  textoLosCotejaOtra,
  textoLosDeshaceOtra,
  textoMarcarCotejados,
  textoTopeTildes,
  TILDAR_PAGOS,
  VER_LO_YA_COTEJADO,
  VER_SIN_EXPLICAR,
} from "./textos";
import { type Acto, useActo } from "./useActo";

// `/pagos/cotejo`: cotejar con el banco, a mano o con el extracto (§10). Cotejar no cambia la orden
// ni mueve dinero. Quién coteja cada pago lo decide el servidor (`puede_cotejar`): el archivo se
// lee, se compara y se guarda allá, y acá solo se elige qué columna es cada dato.
//
// Informe de fricción del 17-11-2026 (tanda 6): la pantalla es la lista de lo que falta cotejar
// (COT-01). El extracto se sube en su ventana, con las columnas como segundo paso de la misma
// ventana y sin volver a pedir la moneda (COT-02, COT-03); el resultado se mira en una hoja que se
// cierra con «Listo» (COT-04). Lo que esta cuenta no puede cotejar ni deshacer va en una línea al
// pie, no en la lista (COT-05). Lo ya cotejado se pide aparte, en filas compactas, y «Deshacer el
// cotejo» está en la ficha de cada uno, con el motivo en su ventana (COT-06). Ningún botón apagado
// porque falta algo (C8P-11, C8P-15, C8P-16).

type Elemento = components["schemas"]["PagoPorCotejar"];
type Cotejado = components["schemas"]["PagoCotejado"];
type Vista = components["schemas"]["VistaExtracto"];
type Columnas = components["schemas"]["ColumnasExtracto"];
type Resultado = components["schemas"]["ResultadoCotejo"];
type Signo = components["schemas"]["SignoExtracto"];

type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; elementos: Elemento[]; siguiente: string | null };
/** Qué columna es cada dato, mientras se elige. */
type Valores = { fecha: string; importe: string; numero: string; signo: Signo };
/** La ventana de subir el extracto: primero el archivo y, si el servidor no reconoce las columnas, cuáles son. */
type Subida = { paso: "archivo" } | { paso: "columnas"; archivo: File; vista: Vista; valores: Valores };

const TIPO_CSV = "text/csv";
const SIN_NUMERO = "";
const LARGO_MOTIVO = 300;
/** Las respuestas que se resuelven eligiendo otras columnas. */
const VUELVE_A_ELEGIR = new Set(["columnas_no_encontradas", "columnas_sin_elegir", "extracto_sin_movimientos"]);
const SIGNOS: readonly { valor: Signo; texto: string }[] = [
  { valor: "con_signo", texto: "Con signo menos, en una columna de importes" },
  { valor: "sin_signo", texto: "Sin signo, en una columna solo de débitos" },
];
const FALTA_FECHA = "Falta elegir la columna de la fecha.";
const FALTA_IMPORTE = "Falta elegir la columna del importe.";

const cantidad = (n: number, una: string, varias: string) => (n === 1 ? una : `${n} ${varias}`);

const detalle = (numero: string | null, fecha: string, nombre: string) =>
  numero === null ? fechaLegible(fecha) : `${nombre} ${numero} · ${fechaLegible(fecha)}`;

/** La columna que parece ser ese dato, para no empezar de cero. */
const parecida = (columnas: string[], patron: RegExp) => columnas.find((c) => patron.test(c)) ?? "";

/** Lo guardado del edificio y, si no, lo que el servidor reconoció por el nombre o lo que se parece. */
function valoresDe({ columnas, columnas_guardadas: guardadas, columnas_reconocidas: reconocidas, signo_sugerido: sugerido }: Vista): Valores {
  const previas = guardadas ?? reconocidas;
  return {
    fecha: previas?.fecha ?? parecida(columnas, /fecha/i),
    importe: previas?.importe ?? parecida(columnas, /importe|d[eé]bito|monto/i),
    numero: previas?.numero_operacion ?? parecida(columnas, /operaci[oó]n|referencia|comprobante/i),
    signo: previas?.signo ?? sugerido,
  };
}

export function Cotejo() {
  const elegido = useConsorcio(REQUISITOS.cotejo);
  return <CotejoDelEdificio key={elegido.consorcio} {...elegido} />;
}

function CotejoDelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar }: ReturnType<typeof useConsorcio>) {
  const id = useId();
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [avisoLectura, setAvisoLectura] = useState<Aviso | null>(null);
  const [despues, setDespues] = useState<string | null>(null);
  const [vuelta, setVuelta] = useState(0);
  const [tope, setTope] = useState<number | null>(null);
  const [maximo, setMaximo] = useState<number | null>(null);
  /** La del extracto que se va a subir. El archivo no la dice; sin leer la lista, manda la guardada. */
  const [moneda, setMoneda] = useState<string | null>(null);
  const [tildados, setTildados] = useState<ReadonlySet<string>>(new Set());
  const [subida, setSubida] = useState<Subida | null>(null);
  const [archivo, setArchivo] = useState<File | null>(null);
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  /** Un rechazo que llega después de pedir el código, con la ventana todavía abierta. */
  const [tardio, setTardio] = useState<string | null>(null);
  const [resultado, setResultado] = useState<Resultado | null>(null);
  const [cotejos, setCotejos] = useState(0);
  const primerBoton = useRef<HTMLButtonElement | null>(null);
  const acto = useActo(aEntrar);
  const { enviando, aviso, setAviso, pendiente, setPendiente } = acto;
  const conPantalla = pantalla.tipo === "lista" && consorcio !== "";
  /** Como en la orden (P-14): la moneda se pregunta solo si el edificio opera en más de una. */
  const ofrecidas = conPantalla ? MONEDAS.filter((m) => monedasDe(pantalla, consorcio).includes(m)) : [];
  const conCampoMoneda = ofrecidas.length > 1;
  const monedaDelExtracto = conCampoMoneda ? (moneda ?? ofrecidas[0] ?? null) : (ofrecidas[0] ?? moneda);

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
          // La guardada solo sugiere: releer la lista no pisa lo que la persona ya eligió.
          setMoneda((elegida) => elegida ?? data.moneda_extracto);
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

  /**
   * Un acto hecho desde una ventana: su rechazo queda en la ventana y no arriba de la pantalla. Si
   * pide el código, la ventana sigue abierta debajo y el reintento termina por su cuenta (lo que
   * salga mal ahí se dice en la ventana, con `tardio`).
   */
  async function enVentana<T>(a: Acto<T>): Promise<FinDeEnvio> {
    let fin: FinDeEnvio = "seguir";
    let primera = true;
    const fallo = (texto: string) => {
      setAviso(null);
      if (primera) fin = { rechazo: texto };
      else setTardio(texto);
    };
    await acto.hacer({
      ...a,
      alTerminar: (data) => {
        fin = "cerrar";
        a.alTerminar(data);
      },
      alFallar: (estado, codigo, error) => {
        fallo(mensajeDeActo(estado, error));
        a.alFallar?.(estado, codigo, error);
      },
      alNoLlegar: () => {
        fallo(SIN_CONEXION);
        a.alNoLlegar?.();
      },
    });
    primera = false;
    return fin;
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
        setCotejos((c) => c + 1);
        setAviso({ texto: cotejados === 0 ? "Esos pagos ya estaban cotejados." : `Listo: ${cantidad(cotejados, "quedó cotejado 1 pago", "pagos quedaron cotejados")}.`, ok: true });
        releer();
      },
      alFallar: (estado) => {
        if (estado === ESTADO.conflicto || estado === ESTADO.noEncontrado) releer();
      },
    });
  }

  function abrirSubida() {
    setAviso(null);
    setArchivo(null);
    setErrores(SIN_ERRORES);
    setTardio(null);
    setSubida({ paso: "archivo" });
  }

  function cerrarSubida() {
    setSubida(null);
    setArchivo(null);
    setErrores(SIN_ERRORES);
    setTardio(null);
  }

  function comparar(elegido: File, vista: Vista, columnas: Columnas | null): Promise<FinDeEnvio> {
    return enVentana({
      quePide: [elegido.name, elegido.size, elegido.lastModified, columnas, monedaDelExtracto],
      nombre: "comparar el extracto",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/pagos/cotejo/extracto", {
          headers: { ...encabezadoCsrf(), "Content-Type": TIPO_CSV },
          params: {
            path: { consorcio_id: consorcio },
            header: { [ENCABEZADO_IDEMPOTENCIA]: clave },
            // Sin elegir, el servidor usa las columnas guardadas del edificio.
            query: columnas ?? (monedaDelExtracto === null ? {} : { moneda: monedaDelExtracto }),
          },
          // El archivo crudo, como en Documentos: `bodySerializer` lo deja pasar sin JSON.
          body: elegido as unknown as string,
          bodySerializer: (cuerpo) => cuerpo,
        }),
      alTerminar: (r) => {
        cerrarSubida();
        setResultado(r);
        setCotejos((c) => c + 1);
        releer();
      },
      alFallar: (_, codigo) => {
        if (codigo !== undefined && VUELVE_A_ELEGIR.has(codigo)) {
          setSubida((s) => (s?.paso === "columnas" ? s : { paso: "columnas", archivo: elegido, vista, valores: valoresDe(vista) }));
        }
      },
    });
  }

  /** El primer paso: el archivo (y la moneda). El tipo y el tamaño ya los controló `SubirArchivo`. */
  async function enviarArchivo(): Promise<FinDeEnvio> {
    if (archivo === null) {
      setErrores({ archivo: FALTA_ARCHIVO_EXTRACTO });
      return "seguir";
    }
    setTardio(null);
    try {
      const { data, error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/pagos/cotejo/columnas", {
        headers: { ...encabezadoCsrf(), "Content-Type": TIPO_CSV },
        params: { path: { consorcio_id: consorcio } },
        body: archivo as unknown as string,
        bodySerializer: (cuerpo) => cuerpo,
      });
      exigirSesion(response);
      if (data === undefined) return { rechazo: mensajeDeError(response.status, codigoDe(error)) };
      // Sin columnas guardadas ni reconocidas por su nombre, se pregunta (P-23); si no, se compara enseguida.
      if (data.columnas_guardadas === null && data.columnas_reconocidas === null) {
        setSubida({ paso: "columnas", archivo, vista: data, valores: valoresDe(data) });
        return "seguir";
      }
      const reconocidas = data.columnas_guardadas === null ? data.columnas_reconocidas : null;
      return await comparar(archivo, data, reconocidas === null ? null : { ...reconocidas, moneda: monedaDelExtracto ?? reconocidas.moneda });
    } catch (e: unknown) {
      if (!(e instanceof SinSesion)) return { rechazo: SIN_CONEXION };
      aEntrar();
      return "seguir";
    }
  }

  /** El segundo paso: qué columna es cada dato. La moneda es la que ya se eligió. */
  function enviarColumnas(paso: Extract<Subida, { paso: "columnas" }>): Promise<FinDeEnvio> | FinDeEnvio {
    const { fecha, importe: monto, numero, signo } = paso.valores;
    const faltan = faltaCompletar({ fecha: [fecha, FALTA_FECHA], importe: [monto, FALTA_IMPORTE] });
    if (hayErrores(faltan)) {
      setErrores(faltan);
      return "seguir";
    }
    setTardio(null);
    return comparar(paso.archivo, paso.vista, {
      fecha,
      importe: monto,
      numero_operacion: numero === SIN_NUMERO ? null : numero,
      signo,
      moneda: monedaDelExtracto ?? "",
    });
  }

  function cambiarValor(campo: keyof Valores, valor: string) {
    setSubida((s) => (s?.paso === "columnas" ? { ...s, valores: { ...s.valores, [campo]: valor } } : s));
    setErrores((e) => sinElDe(e, campo));
  }

  function deshacer(pago: string, motivo: string, alListo: () => void): Promise<FinDeEnvio> {
    return enVentana({
      quePide: ["anular-cotejo", pago, motivo],
      nombre: "deshacer el cotejo",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/pagos/cotejo/{pago_id}/anulacion", {
          headers: encabezadoCsrf(),
          params: { path: { consorcio_id: consorcio, pago_id: pago }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: { motivo },
        }),
      alTerminar: () => {
        setAviso({ texto: "Listo: el cotejo se deshizo y el pago vuelve a lo que falta cotejar.", ok: true });
        alListo();
        releer();
      },
      alFallar: (estado, _, error) => {
        // El pago ya cambió: la ficha se cierra y el motivo se dice arriba.
        if (estado === ESTADO.conflicto || estado === ESTADO.noEncontrado) {
          setAviso({ texto: mensajeDeActo(estado, error), ok: false });
          alListo();
        }
      },
    });
  }

  const siguiente = lectura.tipo === "ok" ? lectura.siguiente : null;
  const rechazoTardio = tardio !== null && (
    <p className="aviso-error" role="alert">
      {tardio}
    </p>
  );

  return (
    <>
      <CabeceraInterna titulo="Cotejar con el banco" volver={RUTA_PAGOS} destino="Pagos" />
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={enviando} elegir={setConsorcio} />
      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">Esta cuenta no tiene edificios en Consorcio en Mano.</p>}
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
      {/* Mientras se pide el código, lo tildado sigue montado: el reintento no lo pierde. */}
      {conPantalla && (
        <div className="cotejo" hidden={pendiente !== null}>
          <p className="salidas-pago salidas-pago--arriba">
            <button type="button" onClick={abrirSubida}>
              {SUBIR_EXTRACTO}
            </button>
          </p>
          <section aria-labelledby={`${id}-falta`}>
            <h2 id={`${id}-falta`} className="titulo-grupo">
              Lo que falta cotejar
            </h2>
            {lectura.tipo === "cargando" && <CargandoPagos />}
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
                setTildados={setTildados}
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
          {/* Después de cotejar, lo leído quedó viejo: vuelve a pedirse. */}
          <YaCotejados key={cotejos} consorcio={consorcio} aEntrar={aEntrar} avisar={setAviso} deshacer={deshacer} />
        </div>
      )}
      {/* Una sola ventana para los dos pasos: el rechazo que lleva a elegir las columnas queda a la vista. */}
      {subida !== null && (
        <VentanaDeFormulario
          titulo={subida.paso === "archivo" ? SUBIR_EXTRACTO : "¿Qué es cada columna?"}
          textoEnviar={subida.paso === "archivo" ? SUBIR_EXTRACTO : COMPARAR}
          alEnviar={() => (subida.paso === "archivo" ? enviarArchivo() : enviarColumnas(subida))}
          alCerrar={cerrarSubida}
        >
          {subida.paso === "columnas" && <CamposColumnas vista={subida.vista} valores={subida.valores} errores={errores} cambiar={cambiarValor} />}
          {subida.paso === "archivo" && conCampoMoneda && monedaDelExtracto !== null && (
            <>
              <label htmlFor={`${id}-moneda`}>Moneda del extracto</label>
              <select id={`${id}-moneda`} data-foco value={monedaDelExtracto} onChange={(e) => setMoneda(e.target.value)}>
                {ofrecidas.map((m) => (
                  <option key={m} value={m}>
                    {NOMBRE_MONEDA[m] ?? m}
                  </option>
                ))}
              </select>
            </>
          )}
          {subida.paso === "archivo" && (
            <>
              <SubirArchivo
                de="del extracto"
                tipos={[CSV]}
                tope={tope}
                reemplaza={archivo !== null}
                elegido={archivo?.name ?? null}
                refPrimero={
                  conCampoMoneda
                    ? undefined
                    : (b) => {
                        primerBoton.current = b;
                      }
                }
                alElegir={(elegido) => {
                  setArchivo(elegido);
                  setErrores((e) => sinElDe(e, "archivo"));
                }}
              />
              <ErrorDeCampo idCampo={`${id}-archivo`} mensaje={errores.archivo} />
              <details className="historial">
                <summary>Ver el ejemplo</summary>
                <pre className="ejemplo-extracto">{EJEMPLO_EXTRACTO}</pre>
              </details>
            </>
          )}
          {rechazoTardio}
        </VentanaDeFormulario>
      )}
      {resultado !== null && <ResultadoDelExtracto r={resultado} consorcio={consorcio} alCerrar={() => setResultado(null)} />}
    </>
  );
}

type PropsColumnas = {
  vista: Vista;
  valores: Valores;
  errores: ErroresDeCampo;
  cambiar: (campo: keyof Valores, valor: string) => void;
};

function CamposColumnas({ vista, valores, errores, cambiar }: PropsColumnas) {
  const id = useId();
  const { columnas, muestra } = vista;
  const ejemplo = (columna: string) => muestra[columnas.indexOf(columna)] ?? "";
  const primeraFila = [valores.fecha, valores.importe, valores.numero].filter((c) => c !== "").map(ejemplo);
  const opciones = columnas.map((c) => (
    <option key={c} value={c}>
      {c}
    </option>
  ));
  return (
    <>
      <p>Queda guardado para este edificio.</p>
      <label htmlFor={`${id}-fecha`}>Fecha</label>
      <select id={`${id}-fecha`} data-foco value={valores.fecha} {...marcaDe(`${id}-fecha`, errores.fecha)} onChange={(e) => cambiar("fecha", e.target.value)}>
        <option value="">Elegir una columna</option>
        {opciones}
      </select>
      <ErrorDeCampo idCampo={`${id}-fecha`} mensaje={errores.fecha} />
      <label htmlFor={`${id}-importe`}>Importe</label>
      <select id={`${id}-importe`} value={valores.importe} {...marcaDe(`${id}-importe`, errores.importe)} onChange={(e) => cambiar("importe", e.target.value)}>
        <option value="">Elegir una columna</option>
        {opciones}
      </select>
      <ErrorDeCampo idCampo={`${id}-importe`} mensaje={errores.importe} />
      <label htmlFor={`${id}-numero`}>Número de operación</label>
      <select id={`${id}-numero`} value={valores.numero} onChange={(e) => cambiar("numero", e.target.value)}>
        <option value={SIN_NUMERO}>No tiene: comparar por importe y fecha</option>
        {opciones}
      </select>
      <label htmlFor={`${id}-signo`}>Cómo figuran los pagos</label>
      <select id={`${id}-signo`} value={valores.signo} onChange={(e) => cambiar("signo", e.target.value)}>
        {SIGNOS.map((s) => (
          <option key={s.valor} value={s.valor}>
            {s.texto}
          </option>
        ))}
      </select>
      {primeraFila.length > 0 && <p>Primera fila: {primeraFila.join(" · ")}</p>}
    </>
  );
}

type PropsCotejados = {
  consorcio: string;
  aEntrar: () => void;
  avisar: (aviso: Aviso) => void;
  deshacer: (pago: string, motivo: string, alListo: () => void) => Promise<FinDeEnvio>;
};

/** Se lee cuando alguien lo pide: deshacer es la excepción, no la tarea de la pantalla. */
function YaCotejados({ consorcio, aEntrar, avisar, deshacer }: PropsCotejados) {
  const id = useId();
  const [elementos, setElementos] = useState<Cotejado[] | null>(null);
  const [siguiente, setSiguiente] = useState<string | null>(null);
  const fichas = useFichas<string>();
  const [deshaciendo, setDeshaciendo] = useState(false);
  const [motivo, setMotivo] = useState("");
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);

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

  function cerrarVentana() {
    setDeshaciendo(false);
    setMotivo("");
    setErrores(SIN_ERRORES);
  }

  /** Hecho (o el pago ya cambió): se cierran la ventana y la ficha, y la lista se vuelve a leer. */
  function listo() {
    cerrarVentana();
    fichas.cerrar(false);
    leer(null);
  }

  if (elementos === null) {
    return (
      <p className="salidas-pago">
        <button type="button" className="secundario" onClick={() => leer(null)}>
          {VER_LO_YA_COTEJADO}
        </button>
      </p>
    );
  }

  const propios = elementos.filter((e) => e.puede_cotejar);
  const ajenos = elementos.length - propios.length;
  const abierto = propios.find((e) => e.pago_id === fichas.abierta);

  return (
    <section aria-labelledby={`${id}-titulo`}>
      <h2 id={`${id}-titulo`} className="titulo-grupo">
        Lo ya cotejado
      </h2>
      {elementos.length === 0 && <p>Todavía no hay pagos cotejados.</p>}
      {propios.length > 0 && (
        <ul className="filas-registro" aria-label="Lo ya cotejado">
          {propios.map((e) => (
            <li key={e.pago_id}>
              <FilaDeRegistro
                titulo={e.proveedor}
                resumen={`${detalle(e.numero_operacion, e.fecha_operacion, "Operación")} · ${importe(e.importe_centavos, e.moneda)}`}
                alAbrir={() => fichas.abrir(e.pago_id)}
                refFila={fichas.refFila(e.pago_id)}
                abierta={fichas.abierta === e.pago_id}
              />
            </li>
          ))}
        </ul>
      )}
      {ajenos > 0 && <p className="comando-no-disponible">{textoLosDeshaceOtra(ajenos)}</p>}
      {siguiente !== null && (
        <div className="formulario">
          <button type="button" className="secundario" onClick={() => leer(siguiente)}>
            Ver más cotejados
          </button>
        </div>
      )}
      {abierto !== undefined && (
        <FichaDeRegistro
          titulo={abierto.proveedor}
          datos={[
            { etiqueta: "Importe", valor: importe(abierto.importe_centavos, abierto.moneda) },
            { etiqueta: "Fecha", valor: fechaLegible(abierto.fecha_operacion) },
            ...(abierto.numero_operacion === null ? [] : [{ etiqueta: "Número de operación", valor: abierto.numero_operacion }]),
          ]}
          alCerrar={() => fichas.cerrar()}
          ventana={
            deshaciendo && (
              <VentanaDeFormulario
                titulo={DESHACER_COTEJO}
                textoEnviar={DESHACER_COTEJO}
                peligro
                alCerrar={cerrarVentana}
                alEnviar={() => {
                  const faltan = faltaCompletar({ motivo: [motivo, FALTA_MOTIVO_DESHACER] });
                  if (hayErrores(faltan)) {
                    setErrores(faltan);
                    return "seguir";
                  }
                  return deshacer(abierto.pago_id, motivo.trim(), listo);
                }}
              >
                <label htmlFor={`${id}-motivo`}>Por qué se deshace</label>
                <textarea
                  id={`${id}-motivo`}
                  data-foco
                  maxLength={LARGO_MOTIVO}
                  rows={3}
                  value={motivo}
                  {...marcaDe(`${id}-motivo`, errores.motivo)}
                  onChange={(e) => {
                    setMotivo(e.target.value);
                    setErrores((x) => sinElDe(x, "motivo"));
                  }}
                />
                <ErrorDeCampo idCampo={`${id}-motivo`} mensaje={errores.motivo} />
              </VentanaDeFormulario>
            )
          }
        >
          <p className="ficha-registro__acciones">
            <Link to={rutaFicha(abierto.orden_id)} state={{ consorcio }} className="enlace-boton enlace-boton--borde">
              Ver la orden
            </Link>
            <button type="button" className="secundario peligro" onClick={() => setDeshaciendo(true)}>
              {DESHACER_COTEJO}
            </button>
          </p>
        </FichaDeRegistro>
      )}
    </section>
  );
}

function Resumen({ r }: { r: Resultado }) {
  return (
    <p className="aviso-ok" role="status">
      {r.cotejados === 0
        ? "Ningún pago nuevo quedó cotejado."
        : `${cantidad(r.cotejados, "1 pago coincide con el banco y quedó cotejado", "pagos coinciden con el banco y quedaron cotejados")}.`}
    </p>
  );
}

/** Lo que el extracto no resolvió por sí solo. Una línea sin leer puede ser un débito sin registrar. */
function pendientesDelExtracto(r: Resultado): string[] {
  const textos: string[] = [];
  if (r.lineas_no_leidas > 0) {
    const lineas = r.filas_no_leidas.join(", ");
    textos.push(`${cantidad(r.lineas_no_leidas, "1 línea no se pudo leer", "líneas no se pudieron leer")} del archivo (línea ${lineas}).`);
  }
  if (r.esperan_conciliar > 0) textos.push(`${cantidad(r.esperan_conciliar, "1 coincide, pero falta conciliarlo", "coinciden, pero falta conciliarlos")}.`);
  if (r.para_otra_persona > 0) {
    textos.push(`${cantidad(r.para_otra_persona, "1 coincide, pero lo tiene que cotejar", "coinciden, pero los tiene que cotejar")} otra persona del Consejo: esta cuenta participó de ese pago.`);
  }
  if (r.anulados > 0) textos.push(`${cantidad(r.anulados, "1 coincide con un cotejo que se deshizo: si corresponde, se marca a mano", "coinciden con cotejos que se deshicieron: si corresponde, se marcan a mano")}.`);
  return textos;
}

function TituloConTotal({ texto, total }: { texto: string; total: number }) {
  return (
    <h3 className="titulo-grupo">
      {texto} <span className="cantidad">{total}</span>
    </h3>
  );
}

type PropsResultado = { r: Resultado; consorcio: string; alCerrar: () => void };

/** Lo que dejó el extracto, en una hoja que se cierra con «Listo»: la pantalla vuelve a la lista. */
function ResultadoDelExtracto({ r, consorcio, alCerrar }: PropsResultado) {
  const pendientes = pendientesDelExtracto(r);
  return (
    <Hoja titulo={`Extracto del ${fechaLegible(r.desde)} al ${fechaLegible(r.hasta)}`} alEscapar={alCerrar}>
      <Resumen r={r} />
      {r.ya_cotejados > 0 && <p>{cantidad(r.ya_cotejados, "1 ya estaba cotejado", "ya estaban cotejados")}.</p>}
      {r.total_aca_no_banco > 0 && (
        <>
          <TituloConTotal texto="Están acá y no en el banco" total={r.total_aca_no_banco} />
          <ul className="filas-registro">
            {r.aca_no_banco.map((p) => (
              <li key={p.pago_id}>
                <FilaEnlace
                  a={rutaFicha(p.orden_id)}
                  estadoNavegacion={{ consorcio }}
                  titulo={p.proveedor}
                  resumen={`${detalle(p.numero_operacion, p.fecha_operacion, "Operación")} · ${importe(p.importe_centavos, p.moneda)}`}
                />
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
                  <strong>{detalle(m.numero_operacion, m.fecha, "Referencia")}</strong>
                  <span className="fila-pago__importe">{importe(m.importe_centavos, m.moneda)}</span>
                </span>
              </li>
            ))}
          </ul>
          {r.total_banco_no_aca > r.banco_no_aca.length && <p>Se ven los primeros {r.banco_no_aca.length}.</p>}
          <p>
            <Link to={RUTA_SIN_EXPLICAR} state={{ consorcio }}>
              {VER_SIN_EXPLICAR}
            </Link>
          </p>
        </>
      )}
      {pendientes.length > 0 && (
        <>
          <TituloConTotal texto="Falta revisar" total={pendientes.length} />
          <ul className="filas-pago">
            {pendientes.map((texto) => (
              <li key={texto} className="fila-pago fila-pago--con-borde">
                {texto}
              </li>
            ))}
          </ul>
        </>
      )}
      <div className="acciones">
        <button type="button" data-foco onClick={alCerrar}>
          Listo
        </button>
      </div>
    </Hoja>
  );
}

type PropsTildar = {
  elementos: Elemento[];
  tildados: ReadonlySet<string>;
  /** Cuántos entran en un pedido: más, la API los rechaza. */
  maximo: number | null;
  enviando: boolean;
  setTildados: (cambio: (t: ReadonlySet<string>) => ReadonlySet<string>) => void;
  alGuardar: () => void;
};

/** Solo los pagos que esta cuenta puede cotejar; los demás, contados en una línea al pie (COT-05). */
function ATildar({ elementos, tildados, maximo, enviando, setTildados, alGuardar }: PropsTildar) {
  /** La fila donde se tocó una casilla de más: ahí se dice el tope, y la casilla no se tilda (C8P-11). */
  const [topeEn, setTopeEn] = useState<string | null>(null);
  const propios = elementos.filter((e) => e.puede_cotejar);
  const ajenos = elementos.length - propios.length;

  function tocar(pago: string) {
    if (!tildados.has(pago) && maximo !== null && tildados.size >= maximo) {
      setTopeEn(pago);
      return;
    }
    setTopeEn(null);
    setTildados((t) => {
      const nuevos = new Set(t);
      if (!nuevos.delete(pago)) nuevos.add(pago);
      return nuevos;
    });
  }

  return (
    <form
      onSubmit={(evento) => {
        evento.preventDefault();
        alGuardar();
      }}
    >
      {propios.length > 0 && (
        <ul className="tildes-pago" aria-label="Lo que falta cotejar">
          {propios.map((e) => (
            <li key={e.pago_id}>
              <label className="tilde-pago">
                <input type="checkbox" checked={tildados.has(e.pago_id)} disabled={enviando} onChange={() => tocar(e.pago_id)} />
                <span className="tilde-pago__datos">
                  <span className="fila-pago__arriba">
                    <strong>{e.proveedor}</strong>
                    <span className="fila-pago__importe">{importe(e.importe_centavos, e.moneda)}</span>
                  </span>
                  <span>{detalle(e.numero_operacion, e.fecha_operacion, "Operación")}</span>
                </span>
              </label>
              {topeEn === e.pago_id && maximo !== null && (
                <p role="status" className="comando-no-disponible">
                  {textoTopeTildes(maximo)}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
      {ajenos > 0 && <p className="comando-no-disponible">{textoLosCotejaOtra(ajenos)}</p>}
      {/* Sin nada tildado no hay botón: se dice qué falta (C8P-16). */}
      {propios.length > 0 && (
        <div className="formulario">
          {tildados.size === 0 ? (
            <p className="comando-no-disponible">{TILDAR_PAGOS}</p>
          ) : (
            <button className="secundario" type="submit" disabled={enviando}>
              {textoMarcarCotejados(tildados.size)}
            </button>
          )}
        </div>
      )}
    </form>
  );
}
