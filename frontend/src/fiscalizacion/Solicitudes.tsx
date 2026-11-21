import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router";

import { CabeceraInterna } from "../CabeceraInterna";
import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { hoyDelDispositivo, TIPOS_DE_DOCUMENTO, tituloDeArchivo, TOPE_BYTES } from "../archivos";
import { encabezadoCsrf } from "../csrf";
import { mensajeDeError as mensajeDeCarga, pedirDescarga } from "../Documentos";
import {
  type Aviso,
  cambioLoVisto,
  ENCABEZADO_IDEMPOTENCIA,
  ESTADO,
  fechaLegible,
  LARGO_TITULO,
  mensajeDeActo,
  mensajeDeError,
  momento,
  renovarClave,
  sinLaFalla,
  sinPisarLoHecho,
} from "../egresos/comun";
import { ElegirConsorcio } from "../egresos/ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "../egresos/EstadoPantalla";
import { exigirSesion, SinSesion, useConsorcio } from "../egresos/useConsorcio";
import { RUTA_INICIO } from "../enlace";
import { ErrorDeCampo, type ErroresDeCampo, faltaCompletar, hayErrores, marcaDe, SIN_ERRORES, sinElDe } from "../ErrorDeCampo";
import { type DatoDeFicha, FichaDeRegistro, FilaDeRegistro, useFichas } from "../FichaDeRegistro";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { ComandoNoDisponible } from "../ComandoNoDisponible";
import { RUTA_MANDATOS } from "../Mandatos";
import { RUTA_PANEL, TITULO_PANEL } from "../panel/Panel";
import { REQUISITOS } from "../permisos";
import { type Resultado, ResultadoDelActo } from "../ResultadoDelActo";
import { SubirArchivo } from "../SubirArchivo";
import { type FinDeEnvio, VentanaDeFormulario } from "../VentanaDeFormulario";

// Página /solicitudes: la solicitud formal de documentación del Consejo a la administración (RF49,
// CU12, PA36; app 0108; diseño `doc/diseno/RF49_Solicitud_Formal.md` del repositorio CEM). El Consejo
// la prepara, dos personas distintas la firman y la segunda firma la emite dirigida al administrador
// vigente. Si el administrador tiene cuenta, la recibe en la aplicación y responde acá; si no, el
// Consejo registra el envío por el medio externo, con el comprobante, y después la respuesta que
// llegó. El plazo corre desde el envío. Tono informativo (RN33): la solicitud pregunta, no acusa.
// Los botones de cada solicitud son los que la API dice que la persona puede usar (`acciones`).
//
// Informe de fricción del 17-11-2026, tanda 8: cada solicitud es una fila (SOL-03) que abre su ficha,
// sola, con sus datos y sus acciones; preparar, retirar, registrar el envío y responder van en su
// ventana (SOL-02, SOL-05), con el botón siempre activo y el campo que falta marcado (C8P-24 a
// C8P-27). Las respondidas y las retiradas quedan plegadas (SOL-06). El resultado sale donde se actuó
// (SOL-11). Lo legal de las dos firmas se dice solo a quien prepara, al preparar (SOL-01).

type Solicitud = components["schemas"]["SolicitudFormal"];
type Lista = components["schemas"]["ListaSolicitudesFormales"];
type Estado = components["schemas"]["EstadoSolicitudFormal"];
type Objeto = components["schemas"]["ObjetoSolicitud"];
type Medio = components["schemas"]["MedioEnvio"];

export const RUTA_SOLICITUDES = "/solicitudes";
export const TITULO_SOLICITUDES = "Solicitudes formales";
export const PREPARAR = "Preparar una solicitud";
export const FIRMAR = "Firmar";
/** C8P-08: la firma emitiría y el consorcio no tiene administrador vigente (A14). */
export const FALTA_ADMINISTRADOR = "Falta un administrador vigente para emitirla: queda en borrador, con una firma, hasta que lo tenga. Se registra en Mandatos.";
export const IR_A_MANDATOS = "Ir a Mandatos";
/** Decisión 15 del 18-11 (FL-SOL-05): al preparar sin administrador vigente se avisa y el borrador se guarda igual. */
export const SIN_ADMINISTRADOR_AL_PREPARAR =
  "Falta un administrador vigente para emitirla: el borrador se puede guardar y firmar una vez; la segunda firma, que la emite, espera a que lo haya. Se registra en Mandatos.";
/** FL-SOL-01: con una firma puesta, la siguiente emite y envía, y ya no se puede retirar. */
export const FIRMAR_Y_EMITIR = "Firmar y emitir";
export const EMITE_AL_FIRMAR = "Con esta firma se emite y se le envía a la administración. Ya no se puede retirar.";
export const RETIRAR = "Retirar el borrador";
export const REGISTRAR_ENVIO = "Registrar el envío";
/** Pregunta 17 (FL-SOL-04, `0112`): el antivirus rechazó el comprobante del envío ya registrado. */
export const SUBIR_OTRO_COMPROBANTE = "Subir otro comprobante";
export const RESPONDER = "Responder";
export const REGISTRAR_RESPUESTA = "Registrar la respuesta recibida";
export const SIN_SOLICITUDES = "Todavía no hay solicitudes formales en este edificio.";
/** Lo legal, para quien prepara: dentro de la ventana de preparar (SOL-01). */
export const DOS_FIRMAS =
  "Pide documentación o una aclaración con plazo y constancia (art. 2064 inc. b del Código Civil y Comercial). Hacen falta dos firmas de personas distintas; la segunda la emite. Preparar no cuenta como firma.";
/** Los largos de `cem_contracts.fiscalizacion` (la API vuelve a controlar). */
export const LARGO_REFERENCIA = 200;
export const LARGO_DOCUMENTACION = 2000;
export const LARGO_RESPUESTA = 4000;
export const LARGO_MOTIVO = 500;
export const LARGO_DETALLE = 300;

/** El orden de la lista de trabajo: lo que espera algo primero. */
export const ORDEN_DE_ESTADOS: readonly Estado[] = ["borrador", "envio_pendiente", "vencida", "enviada", "respondida", "retirada"];
/** Lo que ya no espera nada: plegado, fuera de la lista de trabajo (SOL-06). */
const TERMINADAS: ReadonlySet<Estado> = new Set(["respondida", "retirada"]);

