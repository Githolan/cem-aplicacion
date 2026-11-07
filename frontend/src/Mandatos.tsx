import { type FormEvent, type MutableRefObject, useCallback, useEffect, useId, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { encabezadoCsrf } from "./csrf";
import { RUTA_INICIO } from "./enlace";
import { useAEntrar } from "./sesionEncima";
import { conLaRedDeVuelta, sinElAvisoDeLaRed } from "./egresos/comun";
import { useAlVolverLaRed, useMiPersona } from "./egresos/useConsorcio";
import { nombreDeFacultad } from "./facultades";
import { codigoDe, esDeConexion, mensajeDeFalla, SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "./fallas";
import { REQUISITOS, useAcciones, useEdificioElegido, usePuede } from "./permisos";
import { TextoDeUnaLinea } from "./TextoDeUnaLinea";

// Página /mandatos (H2b 2b-4c, PR 2): los mandatos y las decisiones de un edificio, su detalle,
// registrar una decisión y dar de alta o de baja un mandato. Cada lista y cada formulario aparece
// solo si la persona lo alcanza, y sin ninguno va al inicio. Las decisiones que registró quien mira
// no se ofrecen para dar altas ni para remover (A15) y su propia baja no aparece. Es solo interfaz:
// un 403 al actuar se dice en la pantalla, y la autoridad es la API, la política y la base. Diseño:
// `doc/diseno/H2b_4c_Diseno.md` del repositorio CEM, §7.
// Diseño visual provisorio: el definitivo llega con G3.

type Consorcio = components["schemas"]["ConsorcioResumen"];
type Mandato = components["schemas"]["MandatoResumen"];
type MandatoDetalle = components["schemas"]["MandatoDetalle"];
type Decision = components["schemas"]["DecisionResumen"];
type DecisionDetalle = components["schemas"]["DecisionDetalle"];
type Candidato = components["schemas"]["CandidatoMandato"];
type TipoMandato = components["schemas"]["TipoMandato"];
type EstadoMandato = components["schemas"]["EstadoMandato"];
type TipoDecision = components["schemas"]["TipoDecision"];
type CausaBaja = components["schemas"]["CausaBaja"];

export const RUTA_MANDATOS = "/mandatos";

const ESTADO = {
  sinSesion: 401,
  sinPermiso: 403,
  noEncontrado: 404,
  conflicto: 409,
  invalido: 422,
  limite: 429,
  errorDelServidor: 500,
} as const;
const ENCABEZADO_IDEMPOTENCIA = "Idempotency-Key" as const;
const LARGO_DESCRIPCION = 500;
const LARGO_REFERENCIA = 300;
/** Filas visibles de los textos largos en el celular (doc UX de invitaciones y formularios, §3). */
const FILAS_DESCRIPCION = 4;
const FILAS_REFERENCIA = 2;
const LARGO_NOMBRE = 200;
const LARGO_MATRICULA = 100;
/** Cuánto de la descripción entra en una opción: el ancho de un `<select>` es el de su opción más larga. */
const LARGO_OPCION = 60;
const PATRON_CUIT = "[0-9]{11}";
const PATRON_SHA256 = "[0-9a-fA-F]{64}";
/** Un texto con algo más que espacios (la API rechaza el vacío después de recortar). */
const PATRON_TEXTO = ".*\\S.*";

const TEXTO_TIPO_MANDATO: Record<TipoMandato, string> = {
  miembro_consejo: "Consejo de propietarios",
  administrador: "Administración",
  administrador_externo: "Administración externa",
};
const TIPOS_MANDATO = Object.keys(TEXTO_TIPO_MANDATO) as TipoMandato[];
const TIPO_MANDATO_INICIAL: TipoMandato = "miembro_consejo";

const TEXTO_ESTADO_MANDATO: Record<EstadoMandato, string> = {
  vigente: "Vigente",
  pendiente_revision: "Pendiente de revisión: sin condición para integrar el Consejo",
  futuro: "Empieza más adelante",
  terminado: "Terminado por su fecha",
  dado_de_baja: "Dado de baja",
  error_carga: "Anulado por error de carga",
};

const TEXTO_TIPO_DECISION: Record<TipoDecision, string> = {
  asamblea: "Asamblea",
  consejo: "Reunión del Consejo",
  contrato_administracion: "Contrato de administración",
  inscripcion_rpa: "Inscripción en el registro de administradores",
};
const TIPOS_DECISION = Object.keys(TEXTO_TIPO_DECISION) as TipoDecision[];
const TIPO_DECISION_INICIAL: TipoDecision = "asamblea";

const TEXTO_CAUSA: Record<CausaBaja, string> = {
  remocion: "Remoción por asamblea",
  renuncia: "Renuncia",
  fallecimiento_incapacidad: "Fallecimiento o incapacidad",
  error_carga: "Error de carga",
  otro: "Otra causa",
};
const CAUSAS = Object.keys(TEXTO_CAUSA) as CausaBaja[];
/** Un mandato que ya terminó por su fecha solo admite la baja por error de carga. */
const CAUSAS_TERMINADO: CausaBaja[] = ["error_carga"];
const ESTADOS_CON_BAJA: EstadoMandato[] = ["vigente", "pendiente_revision", "futuro", "terminado"];
/** Respuestas de la baja que dicen que el detalle quedó viejo: se cierra y se vuelve a pedir la lista. */
const BAJA_SOBRE_ESTADO_VIEJO = ["mandato_ya_dado_de_baja", "mandato_terminado"];

// Las fechas del contrato son días sin hora: se muestran tal cual, sin pasar por otra zona.
const FORMATO_DIA = new Intl.DateTimeFormat("es-AR", { dateStyle: "long", timeZone: "UTC" });
const fechaDe = (iso: string) => new Date(`${iso}T00:00:00Z`);
const dia = (iso: string) => FORMATO_DIA.format(fechaDe(iso));
// Un momento (cuándo se registró la baja) sí tiene hora: se muestra en la zona del edificio.
const FORMATO_MOMENTO = new Intl.DateTimeFormat("es-AR", {
  dateStyle: "long",
  timeStyle: "short",
  hourCycle: "h23",
  timeZone: "America/Argentina/Buenos_Aires",
});
const momento = (iso: string) => FORMATO_MOMENTO.format(new Date(iso));
const recortar = (texto: string) => (texto.length > LARGO_OPCION ? `${texto.slice(0, LARGO_OPCION - 1)}…` : texto);

/** Qué se estaba haciendo cuando la API respondió con error. */
export type Accion = "decision" | "alta" | "baja";

const SIN_PERMISO: Record<Accion, string> = {
  decision: "No tenés permiso para registrar decisiones en este edificio.",
  alta: "No tenés permiso para dar este alta. Puede ser que tu mandato no lo incluya o que el alta sea para vos.",
  baja: "No tenés permiso para dar esta baja. Puede ser que tu mandato no lo incluya.",
};

const NO_ENCONTRADO: Record<Accion, string> = {
  decision: "No encontramos el edificio o ya no tenés acceso.",
  alta: "No encontramos el edificio o ya no tenés acceso.",
  baja: "No encontramos ese mandato en este edificio.",
};

/** Los motivos de dominio, por el código del cuerpo uniforme. */
const POR_CODIGO: Record<string, string> = {
  administracion_ocupada: "Ya hay un mandato de administración en esas fechas en este edificio.",
  mandato_superpuesto: "La persona ya tiene un mandato de ese tipo en esas fechas.",
  consejo_incompatible_con_administracion:
    "La persona administra este edificio como externa en esas fechas: el Consejo fiscaliza y no co-administra.",
  mandato_ya_dado_de_baja: "El mandato ya tiene una baja registrada.",
  mandato_terminado: "El mandato ya terminó por su fecha: solo admite una baja por error de carga.",
  fecha_futura: "La fecha de la decisión no puede ser posterior a hoy: se registran actos ya tomados.",
  decision_invalida: "La decisión no existe en este edificio o no corresponde (la remoción pide una asamblea).",
  persona_sin_vinculo: "La persona no tiene un vínculo confirmado, vigente o futuro, en este edificio.",
  externo_por_invitacion:
    "La administración externa se da de alta sin cuenta: la cuenta se asocia después, por invitación.",
  mandato_sin_cuenta: "Un miembro del Consejo o un administrador necesita su cuenta.",
  cuit_invalido: "El CUIT no es válido.",
  facultad_fuera_de_paquete: "Alguna facultad no corresponde a ese tipo de mandato.",
  respaldo_invalido: "La remoción se respalda con una asamblea; cualquier otra causa, con el instrumento.",
};

/** Qué decir ante el error de un envío, según el estado y el código del cuerpo uniforme. */
export function mensajeDeError(estado: number, codigo: string | undefined, accion: Accion): string {
  if (estado === ESTADO.sinPermiso && codigo === "csrf_invalido") return "No pudimos verificar el pedido. Recargá la página.";
  if (estado === ESTADO.sinPermiso) return SIN_PERMISO[accion];
  if (estado === ESTADO.noEncontrado) return NO_ENCONTRADO[accion];
  if (estado === ESTADO.conflicto && codigo === "idempotencia_en_curso")
    return "El pedido se está procesando. Probá de nuevo en unos segundos.";
  if (estado === ESTADO.limite) return "Llegaste al límite de pedidos por ahora. Probá más tarde.";
  if (estado === ESTADO.invalido && codigo === "idempotencia_reutilizada")
    return "Cambiaste los datos de un intento anterior. Volvé a enviar el formulario.";
  if ((estado === ESTADO.conflicto || estado === ESTADO.invalido) && codigo !== undefined && codigo in POR_CODIGO)
    return POR_CODIGO[codigo] as string;
  if (estado === ESTADO.conflicto) return "El pedido choca con el estado actual del edificio. Recargá la página y revisá los datos.";
  if (estado === ESTADO.invalido) return "Revisá los datos del formulario.";
  return mensajeDeFalla(estado, codigo);
}

/**
 * Si el intento terminó y el próximo lleva otra clave. Un 5xx es ambiguo (un proxy puede
 * responder después de que el backend guardó el resultado) y un pedido en curso todavía no
 * terminó: el reintento lleva la misma clave para recibir lo guardado.
 */
function renovarClave(estado: number, codigo: string | undefined): boolean {
  if (codigo === "idempotencia_reutilizada") return true;
  return estado < ESTADO.errorDelServidor && codigo !== "idempotencia_en_curso";
}

type Pantalla =
  | { tipo: "cargando" }
  | { tipo: "sin_servicio"; deConexion: boolean }
  | { tipo: "sin_consorcios" }
  | { tipo: "lista"; consorcios: Consorcio[] };

const SIN_EDIFICIOS: readonly Consorcio[] = [];

/** Una lista del edificio: llegó, no se pudo consultar o no le toca a la persona (no se muestra). */
type Lectura<T> = { tipo: "cargando" } | { tipo: "sin_permiso" } | { tipo: "sin_servicio" } | { tipo: "ok"; elementos: T[] };

type Detalle = { tipo: "mandato"; datos: MandatoDetalle } | { tipo: "decision"; datos: DecisionDetalle };

/** `formulario`: el que falló. Va junto a él, para corregirlo sin recorrer la página. */
type Aviso = { texto: string; ok: boolean; formulario?: Accion };

type Clave = MutableRefObject<string | null>;

type Resultado<T> = { data?: T; error?: unknown; response: Response };

/** Un 401 en cualquier pedido de la página: se va a /entrar. */
class SinSesion extends Error {}

function exigirSesion(response: Response) {
  if (response.status === ESTADO.sinSesion) throw new SinSesion();
}

function leer<T>({ data, response }: Resultado<{ elementos: T[] }>): Lectura<T> {
  exigirSesion(response);
  if (response.status === ESTADO.sinPermiso) return { tipo: "sin_permiso" };
  if (data === undefined) throw new Error(String(response.status));
  return { tipo: "ok", elementos: data.elementos };
}

const CARGANDO = { tipo: "cargando" } as const;
const SIN_PERMISO_DE_LECTURA = { tipo: "sin_permiso" } as const;
const SIN_SERVICIO = { tipo: "sin_servicio" } as const;

/** Una lista que no se pudo consultar no arrastra a las otras; un 401 sí corta todo. */
function cadaUna<T>(pedido: Promise<Lectura<T>>): Promise<Lectura<T>> {
  return pedido.catch((e: unknown) => {
    if (e instanceof SinSesion) throw e;
    return SIN_SERVICIO;
  });
}

/** Una lista que no se pudo consultar vuelve a «Consultando…»; las que están a la vista quedan. */
function aReconsultar<T>(lectura: Lectura<T>): Lectura<T> {
  return lectura.tipo === "sin_servicio" ? CARGANDO : lectura;
}

const ENUMERAR = new Intl.ListFormat("es", { type: "conjunction" });

type Alcance = { veMandatos: boolean; veDecisiones: boolean; registra: boolean; daAltas: boolean; daBajas: boolean };

/** La presentación de la pantalla: promete solo lo que la persona alcanza. */
export function presentacion({ veMandatos, veDecisiones, registra, daAltas, daBajas }: Alcance): string {
  const ve = [
    ...(veMandatos ? ["los mandatos del Consejo y de la administración del edificio"] : []),
    ...(veDecisiones ? [veMandatos ? "las decisiones que los respaldan" : "las decisiones del edificio"] : []),
  ];
  const hace = [
    ...(registra ? ["registrar decisiones"] : []),
    ...(daAltas ? ["dar de alta un mandato"] : []),
    ...(daBajas ? ["dar de baja un mandato"] : []),
  ];
  return [
    ...(ve.length > 0 ? [`Acá ves ${ENUMERAR.format(ve)}.`] : []),
    ...(hace.length > 0 ? [`Podés ${ENUMERAR.format(hace)}.`] : []),
  ].join(" ");
}

type FormularioAbierto = "decision" | "alta";

export function Mandatos() {
  const id = useId();
  const navegar = useNavigate();
  const [pantalla, setPantalla] = useState<Pantalla>({ tipo: "cargando" });
  const { edificios, consorcio, setConsorcio } = useEdificioElegido(
    pantalla.tipo === "lista" ? pantalla.consorcios : SIN_EDIFICIOS,
    REQUISITOS.mandatos,
  );
  const sabeQueAlcanza = useAcciones(consorcio) !== null;
  const puede = usePuede(consorcio);
  const veMandatos = puede("mandatos.ver");
  const veDecisiones = puede("decisiones.ver");
  const veCandidatos = puede("mandatos.ver_candidatos");
  const registraDecisiones = consorcio !== "" && puede("decisiones.registrar");
  // El alta pide elegir a quién y qué decisión la respalda; la baja se da desde el detalle.
  const daAltas = puede("mandatos.alta") && veCandidatos && veDecisiones;
  const daBajas = puede("mandatos.baja") && veMandatos;
  // Nadie se da un mandato a sí mismo: quien mira no se ofrece, y sin saberlo decide la API.
  const yo = useMiPersona(daAltas);
  // Sin mandatos ni decisiones no queda nada, salvo que se sepa que puede registrar.
  const quedaElRegistro = sabeQueAlcanza && registraDecisiones;
  const [mandatos, setMandatos] = useState<Lectura<Mandato>>(CARGANDO);
  const [decisiones, setDecisiones] = useState<Lectura<Decision>>(CARGANDO);
  const [candidatos, setCandidatos] = useState<Lectura<Candidato>>(CARGANDO);
  const [detalle, setDetalle] = useState<Detalle | null>(null);
  // Los formularios se abren desde los dos botones de arriba, de a uno.
  const [abierto, setAbierto] = useState<FormularioAbierto | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  // Cada envío vuelve a pedir las listas: qué se ve lo decide la API.
  const [vuelta, setVuelta] = useState(0);
  const consultado = useRef(false);
  // Una clave por intento lógico de cada formulario: se conserva si el pedido se cae y se renueva
  // al cambiar los datos o cuando el intento terminó.
  const claveDecision = useRef<string | null>(null);
  const claveAlta = useRef<string | null>(null);
  const claveBaja = useRef<string | null>(null);
  // El campo con el foco al enviar: mientras el envío corre queda deshabilitado; al terminar el
  // foco va al resultado, salvo que la persona lo haya llevado a otra parte.
  const foco = useRef<HTMLElement | null>(null);
  const textoAviso = useRef<HTMLParagraphElement | null>(null);
  const tituloDetalle = useRef<HTMLHeadingElement | null>(null);
  // Cada pedido de detalle lleva su número: una respuesta que llega después de otro pedido, de
  // cerrar el detalle o de cambiar de edificio se descarta.
  const pedidoDetalle = useRef(0);
  // El botón «Ver detalle» que abrió el detalle: al cerrarlo, el foco vuelve ahí.
  const origenDetalle = useRef<HTMLElement | null>(null);
  const formularioBaja = useRef<HTMLFormElement | null>(null);
  const confirmarSi = useRef<HTMLButtonElement | null>(null);
  const botonBaja = useRef<HTMLButtonElement | null>(null);
  const botonDecision = useRef<HTMLButtonElement | null>(null);
  const botonAlta = useRef<HTMLButtonElement | null>(null);
  const tituloFormulario = useRef<HTMLHeadingElement | null>(null);

  // Formulario de decisión.
  const [tipoDecision, setTipoDecision] = useState<TipoDecision>(TIPO_DECISION_INICIAL);
  const [fechaDecision, setFechaDecision] = useState("");
  const [descripcionDecision, setDescripcionDecision] = useState("");
  const [referencia, setReferencia] = useState("");
  const [sha256, setSha256] = useState("");

  // Formulario de alta.
  const [tipoMandato, setTipoMandato] = useState<TipoMandato>(TIPO_MANDATO_INICIAL);
  const [persona, setPersona] = useState("");
  const [nombre, setNombre] = useState("");
  const [cuit, setCuit] = useState("");
  const [matricula, setMatricula] = useState("");
  const [decisionAlta, setDecisionAlta] = useState("");
  const [desde, setDesde] = useState("");
  const [hasta, setHasta] = useState("");

  // Formulario de baja (en el detalle del mandato).
  const [causa, setCausa] = useState<CausaBaja>("renuncia");
  const [descripcionBaja, setDescripcionBaja] = useState("");
  const [instrumento, setInstrumento] = useState("");
  const [decisionBaja, setDecisionBaja] = useState("");
  const [confirmarBaja, setConfirmarBaja] = useState(false);

  useEffect(() => {
    if (enviando || foco.current === null) return;
    // Solo si nadie tomó el foco en el medio (la persona pudo ir a otra parte de la página). El
    // campo que lo tenía pudo desmontarse (la baja cierra el detalle): el foco cae en el cuerpo.
    const ahora = document.activeElement;
    if (ahora === null || ahora === document.body || ahora === foco.current) (textoAviso.current ?? foco.current).focus();
    foco.current = null;
  }, [enviando]);

  // La confirmación de la baja: al abrirse, el foco va a «Sí»; al cerrarse con «No», vuelve al
  // botón que la abrió. Mientras se envía no se toca: al terminar, el foco va al aviso (efecto de
  // arriba, que corre antes).
  useEffect(() => {
    if (confirmarBaja) confirmarSi.current?.focus();
    else if (!enviando && document.activeElement === document.body) botonBaja.current?.focus();
  }, [confirmarBaja, enviando]);

  // Al abrir un detalle, el foco va a su título: el lector de pantalla anuncia el resultado.
  useEffect(() => {
    if (detalle !== null) tituloDetalle.current?.focus();
  }, [detalle]);

  const aEntrar = useAEntrar();

  const atajar = useCallback(
    (e: unknown) => {
      if (e instanceof SinSesion) aEntrar();
      else setAviso({ texto: SIN_CONEXION, ok: false });
    },
    [aEntrar],
  );

  const consultarEdificios = useCallback(() => {
    void api
      .GET("/api/v1/me/consorcios")
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data === undefined)
          setPantalla({ tipo: "sin_servicio", deConexion: esDeConexion(response.status, codigoDe(error)) });
        else if (data.length === 0) setPantalla({ tipo: "sin_consorcios" });
        else setPantalla({ tipo: "lista", consorcios: data });
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion) aEntrar();
        else setPantalla({ tipo: "sin_servicio", deConexion: true });
      });
  }, [aEntrar]);

  useEffect(() => {
    if (consultado.current) return;
    consultado.current = true;
    consultarEdificios();
  }, [consultarEdificios]);

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    const path = { consorcio_id: consorcio };
    void Promise.all([
      !veMandatos
        ? SIN_PERMISO_DE_LECTURA
        : cadaUna(api.GET("/api/v1/consorcios/{consorcio_id}/mandatos", { params: { path } }).then(leer<Mandato>)),
      !veDecisiones
        ? SIN_PERMISO_DE_LECTURA
        : cadaUna(api.GET("/api/v1/consorcios/{consorcio_id}/decisiones", { params: { path } }).then(leer<Decision>)),
      !veCandidatos
        ? SIN_PERMISO_DE_LECTURA
        : cadaUna(api.GET("/api/v1/consorcios/{consorcio_id}/mandatos/candidatos", { params: { path } }).then(leer<Candidato>)),
    ])
      .then(([m, d, c]) => {
        // Una respuesta del edificio anterior no pisa al que ya se eligió.
        if (!vigente) return;
        // Los candidatos solos no se muestran: sirven al alta, que pide también las decisiones.
        if (m.tipo === "sin_permiso" && d.tipo === "sin_permiso" && !quedaElRegistro) {
          navegar(RUTA_INICIO, { replace: true });
          return;
        }
        setMandatos(m);
        setDecisiones(d);
        setCandidatos(c);
        if ([m.tipo, d.tipo, c.tipo].includes("sin_servicio")) setAviso(sinElAvisoDeLaRed);
        if (c.tipo !== "ok") return;
        setPersona((actual) => {
          if (c.elementos.some((x) => x.persona_id === actual)) return actual;
          // Otra persona es otro intento: otra clave.
          claveAlta.current = null;
          return c.elementos[0]?.persona_id ?? "";
        });
      })
      .catch((e: unknown) => {
        if (vigente) atajar(e);
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, vuelta, atajar, navegar, veMandatos, veDecisiones, veCandidatos, quedaElRegistro]);

  useEffect(() => {
    if (abierto !== null) tituloFormulario.current?.focus();
  }, [abierto]);

  function cerrarFormulario() {
    (abierto === "alta" ? botonAlta : botonDecision).current?.focus();
    setAbierto(null);
  }

  /** Vuelve a pedir las listas: lo escrito en los formularios no se toca. */
  function reconsultarListas() {
    setMandatos(aReconsultar);
    setDecisiones(aReconsultar);
    setCandidatos(aReconsultar);
    setVuelta((v) => v + 1);
  }

  const hayListaCaida = [mandatos, decisiones, candidatos].some((l) => l.tipo === "sin_servicio");
  useAlVolverLaRed(hayListaCaida, () => {
    reconsultarListas();
    setAviso(conLaRedDeVuelta);
  });
  useAlVolverLaRed(pantalla.tipo === "sin_servicio", () => {
    setPantalla({ tipo: "cargando" });
    consultarEdificios();
  });

  /** Cualquier cambio en los datos de un formulario es otro intento: otra clave. */
  function cambiar<T>(clave: Clave, fijar: (v: T) => void) {
    return (v: T) => {
      clave.current = null;
      fijar(v);
    };
  }

  /** En la baja, además, un cambio cierra la confirmación: lo que se confirma es lo que se ve. */
  function cambiarBaja<T>(fijar: (v: T) => void) {
    return (v: T) => {
      setConfirmarBaja(false);
      cambiar(claveBaja, fijar)(v);
    };
  }

  function enviar<T>(
    clave: Clave,
    accion: Accion,
    pedido: (clave: string) => Promise<Resultado<T>>,
    listo: (datos: T) => void,
    fallo?: (estado: number | null, codigo: string | undefined) => void,
  ) {
    const activo = document.activeElement;
    foco.current = activo instanceof HTMLElement && activo !== document.body ? activo : null;
    clave.current ??= crypto.randomUUID();
    setEnviando(true);
    setAviso(null);
    void pedido(clave.current)
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data === undefined) {
          const codigo = codigoDe(error);
          if (renovarClave(response.status, codigo)) clave.current = null;
          setAviso({ texto: mensajeDeError(response.status, codigo, accion), ok: false, formulario: accion });
          fallo?.(response.status, codigo);
          return;
        }
        clave.current = null;
        listo(data);
        setVuelta((v) => v + 1);
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion) aEntrar();
        else setAviso({ texto: SIN_CONEXION, ok: false, formulario: accion });
        fallo?.(null, undefined);
      })
      .finally(() => setEnviando(false));
  }

  function registrarDecision(evento: FormEvent) {
    evento.preventDefault();
    enviar(
      claveDecision,
      "decision",
      (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/decisiones", {
          params: { path: { consorcio_id: consorcio }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          headers: encabezadoCsrf(),
          body: {
            tipo: tipoDecision,
            fecha: fechaDecision,
            descripcion: descripcionDecision.trim(),
            referencia: referencia.trim(),
            sha256: sha256.trim() === "" ? null : sha256.trim(),
          },
        }),
      (datos) => {
        // Con el formulario vacío no se puede registrar dos veces lo mismo con una clave nueva.
        setTipoDecision(TIPO_DECISION_INICIAL);
        setFechaDecision("");
        setDescripcionDecision("");
        setReferencia("");
        setSha256("");
        const hecho = "Listo: registramos la decisión.";
        setAviso({ texto: hecho, ok: true });
        setAbierto(null);
        origenDetalle.current = botonDecision.current;
        if (veDecisiones) pedirDecision(datos.id, hecho);
      },
    );
  }

  const externo = tipoMandato === "administrador_externo";
  const elegibles = candidatos.tipo === "ok" ? candidatos.elementos.filter((c) => c.persona_id !== yo) : [];
  // Saber quién mira puede llegar después de la lista: lo elegido nunca es alguien que no se ofrece.
  const personaElegida = elegibles.some((c) => c.persona_id === persona) ? persona : (elegibles[0]?.persona_id ?? "");

  function darDeAlta(evento: FormEvent) {
    evento.preventDefault();
    enviar(
      claveAlta,
      "alta",
      (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/mandatos", {
          params: { path: { consorcio_id: consorcio }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          headers: encabezadoCsrf(),
          body: {
            tipo: tipoMandato,
            decision_id: decisionAlta,
            desde,
            hasta: hasta === "" ? null : hasta,
            persona_id: externo ? null : personaElegida,
            nombre: externo ? nombre.trim() : null,
            cuit: externo ? cuit : null,
            matricula: tipoMandato === "miembro_consejo" || matricula.trim() === "" ? null : matricula.trim(),
          },
        }),
      (datos) => {
        // Con el formulario vacío no se puede dar dos veces la misma alta con una clave nueva.
        setTipoMandato(TIPO_MANDATO_INICIAL);
        setPersona("");
        setNombre("");
        setCuit("");
        setMatricula("");
        setDecisionAlta("");
        setDesde("");
        setHasta("");
        const hecho = "Listo: el mandato quedó dado de alta.";
        setAviso({ texto: hecho, ok: true });
        setAbierto(null);
        origenDetalle.current = botonAlta.current;
        if (veMandatos) pedirMandato(datos.id, hecho);
      },
    );
  }

  function limpiarBaja() {
    claveBaja.current = null;
    setConfirmarBaja(false);
    setDescripcionBaja("");
    setInstrumento("");
    setDecisionBaja("");
  }

  function cerrarDetalle() {
    pedidoDetalle.current += 1;
    setDetalle(null);
    origenDetalle.current?.focus();
  }

  function darDeBaja(mandato: MandatoDetalle) {
    // «Sí» envía sin pasar por el envío del formulario: la validación del navegador va acá.
    if (formularioBaja.current !== null && !formularioBaja.current.reportValidity()) {
      setConfirmarBaja(false);
      return;
    }
    const remocion = causa === "remocion";
    enviar(
      claveBaja,
      "baja",
      (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/mandatos/{mandato_id}/baja", {
          params: {
            path: { consorcio_id: consorcio, mandato_id: mandato.id },
            header: { [ENCABEZADO_IDEMPOTENCIA]: clave },
          },
          headers: encabezadoCsrf(),
          body: {
            causa,
            descripcion: descripcionBaja.trim(),
            decision_id: remocion ? decisionBaja : null,
            instrumento: remocion ? null : instrumento.trim(),
          },
        }),
      () => {
        limpiarBaja();
        pedidoDetalle.current += 1;
        setDetalle(null);
        setAviso({ texto: `Listo: dimos de baja el mandato de ${mandato.nombre}.`, ok: true });
      },
      (estado, codigo) => {
        // Un error cierra la confirmación: el reintento se vuelve a confirmar.
        setConfirmarBaja(false);
        if (estado === ESTADO.noEncontrado || (codigo !== undefined && BAJA_SOBRE_ESTADO_VIEJO.includes(codigo))) {
          pedidoDetalle.current += 1;
          setDetalle(null);
          // El formulario se va con el detalle: el aviso sube para que siga a la vista.
          setAviso((actual) => (actual === null ? actual : { texto: actual.texto, ok: actual.ok }));
          setVuelta((v) => v + 1);
        }
      },
    );
  }

  /**
   * Un detalle que no llega. Si se abría solo después de crear (`hecho`), lo creado ya está: el aviso
   * sigue siendo el «Listo» y dice dónde verlo.
   */
  function falloDetalle(texto: string, hecho: string | null) {
    setAviso(hecho === null ? { texto, ok: false } : { texto: `${hecho} No pudimos abrir su detalle: buscalo en la lista.`, ok: true });
  }

  function verMandato(mandato: Mandato) {
    const activo = document.activeElement;
    origenDetalle.current = activo instanceof HTMLElement ? activo : null;
    setAviso(null);
    pedirMandato(mandato.id);
  }

  function pedirMandato(mandato: string, hecho: string | null = null) {
    const numero = ++pedidoDetalle.current;
    // El detalle anterior (y su baja) no queda operable mientras llega el que se eligió.
    setDetalle(null);
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/mandatos/{mandato_id}", {
        params: { path: { consorcio_id: consorcio, mandato_id: mandato } },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (numero !== pedidoDetalle.current) return;
        if (data !== undefined) {
          // Lo escrito para otro mandato no pasa a este.
          limpiarBaja();
          setCausa(data.estado === "terminado" ? "error_carga" : "renuncia");
          setDetalle({ tipo: "mandato", datos: data });
          return;
        }
        if (response.status === ESTADO.sinPermiso) falloDetalle("No tenés permiso para ver ese mandato.", hecho);
        else if (response.status === ESTADO.noEncontrado) {
          falloDetalle("Ese mandato ya no está en este edificio.", hecho);
          setVuelta((v) => v + 1);
        } else falloDetalle(mensajeDeFalla(response.status, codigoDe(error)), hecho);
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion) aEntrar();
        else if (numero === pedidoDetalle.current) falloDetalle(SIN_CONEXION, hecho);
      });
  }

  function verDecision(decision: Decision) {
    const activo = document.activeElement;
    origenDetalle.current = activo instanceof HTMLElement ? activo : null;
    setAviso(null);
    pedirDecision(decision.id);
  }

  function pedirDecision(decision: string, hecho: string | null = null) {
    const numero = ++pedidoDetalle.current;
    // El detalle anterior (y su baja) no queda operable mientras llega el que se eligió.
    setDetalle(null);
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/decisiones/{decision_id}", {
        params: { path: { consorcio_id: consorcio, decision_id: decision } },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (numero !== pedidoDetalle.current) return;
        if (data !== undefined) {
          setDetalle({ tipo: "decision", datos: data });
          return;
        }
        if (response.status === ESTADO.sinPermiso) falloDetalle("No tenés permiso para ver esa decisión.", hecho);
        else if (response.status === ESTADO.noEncontrado) {
          falloDetalle("Esa decisión ya no está en este edificio.", hecho);
          setVuelta((v) => v + 1);
        } else falloDetalle(mensajeDeFalla(response.status, codigoDe(error)), hecho);
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion) aEntrar();
        else if (numero === pedidoDetalle.current) falloDetalle(SIN_CONEXION, hecho);
      });
  }

  function elegirEdificio(valor: string) {
    // Nada del edificio anterior queda elegible mientras llegan los datos del nuevo.
    for (const clave of [claveDecision, claveAlta, claveBaja]) clave.current = null;
    pedidoDetalle.current += 1;
    setMandatos(CARGANDO);
    setDecisiones(CARGANDO);
    setCandidatos(CARGANDO);
    setDetalle(null);
    setAbierto(null);
    setDecisionAlta("");
    limpiarBaja();
    setPersona("");
    setAviso(null);
    setConsorcio(valor);
  }

  // El alta pide elegir a quién (`candidatos`) y qué decisión la respalda: sin una de las dos no se puede dar.
  const ofreceAlta = puede("mandatos.alta") && candidatos.tipo !== "sin_permiso" && decisiones.tipo !== "sin_permiso";
  const listaDecisiones = decisiones.tipo === "ok" ? decisiones.elementos : [];
  // A15: quien registró una decisión no la usa para dar de alta ni para remover. Las del equipo no lo
  // activan. Solo los selectores: el detalle y la lista siguen mostrando todas.
  const usables = listaDecisiones.filter((d) => !d.registrada_por_vos);
  const asambleas = usables.filter((d) => d.tipo === "asamblea");
  // Sin una asamblea que usar no hay remoción. Sin la lista todavía no se sabe y sigue a la vista.
  const sinRemocion = decisiones.tipo === "ok" && asambleas.length === 0 && causa !== "remocion";
  const textoDecision = (d: Decision) => `${TEXTO_TIPO_DECISION[d.tipo]} del ${dia(d.fecha)}: ${d.descripcion}`;
  const conMensaje = (aviso !== null && aviso.formulario === undefined) || pantalla.tipo !== "lista";
  /** El aviso en su lugar: arriba el de la página y junto a su formulario el de un envío rechazado. */
  const avisoEn = (formulario?: Accion) =>
    aviso !== null && aviso.formulario === formulario ? (
      <p ref={textoAviso} tabIndex={-1} role={formulario === undefined ? undefined : "alert"} className={aviso.ok ? "aviso-ok" : "aviso-error"}>
        {aviso.texto}
      </p>
    ) : null;
  // Hasta tres listas pueden caerse a la vez: cada botón dice de qué sección es.
  const consultarDeNuevo = (seccion: string) => (
    <button type="button" onClick={reconsultarListas} disabled={enviando} aria-describedby={seccion}>
      Consultar de nuevo
    </button>
  );

  return (
    <>
      <h1>Mandatos y decisiones</h1>

      <div role="status" aria-live="polite" className={conMensaje ? "tarjeta" : undefined}>
        {pantalla.tipo === "cargando" && <p>Consultando…</p>}
        {pantalla.tipo === "sin_servicio" && (
          <>
            <p className="aviso-error">
              {pantalla.deConexion
                ? "No pudimos consultar tus edificios. Revisá la conexión y volvé en un rato."
                : `No pudimos consultar tus edificios. ${SERVICIO_NO_DISPONIBLE}`}
            </p>
            <button
              type="button"
              onClick={() => {
                setPantalla({ tipo: "cargando" });
                consultarEdificios();
              }}
            >
              Consultar de nuevo
            </button>
          </>
        )}
        {pantalla.tipo === "sin_consorcios" && <p>Todavía no participás de ningún edificio.</p>}
        {avisoEn()}
      </div>

      {pantalla.tipo === "lista" && (
        <>
          <p className="tarjeta">{presentacion({ veMandatos, veDecisiones, registra: registraDecisiones, daAltas, daBajas })}</p>

          {edificios.length > 1 && (
            <div className="tarjeta formulario">
              <label htmlFor={`${id}-consorcio`}>Edificio</label>
              <select
                id={`${id}-consorcio`}
                value={consorcio}
                disabled={enviando}
                onChange={(e) => elegirEdificio(e.target.value)}
              >
                {edificios.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.nombre}
                  </option>
                ))}
              </select>
            </div>
          )}

          {(ofreceAlta || registraDecisiones) && (
            <p className="acciones-pantalla">
              {ofreceAlta && (
                <button
                  type="button"
                  ref={botonAlta}
                  aria-expanded={abierto === "alta"}
                  disabled={enviando}
                  onClick={() => setAbierto(abierto === "alta" ? null : "alta")}
                >
                  Dar de alta un mandato
                </button>
              )}
              {registraDecisiones && (
                <button
                  type="button"
                  ref={botonDecision}
                  className="secundario"
                  aria-expanded={abierto === "decision"}
                  disabled={enviando}
                  onClick={() => setAbierto(abierto === "decision" ? null : "decision")}
                >
                  Registrar una decisión
                </button>
              )}
            </p>
          )}

          {registraDecisiones && abierto === "decision" && (
          <section aria-labelledby={`${id}-nueva-decision`} className="tarjeta">
            <h2 id={`${id}-nueva-decision`} ref={tituloFormulario} tabIndex={-1}>
              Registrar una decisión
            </h2>
            <form className="formulario formulario-movil" onSubmit={registrarDecision}>
              <label htmlFor={`${id}-tipo-decision`}>Tipo</label>
              <select
                id={`${id}-tipo-decision`}
                value={tipoDecision}
                disabled={enviando}
                onChange={(e) => cambiar(claveDecision, setTipoDecision)(e.target.value as TipoDecision)}
              >
                {TIPOS_DECISION.map((t) => (
                  <option key={t} value={t}>
                    {TEXTO_TIPO_DECISION[t]}
                  </option>
                ))}
              </select>
              <label htmlFor={`${id}-fecha-decision`}>Fecha del acto</label>
              <input
                id={`${id}-fecha-decision`}
                type="date"
                required
                disabled={enviando}
                value={fechaDecision}
                onChange={(e) => cambiar(claveDecision, setFechaDecision)(e.target.value)}
              />
              <label htmlFor={`${id}-descripcion-decision`}>Qué se decidió</label>
              <TextoDeUnaLinea
                id={`${id}-descripcion-decision`}
                rows={FILAS_DESCRIPCION}
                autoComplete="off"
                maxLength={LARGO_DESCRIPCION}
                required
                disabled={enviando}
                value={descripcionDecision}
                onValor={cambiar(claveDecision, setDescripcionDecision)}
              />
              <label htmlFor={`${id}-referencia`}>Dónde consta (libro, acta y folio)</label>
              <TextoDeUnaLinea
                id={`${id}-referencia`}
                rows={FILAS_REFERENCIA}
                autoComplete="off"
                maxLength={LARGO_REFERENCIA}
                required
                disabled={enviando}
                value={referencia}
                onValor={cambiar(claveDecision, setReferencia)}
              />
              <label htmlFor={`${id}-sha256`}>Huella SHA-256 del acta escaneada (opcional)</label>
              <input
                id={`${id}-sha256`}
                type="text"
                autoComplete="off"
                pattern={PATRON_SHA256}
                spellCheck={false}
                autoCapitalize="none"
                disabled={enviando}
                value={sha256}
                onChange={(e) => cambiar(claveDecision, setSha256)(e.target.value)}
              />
              <button type="submit" disabled={enviando}>
                Registrar decisión
              </button>
            </form>
            {avisoEn("decision")}
            <p>
              <button type="button" className="secundario" onClick={cerrarFormulario} disabled={enviando}>
                Cancelar
              </button>
            </p>
          </section>
          )}

          {ofreceAlta && abierto === "alta" && (
          <section aria-labelledby={`${id}-alta`} className="tarjeta">
            <h2 id={`${id}-alta`} ref={tituloFormulario} tabIndex={-1}>
              Dar de alta un mandato
            </h2>
            {candidatos.tipo === "cargando" && <p>Consultando…</p>}
            {candidatos.tipo === "sin_servicio" && (
              <>
                <p>No pudimos consultar quiénes pueden recibir un mandato.</p>
                {consultarDeNuevo(`${id}-alta`)}
              </>
            )}
            {candidatos.tipo === "ok" && decisiones.tipo === "sin_servicio" && (
              <>
                <p>Para dar un alta hace falta elegir la decisión que la respalda, y no pudimos consultarlas.</p>
                {consultarDeNuevo(`${id}-alta`)}
              </>
            )}
            {candidatos.tipo === "ok" && decisiones.tipo === "ok" && (
              <form className="formulario formulario-movil" onSubmit={darDeAlta}>
                <label htmlFor={`${id}-tipo-mandato`}>Tipo de mandato</label>
                <select
                  id={`${id}-tipo-mandato`}
                  value={tipoMandato}
                  disabled={enviando}
                  onChange={(e) => cambiar(claveAlta, setTipoMandato)(e.target.value as TipoMandato)}
                >
                  {TIPOS_MANDATO.map((t) => (
                    <option key={t} value={t}>
                      {TEXTO_TIPO_MANDATO[t]}
                    </option>
                  ))}
                </select>
                {externo ? (
                  <>
                    <p>
                      La administración externa se carga por su razón social y su CUIT, sin cuenta: la
                      cuenta se asocia después, por invitación.
                    </p>
                    <label htmlFor={`${id}-nombre`}>Razón social o nombre</label>
                    <input
                      id={`${id}-nombre`}
                      type="text"
                      autoComplete="off"
                      maxLength={LARGO_NOMBRE}
                      pattern={PATRON_TEXTO}
                      required
                      disabled={enviando}
                      value={nombre}
                      onChange={(e) => cambiar(claveAlta, setNombre)(e.target.value)}
                    />
                    <label htmlFor={`${id}-cuit`}>CUIT (11 números, sin guiones)</label>
                    <input
                      id={`${id}-cuit`}
                      type="text"
                      inputMode="numeric"
                      autoComplete="off"
                      pattern={PATRON_CUIT}
                      required
                      disabled={enviando}
                      value={cuit}
                      onChange={(e) => cambiar(claveAlta, setCuit)(e.target.value)}
                    />
                  </>
                ) : elegibles.length === 0 ? (
                  <p>
                    {candidatos.elementos.length === 0
                      ? "Nadie tiene todavía un vínculo confirmado en este edificio: sin él no se puede dar el mandato."
                      : "Nadie más tiene un vínculo confirmado en este edificio, y nadie se da un mandato a sí mismo."}
                  </p>
                ) : (
                  <>
                    <label htmlFor={`${id}-persona`}>Persona (con vínculo confirmado en el edificio)</label>
                    <select
                      id={`${id}-persona`}
                      value={personaElegida}
                      required
                      disabled={enviando}
                      onChange={(e) => cambiar(claveAlta, setPersona)(e.target.value)}
                    >
                      {elegibles.map((c) => (
                        <option key={c.persona_id} value={c.persona_id}>
                          {recortar(`${c.nombre} (${c.unidades.join(", ")})`)}
                        </option>
                      ))}
                    </select>
                  </>
                )}
                {tipoMandato !== "miembro_consejo" && (
                  <>
                    <label htmlFor={`${id}-matricula`}>Matrícula (opcional)</label>
                    <input
                      id={`${id}-matricula`}
                      type="text"
                      autoComplete="off"
                      maxLength={LARGO_MATRICULA}
                      pattern={PATRON_TEXTO}
                      disabled={enviando}
                      value={matricula}
                      onChange={(e) => cambiar(claveAlta, setMatricula)(e.target.value)}
                    />
                  </>
                )}
                {usables.length === 0 ? (
                  <p>
                    {listaDecisiones.length === 0
                      ? "Todavía no hay decisiones registradas: sin una que lo respalde no se puede dar el mandato."
                      : "Las decisiones de este edificio las registraste vos, y quien registra una decisión no la usa: el alta la tiene que dar otra persona del Consejo o de la administración."}
                  </p>
                ) : (
                  <>
                    <label htmlFor={`${id}-decision-alta`}>Decisión que lo respalda</label>
                    <select
                      id={`${id}-decision-alta`}
                      value={decisionAlta}
                      required
                      disabled={enviando}
                      onChange={(e) => cambiar(claveAlta, setDecisionAlta)(e.target.value)}
                    >
                      <option value="">Elegí la decisión</option>
                      {usables.map((d) => (
                        <option key={d.id} value={d.id}>
                          {recortar(textoDecision(d))}
                        </option>
                      ))}
                    </select>
                  </>
                )}
                <label htmlFor={`${id}-desde`}>Desde</label>
                <input
                  id={`${id}-desde`}
                  type="date"
                  required
                  disabled={enviando}
                  value={desde}
                  onChange={(e) => cambiar(claveAlta, setDesde)(e.target.value)}
                />
                <label htmlFor={`${id}-hasta`}>Hasta (opcional; ese día ya no rige)</label>
                <input
                  id={`${id}-hasta`}
                  type="date"
                  min={desde === "" ? undefined : desde}
                  disabled={enviando}
                  aria-describedby={`${id}-vigencia-vacia`}
                  value={hasta}
                  onChange={(e) => cambiar(claveAlta, setHasta)(e.target.value)}
                />
                <p id={`${id}-vigencia-vacia`} aria-live="polite">
                  {hasta !== "" &&
                    hasta === desde &&
                    "Con «hasta» igual a «desde» el mandato nunca rige: el último día es el anterior a «hasta»."}
                </p>
                {(externo || elegibles.length > 0) && usables.length > 0 && (
                  <button type="submit" disabled={enviando}>
                    Dar de alta
                  </button>
                )}
              </form>
            )}
            {avisoEn("alta")}
            <p>
              <button type="button" className="secundario" onClick={cerrarFormulario} disabled={enviando}>
                Cancelar
              </button>
            </p>
          </section>
          )}

          {mandatos.tipo !== "sin_permiso" && (
          <section aria-labelledby={`${id}-mandatos`} className="tarjeta">
            <h2 id={`${id}-mandatos`}>Mandatos</h2>
            {mandatos.tipo === "cargando" && <p>Consultando…</p>}
            {mandatos.tipo === "sin_servicio" && (
              <>
                <p>No pudimos consultar los mandatos.</p>
                {consultarDeNuevo(`${id}-mandatos`)}
              </>
            )}
            {mandatos.tipo === "ok" && mandatos.elementos.length === 0 && <p>Todavía no hay mandatos cargados.</p>}
            {mandatos.tipo === "ok" && mandatos.elementos.length > 0 && (
              <ul className="sesiones">
                {mandatos.elementos.map((m) => (
                  <li key={m.id}>
                    <h3 id={`${id}-${m.id}`}>
                      {m.nombre}, {TEXTO_TIPO_MANDATO[m.tipo]}
                    </h3>
                    <dl className="detalles">
                      <dt>Estado</dt>
                      <dd>{TEXTO_ESTADO_MANDATO[m.estado]}</dd>
                      <dt>Desde</dt>
                      <dd>{dia(m.vigente_desde)}</dd>
                      {m.vigente_hasta && (
                        <>
                          <dt>Hasta</dt>
                          <dd>{dia(m.vigente_hasta)} (ese día ya no rige)</dd>
                        </>
                      )}
                      {m.cuit && (
                        <>
                          <dt>CUIT</dt>
                          <dd>{m.cuit}</dd>
                        </>
                      )}
                      <dt>Cuenta asociada</dt>
                      <dd>{m.con_cuenta ? "Sí" : "No"}</dd>
                    </dl>
                    <button type="button" className="secundario" onClick={() => verMandato(m)} disabled={enviando} aria-describedby={`${id}-${m.id}`}>
                      Ver detalle
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
          )}

          {decisiones.tipo !== "sin_permiso" && (
          <section aria-labelledby={`${id}-decisiones`} className="tarjeta">
            <h2 id={`${id}-decisiones`}>Decisiones</h2>
            {decisiones.tipo === "cargando" && <p>Consultando…</p>}
            {decisiones.tipo === "sin_servicio" && (
              <>
                <p>No pudimos consultar las decisiones.</p>
                {consultarDeNuevo(`${id}-decisiones`)}
              </>
            )}
            {decisiones.tipo === "ok" && decisiones.elementos.length === 0 && <p>Todavía no hay decisiones registradas.</p>}
            {decisiones.tipo === "ok" && decisiones.elementos.length > 0 && (
              <ul className="sesiones">
                {decisiones.elementos.map((d) => (
                  <li key={d.id}>
                    <h3 id={`${id}-${d.id}`}>
                      {TEXTO_TIPO_DECISION[d.tipo]} del {dia(d.fecha)}
                    </h3>
                    <p>{d.descripcion}</p>
                    <button type="button" className="secundario" onClick={() => verDecision(d)} disabled={enviando} aria-describedby={`${id}-${d.id}`}>
                      Ver detalle
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
          )}

          {detalle?.tipo === "decision" && (
            <section aria-labelledby={`${id}-detalle`} className="tarjeta">
              <h2 id={`${id}-detalle`} ref={tituloDetalle} tabIndex={-1}>
                {TEXTO_TIPO_DECISION[detalle.datos.tipo]} del {dia(detalle.datos.fecha)}
              </h2>
              <dl className="detalles">
                <dt>Qué se decidió</dt>
                <dd>{detalle.datos.descripcion}</dd>
                <dt>Dónde consta</dt>
                <dd>{detalle.datos.referencia}</dd>
                {detalle.datos.sha256 && (
                  <>
                    <dt>Huella del acta</dt>
                    <dd>{detalle.datos.sha256}</dd>
                  </>
                )}
                <dt>Quién la registró</dt>
                <dd>
                  {detalle.datos.registrada_por_vos
                    ? "Vos: no podés usarla para dar de alta ni para remover."
                    : detalle.datos.del_equipo
                      ? "El equipo de la plataforma."
                      : "Otra persona del edificio."}
                </dd>
              </dl>
              <button type="button" className="secundario" onClick={cerrarDetalle} disabled={enviando}>
                Cerrar el detalle
              </button>
            </section>
          )}

          {detalle?.tipo === "mandato" && (
            <section aria-labelledby={`${id}-detalle`} className="tarjeta">
              <h2 id={`${id}-detalle`} ref={tituloDetalle} tabIndex={-1}>
                {detalle.datos.nombre}, {TEXTO_TIPO_MANDATO[detalle.datos.tipo]}
              </h2>
              <dl className="detalles">
                <dt>Estado</dt>
                <dd>{TEXTO_ESTADO_MANDATO[detalle.datos.estado]}</dd>
                <dt>Desde</dt>
                <dd>{dia(detalle.datos.vigente_desde)}</dd>
                {detalle.datos.vigente_hasta && (
                  <>
                    <dt>Hasta</dt>
                    <dd>{dia(detalle.datos.vigente_hasta)} (ese día ya no rige)</dd>
                  </>
                )}
                {detalle.datos.cuit && (
                  <>
                    <dt>CUIT</dt>
                    <dd>{detalle.datos.cuit}</dd>
                  </>
                )}
                {detalle.datos.matricula && (
                  <>
                    <dt>Matrícula</dt>
                    <dd>{detalle.datos.matricula}</dd>
                  </>
                )}
                <dt>Decisión que lo respalda</dt>
                <dd>
                  {(() => {
                    const d = listaDecisiones.find((x) => x.id === detalle.datos.decision_id);
                    return d ? textoDecision(d) : "No figura entre las decisiones que podés ver.";
                  })()}
                </dd>
                <dt>Facultades</dt>
                <dd>
                  <ul>
                    {detalle.datos.facultades.map((f) => (
                      <li key={f}>{nombreDeFacultad(f)}</li>
                    ))}
                  </ul>
                </dd>
                {detalle.datos.causa_baja && (
                  <>
                    <dt>Causa de la baja</dt>
                    <dd>{TEXTO_CAUSA[detalle.datos.causa_baja]}</dd>
                  </>
                )}
                {detalle.datos.finalizado_en && (
                  <>
                    <dt>Baja registrada</dt>
                    <dd>{momento(detalle.datos.finalizado_en)}</dd>
                  </>
                )}
                {detalle.datos.motivo_fin && (
                  <>
                    <dt>Motivo</dt>
                    <dd>{detalle.datos.motivo_fin}</dd>
                  </>
                )}
                {detalle.datos.baja_decision_id && (
                  <>
                    <dt>Asamblea que lo removió</dt>
                    <dd>
                      {(() => {
                        const d = listaDecisiones.find((x) => x.id === detalle.datos.baja_decision_id);
                        return d ? textoDecision(d) : "No figura entre las decisiones que podés ver.";
                      })()}
                    </dd>
                  </>
                )}
                {detalle.datos.instrumento_referencia && (
                  <>
                    <dt>Instrumento</dt>
                    <dd>{detalle.datos.instrumento_referencia}</dd>
                  </>
                )}
              </dl>

              {ESTADOS_CON_BAJA.includes(detalle.datos.estado) && puede("mandatos.baja") && !detalle.datos.es_propio && (
                <form
                  ref={formularioBaja}
                  className="formulario formulario-movil"
                  onSubmit={(e) => {
                    e.preventDefault();
                    setConfirmarBaja(true);
                  }}
                >
                  <h3>Dar de baja</h3>
                  <label htmlFor={`${id}-causa`}>Causa</label>
                  <select
                    id={`${id}-causa`}
                    value={causa}
                    disabled={enviando}
                    onChange={(e) => cambiarBaja(setCausa)(e.target.value as CausaBaja)}
                  >
                    {(detalle.datos.estado === "terminado" ? CAUSAS_TERMINADO : CAUSAS).filter((c) => c !== "remocion" || !sinRemocion).map((c) => (
                      <option key={c} value={c}>
                        {TEXTO_CAUSA[c]}
                      </option>
                    ))}
                  </select>
                  <label htmlFor={`${id}-descripcion-baja`}>Por qué termina</label>
                  <input
                    id={`${id}-descripcion-baja`}
                    type="text"
                    autoComplete="off"
                    maxLength={LARGO_DESCRIPCION}
                    pattern={PATRON_TEXTO}
                    required
                    disabled={enviando}
                    value={descripcionBaja}
                    onChange={(e) => cambiarBaja(setDescripcionBaja)(e.target.value)}
                  />
                  {causa === "remocion" ? (
                    <>
                      <label htmlFor={`${id}-decision-baja`}>Asamblea que lo remueve</label>
                      <select
                        id={`${id}-decision-baja`}
                        value={decisionBaja}
                        required
                        disabled={enviando}
                        onChange={(e) => cambiarBaja(setDecisionBaja)(e.target.value)}
                      >
                        <option value="">Elegí la asamblea</option>
                        {asambleas.map((d) => (
                          <option key={d.id} value={d.id}>
                            {recortar(textoDecision(d))}
                          </option>
                        ))}
                      </select>
                    </>
                  ) : (
                    <>
                      <label htmlFor={`${id}-instrumento`}>Instrumento (nota, certificado u otro)</label>
                      <input
                        id={`${id}-instrumento`}
                        type="text"
                        autoComplete="off"
                        maxLength={LARGO_REFERENCIA}
                        pattern={PATRON_TEXTO}
                        required
                        disabled={enviando}
                        value={instrumento}
                        onChange={(e) => cambiarBaja(setInstrumento)(e.target.value)}
                      />
                    </>
                  )}
                  {confirmarBaja ? (
                    <div role="group" aria-labelledby={`${id}-confirmar-baja`}>
                      <p id={`${id}-confirmar-baja`}>
                        ¿Dar de baja el mandato de {detalle.datos.nombre}? La baja no se puede deshacer.
                      </p>
                      <button type="button" ref={confirmarSi} onClick={() => darDeBaja(detalle.datos)} disabled={enviando}>
                        Sí, dar de baja
                      </button>{" "}
                      <button type="button" className="secundario" onClick={() => setConfirmarBaja(false)} disabled={enviando}>
                        No
                      </button>
                    </div>
                  ) : (
                    <button type="submit" ref={botonBaja} disabled={enviando}>
                      Dar de baja
                    </button>
                  )}
                </form>
              )}
              {avisoEn("baja")}
              <button type="button" className="secundario" onClick={cerrarDetalle} disabled={enviando}>
                Cerrar el detalle
              </button>
            </section>
          )}

        </>
      )}
    </>
  );
}
