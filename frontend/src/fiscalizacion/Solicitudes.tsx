import { type ReactNode, useEffect, useId, useRef, useState } from "react";

import { CabeceraInterna } from "../CabeceraInterna";
import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { hoyDelDispositivo, TIPOS_DE_DOCUMENTO, tituloDeArchivo, TOPE_BYTES } from "../archivos";
import { encabezadoCsrf } from "../csrf";
import { mensajeDeError as mensajeDeCarga } from "../Documentos";
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
import { codigoDe, SIN_CONEXION } from "../fallas";
import { REQUISITOS } from "../permisos";
import { SubirArchivo } from "../SubirArchivo";

// Página /solicitudes: la solicitud formal de documentación del Consejo a la administración (RF49,
// CU12, PA36; app 0108; diseño `doc/diseno/RF49_Solicitud_Formal.md` del repositorio CEM). El Consejo
// la prepara, dos personas distintas la firman y la segunda firma la emite dirigida al administrador
// vigente. Si el administrador tiene cuenta, la recibe en la aplicación y responde acá; si no, el
// Consejo registra el envío por el medio externo, con el comprobante, y después la respuesta que
// llegó. El plazo corre desde el envío. Tono informativo (RN33): la solicitud pregunta, no acusa.
// Los botones de cada solicitud son los que la API dice que la persona puede usar (`acciones`).

type Solicitud = components["schemas"]["SolicitudFormal"];
type Lista = components["schemas"]["ListaSolicitudesFormales"];
type Estado = components["schemas"]["EstadoSolicitudFormal"];
type Objeto = components["schemas"]["ObjetoSolicitud"];
type Medio = components["schemas"]["MedioEnvio"];

export const RUTA_SOLICITUDES = "/solicitudes";
export const TITULO_SOLICITUDES = "Solicitudes formales";
export const EXPLICACION =
  "El Consejo pide a la administración documentación o una aclaración sobre algo concreto, con plazo y constancia (art. 2064 inc. b del Código Civil y Comercial). Hacen falta dos firmas de personas distintas; la segunda la emite. El plazo corre desde el envío.";
export const PREPARAR = "Preparar una solicitud";
export const FIRMAR = "Firmar";
export const RETIRAR = "Retirar el borrador";
export const REGISTRAR_ENVIO = "Registrar el envío";
export const RESPONDER = "Responder";
export const REGISTRAR_RESPUESTA = "Registrar la respuesta recibida";
export const SIN_SOLICITUDES = "Todavía no hay solicitudes formales en este edificio.";
/** Los largos de `cem_contracts.fiscalizacion` (la API vuelve a controlar). */
export const LARGO_REFERENCIA = 200;
export const LARGO_DOCUMENTACION = 2000;
export const LARGO_RESPUESTA = 4000;
export const LARGO_MOTIVO = 500;
export const LARGO_DETALLE = 300;

/** El orden de la lista: lo que espera algo primero. */
export const ORDEN_DE_ESTADOS: readonly Estado[] = ["borrador", "envio_pendiente", "vencida", "enviada", "respondida", "retirada"];

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

/** Una línea que resume en qué está cada solicitud. */
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

type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; lista: Lista };

export function Solicitudes() {
  const elegido = useConsorcio(REQUISITOS.solicitudes);
  return <DelEdificio key={elegido.consorcio} {...elegido} />;
}

function DelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar }: ReturnType<typeof useConsorcio>) {
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [preparando, setPreparando] = useState(false);

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

  /** Lo visto ya no es lo vigente (otra persona firmó, retiró o respondió): se avisa y se relee. */
  function alCambiarLoVisto(texto: string) {
    setAviso({ texto, ok: false });
    releer();
  }

  function alCambiar(nueva: Solicitud, texto: string) {
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
    setAviso({ texto, ok: true });
  }

  return (
    <>
      <CabeceraInterna titulo={TITULO_SOLICITUDES} volver={RUTA_INICIO} destino="Inicio" />
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={false} elegir={setConsorcio} />
      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">Esta cuenta no tiene edificios en Consorcio en Mano.</p>}
      <ConsorcioSinServicio pantalla={pantalla} reintentar={reintentar} />
      <AvisoPantalla aviso={aviso} />

      {pantalla.tipo === "lista" && (
        <>
          <p>{EXPLICACION}</p>
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
          {lectura.tipo === "ok" && (
            <>
              {lectura.lista.puede_preparar &&
                (preparando ? (
                  <FormularioPreparar
                    consorcio={consorcio}
                    lista={lectura.lista}
                    aEntrar={aEntrar}
                    alCerrar={() => setPreparando(false)}
                    alPreparar={(nueva) => {
                      setPreparando(false);
                      alCambiar(nueva, `Quedó preparada la solicitud N.º ${nueva.numero}. Falta que la firmen dos personas.`);
                    }}
                  />
                ) : (
                  <div className="acciones">
                    <button type="button" className="secundario" onClick={() => setPreparando(true)}>
                      {PREPARAR}
                    </button>
                  </div>
                ))}
              {lectura.lista.elementos.length === 0 ? (
                <p className="tarjeta">{SIN_SOLICITUDES}</p>
              ) : (
                ORDEN_DE_ESTADOS.map((estado) => {
                  const del = lectura.lista.elementos.filter((s) => s.estado === estado);
                  if (del.length === 0) return null;
                  return (
                    <section key={estado} aria-label={TEXTO_ESTADO[estado]}>
                      <h2>{TEXTO_ESTADO[estado]}</h2>
                      <ul className="filas-registro">
                        {del.map((s) => (
                          <li key={s.id}>
                            <Ficha consorcio={consorcio} solicitud={s} aEntrar={aEntrar} alCambiar={alCambiar} alCambiarLoVisto={alCambiarLoVisto} />
                          </li>
                        ))}
                      </ul>
                    </section>
                  );
                })
              )}
            </>
          )}
        </>
      )}
    </>
  );
}

/** Un acto sobre la solicitud: manda, traduce la respuesta y conserva la clave mientras haya que
 * reintentar lo mismo. */
function useActo(aEntrar: () => void, alCambiarLoVisto: (texto: string) => void) {
  const [enviando, setEnviando] = useState(false);
  const [falla, setFalla] = useState<string | null>(null);
  const clave = useRef<string | null>(null);

  async function hacer(
    pedido: (clave: string) => Promise<{ data?: Solicitud; error?: unknown; response: Response }>,
    alTerminar: (s: Solicitud) => void,
  ) {
    setEnviando(true);
    setFalla(null);
    clave.current ??= crypto.randomUUID();
    try {
      const { data, error, response } = await pedido(clave.current);
      exigirSesion(response);
      if (data === undefined) {
        const codigo = codigoDe(error);
        if (cambioLoVisto(response.status, codigo) && codigo !== "sin_administrador_vigente") {
          clave.current = null;
          alCambiarLoVisto(mensajeDeActo(response.status, error));
          return;
        }
        if (renovarClave(response.status, codigo)) clave.current = null;
        setFalla(mensajeDeActo(response.status, error));
        return;
      }
      clave.current = null;
      alTerminar(data);
    } catch (e: unknown) {
      if (e instanceof SinSesion) aEntrar();
      else setFalla(SIN_CONEXION);
    } finally {
      setEnviando(false);
    }
  }

  return { enviando, falla, setFalla, hacer };
}

function Falla({ texto }: { texto: string | null }) {
  if (texto === null) return null;
  return (
    <p role="alert" className="aviso-error">
      {texto}
    </p>
  );
}

type PropsPreparar = {
  consorcio: string;
  lista: Lista;
  aEntrar: () => void;
  alCerrar: () => void;
  alPreparar: (s: Solicitud) => void;
};