export const TEXTO_ESTADO: Record<Estado, string> = {
  borrador: "Borrador",
  envio_pendiente: "Emitida, con el envío pendiente",
  enviada: "Enviada, esperando respuesta",
  vencida: "Vencida, sin respuesta",
  respondida: "Respondida",
  retirada: "Retirada",
};

export const TEXTO_OBJETO: Record<Objeto, string> = {
  periodo: "Período",
  orden_pago: "Orden de pago",
  contrato: "Contrato",
  movimiento: "Movimiento",
  otro: "Otro",
};

export const TEXTO_MEDIO: Record<Medio, string> = {
  aplicacion: "En la aplicación",
  carta_documento: "Carta documento",
  correo_electronico: "Correo electrónico",
  entrega_en_mano: "Entrega en mano",
  otro: "Otro medio",
};

const MEDIOS_EXTERNOS: readonly Medio[] = ["carta_documento", "correo_electronico", "entrega_en_mano", "otro"];

/** Una línea que resume en qué está cada solicitud: la situación de su ficha. */
export function textoSituacion(s: Solicitud): string {
  const base = TEXTO_ESTADO[s.estado];
  if (s.estado === "borrador") return `${base}: ${s.firmas.length} de 2 firmas.`;
  if (s.estado === "envio_pendiente") return `${base}. No vence mientras no se registre el envío.`;
  if (s.estado === "enviada" && s.envio) return `${base}. Vence el ${fechaLegible(s.envio.vence_el)}.`;
  if (s.estado === "vencida" && s.envio) return `${base}. Venció el ${fechaLegible(s.envio.vence_el)}.`;
  if (s.estado === "respondida" && s.respuesta)
    return `${base} el ${fechaLegible(s.respuesta.respondida_el)}${s.respuesta.fuera_de_plazo ? ", fuera de plazo" : ""}.`;
  return `${base}.`;
}

/** El resumen de la fila de la lista de trabajo: solo lo que el título de su sección no dice (SOL-04). */
export function resumenDeTrabajo(s: Solicitud): string | undefined {
  if (s.estado === "borrador") return `${s.firmas.length} de 2 firmas`;
  if (s.estado === "envio_pendiente") return "No vence mientras no se registre el envío";
  if (s.estado === "enviada" && s.envio) return `Vence el ${fechaLegible(s.envio.vence_el)}`;
  if (s.estado === "vencida" && s.envio) return `Venció el ${fechaLegible(s.envio.vence_el)}`;
  return undefined;
}

export const tituloDe = (s: Solicitud) => `N.º ${s.numero} · ${TEXTO_OBJETO[s.objeto]}: ${s.referencia}`;

/** La descarga de un archivo de Documentos del edificio, como en `Documentos.tsx`: el servidor
 * responde con el 302 a la dirección firmada y decide si la persona puede bajarlo. */
const descarga = (consorcio: string, documento: string, version: string) =>
  `/api/v1/consorcios/${consorcio}/documentos/${documento}/versiones/${version}/descargar`;

/** FL-SOL-04 (tanda 9, app 0110): el archivo en revisión o rechazado no se ofrece para bajar. */
export const ARCHIVO_EN_REVISION = "El antivirus todavía lo está revisando: se puede descargar cuando termine.";
export const ARCHIVO_RECHAZADO = "El antivirus rechazó el archivo: no se puede descargar.";

/** Baja el archivo preguntando antes (FL-SOL-04): la falla queda en la ficha. */
type AlBajar = (documento: string, version: string) => void;

type Adjunto = Pick<NonNullable<Solicitud["envio"]>, "documento_id" | "version_documento_id" | "estado_archivo" | "descargable">;

/** El enlace al archivo solo si quien mira lo puede bajar (`descargable`); si no, por qué no, salvo
 * que sea solo por no leer Documentos: entonces no se dice nada. */
function enlaceDelAdjunto(consorcio: string, adjunto: Adjunto, texto: string, alBajar: AlBajar) {
  const { documento_id: documento, version_documento_id: version } = adjunto;
  if (!documento || !version) return null;
  // FL-SOL-04 (tanda 10): se pregunta antes, como en Documentos; si falla, se dice en la ficha.
  if (adjunto.descargable)
    return (
      <>
        {" "}
        <a
          className="enlace-suelto"
          href={descarga(consorcio, documento, version)}
          onClick={(evento) => {
            evento.preventDefault();
            alBajar(documento, version);
          }}
        >
          {texto}
        </a>
      </>
    );
  if (adjunto.estado_archivo === "en_cuarentena") return <span className="ayuda"> {ARCHIVO_EN_REVISION}</span>;
  if (adjunto.estado_archivo === "rechazado") return <span className="ayuda"> {ARCHIVO_RECHAZADO}</span>;
  return null;
}

type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; lista: Lista };

/** Lo que pone el Resumen de pagos en `state.desde` al abrir las solicitudes: el círculo vuelve ahí. */
export const DESDE_EL_PANEL = "panel";

/** Un borrador ya redactado que otra pantalla propone (el recálculo de la liquidación importada,
 * H5-4): viaja en `state.propuesta` y abre «Preparar una solicitud» con sus datos, para revisarlos.
 * Vale solo en su edificio. */
export type Propuesta = { consorcio: string; objeto: Objeto; referencia: string; documentacion: string };

function propuestaDe(estado: unknown): Propuesta | null {
  if (typeof estado !== "object" || estado === null || !("propuesta" in estado)) return null;
  const p = estado.propuesta as Partial<Propuesta> | null;
  if (p === null || typeof p !== "object") return null;
  const { consorcio, objeto, referencia, documentacion } = p;
  if (typeof consorcio !== "string" || typeof referencia !== "string" || typeof documentacion !== "string") return null;
  if (objeto === undefined || !(objeto in TEXTO_OBJETO)) return null;
  return { consorcio, objeto, referencia, documentacion };
}

export function Solicitudes() {
  const elegido = useConsorcio(REQUISITOS.solicitudes);
  // Se lee una vez, al llegar: `AvisoLlegada` borra el `state` apenas lo ve. Desde el Resumen de
  // pagos, volver vuelve al Resumen (FL-SOL-07).
  const lugar = useLocation();
  const estado: unknown = lugar.state;
  const desdePanel = useRef(typeof estado === "object" && estado !== null && "desde" in estado && estado.desde === DESDE_EL_PANEL);
  // La propuesta también se lee una vez y se saca del historial: volver a esta dirección no la repite.
  const propuesta = useRef(propuestaDe(estado));
  const navegar = useNavigate();
  useEffect(() => {
    if (propuestaDe(lugar.state) === null) return;
    const { pathname, search, hash } = lugar;
    const resto = Object.fromEntries(Object.entries(lugar.state as Record<string, unknown>).filter(([k]) => k !== "propuesta"));
    navegar({ pathname, search, hash }, { replace: true, state: Object.keys(resto).length > 0 ? resto : null });
  }, [lugar, navegar]);
  const deEsteEdificio = propuesta.current !== null && propuesta.current.consorcio === elegido.consorcio ? propuesta.current : null;
  return <DelEdificio key={elegido.consorcio} {...elegido} desdePanel={desdePanel.current} propuesta={deEsteEdificio} />;
}

