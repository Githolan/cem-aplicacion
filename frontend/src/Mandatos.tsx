import { type FormEvent, type MutableRefObject, useCallback, useEffect, useId, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";

import { api } from "./api/cliente";
import { CampoCuit, cuitConError } from "./CampoCuit";
import type { components } from "./api/tipos";
import { encabezadoCsrf } from "./csrf";
import { ErrorDeCampo, type ErroresDeCampo, erroresDe, marcaDe, resumenDe, REVISAR_FORMULARIO, sinElDe, soloDe } from "./ErrorDeCampo";
import { RUTA_INICIO } from "./enlace";
import { FilaDeRegistro } from "./FichaDeRegistro";
import { useAEntrar } from "./sesionEncima";
import { conLaRedDeVuelta, sinElAvisoDeLaRed } from "./egresos/comun";
import { useAlVolverLaRed, useMiPersona } from "./egresos/useConsorcio";
import { nombreDeFacultad } from "./facultades";
import { codigoDe, esDeConexion, mensajeDeFalla, motivoDeSeparacion, SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "./fallas";
import { REQUISITOS, useAcciones, useEdificioElegido, usePuede } from "./permisos";
import { TextoDeUnaLinea } from "./TextoDeUnaLinea";
import { RUTA_DOCUMENTOS } from "./Documentos";
import { VisorDocumento } from "./VisorDocumento";

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
type Documento = components["schemas"]["DocumentoEnLista"];
type InvitacionMandato = components["schemas"]["InvitacionMandato"];

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
/** El mismo tope que el contrato (`LARGO_MAXIMO_CORREO`). */
const LARGO_CORREO = 254;
/** Los estados en que un mandato externo sin cuenta admite la invitación (`mandato_no_vigente`). */
const ESTADOS_CON_INVITACION: EstadoMandato[] = ["vigente", "futuro"];
/** La versión de un documento que puede ser el acta: la disponible más nueva. */
const disponibleDe = (d: Documento) => d.versiones.find((v) => v.estado === "disponible");
/** Cuánto de la descripción entra en una opción: el ancho de un `<select>` es el de su opción más larga. */
const LARGO_OPCION = 60;
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
export type Accion = "decision" | "alta" | "baja" | "revision" | "invitar" | "aprobar_invitacion" | "retirar_invitacion";

const SIN_PERMISO: Record<Accion, string> = {
  decision: "Esta cuenta no tiene permiso para registrar decisiones en este edificio.",
  alta: "El mandato de esta cuenta no incluye dar este alta.",
  baja: "El mandato de esta cuenta no incluye dar esta baja.",
  revision: "El mandato de esta cuenta no incluye mantener este mandato.",
  invitar: "El mandato de esta cuenta no incluye invitar a la administración.",
  aprobar_invitacion: "El mandato de esta cuenta no incluye aprobar esta invitación.",
  retirar_invitacion: "El mandato de esta cuenta no incluye retirar esta invitación.",
};

const NO_ENCONTRADO: Record<Accion, string> = {
  decision: "No se encontró el edificio, o esta cuenta ya no tiene acceso.",
  alta: "No se encontró el edificio, o esta cuenta ya no tiene acceso.",
  baja: "No se encontró ese mandato en este edificio.",
  revision: "No se encontró ese mandato en este edificio.",
  invitar: "No se encontró ese mandato en este edificio.",
  aprobar_invitacion: "La invitación cambió o se retiró mientras tanto: la lista ya está actualizada.",
  retirar_invitacion: "La invitación ya no está en este edificio: la lista ya está actualizada.",
};

/** El estado de un mandato del Consejo que dejó de cumplir la condición para integrarlo (G-41). */
export const CAUSA_REVISION =
  "La persona dejó de cumplir las condiciones para integrar el Consejo (por ejemplo, ya no tiene una unidad confirmada en el edificio). Mientras tanto, el mandato no autoriza actos nuevos. Si vuelve a cumplirlas, una asamblea puede decidir mantenerlo desde una fecha; si no, se da de baja.";
/** Antes del formulario del alta, si no hay una decisión que se pueda usar (G-42). */
export const ALTA_SIN_DECISION_USABLE =
  "Para dar de alta un mandato hace falta una decisión registrada por otra persona: quien registra una decisión no la usa para dar el alta.";
/** En el formulario de la decisión: quién da el alta después (G-42). */
export const QUIEN_DA_EL_ALTA = "Quien registra la decisión no puede usarla para dar el alta: lo hace otra persona.";
/** Lo que pasa con la invitación de la administración externa (G-40, A7: cuatro ojos). */
export const AYUDA_INVITAR =
  "Después otra persona del Consejo o de la administración la aprueba; recién entonces sale el correo con el enlace.";

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
  facultad_fuera_de_paquete: "Algún permiso no corresponde a ese tipo de mandato.",
  respaldo_invalido: "La remoción se respalda con una asamblea; cualquier otra causa, con el documento que lo prueba.",
  documento_invalido: "El acta elegida se retiró de Documentos: no respalda una decisión. Elegir otra.",
  mandato_no_pendiente: "El mandato ya no está pendiente de revisión: la lista ya está actualizada.",
  revision_duplicada: "El mandato ya tiene una revisión desde ese día.",
  eficaz_desde_invalida: "La fecha tiene que ser hoy o después, no antes de la asamblea, y dentro del mandato.",
  sin_condicion_habilitante:
    "De hoy a esa fecha la persona no cumple sin interrupción una condición para integrar el Consejo.",
  mandato_ya_asociado: "El mandato ya tiene una cuenta asociada.",
  mandato_no_vigente: "El mandato terminó o tiene una baja: no admite una cuenta.",
  mandato_no_asociable: "Solo la cuenta de una administración externa se asocia por invitación.",
  invitacion_no_pendiente: "La invitación ya se aprobó o se aceptó: la lista ya está actualizada.",
};

/** Cómo se nombra cada campo de cada formulario en el resumen del error (G-49). */
const NOMBRES_CAMPO: Record<Accion, Record<string, string>> = {
  decision: {
    tipo: "el tipo",
    fecha: "la fecha del acto",
    descripcion: "qué se decidió",
    referencia: "dónde consta",
    sha256: "el acta",
  },
  alta: {
    tipo: "el tipo de mandato",
    nombre: "la razón social o nombre",
    cuit: "el CUIT",
    persona_id: "la persona",
    matricula: "la matrícula",
    decision_id: "la decisión que lo respalda",
    desde: "la fecha «Desde»",
    hasta: "la fecha «Hasta»",
  },
  baja: {
    causa: "la causa",
    descripcion: "por qué termina",
    decision_id: "la asamblea que lo remueve",
    instrumento: "el documento que lo prueba",
  },
  revision: { decision_id: "la asamblea que lo mantiene", eficaz_desde: "desde cuándo vuelve a operar" },
  invitar: { correo: "el correo", referencia: "de dónde sale el correo" },
  aprobar_invitacion: {},
  retirar_invitacion: {},
};

/** Qué decir ante el error de un envío, según el estado y el código del cuerpo uniforme. */
export function mensajeDeError(estado: number, codigo: string | undefined, accion: Accion): string {
  if (estado === ESTADO.sinPermiso && codigo === "csrf_invalido") return "La página quedó abierta mucho tiempo. Hay que recargarla y repetir el paso.";
  if (estado === ESTADO.sinPermiso) return SIN_PERMISO[accion];
  if (estado === ESTADO.noEncontrado) return NO_ENCONTRADO[accion];
  if (estado === ESTADO.conflicto && codigo === "idempotencia_en_curso")
    return "El pedido se está procesando. Reintentar en unos segundos.";
  if (estado === ESTADO.limite) return "Se llegó al límite de pedidos por ahora. Reintentar más tarde.";
  if (estado === ESTADO.invalido && codigo === "idempotencia_reutilizada")
    return "Los datos cambiaron desde un intento anterior. Hay que enviar el formulario de nuevo.";
  if ((estado === ESTADO.conflicto || estado === ESTADO.invalido) && codigo !== undefined && codigo in POR_CODIGO)
    return POR_CODIGO[codigo] as string;
  if (estado === ESTADO.conflicto) return "Los datos del edificio cambiaron mientras tanto: recargar la página y revisar el formulario.";
  if (estado === ESTADO.invalido) return REVISAR_FORMULARIO;
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
    ...(ve.length > 0 ? [`Acá están ${ENUMERAR.format(ve)}.`] : []),
    ...(hace.length > 0 ? [`Esta cuenta puede ${ENUMERAR.format(hace)}.`] : []),
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
  /** Los campos del formulario rechazado, con su motivo (G-49). */
  const [errores, setErrores] = useState<{ accion: Accion; campos: ErroresDeCampo } | null>(null);
  const errorEn = (accion: Accion, campo: string) => (errores?.accion === accion ? errores.campos[campo] : undefined);
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
  /** G-44: el formulario de la baja se despliega a pedido; abrir un detalle no lo muestra. */
  const [bajaAbierta, setBajaAbierta] = useState(false);
  const botonAbrirBaja = useRef<HTMLButtonElement | null>(null);
  const primerCampoBaja = useRef<HTMLElement | null>(null);

  // Mantener un mandato pendiente de revisión (G-41).
  const [decisionRevision, setDecisionRevision] = useState("");
  const [eficazDesde, setEficazDesde] = useState("");
  const claveRevision = useRef<string | null>(null);

  // El acta de la decisión, elegida de Documentos (G-15): la base ya ata el acta por su huella (`0089`).
  const veActas = puede("documentos.ver");
  const cargaDocumentos = puede("documentos.cargar", "documentos.cargar_operativos");
  const [actas, setActas] = useState<Lectura<Documento> | null>(null);
  const pedidoActas = useRef(0);
  const [actaElegida, setActaElegida] = useState("");
  const [viendoActa, setViendoActa] = useState<{ documento: string; version: string; titulo: string } | null>(null);

  // La invitación de la administración externa a asociar su cuenta (G-40; A7, cuatro ojos).
  const [invitaciones, setInvitaciones] = useState<Lectura<InvitacionMandato> | null>(null);
  const [correoInvitacion, setCorreoInvitacion] = useState("");
  const [referenciaInvitacion, setReferenciaInvitacion] = useState("");
  const [confirmarRetiroInvitacion, setConfirmarRetiroInvitacion] = useState(false);
  const claveInvitar = useRef<string | null>(null);
  const claveAprobar = useRef<string | null>(null);
  const claveRetirarInvitacion = useRef<string | null>(null);

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

  // Al desplegar la baja, el foco va a su primer campo (G-44).
  useEffect(() => {
    if (bajaAbierta) primerCampoBaja.current?.focus();
  }, [bajaAbierta]);

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
    setErrores(null);
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
  function cambiar<T>(clave: Clave, fijar: (v: T) => void, campo?: string) {
    return (v: T) => {
      clave.current = null;
      // Lo que se corrige deja de estar marcado (G-49).
      if (campo !== undefined) setErrores((e) => (e === null ? e : { ...e, campos: sinElDe(e.campos, campo) }));
      fijar(v);
    };
  }

  /** En la baja, además, un cambio cierra la confirmación: lo que se confirma es lo que se ve. */
  function cambiarBaja<T>(fijar: (v: T) => void, campo?: string) {
    return (v: T) => {
      setConfirmarBaja(false);
      cambiar(claveBaja, fijar, campo)(v);
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
    setErrores(null);
    void pedido(clave.current)
      .then(({ data, error, response }) => {
        exigirSesion(response);
        // Retirar una invitación responde 204, sin cuerpo: lo que dice si salió es el estado.
        if (!response.ok) {
          const codigo = codigoDe(error);
          if (renovarClave(response.status, codigo)) clave.current = null;
          // Un rechazo que nombra campos del formulario se dice junto a cada uno (G-49).
          const nombres = NOMBRES_CAMPO[accion];
          const mensaje = motivoDeSeparacion(error) ?? mensajeDeError(response.status, codigo, accion);
          const marcados = soloDe(erroresDe(error, mensaje), nombres);
          setErrores({ accion, campos: marcados });
          const texto = Object.keys(marcados).length > 0 ? resumenDe(marcados, nombres) : mensaje;
          setAviso({ texto, ok: false, formulario: accion });
          fallo?.(response.status, codigo);
          return;
        }
        clave.current = null;
        listo(data as T);
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
    // La huella de la versión disponible del acta elegida: así la decisión la cita (`0089`).
    const acta = actasElegibles.find((d) => d.id === actaElegida);
    const actaParaEnviar = acta === undefined ? null : (disponibleDe(acta)?.sha256 ?? null);
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
            sha256: actaParaEnviar,
          },
        }),
      (datos) => {
        // Con el formulario vacío no se puede registrar dos veces lo mismo con una clave nueva.
        setTipoDecision(TIPO_DECISION_INICIAL);
        setFechaDecision("");
        setDescripcionDecision("");
        setReferencia("");
        setActaElegida("");
        const hecho = "Listo: la decisión quedó registrada.";
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
    // El CUIT con el verificador mal ya se ve marcado en el campo: no se manda.
    if (externo && cuitConError(cuit)) return;
    const nombreAlta = externo ? nombre.trim() : (elegibles.find((c) => c.persona_id === personaElegida)?.nombre ?? "");
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
      () => {
        // Con el formulario vacío no se puede dar dos veces la misma alta con una clave nueva.
        setTipoMandato(TIPO_MANDATO_INICIAL);
        setPersona("");
        setNombre("");
        setCuit("");
        setMatricula("");
        setDecisionAlta("");
        setDesde("");
        setHasta("");
        // G-44: el «Listo» con lo que pasó, sin abrir el detalle (que ofrecería deshacerlo).
        const asociar = externo ? " Para asociar la cuenta de la administración, abrir su detalle e invitarla." : "";
        setAviso({ texto: `Listo: el mandato de ${nombreAlta} quedó dado de alta y ya está en la lista.${asociar}`, ok: true });
        setAbierto(null);
      },
    );
  }

  function limpiarBaja() {
    claveBaja.current = null;
    setConfirmarBaja(false);
    setBajaAbierta(false);
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
        setAviso({ texto: `Listo: el mandato de ${mandato.nombre} quedó dado de baja.`, ok: true });
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
    setAviso(hecho === null ? { texto, ok: false } : { texto: `${hecho} No se pudo abrir su detalle: está en la lista.`, ok: true });
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
          limpiarRevision();
          limpiarInvitacion();
          setCausa(data.estado === "terminado" ? "error_carga" : "renuncia");
          setDetalle({ tipo: "mandato", datos: data });
          if (admiteInvitacion(data)) pedirInvitaciones(data.id);
          return;
        }
        if (response.status === ESTADO.sinPermiso) falloDetalle("Esta cuenta no tiene permiso para ver ese mandato.", hecho);
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
          // Con acta, se busca en Documentos para ofrecer verla (G-15).
          if (data.sha256 !== null) pedirActas();
          return;
        }
        if (response.status === ESTADO.sinPermiso) falloDetalle("Esta cuenta no tiene permiso para ver esa decisión.", hecho);
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

  /** Las actas de Documentos, una vez por edificio: al abrir el formulario de la decisión o un detalle
   * con acta. Si no se pudieron consultar, se vuelven a pedir la próxima vez. */
  function pedirActas() {
    if (!veActas || consorcio === "" || (actas !== null && actas.tipo !== "sin_servicio")) return;
    const numero = ++pedidoActas.current;
    setActas(CARGANDO);
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/documentos", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, response }) => {
        exigirSesion(response);
        if (numero !== pedidoActas.current) return;
        if (data !== undefined) setActas({ tipo: "ok", elementos: data.documentos });
        else setActas(response.status === ESTADO.sinPermiso ? SIN_PERMISO_DE_LECTURA : SIN_SERVICIO);
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion) aEntrar();
        else if (numero === pedidoActas.current) setActas(SIN_SERVICIO);
      });
  }

  /** Abre uno de los dos formularios de arriba; el de la decisión pide las actas para elegir. */
  function abrirFormulario(cual: FormularioAbierto | null) {
    if (cual === "decision") pedirActas();
    setAbierto(cual);
  }

  /** Un mandato externo sin cuenta, que todavía rige o va a regir, admite la invitación (A7). */
  function admiteInvitacion(m: MandatoDetalle): boolean {
    return (
      m.tipo === "administrador_externo" && !m.con_cuenta && ESTADOS_CON_INVITACION.includes(m.estado) && puede("mandatos.ver_asociaciones")
    );
  }

  function pedirInvitaciones(mandato: string) {
    const numero = pedidoDetalle.current;
    setInvitaciones(CARGANDO);
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/mandatos/{mandato_id}/invitaciones", {
        params: { path: { consorcio_id: consorcio, mandato_id: mandato } },
      })
      .then(({ data, response }) => {
        exigirSesion(response);
        if (numero !== pedidoDetalle.current) return;
        if (data !== undefined) setInvitaciones({ tipo: "ok", elementos: data.elementos });
        else setInvitaciones(response.status === ESTADO.sinPermiso ? SIN_PERMISO_DE_LECTURA : SIN_SERVICIO);
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion) aEntrar();
        else if (numero === pedidoDetalle.current) setInvitaciones(SIN_SERVICIO);
      });
  }

  function limpiarInvitacion() {
    for (const clave of [claveInvitar, claveAprobar, claveRetirarInvitacion]) clave.current = null;
    setInvitaciones(null);
    setCorreoInvitacion("");
    setReferenciaInvitacion("");
    setConfirmarRetiroInvitacion(false);
  }

  function limpiarRevision() {
    claveRevision.current = null;
    setDecisionRevision("");
    setEficazDesde("");
  }

  /** Emitir la invitación: queda por aprobar, sin correo (A7). */
  function invitar(evento: FormEvent, mandato: MandatoDetalle) {
    evento.preventDefault();
    enviar(
      claveInvitar,
      "invitar",
      (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/mandatos/{mandato_id}/invitaciones", {
          params: { path: { consorcio_id: consorcio, mandato_id: mandato.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          headers: encabezadoCsrf(),
          body: { correo: correoInvitacion.trim(), referencia: referenciaInvitacion.trim() },
        }),
      () => {
        setCorreoInvitacion("");
        setReferenciaInvitacion("");
        setAviso({ texto: `Listo: la invitación a ${mandato.nombre} quedó por aprobar. ${AYUDA_INVITAR}`, ok: true, formulario: "invitar" });
        pedirInvitaciones(mandato.id);
      },
    );
  }

  /** Aprobar la invitación que emitió otra persona: recién ahí sale el correo con el enlace. */
  function aprobarInvitacion(invitacion: InvitacionMandato, mandato: MandatoDetalle) {
    enviar(
      claveAprobar,
      "aprobar_invitacion",
      (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/invitaciones-mandato/{invitacion_id}/aprobar", {
          params: { path: { consorcio_id: consorcio, invitacion_id: invitacion.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          headers: encabezadoCsrf(),
        }),
      (datos) => {
        setAviso({
          texto: `Listo: la invitación quedó aprobada y salió el correo con el enlace a ${mandato.nombre}. Vence el ${momento(datos.vence_en)}.`,
          ok: true,
          formulario: "aprobar_invitacion",
        });
        pedirInvitaciones(mandato.id);
      },
      (estado) => {
        if (estado === ESTADO.noEncontrado || estado === ESTADO.conflicto) pedirInvitaciones(mandato.id);
      },
    );
  }

  /** Retirar la invitación que no se aceptó: el enlace, si salió, deja de servir. */
  function retirarInvitacion(invitacion: InvitacionMandato, mandato: MandatoDetalle) {
    enviar(
      claveRetirarInvitacion,
      "retirar_invitacion",
      (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/invitaciones-mandato/{invitacion_id}/revocar", {
          params: { path: { consorcio_id: consorcio, invitacion_id: invitacion.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          headers: encabezadoCsrf(),
        }),
      () => {
        setConfirmarRetiroInvitacion(false);
        setAviso({ texto: "Listo: la invitación se retiró. Si el correo ya había salido, su enlace ya no sirve.", ok: true, formulario: "retirar_invitacion" });
        pedirInvitaciones(mandato.id);
      },
      () => setConfirmarRetiroInvitacion(false),
    );
  }

  /** Mantener un mandato pendiente de revisión, con una asamblea y desde una fecha (G-41, E62). */
  function mantener(evento: FormEvent, mandato: MandatoDetalle) {
    evento.preventDefault();
    enviar(
      claveRevision,
      "revision",
      (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/mandatos/{mandato_id}/revision", {
          params: { path: { consorcio_id: consorcio, mandato_id: mandato.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          headers: encabezadoCsrf(),
          body: { decision_id: decisionRevision, eficaz_desde: eficazDesde },
        }),
      (datos) => {
        limpiarRevision();
        pedidoDetalle.current += 1;
        setDetalle(null);
        setAviso({ texto: `Listo: el mandato de ${mandato.nombre} se mantiene y vuelve a operar desde el ${dia(datos.eficaz_desde)}.`, ok: true });
      },
    );
  }

  function elegirEdificio(valor: string) {
    // Nada del edificio anterior queda elegible mientras llegan los datos del nuevo.
    for (const clave of [claveDecision, claveAlta, claveBaja]) clave.current = null;
    pedidoActas.current += 1;
    setActas(null);
    setActaElegida("");
    limpiarInvitacion();
    limpiarRevision();
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
  // Las actas que se pueden elegir: las de «Actas y decisiones» con un archivo disponible (G-15).
  const actasElegibles =
    actas?.tipo === "ok" ? actas.elementos.filter((d) => d.categoria === "actas_y_decisiones" && disponibleDe(d) !== undefined) : [];
  /** El documento cuyo archivo disponible tiene esa huella: el acta de la decisión, para verla. */
  const actaDeLaHuella = (sha256: string) => {
    if (actas?.tipo !== "ok") return undefined;
    for (const documento of actas.elementos) {
      const version = documento.versiones.find((v) => v.estado === "disponible" && v.sha256.toLowerCase() === sha256.toLowerCase());
      if (version !== undefined) return { documento, version };
    }
    return undefined;
  };
  // G-42: sin una decisión que se pueda usar, el paso que sigue es registrarla (la registra una
  // persona y da el alta otra); con alguna, el alta.
  const altaEsElPaso = !registraDecisiones || decisiones.tipo !== "ok" || usables.length > 0;
  const nadaAbierto = abierto === null && detalle === null;
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
    <button className="secundario" type="button" onClick={reconsultarListas} disabled={enviando} aria-describedby={seccion}>
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
                ? "No se pudieron consultar los edificios. Hay que revisar la conexión y volver en un rato."
                : `No se pudieron consultar los edificios. ${SERVICIO_NO_DISPONIBLE}`}
            </p>
            <button className="secundario"
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
        {pantalla.tipo === "sin_consorcios" && <p>Esta cuenta todavía no participa de ningún edificio.</p>}
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
                  className={nadaAbierto && altaEsElPaso ? undefined : "secundario"}
                  aria-expanded={abierto === "alta"}
                  disabled={enviando}
                  onClick={() => abrirFormulario(abierto === "alta" ? null : "alta")}
                >
                  Dar de alta un mandato
                </button>
              )}
              {registraDecisiones && (
                <button
                  type="button"
                  ref={botonDecision}
                  className={nadaAbierto && ofreceAlta && !altaEsElPaso ? undefined : "secundario"}
                  aria-expanded={abierto === "decision"}
                  disabled={enviando}
                  onClick={() => abrirFormulario(abierto === "decision" ? null : "decision")}
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
                {...marcaDe(`${id}-tipo-decision`, errorEn("decision", "tipo"))}
                value={tipoDecision}
                disabled={enviando}
                onChange={(e) => cambiar(claveDecision, setTipoDecision, "tipo")(e.target.value as TipoDecision)}
              >
                {TIPOS_DECISION.map((t) => (
                  <option key={t} value={t}>
                    {TEXTO_TIPO_DECISION[t]}
                  </option>
                ))}
              </select>
              <ErrorDeCampo idCampo={`${id}-tipo-decision`} mensaje={errorEn("decision", "tipo")} />
              <label htmlFor={`${id}-fecha-decision`}>Fecha del acto</label>
              <input
                id={`${id}-fecha-decision`}
                {...marcaDe(`${id}-fecha-decision`, errorEn("decision", "fecha"))}
                type="date"
                required
                disabled={enviando}
                value={fechaDecision}
                onChange={(e) => cambiar(claveDecision, setFechaDecision, "fecha")(e.target.value)}
              />
              <ErrorDeCampo idCampo={`${id}-fecha-decision`} mensaje={errorEn("decision", "fecha")} />
              <label htmlFor={`${id}-descripcion-decision`}>Qué se decidió</label>
              <TextoDeUnaLinea
                id={`${id}-descripcion-decision`}
                {...marcaDe(`${id}-descripcion-decision`, errorEn("decision", "descripcion"))}
                rows={FILAS_DESCRIPCION}
                autoComplete="off"
                maxLength={LARGO_DESCRIPCION}
                required
                disabled={enviando}
                value={descripcionDecision}
                onValor={cambiar(claveDecision, setDescripcionDecision, "descripcion")}
              />
              <ErrorDeCampo idCampo={`${id}-descripcion-decision`} mensaje={errorEn("decision", "descripcion")} />
              <label htmlFor={`${id}-referencia`}>Dónde consta (libro, acta y folio)</label>
              <TextoDeUnaLinea
                id={`${id}-referencia`}
                {...marcaDe(`${id}-referencia`, errorEn("decision", "referencia"))}
                rows={FILAS_REFERENCIA}
                autoComplete="off"
                maxLength={LARGO_REFERENCIA}
                required
                disabled={enviando}
                value={referencia}
                onValor={cambiar(claveDecision, setReferencia, "referencia")}
              />
              <ErrorDeCampo idCampo={`${id}-referencia`} mensaje={errorEn("decision", "referencia")} />
              {veActas && actas?.tipo !== "sin_permiso" && (
                <>
                  {actas?.tipo === "ok" ? (
                    <>
                      <label htmlFor={`${id}-acta`}>Acta (opcional)</label>
                      <select
                        id={`${id}-acta`}
                        {...marcaDe(`${id}-acta`, errorEn("decision", "sha256"), `${id}-ayuda-acta`)}
                        value={actaElegida}
                        disabled={enviando}
                        onChange={(e) => cambiar(claveDecision, setActaElegida, "sha256")(e.target.value)}
                      >
                        <option value="">Sin un acta de Documentos</option>
                        {actasElegibles.map((d) => (
                          <option key={d.id} value={d.id}>
                            {recortar(`${d.titulo} (${dia(d.fecha)})`)}
                          </option>
                        ))}
                      </select>
                    </>
                  ) : actas?.tipo === "sin_servicio" ? (
                    <p>No se pudieron consultar las actas de Documentos: la decisión se puede registrar sin ella.</p>
                  ) : (
                    <p>Consultando las actas de Documentos…</p>
                  )}
                  <p id={`${id}-ayuda-acta`}>
                    El acta se elige de Documentos, categoría «Actas y decisiones», y queda vinculada a la decisión.
                    {cargaDocumentos && (
                      <>
                        {" "}Si todavía no está, cargarla antes en <Link to={RUTA_DOCUMENTOS}>Documentos</Link>.
                      </>
                    )}
                  </p>
                  <ErrorDeCampo idCampo={`${id}-acta`} mensaje={errorEn("decision", "sha256")} />
                </>
              )}
              <p>{QUIEN_DA_EL_ALTA}</p>
              <div className="acciones">
                <button type="submit" disabled={enviando}>
                  Registrar decisión
                </button>
                <button type="button" className="secundario" onClick={cerrarFormulario} disabled={enviando}>
                  Cancelar
                </button>
              </div>
            </form>
            {avisoEn("decision")}
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
                <p>No se pudo consultar quiénes pueden recibir un mandato.</p>
                {consultarDeNuevo(`${id}-alta`)}
              </>
            )}
            {candidatos.tipo === "ok" && decisiones.tipo === "sin_servicio" && (
              <>
                <p>Para dar un alta hace falta elegir la decisión que la respalda, y no se pudieron consultar.</p>
                {consultarDeNuevo(`${id}-alta`)}
              </>
            )}
            {candidatos.tipo === "ok" && decisiones.tipo === "ok" && usables.length === 0 && (
              // G-42: antes del formulario, lo que falta para poder darlo.
              <>
                <p>
                  {ALTA_SIN_DECISION_USABLE}{" "}
                  {listaDecisiones.length === 0
                    ? "Todavía no hay ninguna registrada."
                    : "Las de este edificio las registró esta cuenta: el alta la tiene que dar otra persona del Consejo o de la administración."}
                </p>
                {registraDecisiones && (
                  <p>
                    <button type="button" onClick={() => abrirFormulario("decision")} disabled={enviando}>
                      Registrar una decisión
                    </button>
                  </p>
                )}
              </>
            )}
            {candidatos.tipo === "ok" && decisiones.tipo === "ok" && usables.length > 0 && (
              <form className="formulario formulario-movil" onSubmit={darDeAlta}>
                <label htmlFor={`${id}-tipo-mandato`}>Tipo de mandato</label>
                <select
                  id={`${id}-tipo-mandato`}
                  {...marcaDe(`${id}-tipo-mandato`, errorEn("alta", "tipo"))}
                  value={tipoMandato}
                  disabled={enviando}
                  onChange={(e) => cambiar(claveAlta, setTipoMandato, "tipo")(e.target.value as TipoMandato)}
                >
                  {TIPOS_MANDATO.map((t) => (
                    <option key={t} value={t}>
                      {TEXTO_TIPO_MANDATO[t]}
                    </option>
                  ))}
                </select>
                <ErrorDeCampo idCampo={`${id}-tipo-mandato`} mensaje={errorEn("alta", "tipo")} />
                {externo ? (
                  <>
                    <p>
                      La administración externa se carga por su razón social y su CUIT, sin cuenta: la
                      cuenta se asocia después, por invitación.
                    </p>
                    <label htmlFor={`${id}-nombre`}>Razón social o nombre</label>
                    <input
                      id={`${id}-nombre`}
                      {...marcaDe(`${id}-nombre`, errorEn("alta", "nombre"))}
                      type="text"
                      autoComplete="off"
                      maxLength={LARGO_NOMBRE}
                      pattern={PATRON_TEXTO}
                      required
                      disabled={enviando}
                      value={nombre}
                      onChange={(e) => cambiar(claveAlta, setNombre, "nombre")(e.target.value)}
                    />
                    <ErrorDeCampo idCampo={`${id}-nombre`} mensaje={errorEn("alta", "nombre")} />
                    <CampoCuit id={`${id}-cuit`} valor={cuit} deshabilitado={enviando} error={errorEn("alta", "cuit")}
                      alCambiar={cambiar(claveAlta, setCuit, "cuit")} />
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
                      {...marcaDe(`${id}-persona`, errorEn("alta", "persona_id"))}
                      value={personaElegida}
                      required
                      disabled={enviando}
                      onChange={(e) => cambiar(claveAlta, setPersona, "persona_id")(e.target.value)}
                    >
                      {elegibles.map((c) => (
                        <option key={c.persona_id} value={c.persona_id}>
                          {recortar(`${c.nombre} (${c.unidades.join(", ")})`)}
                        </option>
                      ))}
                    </select>
                    <ErrorDeCampo idCampo={`${id}-persona`} mensaje={errorEn("alta", "persona_id")} />
                  </>
                )}
                {tipoMandato !== "miembro_consejo" && (
                  <>
                    <label htmlFor={`${id}-matricula`}>Matrícula (opcional)</label>
                    <input
                      id={`${id}-matricula`}
                      {...marcaDe(`${id}-matricula`, errorEn("alta", "matricula"))}
                      type="text"
                      autoComplete="off"
                      maxLength={LARGO_MATRICULA}
                      pattern={PATRON_TEXTO}
                      disabled={enviando}
                      value={matricula}
                      onChange={(e) => cambiar(claveAlta, setMatricula, "matricula")(e.target.value)}
                    />
                    <ErrorDeCampo idCampo={`${id}-matricula`} mensaje={errorEn("alta", "matricula")} />
                  </>
                )}
                <>
                    <label htmlFor={`${id}-decision-alta`}>Decisión que lo respalda</label>
                    <select
                      id={`${id}-decision-alta`}
                      {...marcaDe(`${id}-decision-alta`, errorEn("alta", "decision_id"))}
                      value={decisionAlta}
                      required
                      disabled={enviando}
                      onChange={(e) => cambiar(claveAlta, setDecisionAlta, "decision_id")(e.target.value)}
                    >
                      <option value="">Elegir la decisión</option>
                      {usables.map((d) => (
                        <option key={d.id} value={d.id}>
                          {recortar(textoDecision(d))}
                        </option>
                      ))}
                    </select>
                    <ErrorDeCampo idCampo={`${id}-decision-alta`} mensaje={errorEn("alta", "decision_id")} />
                </>
                <label htmlFor={`${id}-desde`}>Desde</label>
                <input
                  id={`${id}-desde`}
                  {...marcaDe(`${id}-desde`, errorEn("alta", "desde"))}
                  type="date"
                  required
                  disabled={enviando}
                  value={desde}
                  onChange={(e) => cambiar(claveAlta, setDesde, "desde")(e.target.value)}
                />
                <ErrorDeCampo idCampo={`${id}-desde`} mensaje={errorEn("alta", "desde")} />
                <label htmlFor={`${id}-hasta`}>Hasta (opcional; ese día ya no rige)</label>
                <input
                  id={`${id}-hasta`}
                  {...marcaDe(`${id}-hasta`, errorEn("alta", "hasta"), `${id}-vigencia-vacia`)}
                  type="date"
                  min={desde === "" ? undefined : desde}
                  disabled={enviando}
                  value={hasta}
                  onChange={(e) => cambiar(claveAlta, setHasta, "hasta")(e.target.value)}
                />
                <ErrorDeCampo idCampo={`${id}-hasta`} mensaje={errorEn("alta", "hasta")} />
                <p id={`${id}-vigencia-vacia`} aria-live="polite">
                  {hasta !== "" &&
                    hasta === desde &&
                    "Con «hasta» igual a «desde» el mandato nunca rige: el último día es el anterior a «hasta»."}
                </p>
                {(externo || elegibles.length > 0) && (
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
                <p>No se pudieron consultar los mandatos.</p>
                {consultarDeNuevo(`${id}-mandatos`)}
              </>
            )}
            {mandatos.tipo === "ok" && mandatos.elementos.length === 0 && <p>Todavía no hay mandatos cargados.</p>}
            {mandatos.tipo === "ok" && mandatos.elementos.length > 0 && (
              <ul className="filas-registro" aria-labelledby={`${id}-mandatos`}>
                {/* Cada fila abre el detalle del mandato (comentario 21), con su baja si el rol la permite. */}
                {mandatos.elementos.map((m) => (
                  <li key={m.id}>
                    <FilaDeRegistro
                      titulo={`${m.nombre}, ${TEXTO_TIPO_MANDATO[m.tipo]}`}
                      resumen={[
                        TEXTO_ESTADO_MANDATO[m.estado],
                        `Desde el ${dia(m.vigente_desde)}${m.vigente_hasta ? ` hasta el ${dia(m.vigente_hasta)} (ese día ya no rige)` : ""}`,
                        ...(m.cuit ? [`CUIT ${m.cuit}`] : []),
                        m.con_cuenta ? "Con cuenta asociada" : "Sin cuenta asociada",
                      ].join(" · ")}
                      alAbrir={() => verMandato(m)}
                      deshabilitada={enviando}
                      abierta={detalle?.tipo === "mandato" && detalle.datos.id === m.id}
                    />
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
                <p>No se pudieron consultar las decisiones.</p>
                {consultarDeNuevo(`${id}-decisiones`)}
              </>
            )}
            {decisiones.tipo === "ok" && decisiones.elementos.length === 0 && <p>Todavía no hay decisiones registradas.</p>}
            {decisiones.tipo === "ok" && decisiones.elementos.length > 0 && (
              <ul className="filas-registro" aria-labelledby={`${id}-decisiones`}>
                {decisiones.elementos.map((d) => (
                  <li key={d.id}>
                    <FilaDeRegistro
                      titulo={`${TEXTO_TIPO_DECISION[d.tipo]} del ${dia(d.fecha)}`}
                      resumen={d.descripcion}
                      alAbrir={() => verDecision(d)}
                      deshabilitada={enviando}
                      abierta={detalle?.tipo === "decision" && detalle.datos.id === d.id}
                    />
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
                {detalle.datos.sha256 !== null &&
                  ((acta) =>
                    acta === undefined ? (
                      <>
                        <dt>Huella del acta</dt>
                        <dd>{detalle.datos.sha256}</dd>
                      </>
                    ) : (
                      // G-15: el acta vinculada, para abrirla desde la decisión.
                      <>
                        <dt>Acta</dt>
                        <dd>
                          {acta.documento.titulo}{" "}
                          <button
                            type="button"
                            className="secundario"
                            onClick={() => setViendoActa({ documento: acta.documento.id, version: acta.version.id, titulo: acta.documento.titulo })}
                          >
                            Ver el acta
                          </button>
                        </dd>
                      </>
                    ))(actaDeLaHuella(detalle.datos.sha256))}
                <dt>Quién la registró</dt>
                <dd>
                  {detalle.datos.registrada_por_vos
                    ? "Esta cuenta. Por eso no puede usarla para dar de alta ni para remover."
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
                    return d ? textoDecision(d) : "No figura entre las decisiones visibles para esta cuenta.";
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
                        return d ? textoDecision(d) : "No figura entre las decisiones visibles para esta cuenta.";
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

              {detalle.datos.estado === "pendiente_revision" && (
                <section aria-labelledby={`${id}-revision`}>
                  <h3 id={`${id}-revision`}>Por qué está pendiente de revisión</h3>
                  <p>{CAUSA_REVISION}</p>
                  {/* Con la baja desplegada, mantenerlo se pliega: un solo acto a la vista. */}
                  {puede("mandatos.resolver_revision") && !detalle.datos.es_propio && !bajaAbierta && (
                    asambleas.length === 0 ? (
                      <p>
                        Para mantenerlo hace falta una asamblea registrada por otra persona, y no hay ninguna que esta cuenta
                        pueda usar.
                      </p>
                    ) : (
                      <form className="formulario formulario-movil" onSubmit={(e) => mantener(e, detalle.datos)}>
                        <label htmlFor={`${id}-decision-revision`}>Asamblea que lo mantiene</label>
                        <select
                          id={`${id}-decision-revision`}
                          {...marcaDe(`${id}-decision-revision`, errorEn("revision", "decision_id"))}
                          value={decisionRevision}
                          required
                          disabled={enviando}
                          onChange={(e) => cambiar(claveRevision, setDecisionRevision, "decision_id")(e.target.value)}
                        >
                          <option value="">Elegir la asamblea</option>
                          {asambleas.map((d) => (
                            <option key={d.id} value={d.id}>
                              {recortar(textoDecision(d))}
                            </option>
                          ))}
                        </select>
                        <ErrorDeCampo idCampo={`${id}-decision-revision`} mensaje={errorEn("revision", "decision_id")} />
                        <label htmlFor={`${id}-eficaz-desde`}>Desde cuándo vuelve a operar</label>
                        <input
                          id={`${id}-eficaz-desde`}
                          {...marcaDe(`${id}-eficaz-desde`, errorEn("revision", "eficaz_desde"))}
                          type="date"
                          required
                          disabled={enviando}
                          value={eficazDesde}
                          onChange={(e) => cambiar(claveRevision, setEficazDesde, "eficaz_desde")(e.target.value)}
                        />
                        <ErrorDeCampo idCampo={`${id}-eficaz-desde`} mensaje={errorEn("revision", "eficaz_desde")} />
                        <p>
                          <button type="submit" className={abierto === null ? undefined : "secundario"} disabled={enviando}>
                            Mantener el mandato
                          </button>
                        </p>
                      </form>
                    )
                  )}
                  {avisoEn("revision")}
                </section>
              )}

              {admiteInvitacion(detalle.datos) && !bajaAbierta && invitaciones !== null && invitaciones.tipo !== "sin_permiso" &&
                seccionInvitacion(detalle.datos, invitaciones)}

              {ESTADOS_CON_BAJA.includes(detalle.datos.estado) && puede("mandatos.baja") && !detalle.datos.es_propio && !bajaAbierta && (
                // G-44 y G-45: la baja no se ve desplegada al abrir el detalle; un terminado solo se anula.
                <p>
                  <button type="button" className="secundario peligro" ref={botonAbrirBaja} disabled={enviando} onClick={() => setBajaAbierta(true)}>
                    {detalle.datos.estado === "terminado" ? "Anular por error de carga…" : "Dar de baja…"}
                  </button>
                </p>
              )}
              {ESTADOS_CON_BAJA.includes(detalle.datos.estado) && puede("mandatos.baja") && !detalle.datos.es_propio && bajaAbierta && (
                <form
                  ref={formularioBaja}
                  className="formulario formulario-movil"
                  onSubmit={(e) => {
                    e.preventDefault();
                    setConfirmarBaja(true);
                  }}
                >
                  <h3>{detalle.datos.estado === "terminado" ? "Anular por error de carga" : "Dar de baja"}</h3>
                  {detalle.datos.estado === "terminado" ? (
                    <p>Un mandato que ya terminó por su fecha solo se anula si se cargó por error: así consta que nunca valió.</p>
                  ) : (
                    <>
                      <label htmlFor={`${id}-causa`}>Causa</label>
                      <select
                        id={`${id}-causa`}
                        ref={(e) => {
                          primerCampoBaja.current = e;
                        }}
                        {...marcaDe(`${id}-causa`, errorEn("baja", "causa"))}
                        value={causa}
                        disabled={enviando}
                        onChange={(e) => cambiarBaja(setCausa, "causa")(e.target.value as CausaBaja)}
                      >
                        {CAUSAS.filter((c) => c !== "remocion" || !sinRemocion).map((c) => (
                          <option key={c} value={c}>
                            {TEXTO_CAUSA[c]}
                          </option>
                        ))}
                      </select>
                      <ErrorDeCampo idCampo={`${id}-causa`} mensaje={errorEn("baja", "causa")} />
                    </>
                  )}
                  <label htmlFor={`${id}-descripcion-baja`}>Por qué termina</label>
                  <input
                    id={`${id}-descripcion-baja`}
                    ref={(e) => {
                      if (detalle.datos.estado === "terminado") primerCampoBaja.current = e;
                    }}
                    {...marcaDe(`${id}-descripcion-baja`, errorEn("baja", "descripcion"))}
                    type="text"
                    autoComplete="off"
                    maxLength={LARGO_DESCRIPCION}
                    pattern={PATRON_TEXTO}
                    required
                    disabled={enviando}
                    value={descripcionBaja}
                    onChange={(e) => cambiarBaja(setDescripcionBaja, "descripcion")(e.target.value)}
                  />
                  <ErrorDeCampo idCampo={`${id}-descripcion-baja`} mensaje={errorEn("baja", "descripcion")} />
                  {causa === "remocion" ? (
                    <>
                      <label htmlFor={`${id}-decision-baja`}>Asamblea que lo remueve</label>
                      <select
                        id={`${id}-decision-baja`}
                        {...marcaDe(`${id}-decision-baja`, errorEn("baja", "decision_id"))}
                        value={decisionBaja}
                        required
                        disabled={enviando}
                        onChange={(e) => cambiarBaja(setDecisionBaja, "decision_id")(e.target.value)}
                      >
                        <option value="">Elegir la asamblea</option>
                        {asambleas.map((d) => (
                          <option key={d.id} value={d.id}>
                            {recortar(textoDecision(d))}
                          </option>
                        ))}
                      </select>
                      <ErrorDeCampo idCampo={`${id}-decision-baja`} mensaje={errorEn("baja", "decision_id")} />
                    </>
                  ) : (
                    <>
                      <label htmlFor={`${id}-instrumento`}>Instrumento (nota, certificado u otro)</label>
                      <input
                        id={`${id}-instrumento`}
                        {...marcaDe(`${id}-instrumento`, errorEn("baja", "instrumento"))}
                        type="text"
                        autoComplete="off"
                        maxLength={LARGO_REFERENCIA}
                        pattern={PATRON_TEXTO}
                        required
                        disabled={enviando}
                        value={instrumento}
                        onChange={(e) => cambiarBaja(setInstrumento, "instrumento")(e.target.value)}
                      />
                      <ErrorDeCampo idCampo={`${id}-instrumento`} mensaje={errorEn("baja", "instrumento")} />
                    </>
                  )}
                  {confirmarBaja ? (
                    <div role="group" aria-labelledby={`${id}-confirmar-baja`}>
                      <p id={`${id}-confirmar-baja`}>
                        {detalle.datos.estado === "terminado"
                          ? `¿Anular el mandato de ${detalle.datos.nombre} por error de carga? No se puede deshacer.`
                          : `¿Dar de baja el mandato de ${detalle.datos.nombre}? La baja no se puede deshacer.`}
                      </p>
                      <button type="button" className="peligro" ref={confirmarSi} onClick={() => darDeBaja(detalle.datos)} disabled={enviando}>
                        {detalle.datos.estado === "terminado" ? "Sí, anular" : "Sí, dar de baja"}
                      </button>{" "}
                      <button type="button" className="secundario" onClick={() => setConfirmarBaja(false)} disabled={enviando}>
                        No
                      </button>
                    </div>
                  ) : (
                    <p>
                      <button type="submit" className={abierto === null ? "peligro" : "secundario peligro"} ref={botonBaja} disabled={enviando}>
                        {detalle.datos.estado === "terminado" ? "Anular" : "Dar de baja"}
                      </button>{" "}
                      <button
                        type="button"
                        className="secundario"
                        disabled={enviando}
                        onClick={() => {
                          limpiarBaja();
                          setErrores(null);
                          setAviso((actual) => (actual?.formulario === "baja" ? null : actual));
                        }}
                      >
                        Cancelar
                      </button>
                    </p>
                  )}
                </form>
              )}
              {avisoEn("baja")}
              <button type="button" className="secundario" onClick={cerrarDetalle} disabled={enviando}>
                Cerrar el detalle
              </button>
            </section>
          )}

          {viendoActa !== null && (
            <VisorDocumento consorcio={consorcio} documento={viendoActa.documento} version={viendoActa.version} nombre={viendoActa.titulo}
              alCerrar={() => setViendoActa(null)} aEntrar={aEntrar} />
          )}
        </>
      )}
    </>
  );

  /** La cuenta de la administración externa: la invitación, con sus cuatro ojos (G-40, A7). */
  function seccionInvitacion(mandato: MandatoDetalle, lectura: Lectura<InvitacionMandato>) {
    const titulo = `${id}-invitacion`;
    const ultima = lectura.tipo === "ok" ? lectura.elementos[0] : undefined;
    const enCurso = ultima !== undefined && (ultima.estado === "por_aprobar" || ultima.estado === "pendiente") ? ultima : undefined;
    return (
      <section aria-labelledby={titulo}>
        <h3 id={titulo}>Cuenta de la administración</h3>
        {lectura.tipo === "cargando" && <p>Consultando…</p>}
        {lectura.tipo === "sin_servicio" && (
          <>
            <p>No se pudo consultar la invitación.</p>
            <button className="secundario" type="button" onClick={() => pedirInvitaciones(mandato.id)} disabled={enviando} aria-describedby={titulo}>
              Consultar de nuevo
            </button>
          </>
        )}
        {lectura.tipo === "ok" && enCurso !== undefined && (
          <>
            {enCurso.estado === "por_aprobar" ? (
              <>
                <p>Invitación por aprobar: todavía no salió ningún correo.</p>
                <dl className="detalles">
                  {enCurso.correo !== null && (
                    <>
                      <dt>Correo</dt>
                      <dd>{enCurso.correo}</dd>
                    </>
                  )}
                  {enCurso.referencia !== null && (
                    <>
                      <dt>De dónde sale el correo</dt>
                      <dd>{enCurso.referencia}</dd>
                    </>
                  )}
                  <dt>Emitida el</dt>
                  <dd>{momento(enCurso.creada_en)}</dd>
                </dl>
                {enCurso.emitida_por_mi ? (
                  <p>La emitió esta cuenta: la aprueba otra persona del Consejo o de la administración.</p>
                ) : (
                  puede("mandatos.aprobar_asociacion") && (
                    <p>
                      <button
                        type="button"
                        className={abierto === null ? undefined : "secundario"}
                        disabled={enviando}
                        onClick={() => aprobarInvitacion(enCurso, mandato)}
                      >
                        Aprobar la invitación
                      </button>
                    </p>
                  )
                )}
              </>
            ) : (
              <p>
                Invitación enviada: el correo con el enlace salió
                {enCurso.vence_en ? ` y vence el ${momento(enCurso.vence_en)}` : ""}. Falta que la administración la acepte.
              </p>
            )}
            {puede("mandatos.revocar_asociacion") &&
              (confirmarRetiroInvitacion ? (
                <div role="group" aria-labelledby={`${titulo}-confirmar`}>
                  <p id={`${titulo}-confirmar`}>¿Retirar la invitación? Si el correo ya salió, su enlace deja de servir.</p>
                  <button type="button" className="peligro" disabled={enviando} onClick={() => retirarInvitacion(enCurso, mandato)}>
                    Sí, retirarla
                  </button>{" "}
                  <button type="button" className="secundario" disabled={enviando} onClick={() => setConfirmarRetiroInvitacion(false)}>
                    No
                  </button>
                </div>
              ) : (
                <p>
                  <button type="button" className="secundario peligro" disabled={enviando} onClick={() => setConfirmarRetiroInvitacion(true)}>
                    Retirar la invitación
                  </button>
                </p>
              ))}
          </>
        )}
        {lectura.tipo === "ok" && enCurso === undefined && (
          <>
            <p>
              {ultima?.estado === "vencida"
                ? "La invitación anterior venció sin que nadie la aceptara."
                : ultima?.estado === "revocada"
                  ? "La invitación anterior se retiró."
                  : "La administración todavía no tiene una cuenta asociada: se asocia por invitación a su correo."}
            </p>
            {puede("mandatos.asociar_persona") && (
              <form className="formulario formulario-movil" onSubmit={(e) => invitar(e, mandato)} aria-labelledby={`${titulo}-invitar`}>
                <h4 id={`${titulo}-invitar`}>Invitar a la administración a asociar su cuenta</h4>
                <label htmlFor={`${titulo}-correo`}>Correo de la administración</label>
                <input
                  id={`${titulo}-correo`}
                  {...marcaDe(`${titulo}-correo`, errorEn("invitar", "correo"))}
                  type="email"
                  autoComplete="off"
                  maxLength={LARGO_CORREO}
                  required
                  disabled={enviando}
                  value={correoInvitacion}
                  onChange={(e) => cambiar(claveInvitar, setCorreoInvitacion, "correo")(e.target.value)}
                />
                <ErrorDeCampo idCampo={`${titulo}-correo`} mensaje={errorEn("invitar", "correo")} />
                <label htmlFor={`${titulo}-referencia`}>De dónde sale ese correo (contrato, nota u otro)</label>
                <TextoDeUnaLinea
                  id={`${titulo}-referencia`}
                  {...marcaDe(`${titulo}-referencia`, errorEn("invitar", "referencia"), `${titulo}-ayuda`)}
                  rows={FILAS_REFERENCIA}
                  autoComplete="off"
                  maxLength={LARGO_REFERENCIA}
                  required
                  disabled={enviando}
                  value={referenciaInvitacion}
                  onValor={cambiar(claveInvitar, setReferenciaInvitacion, "referencia")}
                />
                <ErrorDeCampo idCampo={`${titulo}-referencia`} mensaje={errorEn("invitar", "referencia")} />
                <p id={`${titulo}-ayuda`}>{AYUDA_INVITAR}</p>
                <p>
                  {/* Con un formulario de arriba abierto, el principal es el suyo. */}
                  <button type="submit" className={abierto === null ? undefined : "secundario"} disabled={enviando}>
                    Invitar
                  </button>
                </p>
              </form>
            )}
          </>
        )}
        {avisoEn("invitar")}
        {avisoEn("aprobar_invitacion")}
        {avisoEn("retirar_invitacion")}
      </section>
    );
  }
}