function FormularioPreparar({ consorcio, lista, aEntrar, alCerrar, alPreparar }: PropsPreparar) {
  const id = useId();
  const [objeto, setObjeto] = useState<Objeto>("periodo");
  const [referencia, setReferencia] = useState("");
  const [documentacion, setDocumentacion] = useState("");
  const [plazo, setPlazo] = useState(lista.plazo_por_defecto_dias);
  const acto = useActo(aEntrar, (texto) => acto.setFalla(texto));
  const completo = referencia.trim() !== "" && documentacion.trim() !== "";

  return (
    <form
      className="formulario tarjeta"
      aria-label={PREPARAR}
      onSubmit={(ev) => {
        ev.preventDefault();
        void acto.hacer(
          (clave) =>
            api.POST("/api/v1/consorcios/{consorcio_id}/solicitudes-formales", {
              headers: encabezadoCsrf(),
              params: { path: { consorcio_id: consorcio }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
              body: { objeto, referencia: referencia.trim(), documentacion: documentacion.trim(), plazo_dias: plazo },
            }),
          alPreparar,
        );
      }}
    >
      <h2>{PREPARAR}</h2>
      <label htmlFor={`${id}-objeto`}>Sobre qué</label>
      <select id={`${id}-objeto`} value={objeto} disabled={acto.enviando} onChange={(ev) => setObjeto(ev.target.value as Objeto)}>
        {(Object.keys(TEXTO_OBJETO) as Objeto[]).map((o) => (
          <option key={o} value={o}>
            {TEXTO_OBJETO[o]}
          </option>
        ))}
      </select>
      <label htmlFor={`${id}-referencia`}>Cuál (período, número de orden, contrato o movimiento)</label>
      <input
        id={`${id}-referencia`}
        value={referencia}
        maxLength={LARGO_REFERENCIA}
        required
        disabled={acto.enviando}
        onChange={(ev) => setReferencia(ev.target.value)}
      />
      <label htmlFor={`${id}-documentacion`}>Documentación o aclaración que se pide</label>
      <textarea
        id={`${id}-documentacion`}
        value={documentacion}
        maxLength={LARGO_DOCUMENTACION}
        required
        disabled={acto.enviando}
        onChange={(ev) => setDocumentacion(ev.target.value)}
      />
      <label htmlFor={`${id}-plazo`}>Plazo en días corridos desde el envío</label>
      <input
        id={`${id}-plazo`}
        type="number"
        inputMode="numeric"
        min={lista.plazo_minimo_dias}
        max={lista.plazo_maximo_dias}
        value={plazo}
        required
        disabled={acto.enviando}
        onChange={(ev) => setPlazo(Number(ev.target.value))}
      />
      <p className="ayuda">
        Entre {lista.plazo_minimo_dias} y {lista.plazo_maximo_dias} días. Preparar no cuenta como firma.
      </p>
      <Falla texto={acto.falla} />
      <div className="acciones">
        <button type="submit" disabled={acto.enviando || !completo}>
          Guardar el borrador
        </button>
        <button type="button" className="secundario" disabled={acto.enviando} onClick={alCerrar}>
          Cancelar
        </button>
      </div>
    </form>
  );
}

type PropsFicha = {
  consorcio: string;
  solicitud: Solicitud;
  aEntrar: () => void;
  alCambiar: (s: Solicitud, texto: string) => void;
  alCambiarLoVisto: (texto: string) => void;
};

type Abierto = null | "retirar" | "envio" | "responder" | "registrar_respuesta";

function Ficha({ consorcio, solicitud: s, aEntrar, alCambiar, alCambiarLoVisto }: PropsFicha) {
  const id = useId();
  const [abierto, setAbierto] = useState<Abierto>(null);
  const acto = useActo(aEntrar, alCambiarLoVisto);
  const ruta = { consorcio_id: consorcio, solicitud_id: s.id };

  function firmar() {
    void acto.hacer(
      (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/solicitudes-formales/{solicitud_id}/firma", {
          headers: encabezadoCsrf(),
          params: { path: ruta, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
        }),
      (nueva) =>
        alCambiar(
          nueva,
          nueva.estado === "borrador"
            ? "Quedó la firma. Falta la de otra persona."
            : nueva.estado === "envio_pendiente"
              ? "Quedó emitida. El administrador no tiene cuenta: falta registrar el envío por el medio formal."
              : "Quedó emitida y enviada: el administrador la recibe en la aplicación y por correo.",
        ),
    );
  }

  const cerrar = () => setAbierto(null);
  const listo = (texto: string) => (nueva: Solicitud) => {
    cerrar();
    alCambiar(nueva, texto);
  };

  return (
    <article className="tarjeta" aria-labelledby={`${id}-titulo`}>
      <h3 id={`${id}-titulo`}>
        N.º {s.numero} · {TEXTO_OBJETO[s.objeto]}: {s.referencia}
      </h3>
      <p>{textoSituacion(s)}</p>
      <details>
        <summary>Ver el detalle</summary>
        <Detalle s={s} />
      </details>
      <Falla texto={acto.falla} />
      {abierto === null && s.acciones.length > 0 && (
        <div className="acciones">
          {s.acciones.includes("firmar") && (
            <button type="button" className="secundario" disabled={acto.enviando} onClick={firmar}>
              {FIRMAR}
            </button>
          )}
          {s.acciones.includes("registrar_envio") && (
            <button type="button" className="secundario" onClick={() => setAbierto("envio")}>
              {REGISTRAR_ENVIO}
            </button>
          )}
          {s.acciones.includes("responder") && (
            <button type="button" className="secundario" onClick={() => setAbierto("responder")}>
              {RESPONDER}
            </button>
          )}
          {s.acciones.includes("registrar_respuesta") && (
            <button type="button" className="secundario" onClick={() => setAbierto("registrar_respuesta")}>
              {REGISTRAR_RESPUESTA}
            </button>
          )}
          {s.acciones.includes("retirar") && (
            <button type="button" className="secundario peligro" disabled={acto.enviando} onClick={() => setAbierto("retirar")}>
              {RETIRAR}
            </button>
          )}
        </div>
      )}
      {abierto === "retirar" && (
        <FormularioRetiro consorcio={consorcio} s={s} aEntrar={aEntrar} alCerrar={cerrar} alCambiarLoVisto={alCambiarLoVisto}
          alTerminar={listo(`Se retiró el borrador N.º ${s.numero}.`)} />
      )}
      {abierto === "envio" && (
        <FormularioEnvio consorcio={consorcio} s={s} aEntrar={aEntrar} alCerrar={cerrar} alCambiarLoVisto={alCambiarLoVisto}
          alTerminar={listo(`Quedó registrado el envío de la solicitud N.º ${s.numero}: desde esa fecha corre el plazo.`)} />
      )}
      {(abierto === "responder" || abierto === "registrar_respuesta") && (
        <FormularioRespuesta consorcio={consorcio} s={s} recibida={abierto === "registrar_respuesta"} aEntrar={aEntrar} alCerrar={cerrar}
          alCambiarLoVisto={alCambiarLoVisto} alTerminar={listo(`Quedó registrada la respuesta de la solicitud N.º ${s.numero}.`)} />
      )}
    </article>
  );
}

function Detalle({ s }: { s: Solicitud }) {
  const [copiado, setCopiado] = useState(false);
  return (
    <dl className="detalle">
      <dt>Documentación pedida</dt>
      <dd className="texto-con-renglones">{s.documentacion}</dd>
      <dt>Plazo</dt>
      <dd>{s.plazo_dias} días corridos desde el envío</dd>
      <dt>Preparó</dt>
      <dd>
        {s.preparada_por_nombre ?? "Una persona del Consejo"}, {momento(s.preparada_en)}
      </dd>
      <dt>Firmas</dt>
      <dd>
        {s.firmas.length === 0
          ? "Todavía ninguna."
          : s.firmas.map((f) => `${f.nombre ?? "Una persona del Consejo"}${f.propia ? " (esta cuenta)" : ""}, ${momento(f.firmada_en)}`).join("; ")}
      </dd>
      {s.destinatario_nombre !== null && s.destinatario_nombre !== undefined && (
        <>
          <dt>Destinatario</dt>
          <dd>
            {s.destinatario_nombre}
            {s.destinatario_con_cuenta === false ? " (sin cuenta en la aplicación)" : ""}
          </dd>
        </>
      )}
      {s.texto_emitido !== null && s.texto_emitido !== undefined && (
        <>
          <dt>Texto emitido</dt>
          <dd>
            <pre className="texto-con-renglones">{s.texto_emitido}</pre>
            <p className="ayuda">Huella SHA-256: {s.huella_texto}</p>
            <button
              type="button"
              className="secundario"
              onClick={() => {
                const texto = s.texto_emitido ?? "";
                void navigator.clipboard.writeText(texto).then(
                  () => setCopiado(true),
                  () => setCopiado(false),
                );
              }}
            >
              Copiar el texto
            </button>
            {copiado && <span role="status"> Copiado.</span>}
          </dd>
        </>
      )}
      {s.envio && (
        <>
          <dt>Envío</dt>
          <dd>
            {TEXTO_MEDIO[s.envio.medio]}
            {s.envio.detalle ? ` (${s.envio.detalle})` : ""}, el {fechaLegible(s.envio.fecha_envio)}. Vence el {fechaLegible(s.envio.vence_el)}.
          </dd>
        </>
      )}
      {s.respuesta && (
        <>
          <dt>Respuesta</dt>
          <dd>
            <p className="texto-con-renglones">{s.respuesta.texto}</p>
            <p className="ayuda">
              {s.respuesta.medio === "aplicacion" ? "Respondida en la aplicación" : "Llegó por otro medio y la registró el Consejo"} el{" "}
              {fechaLegible(s.respuesta.respondida_el)}
              {s.respuesta.fuera_de_plazo ? ", fuera de plazo" : ""}
              {s.respuesta.documento_id ? ". Con un archivo adjunto en Documentos del edificio, «Otros»." : "."}
            </p>
          </dd>
        </>
      )}
      {s.retiro && (
        <>
          <dt>Retiro</dt>
          <dd>
            {s.retiro.motivo} ({s.retiro.retirada_por_nombre ?? "una persona del Consejo"}, {momento(s.retiro.retirada_en)})
          </dd>
        </>
      )}
    </dl>
  );
}

type PropsFormulario = {
  consorcio: string;
  s: Solicitud;
  aEntrar: () => void;
  alCerrar: () => void;
  alCambiarLoVisto: (texto: string) => void;
  alTerminar: (s: Solicitud) => void;
};

function Botones({ enviando, listo, confirmar, alCerrar, peligro = false }: { enviando: boolean; listo: boolean; confirmar: string; alCerrar: () => void; peligro?: boolean }) {
  return (
    <div className="acciones">
      <button type="submit" className={peligro ? "peligro" : undefined} disabled={enviando || !listo}>
        {confirmar}
      </button>
      <button type="button" className="secundario" disabled={enviando} onClick={alCerrar}>
        Cancelar
      </button>
    </div>
  );
}

function FormularioRetiro({ consorcio, s, aEntrar, alCerrar, alCambiarLoVisto, alTerminar }: PropsFormulario) {
  const id = useId();
  const [motivo, setMotivo] = useState("");
  const acto = useActo(aEntrar, alCambiarLoVisto);
  return (
    <form
      className="formulario"
      onSubmit={(ev) => {
        ev.preventDefault();
        void acto.hacer(
          (clave) =>
            api.POST("/api/v1/consorcios/{consorcio_id}/solicitudes-formales/{solicitud_id}/retiro", {
              headers: encabezadoCsrf(),
              params: { path: { consorcio_id: consorcio, solicitud_id: s.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
              body: { motivo: motivo.trim() },
            }),
          alTerminar,
        );
      }}
    >
      <label htmlFor={`${id}-motivo`}>Motivo del retiro</label>
      <textarea id={`${id}-motivo`} value={motivo} maxLength={LARGO_MOTIVO} required disabled={acto.enviando} onChange={(ev) => setMotivo(ev.target.value)} />
      <Falla texto={acto.falla} />
      <Botones enviando={acto.enviando} listo={motivo.trim() !== ""} confirmar="Confirmar el retiro" alCerrar={alCerrar} peligro />
    </form>
  );
}

/** Sube el archivo para el envío o la respuesta y devuelve su `adjunto_id`, o null si no se pudo
 * (con la falla dicha). */
function useAdjunto(consorcio: string, s: Solicitud, para: "envio" | "respuesta", aEntrar: () => void) {
  const [archivo, setArchivo] = useState<File | null>(null);
  const clave = useRef<string | null>(null);
  // El archivo ya subido y su `adjunto_id`: si el acto que lo cita se hizo pero su respuesta se
  // perdió, el reintento lo vuelve a citar sin subirlo otra vez. Subirlo de nuevo pasaría por el
  // permiso del estado actual, que ya no es el de antes del acto (Codex en app #255).
  const subido = useRef<{ archivo: File; adjunto_id: string } | null>(null);

  async function subir(setFalla: (t: string) => void): Promise<string | null> {
    if (archivo === null) return null;
    if (subido.current?.archivo === archivo) return subido.current.adjunto_id;
    clave.current ??= crypto.randomUUID();
    try {
      const { data, error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/solicitudes-formales/{solicitud_id}/adjuntos", {
        headers: { ...encabezadoCsrf(), "Content-Type": archivo.type },
        params: {
          path: { consorcio_id: consorcio, solicitud_id: s.id },
          query: { para, titulo: tituloDeArchivo(archivo, `Solicitud N.º ${s.numero}`, LARGO_TITULO) },
          header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current },
        },
        // El archivo crudo, como en Documentos: `bodySerializer` lo deja pasar sin JSON.
        body: archivo as unknown as string,
        bodySerializer: (cuerpo) => cuerpo,
      });
      exigirSesion(response);
      if (data === undefined) {
        const codigo = codigoDe(error);
        if (renovarClave(response.status, codigo)) clave.current = null;
        setFalla(`No se cargó el archivo. ${DEL_ARCHIVO.has(response.status) ? mensajeDeCarga(response.status, codigo, "subir", [], response.headers.has("Retry-After")) : mensajeDeActo(response.status, error)}`);
        return null;
      }
      subido.current = { archivo, adjunto_id: data.adjunto_id };
      clave.current = null;
      return data.adjunto_id;
    } catch (e: unknown) {
      if (e instanceof SinSesion) aEntrar();
      else setFalla(`No se cargó el archivo. ${SIN_CONEXION}`);
      return null;
    }
  }

  return { archivo, setArchivo, subir };
}

const DEL_ARCHIVO = new Set([408, 413, 415, 503]);

function CampoArchivo({ de, archivo, deshabilitada, alElegir, nota }: { de: string; archivo: File | null; deshabilitada: boolean; alElegir: (a: File) => void; nota?: string }): ReactNode {
  return (
    <SubirArchivo
      de={de}
      tipos={TIPOS_DE_DOCUMENTO}
      tope={TOPE_BYTES}
      deshabilitada={deshabilitada}
      elegido={archivo === null ? null : archivo.name}
      nota={nota}
      alElegir={alElegir}
    />
  );
}

function FormularioEnvio({ consorcio, s, aEntrar, alCerrar, alCambiarLoVisto, alTerminar }: PropsFormulario) {
  const id = useId();
  const [medio, setMedio] = useState<Medio>("carta_documento");
  const [detalle, setDetalle] = useState("");
  const [fecha, setFecha] = useState(hoyDelDispositivo());
  const acto = useActo(aEntrar, alCambiarLoVisto);
  const adjunto = useAdjunto(consorcio, s, "envio", aEntrar);
  const [subiendo, setSubiendo] = useState(false);
  const ocupado = acto.enviando || subiendo;

  async function registrar() {
    setSubiendo(true);
    acto.setFalla(null);
    const adjunto_id = await adjunto.subir((t) => acto.setFalla(t));
    setSubiendo(false);
    if (adjunto_id === null) return;
    await acto.hacer(
      (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/solicitudes-formales/{solicitud_id}/envio", {
          headers: encabezadoCsrf(),
          params: { path: { consorcio_id: consorcio, solicitud_id: s.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: { medio, fecha_envio: fecha, adjunto_id, ...(detalle.trim() === "" ? {} : { detalle: detalle.trim() }) },
        }),
      alTerminar,
    );
  }

  return (
    <form
      className="formulario"
      onSubmit={(ev) => {
        ev.preventDefault();
        void registrar();
      }}
    >
      <p>El texto emitido está en el detalle, para enviarlo. Acá queda la constancia del envío: desde esa fecha corre el plazo.</p>
      <label htmlFor={`${id}-medio`}>Medio</label>
      <select id={`${id}-medio`} value={medio} disabled={ocupado} onChange={(ev) => setMedio(ev.target.value as Medio)}>
        {MEDIOS_EXTERNOS.map((m) => (
          <option key={m} value={m}>
            {TEXTO_MEDIO[m]}
          </option>
        ))}
      </select>
      <label htmlFor={`${id}-detalle`}>Detalle (por ejemplo, el número de la carta documento)</label>
      <input id={`${id}-detalle`} value={detalle} maxLength={LARGO_DETALLE} disabled={ocupado} onChange={(ev) => setDetalle(ev.target.value)} />
      <label htmlFor={`${id}-fecha`}>Fecha del envío</label>
      <input id={`${id}-fecha`} type="date" value={fecha} required disabled={ocupado} onChange={(ev) => setFecha(ev.target.value)} />
      <CampoArchivo de="del comprobante del envío" archivo={adjunto.archivo} deshabilitada={ocupado} alElegir={adjunto.setArchivo} nota="El comprobante es obligatorio." />
      <Falla texto={acto.falla} />
      <Botones enviando={ocupado} listo={fecha !== "" && adjunto.archivo !== null} confirmar="Registrar el envío" alCerrar={alCerrar} />
    </form>
  );
}

function FormularioRespuesta({ consorcio, s, recibida, aEntrar, alCerrar, alCambiarLoVisto, alTerminar }: PropsFormulario & { recibida: boolean }) {
  const id = useId();
  const [texto, setTexto] = useState("");
  const [fecha, setFecha] = useState(hoyDelDispositivo());
  const acto = useActo(aEntrar, alCambiarLoVisto);
  const adjunto = useAdjunto(consorcio, s, "respuesta", aEntrar);
  const [subiendo, setSubiendo] = useState(false);
  const ocupado = acto.enviando || subiendo;

  async function registrar() {
    acto.setFalla(null);
    let adjunto_id: string | null = null;
    if (adjunto.archivo !== null) {
      setSubiendo(true);
      adjunto_id = await adjunto.subir((t) => acto.setFalla(t));
      setSubiendo(false);
      if (adjunto_id === null) return;
    }
    const conAdjunto = adjunto_id === null ? {} : { adjunto_id };
    await acto.hacer(
      (clave) =>
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
      alTerminar,
    );
  }

  return (
    <form
      className="formulario"
      onSubmit={(ev) => {
        ev.preventDefault();
        void registrar();
      }}
    >
      <label htmlFor={`${id}-texto`}>{recibida ? "Lo que respondió la administración" : "Respuesta"}</label>
      <textarea id={`${id}-texto`} value={texto} maxLength={LARGO_RESPUESTA} required disabled={ocupado} onChange={(ev) => setTexto(ev.target.value)} />
      {recibida && (
        <>
          <label htmlFor={`${id}-fecha`}>Fecha en que llegó</label>
          <input id={`${id}-fecha`} type="date" value={fecha} required disabled={ocupado} onChange={(ev) => setFecha(ev.target.value)} />
        </>
      )}
      <CampoArchivo de="adjunto de la respuesta" archivo={adjunto.archivo} deshabilitada={ocupado} alElegir={adjunto.setArchivo} nota="Opcional." />
      <Falla texto={acto.falla} />
      <Botones enviando={ocupado} listo={texto.trim() !== "" && (!recibida || fecha !== "")} confirmar={recibida ? "Registrar la respuesta" : "Enviar la respuesta"} alCerrar={alCerrar} />
    </form>
  );
}