function DelEdificio({
  pantalla,
  consorcio,
  setConsorcio,
  aEntrar,
  aInicio,
  reintentar,
  desdePanel,
  propuesta,
}: ReturnType<typeof useConsorcio> & { desdePanel: boolean; propuesta: Propuesta | null }) {
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  /** Por qué no llegó la lista. */
  const [aviso, setAviso] = useState<Aviso | null>(null);
  /** El resultado del último acto: en la lista, o en la ficha de la solicitud recién preparada. */
  const [resultado, setResultado] = useState<{ es: Resultado; enFicha: boolean } | null>(null);
  const [preparando, setPreparando] = useState(propuesta !== null);
  /** Lo que propone otra pantalla, solo para la primera vez que se abre «Preparar». */
  const [inicial, setInicial] = useState(propuesta);
  const fichas = useFichas<string>();

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/solicitudes-formales", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        const codigo = codigoDe(error);
        if (data !== undefined) setLectura({ tipo: "ok", lista: data });
        else if (response.status === ESTADO.sinPermiso && codigo === "sin_permiso") aInicio();
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

  function releer() {
    setVuelta((v) => v + 1);
  }

  function conLaNueva(nueva: Solicitud) {
    setLectura((l) =>
      l.tipo === "ok"
        ? {
            tipo: "ok",
            lista: {
              ...l.lista,
              elementos: l.lista.elementos.some((s) => s.id === nueva.id)
                ? l.lista.elementos.map((s) => (s.id === nueva.id ? nueva : s))
                : [nueva, ...l.lista.elementos],
            },
          }
        : l,
    );
  }

  /** Terminó un acto en la ficha: se cierra y el resultado queda en la lista, donde ahora está. Si
   * el paso siguiente es de quien actuó (registrar el envío de la recién emitida), la ficha queda
   * abierta con el resultado adentro y el texto emitido a la vista (FL-SOL-02). */
  function alTerminar(nueva: Solicitud, texto: string) {
    conLaNueva(nueva);
    const sigueEnLaFicha = nueva.acciones.includes("registrar_envio");
    if (!sigueEnLaFicha) fichas.cerrar(false);
    setResultado({ es: { texto, ok: true }, enFicha: sigueEnLaFicha });
  }

  /** Lo visto ya no es lo vigente (otra persona firmó, retiró o respondió): se dice y se relee. */
  function alCambiarLoVisto(texto: string) {
    fichas.cerrar(false);
    setResultado({ es: { texto, ok: false }, enFicha: false });
    releer();
  }

  const lista = lectura.tipo === "ok" ? lectura.lista : null;
  const abierta = lista?.elementos.find((s) => s.id === fichas.abierta);
  const resultadoAca = (enFicha: boolean) => (
    <ResultadoDelActo resultado={resultado !== null && resultado.enFicha === enFicha ? resultado.es : null} alOtroActo={() => setResultado(null)} />
  );
  const fila = (s: Solicitud, resumen: string | undefined) => (
    <li key={s.id}>
      <FilaDeRegistro titulo={tituloDe(s)} resumen={resumen} alAbrir={() => fichas.abrir(s.id)} refFila={fichas.refFila(s.id)}
        abierta={fichas.abierta === s.id} />
    </li>
  );
  const terminadas = lista?.elementos.filter((s) => TERMINADAS.has(s.estado)) ?? [];

  return (
    <>
      <CabeceraInterna
        titulo={TITULO_SOLICITUDES}
        volver={desdePanel ? RUTA_PANEL : RUTA_INICIO}
        destino={desdePanel ? TITULO_PANEL : "Inicio"}
      />
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={false} elegir={setConsorcio} />
      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">Esta cuenta no tiene edificios en Consorcio en Mano.</p>}
      <ConsorcioSinServicio pantalla={pantalla} reintentar={reintentar} />
      <AvisoPantalla aviso={aviso} />

      {pantalla.tipo === "lista" && (
        <>
          {lectura.tipo === "cargando" && <p role="status">Cargando las solicitudes…</p>}
          {lectura.tipo === "sin_servicio" && (
            <FallaDeLectura
              alReintentar={() => {
                setAviso(sinLaFalla);
                setLectura({ tipo: "cargando" });
                releer();
              }}
              avisar={setAviso}
            />
          )}
          {lista !== null && (
            <>
              {lista.puede_preparar && (
                <div className="acciones">
                  <button type="button" className="secundario" onClick={() => setPreparando(true)}>
                    {PREPARAR}
                  </button>
                </div>
              )}
              {resultadoAca(false)}
              {lista.elementos.length === 0 ? (
                <p className="tarjeta">{SIN_SOLICITUDES}</p>
              ) : (
                <>
                  {ORDEN_DE_ESTADOS.filter((e) => !TERMINADAS.has(e)).map((estado) => {
                    const del = lista.elementos.filter((s) => s.estado === estado);
                    if (del.length === 0) return null;
                    return (
                      <section key={estado} aria-label={TEXTO_ESTADO[estado]}>
                        <h2>{TEXTO_ESTADO[estado]}</h2>
                        <ul className="filas-registro">{del.map((s) => fila(s, resumenDeTrabajo(s)))}</ul>
                      </section>
                    );
                  })}
                  {terminadas.length > 0 && (
                    <details>
                      <summary>Ver respondidas y retiradas ({terminadas.length})</summary>
                      <ul className="filas-registro" aria-label="Respondidas y retiradas">
                        {terminadas.map((s) => fila(s, textoSituacion(s)))}
                      </ul>
                    </details>
                  )}
                </>
              )}
              {preparando && lista.puede_preparar && (
                <VentanaPreparar
                  consorcio={consorcio}
                  lista={lista}
                  inicial={inicial}
                  aEntrar={aEntrar}
                  alCerrar={() => {
                    setPreparando(false);
                    setInicial(null);
                  }}
                  alPreparar={(nueva) => {
                    setInicial(null);
                    // Se abre la ficha del borrador nuevo, que es donde se firma (SOL-02).
                    conLaNueva(nueva);
                    fichas.abrir(nueva.id);
                    setResultado({
                      es: { texto: `Quedó preparada la solicitud N.º ${nueva.numero}. Falta que la firmen dos personas.`, ok: true },
                      enFicha: true,
                    });
                  }}
                />
              )}
              {abierta !== undefined && (
                <FichaSolicitud key={abierta.id} consorcio={consorcio} s={abierta} aEntrar={aEntrar} resultado={resultadoAca(true)}
                  alCerrar={() => fichas.cerrar()} alTerminar={alTerminar} alCambiarLoVisto={alCambiarLoVisto} />
              )}
            </>
          )}
        </>
      )}
    </>
  );
}

/** Cómo terminó un pedido de un acto sobre una solicitud. */
type FinDelActo = { tipo: "ok"; s: Solicitud } | { tipo: "falla"; texto: string } | { tipo: "cambio"; texto: string } | { tipo: "sin_sesion" };

/** Un acto sobre la solicitud: manda, traduce la respuesta y conserva la clave mientras haya que
 * reintentar lo mismo. */
function useActo(aEntrar: () => void) {
  const clave = useRef<string | null>(null);
  return async function hacer(pedido: (clave: string) => Promise<{ data?: Solicitud; error?: unknown; response: Response }>): Promise<FinDelActo> {
    clave.current ??= crypto.randomUUID();
    try {
      const { data, error, response } = await pedido(clave.current);
      exigirSesion(response);
      if (data === undefined) {
        const codigo = codigoDe(error);
        if (cambioLoVisto(response.status, codigo) && codigo !== "sin_administrador_vigente") {
          clave.current = null;
          return { tipo: "cambio", texto: mensajeDeActo(response.status, error) };
        }
        if (renovarClave(response.status, codigo)) clave.current = null;
        return { tipo: "falla", texto: mensajeDeActo(response.status, error) };
      }
      clave.current = null;
      return { tipo: "ok", s: data };
    } catch (e: unknown) {
      if (!(e instanceof SinSesion)) return { tipo: "falla", texto: SIN_CONEXION };
      aEntrar();
      return { tipo: "sin_sesion" };
    }
  };
}

type PropsPreparar = {
  consorcio: string;
  lista: Lista;
  inicial: Propuesta | null;
  aEntrar: () => void;
  alCerrar: () => void;
  alPreparar: (s: Solicitud) => void;
};

function VentanaPreparar({ consorcio, lista, inicial, aEntrar, alCerrar, alPreparar }: PropsPreparar) {
  const id = useId();
  const [objeto, setObjeto] = useState<Objeto>(inicial?.objeto ?? "periodo");
  const [referencia, setReferencia] = useState(inicial?.referencia ?? "");
  const [documentacion, setDocumentacion] = useState(inicial?.documentacion ?? "");
  const [plazo, setPlazo] = useState(String(lista.plazo_por_defecto_dias));
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  const hacer = useActo(aEntrar);

  async function guardar(): Promise<FinDeEnvio> {
    const faltan: Record<string, string> = {
      ...faltaCompletar({
        referencia: [referencia, "Falta decir cuál."],
        documentacion: [documentacion, "Falta escribir la documentación que se pide."],
      }),
    };
    const dias = Number(plazo);
    if (!Number.isInteger(dias) || dias < lista.plazo_minimo_dias || dias > lista.plazo_maximo_dias)
      faltan.plazo = `El plazo va de ${lista.plazo_minimo_dias} a ${lista.plazo_maximo_dias} días.`;
    if (hayErrores(faltan)) {
      setErrores(faltan);
      return "seguir";
    }
    const r = await hacer((clave) =>
      api.POST("/api/v1/consorcios/{consorcio_id}/solicitudes-formales", {
        headers: encabezadoCsrf(),
        params: { path: { consorcio_id: consorcio }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
        body: { objeto, referencia: referencia.trim(), documentacion: documentacion.trim(), plazo_dias: dias },
      }),
    );
    if (r.tipo === "ok") {
      alPreparar(r.s);
      return "cerrar";
    }
    return r.tipo === "sin_sesion" ? "seguir" : { rechazo: r.texto };
  }

  const campo = (nombre: string) => ({ id: `${id}-${nombre}`, ...marcaDe(`${id}-${nombre}`, errores[nombre]) });
  const escribir = (nombre: string, poner: (v: string) => void) => (v: string) => {
    poner(v);
    setErrores((e) => sinElDe(e, nombre));
  };

  return (
    <VentanaDeFormulario titulo={PREPARAR} textoEnviar="Guardar el borrador" alEnviar={guardar} alCerrar={alCerrar} ancha>
      {/* Decisión 15: sin administrador vigente se avisa y se deja guardar. FL-SOL-05 (tanda 9,
          app 0110): la lista dice si lo hay. */}
      {!lista.hay_administrador_vigente && (
        <ComandoNoDisponible motivo={SIN_ADMINISTRADOR_AL_PREPARAR} resolver={{ texto: IR_A_MANDATOS, a: RUTA_MANDATOS }} />
      )}
      <p>{DOS_FIRMAS}</p>
      <label htmlFor={`${id}-objeto`}>Sobre qué</label>
      <select id={`${id}-objeto`} data-foco value={objeto} onChange={(ev) => setObjeto(ev.target.value as Objeto)}>
        {(Object.keys(TEXTO_OBJETO) as Objeto[]).map((o) => (
          <option key={o} value={o}>
            {TEXTO_OBJETO[o]}
          </option>
        ))}
      </select>
      <label htmlFor={`${id}-referencia`}>Cuál (período, número de orden, contrato o movimiento)</label>
      <input {...campo("referencia")} value={referencia} maxLength={LARGO_REFERENCIA}
        onChange={(ev) => escribir("referencia", setReferencia)(ev.target.value)} />
      <ErrorDeCampo idCampo={`${id}-referencia`} mensaje={errores.referencia} />
      <label htmlFor={`${id}-documentacion`}>Documentación o aclaración que se pide</label>
      <textarea {...campo("documentacion")} value={documentacion} maxLength={LARGO_DOCUMENTACION}
        onChange={(ev) => escribir("documentacion", setDocumentacion)(ev.target.value)} />
      <ErrorDeCampo idCampo={`${id}-documentacion`} mensaje={errores.documentacion} />
      <label htmlFor={`${id}-plazo`}>Plazo en días corridos desde el envío</label>
      <input id={`${id}-plazo`} {...marcaDe(`${id}-plazo`, errores.plazo, `${id}-plazo-ayuda`)} type="number" inputMode="numeric"
        min={lista.plazo_minimo_dias} max={lista.plazo_maximo_dias} value={plazo}
        onChange={(ev) => escribir("plazo", setPlazo)(ev.target.value)} />
      <p id={`${id}-plazo-ayuda`} className="ayuda">
        Entre {lista.plazo_minimo_dias} y {lista.plazo_maximo_dias} días.
      </p>
      <ErrorDeCampo idCampo={`${id}-plazo`} mensaje={errores.plazo} />
    </VentanaDeFormulario>
  );
}

type PropsFicha = {
  consorcio: string;
  s: Solicitud;
  aEntrar: () => void;
  /** El «Listo» de preparar, si esta es la solicitud recién preparada. */
  resultado: ReactNode;
  alCerrar: () => void;
  alTerminar: (s: Solicitud, texto: string) => void;
  alCambiarLoVisto: (texto: string) => void;
};

type Abierto = null | "retirar" | "envio" | "comprobante" | "responder" | "registrar_respuesta";

/** La ficha de una solicitud: sus datos y, abajo, solo las acciones que trae `s.acciones` (SOL-03). */
function FichaSolicitud({ consorcio, s, aEntrar, resultado, alCerrar, alTerminar, alCambiarLoVisto }: PropsFicha) {
  const [abierto, setAbierto] = useState<Abierto>(null);
  const [firmando, setFirmando] = useState(false);
  /** Por qué no se firmó: en la ficha, donde se tocó «Firmar». */
  const [falla, setFalla] = useState<string | null>(null);
  const [copiado, setCopiado] = useState(false);
  /** Por qué no se pudo bajar el adjunto: en la ficha, que no se cierra (FL-SOL-04). */
  const [fallaDescarga, setFallaDescarga] = useState<string | null>(null);
  const hacer = useActo(aEntrar);

  function bajar(documento: string, version: string) {
    setFallaDescarga(null);
    void pedirDescarga(consorcio, documento, version).then((r) => {
      if (r.tipo === "sin_sesion") aEntrar();
      else if (r.tipo === "falla") setFallaDescarga(`No se pudo bajar el archivo. ${r.texto}`);
    });
  }
  const ruta = { consorcio_id: consorcio, solicitud_id: s.id };

  /** Lo que la ventana hace con el final de su acto. */
  function finDe(r: FinDelActo, texto: string): FinDeEnvio {
    if (r.tipo === "ok") {
      alTerminar(r.s, texto);
      return "cerrar";
    }
    if (r.tipo === "cambio") {
      alCambiarLoVisto(r.texto);
      return "cerrar";
    }
    return r.tipo === "sin_sesion" ? "seguir" : { rechazo: r.texto };
  }

  async function firmar() {
    setFirmando(true);
    setFalla(null);
    const r = await hacer((clave) =>
      api.POST("/api/v1/consorcios/{consorcio_id}/solicitudes-formales/{solicitud_id}/firma", {
        headers: encabezadoCsrf(),
        params: { path: ruta, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
      }),
    );
    setFirmando(false);
    if (r.tipo === "ok")
      alTerminar(
        r.s,
        r.s.estado === "borrador"
          ? "Quedó la firma. Falta la de otra persona."
          : r.s.estado === "envio_pendiente"
            ? "Quedó emitida. El administrador no tiene cuenta: falta registrar el envío por el medio formal."
            : "Quedó emitida y enviada: el administrador la recibe en la aplicación y por correo.",
      );
    else if (r.tipo === "cambio") alCambiarLoVisto(r.texto);
    else if (r.tipo === "falla") setFalla(r.texto);
  }

  const cerrarVentana = () => setAbierto(null);
  const props = { consorcio, s, hacer, alCerrar: cerrarVentana, finDe };
  /** FL-SOL-01: con una firma ya puesta, la de quien mira es la segunda: emite y envía. */
  const emite = s.acciones.includes("firmar") && s.firmas.length > 0;

  return (
    <FichaDeRegistro
      titulo={tituloDe(s)}
      datos={datosDe(s, consorcio, copiado, setCopiado, bajar)}
      alCerrar={alCerrar}
      ocupada={firmando}
      ventana={
        abierto === "retirar" ? (
          <VentanaRetiro {...props} />
        ) : abierto === "envio" ? (
          <VentanaEnvio {...props} aEntrar={aEntrar} />
        ) : abierto === "comprobante" ? (
          <VentanaComprobante {...props} aEntrar={aEntrar} />
        ) : abierto === "responder" || abierto === "registrar_respuesta" ? (
          <VentanaRespuesta {...props} aEntrar={aEntrar} recibida={abierto === "registrar_respuesta"} />
        ) : null
      }
    >
      {resultado}
      {fallaDescarga !== null && (
        <p role="alert" className="aviso-error">
          {fallaDescarga}
        </p>
      )}
      {falla !== null && (
        <p role="alert" className="aviso-error">
          {falla}
        </p>
      )}
      {s.falta_administrador_vigente && (
        <ComandoNoDisponible motivo={FALTA_ADMINISTRADOR} resolver={{ texto: IR_A_MANDATOS, a: RUTA_MANDATOS }} />
      )}
      {emite && <p>{EMITE_AL_FIRMAR}</p>}
      {s.acciones.length > 0 && (
        <p className="ficha-registro__acciones acciones">
          {s.acciones.includes("firmar") && (
            <button type="button" disabled={firmando} onClick={() => void firmar()}>
              {emite ? FIRMAR_Y_EMITIR : FIRMAR}
            </button>
          )}
          {s.acciones.includes("registrar_envio") && (
            <button type="button" onClick={() => setAbierto("envio")}>
              {REGISTRAR_ENVIO}
            </button>
          )}
          {s.acciones.includes("reemplazar_comprobante") && (
            <button type="button" onClick={() => setAbierto("comprobante")}>
              {SUBIR_OTRO_COMPROBANTE}
            </button>
          )}
          {s.acciones.includes("responder") && (
            <button type="button" onClick={() => setAbierto("responder")}>
              {RESPONDER}
            </button>
          )}
          {s.acciones.includes("registrar_respuesta") && (
            <button type="button" onClick={() => setAbierto("registrar_respuesta")}>
              {REGISTRAR_RESPUESTA}
            </button>
          )}
          {s.acciones.includes("retirar") && (
            <button type="button" className="secundario peligro" disabled={firmando} onClick={() => setAbierto("retirar")}>
              {RETIRAR}
            </button>
          )}
        </p>
      )}
    </FichaDeRegistro>
  );
}

/** Los datos de la ficha, sin lo técnico que no sirve para actuar (SOL-08) y con los adjuntos a mano
 * (SOL-09). El plazo dice solo los días: la situación ya dice desde cuándo corre (SOL-07). */
function datosDe(s: Solicitud, consorcio: string, copiado: boolean, setCopiado: (v: boolean) => void, alBajar: AlBajar): DatoDeFicha[] {
  const datos: DatoDeFicha[] = [
    { etiqueta: "Situación", valor: textoSituacion(s) },
    { etiqueta: "Documentación pedida", valor: <span className="texto-con-renglones">{s.documentacion}</span> },
    { etiqueta: "Plazo", valor: `${s.plazo_dias} días` },
    { etiqueta: "Preparó", valor: s.preparada_por_nombre ?? "Una persona del Consejo" },
  ];
  if (s.firmas.length > 0)
    datos.push({
      etiqueta: "Firmas",
      valor: s.firmas.map((f) => `${f.nombre ?? "Una persona del Consejo"}${f.propia ? " (esta cuenta)" : ""}`).join(", "),
    });
  if (s.destinatario_nombre !== null && s.destinatario_nombre !== undefined)
    datos.push({
      etiqueta: "Destinatario",
      valor: `${s.destinatario_nombre}${s.destinatario_con_cuenta === false ? " (sin cuenta en la aplicación)" : ""}`,
    });
  if (s.texto_emitido !== null && s.texto_emitido !== undefined) {
    const texto = s.texto_emitido;
    datos.push({
      etiqueta: "Texto emitido",
      valor: (
        <>
          <pre className="texto-con-renglones">{texto}</pre>
          {/* Hecha la copia, el botón no se ofrece otra vez: vuelve al reabrir la ficha (SOL-10). */}
          {copiado ? (
            <span role="status">Copiado.</span>
          ) : (
            <button
              type="button"
              className="secundario"
              onClick={() => {
                void navigator.clipboard.writeText(texto).then(
                  () => setCopiado(true),
                  () => setCopiado(false),
                );
              }}
            >
              Copiar el texto
            </button>
          )}
        </>
      ),
    });
    // La huella del texto emitido queda a la vista: el propietario la quiere (17-11-2026, deja de lado SOL-08 en esto).
    if (s.huella_texto !== null && s.huella_texto !== undefined) datos.push({ etiqueta: "Huella SHA-256", valor: s.huella_texto });
  }
  if (s.envio) {
    datos.push({
      etiqueta: "Envío",
      valor: (
        <>
          {TEXTO_MEDIO[s.envio.medio]}
          {s.envio.detalle ? ` (${s.envio.detalle})` : ""}, el {fechaLegible(s.envio.fecha_envio)}. Vence el {fechaLegible(s.envio.vence_el)}.
          {enlaceDelAdjunto(consorcio, s.envio, "Descargar el comprobante", alBajar)}
        </>
      ),
    });
  }
  if (s.respuesta) {
    datos.push({
      etiqueta: "Respuesta",
      valor: (
        <>
          <span className="texto-con-renglones">{s.respuesta.texto}</span>{" "}
          <span className="ayuda">
            {s.respuesta.medio === "aplicacion" ? "Respondida en la aplicación" : "Llegó por otro medio y la registró el Consejo"} el{" "}
            {fechaLegible(s.respuesta.respondida_el)}
            {s.respuesta.fuera_de_plazo ? ", fuera de plazo" : ""}.
          </span>
          {enlaceDelAdjunto(consorcio, s.respuesta, "Descargar el adjunto", alBajar)}
        </>
      ),
    });
  }
  if (s.retiro)
    datos.push({
      etiqueta: "Retiro",
      valor: `${s.retiro.motivo} (${s.retiro.retirada_por_nombre ?? "una persona del Consejo"}, ${momento(s.retiro.retirada_en)})`,
    });
  return datos;
}

type PropsVentana = {
  consorcio: string;
  s: Solicitud;
  hacer: ReturnType<typeof useActo>;
  alCerrar: () => void;
  finDe: (r: FinDelActo, texto: string) => FinDeEnvio;
};

function VentanaRetiro({ consorcio, s, hacer, alCerrar, finDe }: PropsVentana) {
  const id = useId();
  const [motivo, setMotivo] = useState("");
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);

  async function retirar(): Promise<FinDeEnvio> {
    const faltan = faltaCompletar({ motivo: [motivo, "Falta escribir el motivo del retiro."] });
    if (hayErrores(faltan)) {
      setErrores(faltan);
      return "seguir";
    }
    const r = await hacer((clave) =>
      api.POST("/api/v1/consorcios/{consorcio_id}/solicitudes-formales/{solicitud_id}/retiro", {
        headers: encabezadoCsrf(),
        params: { path: { consorcio_id: consorcio, solicitud_id: s.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
        body: { motivo: motivo.trim() },
      }),
    );
    return finDe(r, `Se retiró el borrador N.º ${s.numero}.`);
  }

  return (
    <VentanaDeFormulario titulo={RETIRAR} textoEnviar={RETIRAR} peligro alEnviar={retirar} alCerrar={alCerrar}>
      <label htmlFor={`${id}-motivo`}>Motivo del retiro</label>
      <textarea id={`${id}-motivo`} data-foco value={motivo} maxLength={LARGO_MOTIVO} {...marcaDe(`${id}-motivo`, errores.motivo)}
        onChange={(ev) => {
          setMotivo(ev.target.value);
          setErrores((e) => sinElDe(e, "motivo"));
        }} />
      <ErrorDeCampo idCampo={`${id}-motivo`} mensaje={errores.motivo} />
    </VentanaDeFormulario>
  );
}

/** Sube el archivo para el envío o la respuesta y devuelve su `adjunto_id`, o la falla dicha. */
function useAdjunto(consorcio: string, s: Solicitud, para: "envio" | "respuesta", aEntrar: () => void) {
  const [archivo, setArchivo] = useState<File | null>(null);
  const clave = useRef<string | null>(null);
  // El archivo ya subido y su `adjunto_id`: si el acto que lo cita se hizo pero su respuesta se
  // perdió, el reintento lo vuelve a citar sin subirlo otra vez. Subirlo de nuevo pasaría por el
  // permiso del estado actual, que ya no es el de antes del acto (Codex en app #255).
  const subido = useRef<{ archivo: File; adjunto_id: string } | null>(null);

  async function subir(elegido: File): Promise<{ adjunto_id: string } | { falla: string } | null> {
    if (subido.current?.archivo === elegido) return { adjunto_id: subido.current.adjunto_id };
    clave.current ??= crypto.randomUUID();
    try {
      const { data, error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/solicitudes-formales/{solicitud_id}/adjuntos", {
        headers: { ...encabezadoCsrf(), "Content-Type": elegido.type },
        params: {
          path: { consorcio_id: consorcio, solicitud_id: s.id },
          query: { para, titulo: tituloDeArchivo(elegido, `Solicitud N.º ${s.numero}`, LARGO_TITULO) },
          header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current },
        },
        // El archivo crudo, como en Documentos: `bodySerializer` lo deja pasar sin JSON.
        body: elegido as unknown as string,
        bodySerializer: (cuerpo) => cuerpo,
      });
      exigirSesion(response);
      if (data === undefined) {
        const codigo = codigoDe(error);
        if (renovarClave(response.status, codigo)) clave.current = null;
        return {
          falla: `No se cargó el archivo. ${DEL_ARCHIVO.has(response.status) ? mensajeDeCarga(response.status, codigo, "subir", [], response.headers.has("Retry-After")) : mensajeDeActo(response.status, error)}`,
        };
      }
      subido.current = { archivo: elegido, adjunto_id: data.adjunto_id };
      clave.current = null;
      return { adjunto_id: data.adjunto_id };
    } catch (e: unknown) {
      if (!(e instanceof SinSesion)) return { falla: `No se cargó el archivo. ${SIN_CONEXION}` };
      aEntrar();
      return null;
    }
  }

  return { archivo, setArchivo, subir };
}

const DEL_ARCHIVO = new Set([408, 413, 415, 503]);

/** Los días que la base acepta para la fecha del envío o de la respuesta recibida: desde un día
 * conocido (la emisión, el envío) hasta hoy (FL-SOL-06). */
type Rango = { desde: string | null; hasta: string };
const rangoDeFechas = (desde: string | null): Rango => ({ desde, hasta: hoyDelDispositivo() });
/** Las fechas `AAAA-MM-DD` se comparan como texto. */
const fueraDe = (r: Rango, fecha: string) => (r.desde !== null && fecha < r.desde) || fecha > r.hasta;
function textoDelRango(r: Rango, desdeQue: string): string {
  return r.desde === null ? "La fecha no puede ser posterior a hoy." : `La fecha va desde ${desdeQue} (${fechaLegible(r.desde)}) hasta hoy.`;
}

function CampoArchivo({ id, de, archivo, alElegir, nota, falta }: { id: string; de: string; archivo: File | null; alElegir: (a: File) => void; nota?: string; falta?: string }) {
  return (
    <>
      <SubirArchivo de={de} tipos={TIPOS_DE_DOCUMENTO} tope={TOPE_BYTES} elegido={archivo === null ? null : archivo.name} nota={nota} alElegir={alElegir} />
      <ErrorDeCampo idCampo={id} mensaje={falta} />
    </>
  );
}

function VentanaEnvio({ consorcio, s, hacer, alCerrar, finDe, aEntrar }: PropsVentana & { aEntrar: () => void }) {
  const id = useId();
  const [medio, setMedio] = useState<Medio>("carta_documento");
  const [detalle, setDetalle] = useState("");
  const [fecha, setFecha] = useState(hoyDelDispositivo());
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  const adjunto = useAdjunto(consorcio, s, "envio", aEntrar);
  // FL-SOL-06: la fecha va entre la emisión y hoy, como la controla la base.
  const rango = rangoDeFechas(s.emitida_en === null || s.emitida_en === undefined ? null : hoyDelDispositivo(new Date(s.emitida_en)));

  async function registrar(): Promise<FinDeEnvio> {
    const faltan: Record<string, string> = { ...faltaCompletar({ fecha: [fecha, "Falta la fecha del envío."] }) };
    if (faltan.fecha === undefined && fueraDe(rango, fecha)) faltan.fecha = textoDelRango(rango, "la emisión");
    if (adjunto.archivo === null) faltan.archivo = "Falta el comprobante del envío.";
    if (hayErrores(faltan) || adjunto.archivo === null) {
      setErrores(faltan);
      return "seguir";
    }
    const subido = await adjunto.subir(adjunto.archivo);
    if (subido === null) return "seguir";
    if ("falla" in subido) return { rechazo: subido.falla };
    const r = await hacer((clave) =>
      api.POST("/api/v1/consorcios/{consorcio_id}/solicitudes-formales/{solicitud_id}/envio", {
        headers: encabezadoCsrf(),
        params: { path: { consorcio_id: consorcio, solicitud_id: s.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
        body: { medio, fecha_envio: fecha, adjunto_id: subido.adjunto_id, ...(detalle.trim() === "" ? {} : { detalle: detalle.trim() }) },
      }),
    );
    return finDe(r, `Quedó registrado el envío de la solicitud N.º ${s.numero}.`);
  }

  return (
    <VentanaDeFormulario titulo={REGISTRAR_ENVIO} textoEnviar={REGISTRAR_ENVIO} alEnviar={registrar} alCerrar={alCerrar} ancha>
      <label htmlFor={`${id}-medio`}>Medio</label>
      <select id={`${id}-medio`} data-foco value={medio} onChange={(ev) => setMedio(ev.target.value as Medio)}>
        {MEDIOS_EXTERNOS.map((m) => (
          <option key={m} value={m}>
            {TEXTO_MEDIO[m]}
          </option>
        ))}
      </select>
      <label htmlFor={`${id}-detalle`}>Detalle (por ejemplo, el número de la carta documento)</label>
      <input id={`${id}-detalle`} value={detalle} maxLength={LARGO_DETALLE} onChange={(ev) => setDetalle(ev.target.value)} />
      <label htmlFor={`${id}-fecha`}>Fecha del envío</label>
      <input id={`${id}-fecha`} type="date" value={fecha} min={rango.desde ?? undefined} max={rango.hasta}
        {...marcaDe(`${id}-fecha`, errores.fecha)}
        onChange={(ev) => {
          setFecha(ev.target.value);
          setErrores((e) => sinElDe(e, "fecha"));
        }} />
      <ErrorDeCampo idCampo={`${id}-fecha`} mensaje={errores.fecha} />
      <CampoArchivo id={`${id}-archivo`} de="del comprobante del envío" archivo={adjunto.archivo} falta={errores.archivo}
        alElegir={(a) => {
          adjunto.setArchivo(a);
          setErrores((e) => sinElDe(e, "archivo"));
        }} />
    </VentanaDeFormulario>
  );
}

/** Otro comprobante para el envío ya registrado: el medio, la fecha y el vencimiento no cambian. */
function VentanaComprobante({ consorcio, s, hacer, alCerrar, finDe, aEntrar }: PropsVentana & { aEntrar: () => void }) {
  const id = useId();
  const [falta, setFalta] = useState<string | undefined>(undefined);
  const adjunto = useAdjunto(consorcio, s, "envio", aEntrar);

  async function reemplazar(): Promise<FinDeEnvio> {
    if (adjunto.archivo === null) {
      setFalta("Falta el comprobante nuevo.");
      return "seguir";
    }
    const subido = await adjunto.subir(adjunto.archivo);
    if (subido === null) return "seguir";
    if ("falla" in subido) return { rechazo: subido.falla };
    const r = await hacer((clave) =>
      api.POST("/api/v1/consorcios/{consorcio_id}/solicitudes-formales/{solicitud_id}/envio/comprobante", {
        headers: encabezadoCsrf(),
        params: { path: { consorcio_id: consorcio, solicitud_id: s.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
        body: { adjunto_id: subido.adjunto_id },
      }),
    );
    return finDe(r, `Quedó el comprobante nuevo del envío de la solicitud N.º ${s.numero}. El antivirus lo revisa antes de que se pueda descargar.`);
  }

  return (
    <VentanaDeFormulario titulo={SUBIR_OTRO_COMPROBANTE} textoEnviar={SUBIR_OTRO_COMPROBANTE} alEnviar={reemplazar} alCerrar={alCerrar} ancha>
      <p>{ARCHIVO_RECHAZADO} La fecha del envío y el vencimiento no cambian.</p>
      <CampoArchivo id={`${id}-archivo`} de="del comprobante del envío" archivo={adjunto.archivo} falta={falta}
        alElegir={(a) => {
          adjunto.setArchivo(a);
          setFalta(undefined);
        }} />
    </VentanaDeFormulario>
  );
}

function VentanaRespuesta({ consorcio, s, hacer, alCerrar, finDe, aEntrar, recibida }: PropsVentana & { aEntrar: () => void; recibida: boolean }) {
  const id = useId();
  const [texto, setTexto] = useState("");
  const [fecha, setFecha] = useState(hoyDelDispositivo());
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  const adjunto = useAdjunto(consorcio, s, "respuesta", aEntrar);
  // FL-SOL-06: la respuesta recibida llega entre el envío y hoy, como lo controla la base.
  const rango = rangoDeFechas(s.envio?.fecha_envio ?? null);

  async function registrar(): Promise<FinDeEnvio> {
    const faltan: Record<string, string> = faltaCompletar({
      texto: [texto, "Falta escribir la respuesta."],
      ...(recibida ? { fecha: [fecha, "Falta la fecha en que llegó."] as const } : {}),
    });
    if (recibida && faltan.fecha === undefined && fueraDe(rango, fecha)) faltan.fecha = textoDelRango(rango, "el envío");
    if (hayErrores(faltan)) {
      setErrores(faltan);
      return "seguir";
    }
    let conAdjunto = {};
    if (adjunto.archivo !== null) {
      const subido = await adjunto.subir(adjunto.archivo);
      if (subido === null) return "seguir";
      if ("falla" in subido) return { rechazo: subido.falla };
      conAdjunto = { adjunto_id: subido.adjunto_id };
    }
    const r = await hacer((clave) =>
      recibida
        ? api.POST("/api/v1/consorcios/{consorcio_id}/solicitudes-formales/{solicitud_id}/respuesta-recibida", {
            headers: encabezadoCsrf(),
            params: { path: { consorcio_id: consorcio, solicitud_id: s.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
            body: { texto: texto.trim(), recibida_el: fecha, ...conAdjunto },
          })
        : api.POST("/api/v1/consorcios/{consorcio_id}/solicitudes-formales/{solicitud_id}/respuesta", {
            headers: encabezadoCsrf(),
            params: { path: { consorcio_id: consorcio, solicitud_id: s.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
            body: { texto: texto.trim(), ...conAdjunto },
          }),
    );
    return finDe(r, `Quedó registrada la respuesta de la solicitud N.º ${s.numero}.`);
  }

  return (
    <VentanaDeFormulario titulo={recibida ? REGISTRAR_RESPUESTA : RESPONDER} textoEnviar={recibida ? "Registrar la respuesta" : "Enviar la respuesta"}
      alEnviar={registrar} alCerrar={alCerrar} ancha>
      <label htmlFor={`${id}-texto`}>{recibida ? "Lo que respondió la administración" : "Respuesta"}</label>
      <textarea id={`${id}-texto`} data-foco value={texto} maxLength={LARGO_RESPUESTA} {...marcaDe(`${id}-texto`, errores.texto)}
        onChange={(ev) => {
          setTexto(ev.target.value);
          setErrores((e) => sinElDe(e, "texto"));
        }} />
      <ErrorDeCampo idCampo={`${id}-texto`} mensaje={errores.texto} />
      {recibida && (
        <>
          <label htmlFor={`${id}-fecha`}>Fecha en que llegó</label>
          <input id={`${id}-fecha`} type="date" value={fecha} min={rango.desde ?? undefined} max={rango.hasta}
            {...marcaDe(`${id}-fecha`, errores.fecha)}
            onChange={(ev) => {
              setFecha(ev.target.value);
              setErrores((e) => sinElDe(e, "fecha"));
            }} />
          <ErrorDeCampo idCampo={`${id}-fecha`} mensaje={errores.fecha} />
        </>
      )}
      <CampoArchivo id={`${id}-archivo`} de="adjunto de la respuesta" archivo={adjunto.archivo} alElegir={adjunto.setArchivo} nota="Opcional." />
    </VentanaDeFormulario>
  );
}
