import { type MutableRefObject, type ReactNode, useCallback, useEffect, useId, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { api } from "./api/cliente";
import { hoyDelDispositivo, TIPOS_DE_DOCUMENTO, tituloDeArchivo, TOPE_BYTES } from "./archivos";
import { CampoCuit, cuitConError } from "./CampoCuit";
import type { components } from "./api/tipos";
import { ComandoNoDisponible } from "./ComandoNoDisponible";
import { encabezadoCsrf } from "./csrf";
import {
  ErrorDeCampo,
  type ErroresDeCampo,
  erroresDe,
  faltaCompletar,
  hayErrores,
  marcaDe,
  resumenDe,
  REVISAR_DATO,
  REVISAR_FORMULARIO,
  sinElDe,
  soloDe,
} from "./ErrorDeCampo";
import { RUTA_INICIO } from "./enlace";
import { type DatoDeFicha, FichaDeRegistro, FilaDeRegistro, useFichas } from "./FichaDeRegistro";
import { useAEntrar } from "./sesionEncima";
import { conLaRedDeVuelta, LARGO_TITULO, sinElAvisoDeLaRed } from "./egresos/comun";
import { useAlVolverLaRed, useConsultaDelAntivirus, useMiPersona } from "./egresos/useConsorcio";
import { nombreDeFacultad } from "./facultades";
import { codigoDe, esDeConexion, mensajeDeFalla, motivoDeSeparacion, SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "./fallas";
import { RUTA_PADRON } from "./Padron";
import { REQUISITOS, useAcciones, useEdificioElegido, usePuede } from "./permisos";
import { type Resultado as ResultadoDeUnActo, ResultadoDelActo } from "./ResultadoDelActo";
import { SubirArchivo } from "./SubirArchivo";
import { TextoDeUnaLinea } from "./TextoDeUnaLinea";
import { MENSAJES_PROPIOS, mensajeDeError as mensajeDeCarga, tamano } from "./Documentos";
import { type FinDeEnvio, VentanaDeFormulario } from "./VentanaDeFormulario";
import { VisorDocumento } from "./VisorDocumento";

// Página /mandatos (H2b 2b-4c, PR 2): los mandatos y las decisiones de un edificio, su ficha,
// registrar una decisión y dar de alta o de baja un mandato. Cada lista y cada acción aparece solo
// si la persona la alcanza, y sin ninguna va al inicio. Las decisiones que registró quien mira no se
// ofrecen para dar altas ni para remover (A15) y su propia baja no aparece. Es solo interfaz: un 403
// al actuar se dice en la pantalla, y la autoridad es la API, la política y la base. Diseño:
// `doc/diseno/H2b_4c_Diseno.md` del repositorio CEM, §7.
//
// Informe de fricción del 17-11-2026, tanda 3 (comentarios 35 y 36 del propietario): cada fila abre
// solo su ficha (`FichaDeRegistro`, en una hoja sobre las listas) y toda carga de datos va en su
// ventana (`VentanaDeFormulario`): registrar una decisión, subir el acta (encima de la decisión), dar
// de alta, y desde la ficha dar de baja, mantener, invitar, retirar la invitación y desasociar.

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
type Categoria = components["schemas"]["CategoriaDocumento"];

/** La categoría de Documentos de donde sale el acta de una decisión, y adonde va la que se sube acá (G-15). */
const CATEGORIA_ACTAS: Categoria = "actas_y_decisiones";

export const RUTA_MANDATOS = "/mandatos";
/** C8G-04: la persona del mandato no cumple hoy ninguna condición para integrar el Consejo. */
export const SIN_CONDICION_PARA_MANTENER =
  "Para mantenerlo, la persona tiene que volver a tener un vínculo confirmado en el edificio: se confirma en el Padrón.";

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
/** Cuánto de un texto largo entra en una opción o en una fila: un renglón. */
const LARGO_OPCION = 60;

const TEXTO_TIPO_MANDATO: Record<TipoMandato, string> = {
  miembro_consejo: "Consejo de propietarios",
  administrador: "Administración",
  administrador_externo: "Administración externa",
};
const TIPOS_MANDATO = Object.keys(TEXTO_TIPO_MANDATO) as TipoMandato[];
const TIPO_MANDATO_INICIAL: TipoMandato = "miembro_consejo";
/** Los dos tipos de administración: no puede haber dos en las mismas fechas (`administracion_ocupada`). */
const ES_ADMINISTRACION: ReadonlySet<TipoMandato> = new Set(["administrador", "administrador_externo"]);
/** Los estados de un mandato que ocupan sus fechas: los que rigen o van a regir. */
const OCUPAN_SUS_FECHAS: ReadonlySet<EstadoMandato> = new Set(["vigente", "futuro", "pendiente_revision"]);

/** El estado en la ficha: una frase, con el motivo si lo hay (MAN-10). */
const TEXTO_ESTADO_MANDATO: Record<EstadoMandato, string> = {
  vigente: "Vigente",
  pendiente_revision: "Pendiente de revisión: sin condición para integrar el Consejo",
  futuro: "Empieza más adelante",
  terminado: "Terminado por su fecha",
  dado_de_baja: "Dado de baja",
  error_carga: "Anulado por error de carga",
};

/** Lo que falta resolver en un mandato, dicho en la fila (FL-MAN-10). */
const EN_REVISION = "En revisión";
const SIN_CUENTA = "Sin cuenta asociada";

/** Los que ya no rigen ni van a regir: van plegados en «Mandatos anteriores» (D-34). */
const YA_NO_RIGEN: ReadonlySet<EstadoMandato> = new Set(["terminado", "dado_de_baja", "error_carga"]);
const MANDATOS_ANTERIORES = "Mandatos anteriores";

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
/** Respuestas de la baja que dicen que la ficha quedó vieja: se cierra y se vuelve a pedir la lista. */
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

/** El estado de un mandato en su fila, en una o dos palabras (MAN-07): el resto va en la ficha. */
function estadoCorto(m: Mandato): string {
  if (m.estado === "futuro") return `Empieza el ${dia(m.vigente_desde)}`;
  if (m.estado === "terminado") return "Terminado";
  if (m.estado === "error_carga") return "Anulado";
  return TEXTO_ESTADO_MANDATO[m.estado];
}

/** Qué se estaba haciendo cuando la API respondió con error. */
export type Accion =
  | "decision"
  | "alta"
  | "baja"
  | "revision"
  | "invitar"
  | "aprobar_invitacion"
  | "retirar_invitacion"
  | "desasociar";

/** Las acciones que se hacen en una ventana sobre la ficha del mandato. */
type AccionDeFicha = "baja" | "revision" | "invitar" | "retirar_invitacion" | "desasociar";

const SIN_PERMISO: Record<Accion, string> = {
  decision: "Esta cuenta no tiene permiso para registrar decisiones en este edificio.",
  alta: "El mandato de esta cuenta no incluye dar este alta.",
  baja: "El mandato de esta cuenta no incluye dar esta baja.",
  revision: "El mandato de esta cuenta no incluye mantener este mandato.",
  invitar: "El mandato de esta cuenta no incluye invitar a la administración.",
  aprobar_invitacion: "El mandato de esta cuenta no incluye aprobar esta invitación.",
  retirar_invitacion: "El mandato de esta cuenta no incluye retirar esta invitación.",
  desasociar: "El mandato de esta cuenta no incluye desasociar la cuenta de la administración.",
};

const NO_ENCONTRADO: Record<Accion, string> = {
  decision: "No se encontró el edificio, o esta cuenta ya no tiene acceso.",
  alta: "No se encontró el edificio, o esta cuenta ya no tiene acceso.",
  baja: "No se encontró ese mandato en este edificio.",
  revision: "No se encontró ese mandato en este edificio.",
  invitar: "No se encontró ese mandato en este edificio.",
  aprobar_invitacion: "La invitación cambió o se retiró mientras tanto: la ficha ya está actualizada.",
  retirar_invitacion: "La invitación ya no está en este edificio: la ficha ya está actualizada.",
  desasociar: "No se encontró ese mandato en este edificio.",
};

/** El estado de un mandato del Consejo que dejó de cumplir la condición para integrarlo (G-41). Se
 * explica dentro de la ventana de mantenerlo, donde sirve para decidir (MAN-10). */
export const CAUSA_REVISION =
  "La persona dejó de cumplir las condiciones para integrar el Consejo (por ejemplo, ya no tiene una unidad confirmada en el edificio). Mientras tanto, el mandato no autoriza actos nuevos. Si vuelve a cumplirlas, una asamblea puede decidir mantenerlo desde una fecha; si no, se da de baja.";
/** En lugar de «Dar de alta un mandato» si no hay una decisión que se pueda usar (G-42, C8G-01). */
export const ALTA_SIN_DECISION_USABLE = "Para dar de alta un mandato falta una decisión registrada por otra persona del Consejo o de la administración.";
/** Lo mismo, para quien registra decisiones y todavía no hay ninguna. */
export const ALTA_SIN_DECISIONES =
  "Para dar de alta un mandato falta una decisión registrada. Se registra con «Registrar una decisión», y el alta la da otra persona del Consejo o de la administración.";
/** C8G-02 y FL-MAN-09: por qué el alta no ofrece el Consejo ni la administración. */
export const ALTA_SIN_NADIE =
  "Para dar un mandato del Consejo o de administración hace falta una persona con un vínculo confirmado en este edificio, y todavía nadie lo tiene. Los vínculos se confirman en el Padrón del edificio.";
export const ALTA_SIN_NADIE_MAS =
  "Para dar un mandato del Consejo o de administración hace falta otra persona con un vínculo confirmado en este edificio: nadie se da un mandato a sí mismo. Los vínculos se confirman en el Padrón del edificio.";
/** El «Listo» de la decisión (FL-DEC-03): quién da el alta lo dice la línea de «falta una decisión
 * registrada por otra persona», y no toda decisión respalda un alta. */
export const LISTO_DECISION = "Listo: la decisión quedó registrada.";
/** El «Listo» de la decisión que esperó a su acta en revisión (decisión 6 del propietario, FL-DEC-01). */
export const LISTO_DECISION_CON_ACTA = "Listo: el acta pasó la revisión del antivirus y la decisión quedó registrada con ella.";
/** Mientras espera al acta en revisión, en lugar del botón principal (FL-DEC-01). */
export const DECISION_ESPERA_EL_ACTA =
  "La decisión se registra sola, con el acta, cuando el antivirus termine de revisarla. Si se cierra esta ventana antes, no se registra.";

/** Lo que falta completar en cada ventana (decisión 4: el botón queda activo y marca el campo). */
const FALTA = {
  fecha: "Falta la fecha del acto.",
  descripcion: "Falta escribir qué se decidió.",
  referencia: "Falta escribir dónde consta.",
  nombre: "Falta escribir la razón social o el nombre.",
  cuit: "Falta escribir el CUIT.",
  decisionAlta: "Falta elegir la decisión que lo respalda.",
  desde: "Falta la fecha «Desde».",
  descripcionBaja: "Falta escribir por qué termina.",
  asambleaBaja: "Falta elegir la asamblea que lo remueve.",
  instrumento: "Falta escribir el instrumento.",
  asambleaRevision: "Falta elegir la asamblea que lo mantiene.",
  eficazDesde: "Falta la fecha desde la que vuelve a operar.",
  correo: "Falta escribir el correo de la administración.",
  referenciaInvitacion: "Falta escribir de dónde sale ese correo.",
  referenciaDesasociar: "Falta escribir por qué esa cuenta no es la de la administración.",
  archivoActa: "Falta elegir el archivo del acta.",
  tituloActa: "Falta el título del acta.",
} as const;

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
  invitacion_no_pendiente: "La invitación ya se aprobó o se aceptó: la ficha ya está actualizada.",
};

/** La marca de la fila del mandato que se acaba de dar de alta (G-44). */
export const RECIEN_DADO_DE_ALTA = "Recién dado de alta";

/** C8G-03 y FL-MAN-08: por qué la baja no ofrece la remoción por asamblea; va en la ficha del mandato. */
export const REMOCION_SIN_ASAMBLEA =
  "Para darlo de baja por remoción hace falta una asamblea registrada por otra persona del Consejo o de la administración, y todavía no hay ninguna que esta cuenta pueda usar.";
const REMOCION_SIN_DECISIONES = "No se pudieron consultar las decisiones: sin ellas no se ofrece la remoción por asamblea.";

/** Lo que pasa al desasociar la cuenta de la administración externa (G-40, P1-c): va en su ventana. */
export const AYUDA_DESASOCIAR =
  "El mandato, su CUIT y su vigencia quedan como están. Esa persona pierde el acceso: se cierran sus sesiones.";

/** Los motivos de la desasociación que no se dicen igual que los de la invitación (G-40). */
const POR_CODIGO_DESASOCIAR: Record<string, string> = {
  mandato_sin_persona: "El mandato ya no tiene una cuenta asociada: la ficha ya está actualizada.",
  mandato_no_asociable: "Solo se desasocia así la cuenta de una administración externa.",
};
/** Los rechazos de la desasociación que dicen que la ficha quedó vieja: se vuelve a pedir. */
const DESASOCIAR_SOBRE_ESTADO_VIEJO = Object.keys(POR_CODIGO_DESASOCIAR);

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
  desasociar: { referencia: "por qué esa cuenta no es la de la administración" },
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
  if (estado === ESTADO.conflicto && accion === "desasociar" && codigo !== undefined && codigo in POR_CODIGO_DESASOCIAR)
    return POR_CODIGO_DESASOCIAR[codigo] as string;
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

/** La ficha abierta: su título sale de la fila, y los datos llegan con el detalle. */
type FichaAbierta = { tipo: "mandato" | "decision"; id: string; titulo: string };

/** `invitar`: el «Listo» del alta de una administración externa trae el paso que sigue (FL-MAN-06). */
type Aviso = { texto: string; ok: boolean; invitar?: FichaAbierta };

type Clave = MutableRefObject<string | null>;

type Respuesta<T> = { data?: T; error?: unknown; response: Response };

/** Un 401 en cualquier pedido de la página: se va a /entrar. */
class SinSesion extends Error {}

function exigirSesion(response: Response) {
  if (response.status === ESTADO.sinSesion) throw new SinSesion();
}

function leer<T>({ data, response }: Respuesta<{ elementos: T[] }>): Lectura<T> {
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

type VentanaDePagina = "decision" | "alta";

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
  // El alta pide elegir a quién y qué decisión la respalda; la baja se da desde la ficha.
  const daAltas = puede("mandatos.alta") && veCandidatos && veDecisiones;
  // Nadie se da un mandato a sí mismo: quien mira no se ofrece, y sin saberlo decide la API.
  const yo = useMiPersona(daAltas);
  // Sin mandatos ni decisiones no queda nada, salvo que se sepa que puede registrar.
  const quedaElRegistro = sabeQueAlcanza && registraDecisiones;
  const [mandatos, setMandatos] = useState<Lectura<Mandato>>(CARGANDO);
  const [decisiones, setDecisiones] = useState<Lectura<Decision>>(CARGANDO);
  const [candidatos, setCandidatos] = useState<Lectura<Candidato>>(CARGANDO);
  const fichas = useFichas<string>();
  const [ficha, setFicha] = useState<FichaAbierta | null>(null);
  const [detalle, setDetalle] = useState<Detalle | null>(null);
  // La ventana de la página (registrar una decisión o dar de alta) y la de la ficha, de a una.
  const [ventana, setVentana] = useState<VentanaDePagina | null>(null);
  const [accionFicha, setAccionFicha] = useState<AccionDeFicha | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  /** El resultado de un acto hecho desde la ficha, que la deja abierta: sale en la ficha. */
  const [resultadoFicha, setResultadoFicha] = useState<ResultadoDeUnActo | null>(null);
  /** Los campos que faltan o que la API rechazó, con su motivo (G-49, decisión 4). */
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
  const textoAviso = useRef<HTMLParagraphElement | null>(null);
  /** El aviso de la página toma el foco cuando lo da un acto que cerró su ventana o la ficha. */
  const alAviso = useRef(false);
  // Cada pedido de detalle lleva su número: una respuesta que llega después de otro pedido, de
  // cerrar la ficha o de cambiar de edificio se descarta.
  const pedidoDetalle = useRef(0);

  // Ventana de la decisión.
  const [tipoDecision, setTipoDecision] = useState<TipoDecision>(TIPO_DECISION_INICIAL);
  const [fechaDecision, setFechaDecision] = useState("");
  const [descripcionDecision, setDescripcionDecision] = useState("");
  const [referencia, setReferencia] = useState("");

  // Ventana del alta.
  const [tipoMandato, setTipoMandato] = useState<TipoMandato>(TIPO_MANDATO_INICIAL);
  const [persona, setPersona] = useState("");
  const [nombre, setNombre] = useState("");
  const [cuit, setCuit] = useState("");
  const [matricula, setMatricula] = useState("");
  const [decisionAlta, setDecisionAlta] = useState("");
  const [desde, setDesde] = useState("");
  const [hasta, setHasta] = useState("");

  // Ventana de la baja, sobre la ficha del mandato.
  const [causa, setCausa] = useState<CausaBaja>("renuncia");
  const [descripcionBaja, setDescripcionBaja] = useState("");
  const [instrumento, setInstrumento] = useState("");
  const [decisionBaja, setDecisionBaja] = useState("");
  /** La baja no se deshace: la ventana pregunta antes de enviar. */
  const [confirmarBaja, setConfirmarBaja] = useState(false);

  // Mantener un mandato pendiente de revisión (G-41).
  const [decisionRevision, setDecisionRevision] = useState("");
  const [eficazDesde, setEficazDesde] = useState("");
  const claveRevision = useRef<string | null>(null);

  // El acta de la decisión, elegida de Documentos (G-15): la base ya ata el acta por su huella (`0089`).
  const veActas = puede("documentos.ver");
  const cargaDocumentos = puede("documentos.cargar", "documentos.cargar_operativos");
  const [actas, setActas] = useState<Lectura<Documento> | null>(null);
  const pedidoActas = useRef(0);
  /** Sube al cambiar de edificio, al registrar la decisión y al empezar otra subida: una respuesta
   * del acta pedida antes (subida, antivirus o lista) no toca el edificio nuevo, la decisión
   * siguiente ni el acta que la reemplaza (Codex en #245). */
  const generacionActa = useRef(0);
  const [actaElegida, setActaElegida] = useState("");
  const [viendoActa, setViendoActa] = useState<{ documento: string; version: string; titulo: string } | null>(null);
  // Subir el acta desde la ventana de la decisión (G-15), en su propia ventana encima: los dos pasos
  // de Documentos (crear el documento en «Actas y decisiones» y subir el archivo, que entra en
  // cuarentena). Se elige sola cuando el antivirus la aprueba.
  const [subeActa, setSubeActa] = useState(false);
  /** Ya se subió un acta para esta decisión: no se ofrece subir otra (MAN-05), salvo que la rechace el antivirus. */
  const [actaSubida, setActaSubida] = useState(false);
  const [archivoActa, setArchivoActa] = useState<File | null>(null);
  const [tituloActa, setTituloActa] = useState("");
  /** El último título que propuso un archivo: si la persona no lo cambió, otro archivo lo reemplaza. */
  const tituloActaPropuesto = useRef("");
  const [errorTituloActa, setErrorTituloActa] = useState<string | undefined>(undefined);
  /** El documento ya creado cuyo archivo no se subió: el reintento sube solo el archivo. */
  const [actaCreada, setActaCreada] = useState<{ documento: string; titulo: string } | null>(null);
  /** El archivo subido que el antivirus todavía revisa: cuando queda disponible, se elige. */
  const [actaEnRevision, setActaEnRevision] = useState<{ documento: string; version: string; titulo: string } | null>(null);
  const [estadoActa, setEstadoActa] = useState<{ texto: string; ok: boolean } | null>(null);
  /** Decisión 6 del propietario (FL-DEC-01): con el acta en revisión, «Registrar decisión» no manda la
   * decisión sin ella; espera, y la registra sola cuando el acta queda elegida. El ref lo lee la
   * respuesta del antivirus, que llega después del dibujo en que se pidió. */
  const [esperaElActa, setEsperaElActa] = useState(false);
  const esperandoActa = useRef(false);
  const claveCrearActa = useRef<string | null>(null);
  const claveSubirActa = useRef<string | null>(null);
  const textoActa = useRef<HTMLParagraphElement | null>(null);
  const alEstadoActa = useRef(false);

  // La invitación de la administración externa a asociar su cuenta (G-40; A7, cuatro ojos).
  const [invitaciones, setInvitaciones] = useState<Lectura<InvitacionMandato> | null>(null);
  const [correoInvitacion, setCorreoInvitacion] = useState("");
  const [referenciaInvitacion, setReferenciaInvitacion] = useState("");
  const claveInvitar = useRef<string | null>(null);
  const claveAprobar = useRef<string | null>(null);
  const claveRetirarInvitacion = useRef<string | null>(null);

  // Desasociar la cuenta de la administración externa (G-40, P1-c).
  const [referenciaDesasociar, setReferenciaDesasociar] = useState("");
  const claveDesasociar = useRef<string | null>(null);

  // G-44: el mandato recién dado de alta, resaltado en la lista hasta el próximo paso.
  const [mandatoNuevo, setMandatoNuevo] = useState<string | null>(null);
  const filaNueva = useRef<HTMLLIElement | null>(null);

  // El aviso de un acto que cerró su ventana o la ficha toma el foco. Corre después de que la hoja
  // que se cerró devolvió el foco a lo de abajo, en el mismo dibujo.
  useEffect(() => {
    if (!alAviso.current || aviso === null) return;
    alAviso.current = false;
    textoAviso.current?.focus();
  }, [aviso]);

  // El resultado de subir el acta toma el foco en la ventana de la decisión (como en Documentos).
  useEffect(() => {
    if (!alEstadoActa.current || estadoActa === null) return;
    alEstadoActa.current = false;
    textoActa.current?.focus();
  }, [estadoActa]);

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

  // G-44: cuando la lista vuelve con el mandato recién dado de alta, su fila queda a la vista. La
  // fila todavía no está mientras llega la lista nueva: el efecto vuelve a correr cuando llega.
  useEffect(() => {
    if (mandatoNuevo !== null && mandatos.tipo === "ok") filaNueva.current?.scrollIntoView({ block: "nearest" });
  }, [mandatoNuevo, mandatos]);

  /** El aviso de la página, con el foco: lo da un acto que cerró su ventana o la ficha. */
  function avisar(texto: string, ok: boolean, invitar?: FichaAbierta) {
    alAviso.current = true;
    setAviso({ texto, ok, invitar });
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
      // Lo que espera al acta es lo que estaba escrito: un cambio deja de esperar (FL-DEC-01).
      if (clave === claveDecision) esperarElActa(false);
      // Lo que se corrige deja de estar marcado (G-49).
      if (campo !== undefined) setErrores((e) => (e === null ? e : { ...e, campos: sinElDe(e.campos, campo) }));
      fijar(v);
    };
  }

  /** En la baja, además, un cambio deshace la pregunta: lo que se confirma es lo que se ve. */
  function cambiarBaja<T>(fijar: (v: T) => void, campo?: string) {
    return (v: T) => {
      setConfirmarBaja(false);
      cambiar(claveBaja, fijar, campo)(v);
    };
  }

  /** Lo que falta completar queda marcado en su campo y no se manda nada (decisión 4). */
  function faltan(accion: Accion, campos: Parameters<typeof faltaCompletar>[0]): boolean {
    const marcados = faltaCompletar(campos);
    if (!hayErrores(marcados)) return false;
    setErrores({ accion, campos: marcados });
    return true;
  }

  /**
   * Manda un acto. Un rechazo marca los campos que nombra y vuelve a la ventana con su motivo; si
   * `alRechazo` dice cómo seguir (la ficha quedó vieja), sigue así. Salido bien, `listo` dice cómo
   * termina la ventana.
   */
  async function pedirActo<T>(
    clave: Clave,
    accion: Accion,
    pedido: (clave: string) => Promise<Respuesta<T>>,
    listo: (datos: T) => FinDeEnvio,
    alRechazo?: (estado: number, codigo: string | undefined, motivo: string) => FinDeEnvio | undefined,
  ): Promise<FinDeEnvio> {
    clave.current ??= crypto.randomUUID();
    setEnviando(true);
    setAviso(null);
    setErrores(null);
    // El resaltado del mandato nuevo dura hasta el próximo paso (G-44).
    setMandatoNuevo(null);
    try {
      const { data, error, response } = await pedido(clave.current);
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
        const motivo = Object.keys(marcados).length > 0 ? resumenDe(marcados, nombres) : mensaje;
        return alRechazo?.(response.status, codigo, motivo) ?? { rechazo: motivo };
      }
      clave.current = null;
      setVuelta((v) => v + 1);
      return listo(data as T);
    } catch (e: unknown) {
      if (e instanceof SinSesion) {
        // La sesión se pide encima: lo escrito queda en la ventana para repetir el paso.
        aEntrar();
        return "seguir";
      }
      return { rechazo: SIN_CONEXION };
    } finally {
      setEnviando(false);
    }
  }

  function esperarElActa(espera: boolean) {
    esperandoActa.current = espera;
    setEsperaElActa(espera);
  }

  /**
   * «Registrar decisión». Con un acta subida acá que el antivirus todavía revisa, no se manda: queda
   * esperando y la registra `registrarConElActa` cuando el acta queda elegida (decisión 6, FL-DEC-01).
   * `huellaDelActa`: la del acta que acaba de pasar la revisión.
   */
  function registrarDecision(huellaDelActa?: string): FinDeEnvio | Promise<FinDeEnvio> {
    if (
      faltan("decision", {
        fecha: [fechaDecision, FALTA.fecha],
        descripcion: [descripcionDecision, FALTA.descripcion],
        referencia: [referencia, FALTA.referencia],
      })
    )
      return "seguir";
    if (huellaDelActa === undefined && actaEnRevision !== null) {
      esperarElActa(true);
      return "seguir";
    }
    // La huella de la versión disponible del acta elegida: así la decisión la cita (`0089`). Un acta
    // elegida que ya no está disponible no se cambia en silencio por «sin acta».
    const acta = actasElegibles.find((d) => d.id === actaElegida);
    const huellaElegida = acta === undefined ? undefined : disponibleDe(acta)?.sha256;
    if (huellaDelActa === undefined && actaElegida !== "" && huellaElegida === undefined)
      return { rechazo: POR_CODIGO.documento_invalido as string };
    const actaParaEnviar = huellaDelActa ?? huellaElegida ?? null;
    return pedirActo(
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
      () => {
        // Con el formulario vacío no se puede registrar dos veces lo mismo con una clave nueva.
        setTipoDecision(TIPO_DECISION_INICIAL);
        setFechaDecision("");
        setDescripcionDecision("");
        setReferencia("");
        setActaElegida("");
        // Un acta que se estaba subiendo o revisando ya no se elige para otra decisión, tampoco por
        // una respuesta que estaba en camino.
        generacionActa.current += 1;
        pedidoActas.current += 1;
        // La lista se vuelve a pedir al abrir la ventana: trae el acta si ya quedó disponible.
        setActas(null);
        cerrarSubidaActa();
        setActaSubida(false);
        setActaEnRevision(null);
        setEstadoActa(null);
        esperarElActa(false);
        // La decisión queda en la lista (FL-DEC-03: quién da el alta lo dice la línea de la página).
        setVentana(null);
        avisar(huellaDelActa === undefined ? LISTO_DECISION : LISTO_DECISION_CON_ACTA, true);
        return "cerrar";
      },
    );
  }

  /** El acta que esperaba la decisión pasó la revisión: la decisión se registra sola, con ella. Un
   * rechazo queda junto al campo «Acta» y vuelve el botón principal (FL-DEC-01). */
  async function registrarConElActa(huella: string) {
    esperarElActa(false);
    const fin = await registrarDecision(huella);
    if (typeof fin === "object")
      avisarActa(`El acta pasó la revisión del antivirus, pero la decisión no se registró. ${fin.rechazo}`, false, true);
  }

  const listaDecisiones = decisiones.tipo === "ok" ? decisiones.elementos : [];
  // A15: quien registró una decisión no la usa para dar de alta ni para remover. Las del equipo no lo
  // activan. Solo los selectores: la ficha y la lista siguen mostrando todas.
  const usables = listaDecisiones.filter((d) => !d.registrada_por_vos);
  const asambleas = usables.filter((d) => d.tipo === "asamblea");
  // FL-MAN-05: con una sola decisión (o asamblea) que se pueda usar, ya va elegida.
  const decisionAltaElegida = usables.length === 1 ? (usables[0]?.id ?? "") : decisionAlta;
  const asambleaBajaElegida = asambleas.length === 1 ? (asambleas[0]?.id ?? "") : decisionBaja;
  const asambleaRevisionElegida = asambleas.length === 1 ? (asambleas[0]?.id ?? "") : decisionRevision;
  const elegibles = candidatos.tipo === "ok" ? candidatos.elementos.filter((c) => c.persona_id !== yo) : [];
  // FL-MAN-01: con una administración vigente o futura sin fecha de fin, cualquier «Desde» choca: no se
  // ofrece otra administración. Se sabe con la lista de mandatos ya leída.
  const administracionSinFin =
    mandatos.tipo === "ok"
      ? mandatos.elementos.find((m) => ES_ADMINISTRACION.has(m.tipo) && OCUPAN_SUS_FECHAS.has(m.estado) && m.vigente_hasta === null)
      : undefined;
  // FL-MAN-09: el alta ofrece solo los tipos que se pueden dar. El Consejo y la administración piden
  // una persona con vínculo confirmado; la administración, además, que no haya otra sin fecha de fin.
  const tiposPosibles = TIPOS_MANDATO.filter(
    (t) => (t === "administrador_externo" || elegibles.length > 0) && !(ES_ADMINISTRACION.has(t) && administracionSinFin !== undefined),
  );
  const tipoAlta: TipoMandato | undefined = tiposPosibles.includes(tipoMandato) ? tipoMandato : tiposPosibles[0];
  const externo = tipoAlta === "administrador_externo";
  // Saber quién mira puede llegar después de la lista: lo elegido nunca es alguien que no se ofrece.
  const personaElegida = elegibles.some((c) => c.persona_id === persona) ? persona : (elegibles[0]?.persona_id ?? "");

  function darDeAlta(): FinDeEnvio | Promise<FinDeEnvio> {
    if (
      faltan("alta", {
        ...(externo ? { nombre: [nombre, FALTA.nombre] as const, cuit: [cuit, FALTA.cuit] as const } : {}),
        decision_id: [decisionAltaElegida, FALTA.decisionAlta],
        desde: [desde, FALTA.desde],
      })
    )
      return "seguir";
    // El CUIT con el verificador mal ya se ve marcado en el campo: no se manda.
    if (externo && cuitConError(cuit)) return "seguir";
    // Sin ningún tipo posible la ventana no se ofrece (FL-MAN-09).
    if (tipoAlta === undefined) return "seguir";
    const nombreAlta = externo ? nombre.trim() : (elegibles.find((c) => c.persona_id === personaElegida)?.nombre ?? "");
    return pedirActo(
      claveAlta,
      "alta",
      (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/mandatos", {
          params: { path: { consorcio_id: consorcio }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          headers: encabezadoCsrf(),
          body: {
            tipo: tipoAlta,
            decision_id: decisionAltaElegida,
            desde,
            hasta: hasta === "" ? null : hasta,
            persona_id: externo ? null : personaElegida,
            nombre: externo ? nombre.trim() : null,
            cuit: externo ? cuit : null,
            matricula: tipoAlta === "miembro_consejo" || matricula.trim() === "" ? null : matricula.trim(),
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
        // G-44: el «Listo» con lo que pasó, sin abrir la ficha (que ofrecería deshacerlo).
        setVentana(null);
        // FL-MAN-06: para que la administración externa entre, lo que sigue es invitarla.
        const invitarla =
          externo && puede("mandatos.asociar_persona") && puede("mandatos.ver_asociaciones")
            ? { tipo: "mandato" as const, id: datos.id, titulo: tituloDeMandato({ nombre: nombreAlta, tipo: "administrador_externo" }) }
            : undefined;
        avisar(`Listo: el mandato de ${nombreAlta} quedó dado de alta y ya está en la lista.`, true, invitarla);
        // G-44: su fila queda resaltada y a la vista cuando llega la lista nueva.
        setMandatoNuevo(datos.id);
        return "cerrar";
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

  /** Cierra la ficha. Si la cierra un resultado, el foco va a su aviso y no a la fila. */
  function cerrarFicha(devolverFoco = true) {
    pedidoDetalle.current += 1;
    setAccionFicha(null);
    setResultadoFicha(null);
    setErrores(null);
    setDetalle(null);
    setFicha(null);
    setViendoActa(null);
    fichas.cerrar(devolverFoco);
  }

  /** Un acto que deja vieja la ficha (o que la resolvió): se cierra y el aviso queda en la página. */
  function cerrarFichaCon(texto: string, ok: boolean): FinDeEnvio {
    cerrarFicha(false);
    avisar(texto, ok);
    return "cerrar";
  }

  /** Cierra la ventana de la ficha: lo escrito se pierde y la ficha vuelve a sus datos y botones. */
  function cerrarAccionFicha() {
    setAccionFicha(null);
    setErrores(null);
    limpiarBaja();
    limpiarRevision();
    claveDesasociar.current = null;
    setReferenciaDesasociar("");
    claveInvitar.current = null;
    setCorreoInvitacion("");
    setReferenciaInvitacion("");
  }

  function abrirAccionFicha(accion: AccionDeFicha) {
    cerrarAccionFicha();
    setResultadoFicha(null);
    setAccionFicha(accion);
  }

  function darDeBaja(mandato: MandatoDetalle): FinDeEnvio | Promise<FinDeEnvio> {
    const remocion = causa === "remocion";
    if (
      faltan("baja", {
        descripcion: [descripcionBaja, FALTA.descripcionBaja],
        ...(remocion ? { decision_id: [asambleaBajaElegida, FALTA.asambleaBaja] as const } : { instrumento: [instrumento, FALTA.instrumento] as const }),
      })
    ) {
      setConfirmarBaja(false);
      return "seguir";
    }
    // La baja no se deshace: primero la pregunta, con lo que se va a mandar a la vista.
    if (!confirmarBaja) {
      setConfirmarBaja(true);
      return "seguir";
    }
    return pedirActo(
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
            decision_id: remocion ? asambleaBajaElegida : null,
            instrumento: remocion ? null : instrumento.trim(),
          },
        }),
      () => {
        limpiarBaja();
        return cerrarFichaCon(`Listo: el mandato de ${mandato.nombre} quedó dado de baja.`, true);
      },
      (estado, codigo, motivo) => {
        // Un error deshace la pregunta: el reintento se vuelve a confirmar.
        setConfirmarBaja(false);
        if (estado !== ESTADO.noEncontrado && (codigo === undefined || !BAJA_SOBRE_ESTADO_VIEJO.includes(codigo))) return undefined;
        // La ficha quedó vieja: se cierra, el motivo queda en la página y la lista se vuelve a pedir.
        setVuelta((v) => v + 1);
        return cerrarFichaCon(motivo, false);
      },
    );
  }

  /**
   * Un detalle que no llega: la ficha se cierra y el motivo queda en la página. Si se pidió para
   * actualizar la ficha después de un acto, el «Listo» de ese acto sigue siendo lo que pasó.
   */
  function falloDetalle(texto: string) {
    cerrarFicha(false);
    avisar(texto, false);
  }

  /** Abre la ficha con el título de la fila y «Consultando…» hasta que llegan sus datos. */
  function abrirFicha(abierta: FichaAbierta) {
    setAviso(null);
    setMandatoNuevo(null);
    setAccionFicha(null);
    setResultadoFicha(null);
    setErrores(null);
    setFicha(abierta);
    fichas.abrir(abierta.id);
  }

  /** FL-MAN-06: desde el «Listo» del alta, la ficha de la administración nueva con su invitación abierta. */
  function invitarLaNueva(nueva: FichaAbierta) {
    abrirFicha(nueva);
    setAccionFicha("invitar");
    pedirMandato(nueva.id);
  }

  function verMandato(mandato: Mandato) {
    abrirFicha({ tipo: "mandato", id: mandato.id, titulo: tituloDeMandato(mandato) });
    pedirMandato(mandato.id);
  }

  function pedirMandato(mandato: string) {
    const numero = ++pedidoDetalle.current;
    // La ficha anterior (y su baja) no queda operable mientras llega la que se eligió.
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
          claveDesasociar.current = null;
          setReferenciaDesasociar("");
          setCausa(data.estado === "terminado" ? "error_carga" : "renuncia");
          setDetalle({ tipo: "mandato", datos: data });
          if (admiteInvitacion(data)) pedirInvitaciones(data.id);
          return;
        }
        if (response.status === ESTADO.sinPermiso) falloDetalle("Esta cuenta no tiene permiso para ver ese mandato.");
        else if (response.status === ESTADO.noEncontrado) {
          falloDetalle("Ese mandato ya no está en este edificio.");
          setVuelta((v) => v + 1);
        } else falloDetalle(mensajeDeFalla(response.status, codigoDe(error)));
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion) aEntrar();
        else if (numero === pedidoDetalle.current) falloDetalle(SIN_CONEXION);
      });
  }

  function verDecision(decision: Decision) {
    abrirFicha({ tipo: "decision", id: decision.id, titulo: tituloDeDecision(decision) });
    const numero = ++pedidoDetalle.current;
    setDetalle(null);
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/decisiones/{decision_id}", {
        params: { path: { consorcio_id: consorcio, decision_id: decision.id } },
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
        if (response.status === ESTADO.sinPermiso) falloDetalle("Esta cuenta no tiene permiso para ver esa decisión.");
        else if (response.status === ESTADO.noEncontrado) {
          falloDetalle("Esa decisión ya no está en este edificio.");
          setVuelta((v) => v + 1);
        } else falloDetalle(mensajeDeFalla(response.status, codigoDe(error)));
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion) aEntrar();
        else if (numero === pedidoDetalle.current) falloDetalle(SIN_CONEXION);
      });
  }

  /** Las actas de Documentos, una vez por edificio: al abrir la ventana de la decisión o una ficha
   * con acta. Si no se pudieron consultar, se vuelven a pedir la próxima vez. `elegir`: el acta
   * recién subida acá, que vuelve a pedir la lista sin esperar a la próxima vez y, si ya está
   * disponible, queda elegida (G-15). */
  function pedirActas(elegir?: string) {
    if (!veActas || consorcio === "") return;
    if (elegir === undefined && actas !== null && actas.tipo !== "sin_servicio") return;
    const numero = ++pedidoActas.current;
    // Al volver a pedirla por el acta nueva, la lista a la vista queda mientras llega la otra.
    if (elegir === undefined) setActas(CARGANDO);
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/documentos", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, response }) => {
        exigirSesion(response);
        if (numero !== pedidoActas.current) return;
        if (data === undefined) {
          setActas(response.status === ESTADO.sinPermiso ? SIN_PERMISO_DE_LECTURA : SIN_SERVICIO);
          return;
        }
        setActas({ tipo: "ok", elementos: data.documentos });
        const nueva = elegir === undefined ? undefined : data.documentos.find((d) => d.id === elegir);
        const disponible = nueva !== undefined && nueva.categoria === CATEGORIA_ACTAS ? disponibleDe(nueva) : undefined;
        if (nueva !== undefined && disponible !== undefined) {
          // Leído antes de `cambiar`, que deja de esperar: elegir el acta no es un cambio de la persona.
          const laEsperaba = esperandoActa.current;
          cambiar(claveDecision, setActaElegida, "sha256")(nueva.id);
          setActaEnRevision(null);
          if (laEsperaba) void registrarConElActa(disponible.sha256);
          else avisarActa(`Listo: el acta «${nueva.titulo}» pasó la revisión del antivirus y quedó elegida.`, true);
        }
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion) aEntrar();
        else if (numero === pedidoActas.current) setActas(SIN_SERVICIO);
      });
  }

  /** Abre una de las dos ventanas de la página; la de la decisión pide las actas para elegir. */
  function abrirVentana(cual: VentanaDePagina) {
    if (cual === "decision") pedirActas();
    setAviso(null);
    setMandatoNuevo(null);
    setErrores(null);
    setVentana(cual);
  }

  /** «Cancelar» o Escape: lo escrito queda para la próxima vez que se abra. */
  function cerrarVentana() {
    // Cerrada la ventana, la decisión que esperaba al acta no se registra (FL-DEC-01).
    esperarElActa(false);
    setErrores(null);
    setVentana(null);
  }

  /** Lo que pasó con el acta que se sube acá; `enfocar` cuando responde a un toque de la persona. */
  function avisarActa(texto: string, ok: boolean, enfocar = false) {
    if (enfocar) alEstadoActa.current = true;
    setEstadoActa({ texto, ok });
  }

  /** «Subir el acta»: el acta queda en Documentos con la fecha del acto, así que primero la fecha (FL-DEC-02). */
  function abrirSubidaActa() {
    if (fechaDecision === "") {
      setErrores({ accion: "decision", campos: { fecha: FALTA.fecha } });
      document.getElementById(`${id}-fecha-decision`)?.focus();
      return;
    }
    setSubeActa(true);
  }

  function cerrarSubidaActa() {
    tituloActaPropuesto.current = "";
    claveCrearActa.current = null;
    claveSubirActa.current = null;
    setArchivoActa(null);
    setTituloActa("");
    setErrorTituloActa(undefined);
    setActaCreada(null);
    setSubeActa(false);
  }

  function cambiarTituloActa(valor: string) {
    claveCrearActa.current = null;
    setErrorTituloActa(undefined);
    setTituloActa(valor);
  }

  function elegirArchivoActa(elegido: File) {
    claveSubirActa.current = null;
    setArchivoActa(elegido);
    // El nombre del archivo propone el título (como en Documentos), sin pisar uno que la persona escribió.
    const propuesto = tituloDeArchivo(elegido, "", LARGO_TITULO);
    if (propuesto !== "" && (tituloActa === "" || tituloActa === tituloActaPropuesto.current)) {
      tituloActaPropuesto.current = propuesto;
      cambiarTituloActa(propuesto);
    }
  }

  /** «Subir el acta»: crea el documento (salvo que ya se haya creado) y sube el archivo. */
  async function subirActa(): Promise<FinDeEnvio> {
    if (archivoActa === null) return { rechazo: FALTA.archivoActa };
    const titulo = actaCreada?.titulo ?? tituloActa.trim();
    if (titulo === "") {
      setErrorTituloActa(FALTA.tituloActa);
      return "seguir";
    }
    setEnviando(true);
    setEstadoActa(null);
    // Otra subida reemplaza a la anterior: lo que esa tenía en camino ya no elige nada.
    generacionActa.current += 1;
    pedidoActas.current += 1;
    setActaEnRevision(null);
    const generacion = generacionActa.current;
    try {
      const resultado = actaCreada !== null ? { creada: actaCreada.documento } : await crearActa(titulo, generacion);
      if ("fin" in resultado) return resultado.fin;
      return await subirArchivoActa(resultado.creada, titulo, archivoActa, actaCreada === null, generacion);
    } catch (e: unknown) {
      if (e instanceof SinSesion) {
        aEntrar();
        return "seguir";
      }
      return { rechazo: SIN_CONEXION };
    } finally {
      setEnviando(false);
    }
  }

  /** Primer paso: el documento en «Actas y decisiones», con la fecha del acto, que «Subir el acta» pide
   * antes de abrir su ventana (FL-DEC-02). Devuelve su id o cómo sigue la ventana. */
  /** El id del acta creada, o cómo termina la ventana: «seguir» no es un id (Codex en #257, mismo caso que Documentos). */
  async function crearActa(titulo: string, generacion: number): Promise<{ creada: string } | { fin: FinDeEnvio }> {
    claveCrearActa.current ??= crypto.randomUUID();
    const { data, error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/documentos", {
      headers: encabezadoCsrf(),
      params: { path: { consorcio_id: consorcio }, header: { [ENCABEZADO_IDEMPOTENCIA]: claveCrearActa.current } },
      body: { categoria: CATEGORIA_ACTAS, titulo, fecha: fechaDecision },
    });
    exigirSesion(response);
    if (generacion !== generacionActa.current) return { fin: "seguir" };
    if (data === undefined) {
      const codigo = codigoDe(error);
      if (renovarClave(response.status, codigo)) claveCrearActa.current = null;
      const campos = erroresDe(error, REVISAR_DATO, MENSAJES_PROPIOS);
      setErrorTituloActa(campos.titulo);
      return { fin: { rechazo: mensajeDeCarga(response.status, codigo, "crear", Object.keys(campos).map((c) => `body.${c}`)) } };
    }
    claveCrearActa.current = null;
    return { creada: data.id };
  }

  /** Segundo paso: el archivo crudo, con su tipo como `Content-Type`, igual que en Documentos. */
  async function subirArchivoActa(documento: string, titulo: string, elegido: File, recienCreado: boolean, generacion: number): Promise<FinDeEnvio> {
    claveSubirActa.current ??= crypto.randomUUID();
    // Si el documento se acaba de crear y el archivo no entra, el documento queda sin archivo: se
    // dice, y el reintento sube el archivo a ese mismo documento.
    const fallo = (texto: string): FinDeEnvio => {
      if (recienCreado) setActaCreada({ documento, titulo });
      return { rechazo: recienCreado ? `El acta «${titulo}» quedó creada en Documentos, pero el archivo no se subió. ${texto}` : texto };
    };
    try {
      const { data, error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/documentos/{documento_id}/versiones", {
        headers: { ...encabezadoCsrf(), "Content-Type": elegido.type },
        params: {
          path: { consorcio_id: consorcio, documento_id: documento },
          header: { [ENCABEZADO_IDEMPOTENCIA]: claveSubirActa.current },
        },
        // El generador tipa `format: binary` como texto; el cuerpo es el archivo tal cual.
        body: elegido as unknown as string,
        bodySerializer: (cuerpo) => cuerpo,
      });
      exigirSesion(response);
      if (generacion !== generacionActa.current) return "seguir";
      if (data === undefined) {
        const codigo = codigoDe(error);
        if (renovarClave(response.status, codigo)) claveSubirActa.current = null;
        return fallo(mensajeDeCarga(response.status, codigo, "subir", [], response.headers.has("Retry-After")));
      }
      // La ventana del acta se cierra y el resultado queda junto al campo «Acta» (MAN-04).
      cerrarSubidaActa();
      setActaSubida(true);
      setActaEnRevision({ documento, version: data.id, titulo });
      avisarActa("Listo: el acta se subió. Se elige sola cuando termine la revisión del antivirus.", true, true);
      pedirActas(documento);
      return "cerrar";
    } catch (e: unknown) {
      if (e instanceof SinSesion) throw e;
      return fallo(SIN_CONEXION);
    }
  }

  /** Cómo va el acta subida acá: disponible (se elige), rechazada o todavía en revisión. */
  function mirarActaEnRevision() {
    if (actaEnRevision === null) return;
    const cual = actaEnRevision;
    const generacion = generacionActa.current;
    const noSeSupo = `No se pudo consultar si el acta «${cual.titulo}» ya pasó la revisión del antivirus: se vuelve a consultar sola.`;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/documentos/{documento_id}/versiones/{version_id}/estado", {
        params: { path: { consorcio_id: consorcio, documento_id: cual.documento, version_id: cual.version } },
      })
      .then(({ data, response }) => {
        exigirSesion(response);
        if (generacion !== generacionActa.current) return;
        if (data === undefined) avisarActa(noSeSupo, false);
        else if (data.estado === "disponible") pedirActas(cual.documento);
        else if (data.estado === "rechazado") {
          setActaEnRevision(null);
          // Es el único caso en que se vuelve a ofrecer subir un acta para esta decisión (MAN-05).
          setActaSubida(false);
          // La decisión que lo esperaba no se registra: vuelve su botón, y se decide con otra acta o sin ella.
          const laEsperaba = esperandoActa.current;
          esperarElActa(false);
          avisarActa(
            `El archivo del acta «${cual.titulo}» no pasó la revisión del antivirus y no se puede usar: hay que subir otro.${
              laEsperaba ? " La decisión no se registró." : ""
            }`,
            false,
          );
        }
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion) aEntrar();
        else if (generacion === generacionActa.current) avisarActa(noSeSupo, false);
      });
  }
  const antivirusTarda = useConsultaDelAntivirus(ventana === "decision" && actaEnRevision !== null, mirarActaEnRevision);

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
  }

  function limpiarRevision() {
    claveRevision.current = null;
    setDecisionRevision("");
    setEficazDesde("");
  }

  /** Un acto que deja la ficha abierta: la ventana se cierra y el resultado sale en la ficha. */
  function enLaFicha(texto: string, ok: boolean): FinDeEnvio {
    setAccionFicha(null);
    setResultadoFicha({ texto, ok });
    return "cerrar";
  }

  /** Emitir la invitación: queda por aprobar, sin correo (A7). */
  function invitar(mandato: MandatoDetalle): FinDeEnvio | Promise<FinDeEnvio> {
    if (
      faltan("invitar", {
        correo: [correoInvitacion, FALTA.correo],
        referencia: [referenciaInvitacion, FALTA.referenciaInvitacion],
      })
    )
      return "seguir";
    return pedirActo(
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
        pedirInvitaciones(mandato.id);
        return enLaFicha("Listo: la invitación quedó por aprobar.", true);
      },
    );
  }

  /** Aprobar la invitación que emitió otra persona: recién ahí sale el correo con el enlace. Sin
   * campos: se aprueba desde su botón y el resultado sale en la ficha. */
  async function aprobarInvitacion(invitacion: InvitacionMandato, mandato: MandatoDetalle) {
    setResultadoFicha(null);
    const fin = await pedirActo(
      claveAprobar,
      "aprobar_invitacion",
      (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/invitaciones-mandato/{invitacion_id}/aprobar", {
          params: { path: { consorcio_id: consorcio, invitacion_id: invitacion.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          headers: encabezadoCsrf(),
        }),
      (datos) => {
        pedirInvitaciones(mandato.id);
        return enLaFicha(`Listo: la invitación quedó aprobada y salió el correo con el enlace. Vence el ${momento(datos.vence_en)}.`, true);
      },
      (estado) => {
        if (estado === ESTADO.noEncontrado || estado === ESTADO.conflicto) pedirInvitaciones(mandato.id);
        return undefined;
      },
    );
    if (typeof fin === "object") setResultadoFicha({ texto: fin.rechazo, ok: false });
  }

  /** Retirar la invitación que no se aceptó: el enlace, si salió, deja de servir. */
  function retirarInvitacion(invitacion: InvitacionMandato, mandato: MandatoDetalle): Promise<FinDeEnvio> {
    return pedirActo(
      claveRetirarInvitacion,
      "retirar_invitacion",
      (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/invitaciones-mandato/{invitacion_id}/revocar", {
          params: { path: { consorcio_id: consorcio, invitacion_id: invitacion.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          headers: encabezadoCsrf(),
        }),
      () => {
        pedirInvitaciones(mandato.id);
        return enLaFicha("Listo: la invitación se retiró.", true);
      },
      (estado, _codigo, motivo) => {
        if (estado !== ESTADO.noEncontrado && estado !== ESTADO.conflicto) return undefined;
        // La invitación ya cambió: no hay qué retirar, y la ficha se actualiza.
        pedirInvitaciones(mandato.id);
        return enLaFicha(motivo, false);
      },
    );
  }

  /** Una administración externa con cuenta, que no es la de quien mira, admite desasociarla (G-40, P1-c). */
  function admiteDesasociar(m: MandatoDetalle): boolean {
    return m.tipo === "administrador_externo" && m.con_cuenta && !m.es_propio && puede("mandatos.desasociar_persona");
  }

  /** Desasociar la cuenta que no es la de la administración: el mandato queda como estaba, sin cuenta. */
  function desasociar(mandato: MandatoDetalle): FinDeEnvio | Promise<FinDeEnvio> {
    if (faltan("desasociar", { referencia: [referenciaDesasociar, FALTA.referenciaDesasociar] })) return "seguir";
    return pedirActo(
      claveDesasociar,
      "desasociar",
      (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/mandatos/{mandato_id}/desasociar-persona", {
          params: { path: { consorcio_id: consorcio, mandato_id: mandato.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          headers: encabezadoCsrf(),
          body: { referencia: referenciaDesasociar.trim() },
        }),
      () => {
        // La ficha vuelve sin cuenta y con la invitación para asociar otra.
        pedirMandato(mandato.id);
        return enLaFicha("Listo: la cuenta quedó desasociada del mandato.", true);
      },
      (estado, codigo, motivo) => {
        if (estado !== ESTADO.noEncontrado && (codigo === undefined || !DESASOCIAR_SOBRE_ESTADO_VIEJO.includes(codigo))) return undefined;
        pedirMandato(mandato.id);
        return enLaFicha(motivo, false);
      },
    );
  }

  /** Mantener un mandato pendiente de revisión, con una asamblea y desde una fecha (G-41, E62). */
  function mantener(mandato: MandatoDetalle): FinDeEnvio | Promise<FinDeEnvio> {
    if (
      faltan("revision", {
        decision_id: [asambleaRevisionElegida, FALTA.asambleaRevision],
        eficaz_desde: [eficazDesde, FALTA.eficazDesde],
      })
    )
      return "seguir";
    return pedirActo(
      claveRevision,
      "revision",
      (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/mandatos/{mandato_id}/revision", {
          params: { path: { consorcio_id: consorcio, mandato_id: mandato.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          headers: encabezadoCsrf(),
          body: { decision_id: asambleaRevisionElegida, eficaz_desde: eficazDesde },
        }),
      (datos) => {
        limpiarRevision();
        return cerrarFichaCon(`Listo: el mandato de ${mandato.nombre} se mantiene y vuelve a operar desde el ${dia(datos.eficaz_desde)}.`, true);
      },
    );
  }

  function elegirEdificio(valor: string) {
    // Nada del edificio anterior queda elegible mientras llegan los datos del nuevo.
    for (const clave of [claveDecision, claveAlta, claveBaja]) clave.current = null;
    pedidoActas.current += 1;
    generacionActa.current += 1;
    setActas(null);
    setActaElegida("");
    cerrarSubidaActa();
    setActaSubida(false);
    setActaEnRevision(null);
    setEstadoActa(null);
    esperarElActa(false);
    setMandatoNuevo(null);
    limpiarInvitacion();
    limpiarRevision();
    cerrarFicha(false);
    setMandatos(CARGANDO);
    setDecisiones(CARGANDO);
    setCandidatos(CARGANDO);
    setVentana(null);
    setDecisionAlta("");
    limpiarBaja();
    setPersona("");
    setAviso(null);
    setConsorcio(valor);
  }

  // El alta pide elegir a quién (`candidatos`) y qué decisión la respalda: sin una de las dos no se puede dar.
  const ofreceAlta = puede("mandatos.alta") && candidatos.tipo !== "sin_permiso" && decisiones.tipo !== "sin_permiso";
  // C8G-03: la remoción se ofrece solo con una asamblea que se pueda usar, sabida con la lista leída.
  const ofreceRemocion = decisiones.tipo === "ok" && asambleas.length > 0;
  const textoDecision = (d: Decision) => `${TEXTO_TIPO_DECISION[d.tipo]} del ${dia(d.fecha)}: ${d.descripcion}`;
  // Las actas que se pueden elegir: las de «Actas y decisiones» con un archivo disponible (G-15).
  const actasElegibles =
    actas?.tipo === "ok" ? actas.elementos.filter((d) => d.categoria === CATEGORIA_ACTAS && disponibleDe(d) !== undefined) : [];
  /** El documento cuyo archivo disponible tiene esa huella: el acta de la decisión, para verla. */
  const actaDeLaHuella = (sha256: string) => {
    if (actas?.tipo !== "ok") return undefined;
    for (const documento of actas.elementos) {
      const version = documento.versiones.find((v) => v.estado === "disponible" && v.sha256.toLowerCase() === sha256.toLowerCase());
      if (version !== undefined) return { documento, version };
    }
    return undefined;
  };
  // MAN-14: las tres listas llegan juntas; mientras tanto, un solo «Consultando…» y nada más. Al
  // reintentar una que no se pudo consultar, las que están a la vista quedan y solo esa lo dice.
  const lecturas = [mandatos, decisiones, candidatos];
  const listasCargando =
    lecturas.some((l) => l.tipo === "cargando") && lecturas.every((l) => l.tipo === "cargando" || l.tipo === "sin_permiso");
  const listaDeMandatos = mandatos.tipo === "ok" ? mandatos.elementos : [];
  const actuales = listaDeMandatos.filter((m) => !YA_NO_RIGEN.has(m.estado));
  const anteriores = listaDeMandatos.filter((m) => YA_NO_RIGEN.has(m.estado));
  // G-42: sin una decisión que se pueda usar, el paso que sigue es registrarla (la registra una
  // persona y da el alta otra); con alguna, el alta.
  const altaConDecision = ofreceAlta && candidatos.tipo === "ok" && decisiones.tipo === "ok" && usables.length > 0;
  // FL-MAN-09: sin ningún tipo que se pueda dar, en lugar del botón, por qué.
  const altaListaParaDar = altaConDecision && tiposPosibles.length > 0;
  const altaSinTipos = altaConDecision && tiposPosibles.length === 0;
  const invitarLaDelAviso = aviso?.invitar;
  // Un solo principal: con el «Invitar a la administración» del «Listo» (FL-MAN-06), los de arriba van secundarios.
  const nadaAbierto = ventana === null && ficha === null && invitarLaDelAviso === undefined;
  const conMensaje = aviso !== null || pantalla.tipo !== "lista" || listasCargando;
  // Hasta tres listas pueden caerse a la vez: cada botón dice de qué sección es.
  const consultarDeNuevo = (seccion: string) => (
    <button className="secundario" type="button" onClick={reconsultarListas} disabled={enviando} aria-describedby={seccion}>
      Consultar de nuevo
    </button>
  );
  const resumenDeFila = (m: Mandato) => {
    const falta = loQueFalta(m);
    if (falta === undefined || loResuelveQuienMira(m)) return m.estado === "pendiente_revision" ? undefined : estadoCorto(m);
    return m.estado === "pendiente_revision" ? falta : `${estadoCorto(m)} · ${falta}`;
  };
  const filaDeMandato = (m: Mandato) => (
    // G-44: la del mandato recién dado de alta se resalta y lo dice en palabras (no solo con el color).
    <li key={m.id} ref={m.id === mandatoNuevo ? filaNueva : undefined} className={m.id === mandatoNuevo ? "fila-nueva" : undefined}>
      <FilaDeRegistro
        titulo={tituloDeMandato(m)}
        resumen={resumenDeFila(m)}
        estado={loResuelveQuienMira(m) ? loQueFalta(m) : undefined}
        alAbrir={() => verMandato(m)}
        refFila={fichas.refFila(m.id)}
        deshabilitada={enviando}
        abierta={fichas.abierta === m.id}
      />
      {m.id === mandatoNuevo && <span className="fila-nueva__marca">{RECIEN_DADO_DE_ALTA}</span>}
    </li>
  );

  return (
    <>
      <h1>Mandatos y decisiones</h1>

      <div role="status" aria-live="polite" className={conMensaje ? "tarjeta" : undefined}>
        {(pantalla.tipo === "cargando" || (pantalla.tipo === "lista" && listasCargando)) && <p>Consultando…</p>}
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
        {aviso !== null && (
          <p ref={textoAviso} tabIndex={-1} className={aviso.ok ? "aviso-ok" : "aviso-error"}>
            {aviso.texto}
          </p>
        )}
        {invitarLaDelAviso !== undefined && (
          <p>
            <button type="button" disabled={enviando} onClick={() => invitarLaNueva(invitarLaDelAviso)}>
              Invitar a la administración
            </button>
          </p>
        )}
      </div>

      {pantalla.tipo === "lista" && (
        <>
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

          {!listasCargando && accionesDeLaPagina()}

          {!listasCargando && mandatos.tipo !== "sin_permiso" && (
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
            {mandatos.tipo === "ok" && actuales.length > 0 && (
              <ul className="filas-registro" aria-labelledby={`${id}-mandatos`}>
                {/* Cada fila abre la ficha del mandato (comentario 21), con su baja si el rol la permite. */}
                {actuales.map(filaDeMandato)}
              </ul>
            )}
            {/* Lo que ya no rige queda plegado debajo (D-34, decisión 7 del propietario del 14-11-2026). */}
            {mandatos.tipo === "ok" && anteriores.length > 0 && (
              <details className="historial">
                <summary>{`${MANDATOS_ANTERIORES} (${anteriores.length})`}</summary>
                <ul className="filas-registro" aria-label={MANDATOS_ANTERIORES}>
                  {anteriores.map(filaDeMandato)}
                </ul>
              </details>
            )}
          </section>
          )}

          {!listasCargando && decisiones.tipo !== "sin_permiso" && (
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
                      titulo={tituloDeDecision(d)}
                      resumen={recortar(d.descripcion)}
                      alAbrir={() => verDecision(d)}
                      refFila={fichas.refFila(d.id)}
                      deshabilitada={enviando}
                      abierta={fichas.abierta === d.id}
                    />
                  </li>
                ))}
              </ul>
            )}
          </section>
          )}

          {registraDecisiones && ventana === "decision" && ventanaDecision()}
          {registraDecisiones && ventana === "decision" && subeActa && ventanaActa()}
          {altaListaParaDar && ventana === "alta" && tipoAlta !== undefined && ventanaAlta(tipoAlta)}
          {ficha !== null && fichaAbierta(ficha)}
        </>
      )}
    </>
  );

  function tituloDeMandato(m: { nombre: string; tipo: TipoMandato }) {
    return `${m.nombre}, ${TEXTO_TIPO_MANDATO[m.tipo]}`;
  }

  function tituloDeDecision(d: { tipo: TipoDecision; fecha: string }) {
    return `${TEXTO_TIPO_DECISION[d.tipo]} del ${dia(d.fecha)}`;
  }

  /** Un mandato en revisión o una administración sin cuenta: lo que alguien tiene que resolver. */
  function loQueFalta(m: Mandato): string | undefined {
    if (m.estado === "pendiente_revision") return EN_REVISION;
    if (m.tipo === "administrador_externo" && !m.con_cuenta && ESTADOS_CON_INVITACION.includes(m.estado)) return SIN_CUENTA;
    return undefined;
  }

  /** FL-MAN-10: lo que falta se destaca como etiqueta solo para quien puede resolverlo; para el resto
   * va en el resumen de la fila, sin destacar. */
  function loResuelveQuienMira(m: Mandato): boolean {
    if (m.estado === "pendiente_revision") return puede("mandatos.resolver_revision") || puede("mandatos.baja");
    return puede("mandatos.asociar_persona");
  }

  /** Los dos botones de arriba; en lugar del alta que no se puede dar, qué falta (C8G-01). */
  function accionesDeLaPagina() {
    const altaCaida = ofreceAlta && (candidatos.tipo === "sin_servicio" || decisiones.tipo === "sin_servicio");
    const sinDecisionUsable = ofreceAlta && !altaCaida && decisiones.tipo === "ok" && usables.length === 0;
    if (!ofreceAlta && !registraDecisiones) return null;
    return (
      <>
        {(altaListaParaDar || registraDecisiones) && (
          <p className="acciones-pantalla">
            {altaListaParaDar && (
              <button type="button" className={nadaAbierto ? undefined : "secundario"} disabled={enviando} onClick={() => abrirVentana("alta")}>
                Dar de alta un mandato
              </button>
            )}
            {registraDecisiones && (
              <button
                type="button"
                className={nadaAbierto && !altaListaParaDar ? undefined : "secundario"}
                disabled={enviando}
                onClick={() => abrirVentana("decision")}
              >
                Registrar una decisión
              </button>
            )}
          </p>
        )}
        {sinDecisionUsable && (
          <ComandoNoDisponible motivo={registraDecisiones && listaDecisiones.length === 0 ? ALTA_SIN_DECISIONES : ALTA_SIN_DECISION_USABLE} />
        )}
        {altaSinTipos && tiposQueFaltan()}
        {altaCaida && candidatos.tipo === "sin_servicio" && (
          <>
            <p id={`${id}-alta-caida`}>No se pudo consultar quiénes pueden recibir un mandato: sin eso no se puede dar un alta.</p>
            {consultarDeNuevo(`${id}-alta-caida`)}
          </>
        )}
      </>
    );
  }

  /** «Registrar una decisión», en su ventana (MAN-03); el acta se sube en otra, encima (MAN-04). */
  function ventanaDecision() {
    return (
      <VentanaDeFormulario
        titulo="Registrar una decisión"
        textoEnviar="Registrar decisión"
        alEnviar={() => registrarDecision()}
        alCerrar={cerrarVentana}
        sinEnviar={esperaElActa}
      >
        <label htmlFor={`${id}-tipo-decision`}>Tipo</label>
        <select
          id={`${id}-tipo-decision`}
          data-foco
          {...marcaDe(`${id}-tipo-decision`, errorEn("decision", "tipo"))}
          value={tipoDecision}
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
          // FL-DEC-04: se registran actos ya tomados (`fecha_futura`).
          max={hoyDelDispositivo()}
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
          value={referencia}
          onValor={cambiar(claveDecision, setReferencia, "referencia")}
        />
        <ErrorDeCampo idCampo={`${id}-referencia`} mensaje={errorEn("decision", "referencia")} />
        {veActas && actas?.tipo !== "sin_permiso" && campoDelActa()}
      </VentanaDeFormulario>
    );
  }

  /** El acta de la decisión: se elige de Documentos y, si no está, se sube en su ventana (G-15). */
  function campoDelActa() {
    return (
      <>
        {actas?.tipo === "ok" ? (
          <>
            <label htmlFor={`${id}-acta`}>Acta (opcional)</label>
            <select
              id={`${id}-acta`}
              {...marcaDe(`${id}-acta`, errorEn("decision", "sha256"), `${id}-ayuda-acta`)}
              value={actaElegida}
              onChange={(e) => cambiar(claveDecision, setActaElegida, "sha256")(e.target.value)}
            >
              <option value="">Sin un acta de Documentos</option>
              {actasElegibles.map((d) => (
                <option key={d.id} value={d.id}>
                  {recortar(`${d.titulo} (${dia(d.fecha)})`)}
                </option>
              ))}
            </select>
            <p id={`${id}-ayuda-acta`}>Sale de Documentos, «Actas y decisiones».</p>
          </>
        ) : actas?.tipo === "sin_servicio" ? (
          <p>No se pudieron consultar las actas de Documentos: la decisión se puede registrar sin ella.</p>
        ) : (
          <p>Consultando las actas de Documentos…</p>
        )}
        <ErrorDeCampo idCampo={`${id}-acta`} mensaje={errorEn("decision", "sha256")} />
        {/* MAN-05: subida un acta para esta decisión, no se ofrece otra (salvo que la rechace el antivirus). */}
        {cargaDocumentos && actas?.tipo === "ok" && !actaSubida && actaEnRevision === null && (
          <p>
            <button type="button" className="secundario" onClick={abrirSubidaActa}>
              Subir el acta
            </button>
          </p>
        )}
        {estadoActa !== null && (
          <p ref={textoActa} tabIndex={-1} role={estadoActa.ok ? undefined : "alert"} aria-live={estadoActa.ok ? "polite" : undefined}
            className={estadoActa.ok ? "aviso-ok" : "aviso-error"}>
            {estadoActa.texto}
          </p>
        )}
        {/* FL-DEC-01: con el acta en revisión, la decisión espera; no se invita a registrarla sin ella. */}
        {actaEnRevision !== null && esperaElActa && <p>{DECISION_ESPERA_EL_ACTA}</p>}
        {actaEnRevision !== null && antivirusTarda && <p>El antivirus está tardando: se sigue consultando.</p>}
      </>
    );
  }

  /** Subir el acta (G-15), en su ventana encima de la de la decisión: el archivo propone el título. */
  function ventanaActa() {
    const titulo = `${id}-subir-acta`;
    return (
      <VentanaDeFormulario titulo="Subir el acta" textoEnviar="Subir el acta" alEnviar={subirActa} alCerrar={cerrarSubidaActa}>
        <SubirArchivo
          de="del acta"
          tipos={TIPOS_DE_DOCUMENTO}
          tope={TOPE_BYTES}
          reemplaza={archivoActa !== null}
          nota="Queda en Documentos, «Actas y decisiones». Antes de poder elegirla la revisa el antivirus."
          elegido={archivoActa === null ? null : `${archivoActa.name} (${tamano(archivoActa.size)})`}
          refPrimero={() => undefined}
          alElegir={elegirArchivoActa}
        />
        <label htmlFor={`${titulo}-titulo`}>Título del acta</label>
        <input
          id={`${titulo}-titulo`}
          {...marcaDe(`${titulo}-titulo`, errorTituloActa)}
          type="text"
          autoComplete="off"
          maxLength={LARGO_TITULO}
          disabled={actaCreada !== null}
          value={actaCreada?.titulo ?? tituloActa}
          onChange={(e) => cambiarTituloActa(e.target.value)}
        />
        <ErrorDeCampo idCampo={`${titulo}-titulo`} mensaje={errorTituloActa} />
      </VentanaDeFormulario>
    );
  }

  /** La administración que choca con el alta (C8G-05): con la lista a la vista, se sabe antes de enviar. */
  function administracionQueChoca(): Mandato | undefined {
    if (tipoAlta === undefined || !ES_ADMINISTRACION.has(tipoAlta) || desde === "") return undefined;
    return listaDeMandatos.find(
      (m) =>
        ES_ADMINISTRACION.has(m.tipo) &&
        OCUPAN_SUS_FECHAS.has(m.estado) &&
        (m.vigente_hasta === null || desde < m.vigente_hasta) &&
        (hasta === "" || m.vigente_desde < hasta),
    );
  }

  /** Por qué faltan tipos en el selector del alta (FL-MAN-09), con dónde se resuelve cada uno. */
  function tiposQueFaltan() {
    return (
      <>
        {/* C8G-02: sin nadie a quien dárselo, el Consejo y la administración no se ofrecen. */}
        {elegibles.length === 0 && (
          <ComandoNoDisponible
            motivo={candidatos.tipo === "ok" && candidatos.elementos.length === 0 ? ALTA_SIN_NADIE : ALTA_SIN_NADIE_MAS}
            resolver={puede("padron.ver") ? { texto: "Ir al Padrón", a: RUTA_PADRON } : undefined}
          />
        )}
        {/* FL-MAN-01: con otra administración sin fecha de fin, no se piden los datos de una nueva. */}
        {administracionSinFin !== undefined && (
          <>
            <ComandoNoDisponible
              motivo={`Ya hay una administración sin fecha de fin: ${administracionSinFin.nombre}, desde el ${dia(
                administracionSinFin.vigente_desde,
              )}. Para dar otra administración, antes hay que darla de baja desde su ficha.`}
            />
            <p>
              <button
                type="button"
                className="secundario"
                disabled={enviando}
                onClick={() => {
                  cerrarVentana();
                  verMandato(administracionSinFin);
                }}
              >
                Abrir su ficha
              </button>
            </p>
          </>
        )}
      </>
    );
  }

  /** «Dar de alta un mandato», en su ventana (MAN-03), con solo los tipos que se pueden dar (FL-MAN-09). */
  function ventanaAlta(tipo: TipoMandato) {
    const choca = administracionQueChoca();
    // FL-MAN-01: si otra administración ocupa fechas, una administración pide primero las fechas, y si
    // chocan queda solo el impedimento (C9).
    const fechasPrimero =
      ES_ADMINISTRACION.has(tipo) && listaDeMandatos.some((m) => ES_ADMINISTRACION.has(m.tipo) && OCUPAN_SUS_FECHAS.has(m.estado));
    return (
      <VentanaDeFormulario
        titulo="Dar de alta un mandato"
        textoEnviar="Dar de alta"
        alEnviar={darDeAlta}
        alCerrar={cerrarVentana}
        sinEnviar={choca !== undefined}
      >
        <label htmlFor={`${id}-tipo-mandato`}>Tipo de mandato</label>
        <select
          id={`${id}-tipo-mandato`}
          data-foco
          {...marcaDe(`${id}-tipo-mandato`, errorEn("alta", "tipo"))}
          value={tipo}
          onChange={(e) => cambiar(claveAlta, setTipoMandato, "tipo")(e.target.value as TipoMandato)}
        >
          {tiposPosibles.map((t) => (
            <option key={t} value={t}>
              {TEXTO_TIPO_MANDATO[t]}
            </option>
          ))}
        </select>
        <ErrorDeCampo idCampo={`${id}-tipo-mandato`} mensaje={errorEn("alta", "tipo")} />
        {tiposQueFaltan()}
        {fechasPrimero && camposDeFechas(choca)}
        {!(fechasPrimero && choca !== undefined) && camposDelAlta(tipo)}
        {!fechasPrimero && camposDeFechas(choca)}
      </VentanaDeFormulario>
    );
  }

  function camposDeFechas(choca: Mandato | undefined) {
    return (
      <>
        <label htmlFor={`${id}-desde`}>Desde</label>
        <input
          id={`${id}-desde`}
          {...marcaDe(`${id}-desde`, errorEn("alta", "desde"))}
          type="date"
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
          value={hasta}
          onChange={(e) => cambiar(claveAlta, setHasta, "hasta")(e.target.value)}
        />
        <ErrorDeCampo idCampo={`${id}-hasta`} mensaje={errorEn("alta", "hasta")} />
        <p id={`${id}-vigencia-vacia`} aria-live="polite">
          {hasta !== "" &&
            hasta === desde &&
            "Con «hasta» igual a «desde» el mandato nunca rige: el último día es el anterior a «hasta»."}
        </p>
        {choca !== undefined && (
          <ComandoNoDisponible
            motivo={`Ya hay una administración en esas fechas: ${choca.nombre}, desde el ${dia(choca.vigente_desde)}${
              choca.vigente_hasta ? ` hasta el ${dia(choca.vigente_hasta)}` : ""
            }. Para dar esta alta hay que darla de baja desde su ficha o cambiar las fechas.`}
          />
        )}
      </>
    );
  }

  function camposDelAlta(tipo: TipoMandato) {
    return (
      <>
        {tipo === "administrador_externo" ? (
          <>
            <label htmlFor={`${id}-nombre`}>Razón social o nombre</label>
            <input
              id={`${id}-nombre`}
              {...marcaDe(`${id}-nombre`, errorEn("alta", "nombre"))}
              type="text"
              autoComplete="off"
              maxLength={LARGO_NOMBRE}
              value={nombre}
              onChange={(e) => cambiar(claveAlta, setNombre, "nombre")(e.target.value)}
            />
            <ErrorDeCampo idCampo={`${id}-nombre`} mensaje={errorEn("alta", "nombre")} />
            <CampoCuit id={`${id}-cuit`} valor={cuit} deshabilitado={enviando} error={errorEn("alta", "cuit")}
              alCambiar={cambiar(claveAlta, setCuit, "cuit")} />
          </>
        ) : (
          <>
            <label htmlFor={`${id}-persona`}>Persona (con vínculo confirmado en el edificio)</label>
            <select
              id={`${id}-persona`}
              {...marcaDe(`${id}-persona`, errorEn("alta", "persona_id"))}
              value={personaElegida}
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
        {tipo !== "miembro_consejo" && (
          <>
            <label htmlFor={`${id}-matricula`}>Matrícula (opcional)</label>
            <input
              id={`${id}-matricula`}
              {...marcaDe(`${id}-matricula`, errorEn("alta", "matricula"))}
              type="text"
              autoComplete="off"
              maxLength={LARGO_MATRICULA}
              value={matricula}
              onChange={(e) => cambiar(claveAlta, setMatricula, "matricula")(e.target.value)}
            />
            <ErrorDeCampo idCampo={`${id}-matricula`} mensaje={errorEn("alta", "matricula")} />
          </>
        )}
        <label htmlFor={`${id}-decision-alta`}>Decisión que lo respalda</label>
        <select
          id={`${id}-decision-alta`}
          {...marcaDe(`${id}-decision-alta`, errorEn("alta", "decision_id"))}
          value={decisionAltaElegida}
          onChange={(e) => cambiar(claveAlta, setDecisionAlta, "decision_id")(e.target.value)}
        >
          {usables.length !== 1 && <option value="">Elegir la decisión</option>}
          {usables.map((d) => (
            <option key={d.id} value={d.id}>
              {recortar(textoDecision(d))}
            </option>
          ))}
        </select>
        <ErrorDeCampo idCampo={`${id}-decision-alta`} mensaje={errorEn("alta", "decision_id")} />
      </>
    );
  }

  /** La ficha abierta, sola encima de las listas (MAN-01): «Consultando…» hasta que llega. */
  function fichaAbierta(abierta: FichaAbierta) {
    const datos = detalle !== null && detalle.datos.id === abierta.id ? detalle : null;
    const resultado = <ResultadoDelActo resultado={resultadoFicha} alOtroActo={() => setResultadoFicha(null)} />;
    if (datos === null)
      return (
        <FichaDeRegistro titulo={abierta.titulo} datos={[]} alCerrar={() => cerrarFicha()} ocupada={enviando}>
          {resultado}
          <p>Consultando…</p>
        </FichaDeRegistro>
      );
    if (datos.tipo === "decision") return fichaDeDecision(datos.datos);
    return fichaDeMandato(datos.datos, resultado);
  }

  /** La ficha de una decisión: lo decidido, dónde consta, el acta y quién la registró. */
  function fichaDeDecision(d: DecisionDetalle) {
    const datos: DatoDeFicha[] = [
      { etiqueta: "Qué se decidió", valor: d.descripcion },
      { etiqueta: "Dónde consta", valor: d.referencia },
      ...(d.sha256 === null ? [] : datoDelActa(d.sha256)),
      {
        etiqueta: "Quién la registró",
        valor: d.registrada_por_vos ? "Esta cuenta." : d.del_equipo ? "El equipo de la plataforma." : "Otra persona del edificio.",
      },
    ];
    return (
      <FichaDeRegistro
        titulo={tituloDeDecision(d)}
        datos={datos}
        alCerrar={() => cerrarFicha()}
        ocupada={enviando}
        ventana={
          viendoActa !== null && (
            <VisorDocumento consorcio={consorcio} documento={viendoActa.documento} version={viendoActa.version} nombre={viendoActa.titulo}
              alCerrar={() => setViendoActa(null)} aEntrar={aEntrar} />
          )
        }
      />
    );
  }

  /** El acta de la decisión (MAN-11): nunca su huella. Para quien no ve Documentos, nada. */
  function datoDelActa(sha256: string): DatoDeFicha[] {
    if (!veActas) return [];
    if (actas === null || actas.tipo === "cargando") return [{ etiqueta: "Acta", valor: "Consultando…" }];
    const acta = actaDeLaHuella(sha256);
    if (acta === undefined) return [{ etiqueta: "Acta", valor: "Registrada" }];
    return [
      {
        etiqueta: "Acta",
        // G-15: el acta vinculada, para abrirla desde la decisión.
        valor: (
          <>
            {acta.documento.titulo}{" "}
            <button
              type="button"
              className="secundario"
              onClick={() => setViendoActa({ documento: acta.documento.id, version: acta.version.id, titulo: acta.documento.titulo })}
            >
              Ver el acta
            </button>
          </>
        ),
      },
    ];
  }

  /** La decisión de la lista, si la cuenta la ve (MAN-12): si no la ve, el dato no va. */
  function datoDeDecision(etiqueta: string, decisionId: string | null | undefined): DatoDeFicha[] {
    const d = decisionId ? listaDecisiones.find((x) => x.id === decisionId) : undefined;
    return d === undefined ? [] : [{ etiqueta, valor: textoDecision(d) }];
  }

  /** La invitación en curso del mandato externo sin cuenta (la última, si está por aprobar o enviada). */
  function invitacionEnCurso(): InvitacionMandato | undefined {
    const ultima = invitaciones?.tipo === "ok" ? invitaciones.elementos[0] : undefined;
    return ultima !== undefined && (ultima.estado === "por_aprobar" || ultima.estado === "pendiente") ? ultima : undefined;
  }

  /** El dato «Cuenta» de la administración externa (MAN-09): un solo estado, con los cuatro ojos dichos una vez. */
  function datosDeLaCuenta(m: MandatoDetalle): DatoDeFicha[] {
    if (m.tipo !== "administrador_externo") return [];
    if (m.con_cuenta || !admiteInvitacion(m) || invitaciones === null || invitaciones.tipo === "sin_permiso")
      return [{ etiqueta: "Cuenta", valor: m.con_cuenta ? "Con cuenta asociada" : "Sin cuenta asociada" }];
    if (invitaciones.tipo === "cargando") return [{ etiqueta: "Cuenta", valor: "Consultando…" }];
    if (invitaciones.tipo === "sin_servicio") return [{ etiqueta: "Cuenta", valor: "No se pudo consultar la invitación." }];
    const enCurso = invitacionEnCurso();
    if (enCurso === undefined) {
      const ultima = invitaciones.elementos[0];
      return [
        {
          etiqueta: "Cuenta",
          valor:
            ultima?.estado === "vencida"
              ? "Sin cuenta asociada: la invitación anterior venció sin que nadie la aceptara."
              : ultima?.estado === "revocada"
                ? "Sin cuenta asociada: la invitación anterior se retiró."
                : "Sin cuenta asociada",
        },
      ];
    }
    if (enCurso.estado === "pendiente")
      return [
        {
          etiqueta: "Cuenta",
          valor: `Invitación enviada${enCurso.vence_en ? `, vence el ${momento(enCurso.vence_en)}` : ""}. Falta que la administración la acepte.`,
        },
      ];
    return [
      {
        etiqueta: "Cuenta",
        valor: enCurso.emitida_por_mi
          ? "Invitación por aprobar: la aprueba otra persona del Consejo o de la administración."
          : "Invitación por aprobar: todavía no salió ningún correo.",
      },
      ...(enCurso.correo === null ? [] : [{ etiqueta: "Correo invitado", valor: enCurso.correo }]),
      ...(enCurso.referencia === null ? [] : [{ etiqueta: "De dónde sale el correo", valor: enCurso.referencia }]),
    ];
  }

  /** La ficha del mandato: sus datos y solo los botones de lo que el rol permite (MAN-02, MAN-06). */
  function fichaDeMandato(m: MandatoDetalle, resultado: ReactNode) {
    const datos: DatoDeFicha[] = [
      { etiqueta: "Estado", valor: TEXTO_ESTADO_MANDATO[m.estado] },
      { etiqueta: "Desde", valor: dia(m.vigente_desde) },
      ...(m.vigente_hasta ? [{ etiqueta: "Hasta", valor: `${dia(m.vigente_hasta)} (ese día ya no rige)` }] : []),
      ...(m.cuit ? [{ etiqueta: "CUIT", valor: m.cuit }] : []),
      ...(m.matricula ? [{ etiqueta: "Matrícula", valor: m.matricula }] : []),
      // La cuenta de la administración externa se asocia por invitación y se puede desasociar (G-40).
      ...datosDeLaCuenta(m),
      ...datoDeDecision("Decisión que lo respalda", m.decision_id),
      {
        etiqueta: "Facultades",
        valor: (
          <ul>
            {m.facultades.map((f) => (
              <li key={f}>{nombreDeFacultad(f)}</li>
            ))}
          </ul>
        ),
      },
      ...(m.causa_baja ? [{ etiqueta: "Causa de la baja", valor: TEXTO_CAUSA[m.causa_baja] }] : []),
      ...(m.finalizado_en ? [{ etiqueta: "Baja registrada", valor: momento(m.finalizado_en) }] : []),
      ...(m.motivo_fin ? [{ etiqueta: "Motivo", valor: m.motivo_fin }] : []),
      ...datoDeDecision("Asamblea que lo removió", m.baja_decision_id),
      ...(m.instrumento_referencia ? [{ etiqueta: "Instrumento", valor: m.instrumento_referencia }] : []),
    ];
    const enCurso = invitacionEnCurso();
    const conInvitacion = admiteInvitacion(m) && invitaciones?.tipo === "ok";
    const resuelveRevision = m.estado === "pendiente_revision" && puede("mandatos.resolver_revision");
    /** C8G-04: sin la condición hoy, mantenerlo se niega (`sin_condicion_habilitante`); lo dice la API. */
    const sinCondicion = resuelveRevision && m.condicion_cumplida_hoy === false;
    const daBaja = ESTADOS_CON_BAJA.includes(m.estado) && puede("mandatos.baja");
    const botones: ReactNode[] = [];
    if (resuelveRevision && !m.es_propio && !sinCondicion && asambleas.length > 0)
      botones.push(
        <button key="revision" type="button" onClick={() => abrirAccionFicha("revision")}>
          Mantener el mandato…
        </button>,
      );
    if (conInvitacion && enCurso === undefined && puede("mandatos.asociar_persona"))
      botones.push(
        <button key="invitar" type="button" onClick={() => abrirAccionFicha("invitar")}>
          Invitar a la administración…
        </button>,
      );
    if (conInvitacion && enCurso?.estado === "por_aprobar" && !enCurso.emitida_por_mi && puede("mandatos.aprobar_asociacion"))
      botones.push(
        <button key="aprobar" type="button" disabled={enviando} onClick={() => void aprobarInvitacion(enCurso, m)}>
          Aprobar la invitación
        </button>,
      );
    if (conInvitacion && enCurso !== undefined && puede("mandatos.revocar_asociacion"))
      botones.push(
        <button key="retirar" type="button" className="secundario peligro" onClick={() => abrirAccionFicha("retirar_invitacion")}>
          Retirar la invitación…
        </button>,
      );
    if (admiteDesasociar(m))
      botones.push(
        <button key="desasociar" type="button" className="secundario peligro" onClick={() => abrirAccionFicha("desasociar")}>
          Desasociar la cuenta…
        </button>,
      );
    // G-44 y G-45: la baja se abre en su ventana; un terminado solo se anula.
    if (daBaja && !m.es_propio)
      botones.push(
        <button key="baja" type="button" className="secundario peligro" onClick={() => abrirAccionFicha("baja")}>
          {m.estado === "terminado" ? "Anular por error de carga…" : "Dar de baja…"}
        </button>,
      );
    // C8G-06: en el mandato propio, en lugar de la baja y de mantenerlo, quién lo hace.
    const propiosQueNoVan = m.es_propio ? [...(resuelveRevision ? ["mantenerlo"] : []), ...(daBaja ? ["darlo de baja"] : [])] : [];
    return (
      <FichaDeRegistro
        titulo={tituloDeMandato(m)}
        datos={datos}
        alCerrar={() => cerrarFicha()}
        ocupada={enviando}
        ventana={accionFicha !== null && ventanaDeLaFicha(m, accionFicha, enCurso)}
      >
        {resultado}
        {botones.length > 0 && <p className="ficha-registro__acciones">{botones}</p>}
        {sinCondicion && !m.es_propio && (
          <ComandoNoDisponible motivo={SIN_CONDICION_PARA_MANTENER} resolver={{ texto: "Ir al Padrón", a: RUTA_PADRON }} />
        )}
        {resuelveRevision && !m.es_propio && !sinCondicion && asambleas.length === 0 && (
          <ComandoNoDisponible motivo="Para mantenerlo hace falta una asamblea registrada por otra persona, y no hay ninguna que esta cuenta pueda usar." />
        )}
        {/* C8G-03 y FL-MAN-08: sin una asamblea que se pueda usar, la remoción no se ofrece; lo dice la
            ficha, con el paso que falta, y no la ventana de la baja que se abre por otra causa. */}
        {daBaja && !m.es_propio && m.estado !== "terminado" && !ofreceRemocion && (decisiones.tipo === "ok" || decisiones.tipo === "sin_servicio") && (
          <ComandoNoDisponible
            motivo={decisiones.tipo === "sin_servicio" ? REMOCION_SIN_DECISIONES : REMOCION_SIN_ASAMBLEA}
          />
        )}
        {propiosQueNoVan.length > 0 && (
          <ComandoNoDisponible
            motivo={`Este mandato es de esta cuenta: ${propiosQueNoVan.join(" o ")} lo hace otra persona del Consejo o de la administración.`}
          />
        )}
        {admiteInvitacion(m) && invitaciones?.tipo === "sin_servicio" && (
          <p>
            <button className="secundario" type="button" onClick={() => pedirInvitaciones(m.id)} disabled={enviando}>
              Consultar de nuevo la invitación
            </button>
          </p>
        )}
      </FichaDeRegistro>
    );
  }

  /** La ventana de la acción abierta, encima de la ficha del mandato. */
  function ventanaDeLaFicha(m: MandatoDetalle, accion: AccionDeFicha, enCurso: InvitacionMandato | undefined) {
    if (accion === "baja") return ventanaBaja(m);
    if (accion === "revision") return ventanaRevision(m);
    if (accion === "invitar") return admiteInvitacion(m) && invitacionEnCurso() === undefined && ventanaInvitar(m);
    if (accion === "retirar_invitacion")
      return (
        enCurso !== undefined && (
          <VentanaDeFormulario
            titulo="Retirar la invitación"
            textoEnviar="Sí, retirarla"
            peligro
            alEnviar={() => retirarInvitacion(enCurso, m)}
            alCerrar={cerrarAccionFicha}
          >
            <p>¿Retirar la invitación? Si el correo ya salió, su enlace deja de servir.</p>
          </VentanaDeFormulario>
        )
      );
    return ventanaDesasociar(m);
  }

  /** «Dar de baja» o «Anular por error de carga», en su ventana (comentario 36, MAN-02). */
  function ventanaBaja(m: MandatoDetalle) {
    const terminado = m.estado === "terminado";
    return (
      <VentanaDeFormulario
        titulo={terminado ? "Anular por error de carga" : `Dar de baja el mandato de ${m.nombre}`}
        textoEnviar={terminado ? (confirmarBaja ? "Sí, anular" : "Anular") : confirmarBaja ? "Sí, dar de baja" : "Dar de baja"}
        peligro
        alEnviar={() => darDeBaja(m)}
        alCerrar={cerrarAccionFicha}
      >
        {!terminado && (
          <>
            <label htmlFor={`${id}-causa`}>Causa</label>
            <select
              id={`${id}-causa`}
              data-foco
              {...marcaDe(`${id}-causa`, errorEn("baja", "causa"))}
              value={causa}
              onChange={(e) => cambiarBaja(setCausa, "causa")(e.target.value as CausaBaja)}
            >
              {CAUSAS.filter((c) => c !== "remocion" || ofreceRemocion || causa === "remocion").map((c) => (
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
          {...(terminado ? { "data-foco": true } : {})}
          {...marcaDe(`${id}-descripcion-baja`, errorEn("baja", "descripcion"))}
          type="text"
          autoComplete="off"
          maxLength={LARGO_DESCRIPCION}
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
              value={asambleaBajaElegida}
              onChange={(e) => cambiarBaja(setDecisionBaja, "decision_id")(e.target.value)}
            >
              {asambleas.length !== 1 && <option value="">Elegir la asamblea</option>}
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
              value={instrumento}
              onChange={(e) => cambiarBaja(setInstrumento, "instrumento")(e.target.value)}
            />
            <ErrorDeCampo idCampo={`${id}-instrumento`} mensaje={errorEn("baja", "instrumento")} />
          </>
        )}
        {confirmarBaja && (
          <p>
            {terminado
              ? `¿Anular el mandato de ${m.nombre} por error de carga? Así consta que nunca valió. No se puede deshacer.`
              : `¿Dar de baja el mandato de ${m.nombre}? La baja no se puede deshacer.`}
          </p>
        )}
      </VentanaDeFormulario>
    );
  }

  /** «Mantener el mandato», en su ventana (MAN-06), con la explicación que sirve para decidir (MAN-10). */
  function ventanaRevision(m: MandatoDetalle) {
    const hoy = hoyDelDispositivo();
    const fechaAsamblea = asambleas.find((d) => d.id === asambleaRevisionElegida)?.fecha;
    const minimoEficaz = fechaAsamblea !== undefined && fechaAsamblea > hoy ? fechaAsamblea : hoy;
    return (
      <VentanaDeFormulario titulo="Mantener el mandato" textoEnviar="Mantener el mandato" alEnviar={() => mantener(m)} alCerrar={cerrarAccionFicha}>
        <p>{CAUSA_REVISION}</p>
        <label htmlFor={`${id}-decision-revision`}>Asamblea que lo mantiene</label>
        <select
          id={`${id}-decision-revision`}
          data-foco
          {...marcaDe(`${id}-decision-revision`, errorEn("revision", "decision_id"))}
          value={asambleaRevisionElegida}
          onChange={(e) => cambiar(claveRevision, setDecisionRevision, "decision_id")(e.target.value)}
        >
          {asambleas.length !== 1 && <option value="">Elegir la asamblea</option>}
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
          // FL-DEC-04: hoy o después, y no antes de la asamblea elegida (`eficaz_desde_invalida`).
          min={minimoEficaz}
          value={eficazDesde}
          onChange={(e) => cambiar(claveRevision, setEficazDesde, "eficaz_desde")(e.target.value)}
        />
        <ErrorDeCampo idCampo={`${id}-eficaz-desde`} mensaje={errorEn("revision", "eficaz_desde")} />
      </VentanaDeFormulario>
    );
  }

  /** «Invitar a la administración» a asociar su cuenta, en su ventana (MAN-06). */
  function ventanaInvitar(m: MandatoDetalle) {
    const titulo = `${id}-invitacion`;
    return (
      <VentanaDeFormulario titulo="Invitar a la administración" textoEnviar="Invitar" alEnviar={() => invitar(m)} alCerrar={cerrarAccionFicha}>
        <label htmlFor={`${titulo}-correo`}>Correo de la administración</label>
        <input
          id={`${titulo}-correo`}
          data-foco
          {...marcaDe(`${titulo}-correo`, errorEn("invitar", "correo"))}
          type="email"
          autoComplete="off"
          maxLength={LARGO_CORREO}
          value={correoInvitacion}
          onChange={(e) => cambiar(claveInvitar, setCorreoInvitacion, "correo")(e.target.value)}
        />
        <ErrorDeCampo idCampo={`${titulo}-correo`} mensaje={errorEn("invitar", "correo")} />
        <label htmlFor={`${titulo}-referencia`}>De dónde sale ese correo (contrato, nota u otro)</label>
        <TextoDeUnaLinea
          id={`${titulo}-referencia`}
          {...marcaDe(`${titulo}-referencia`, errorEn("invitar", "referencia"))}
          rows={FILAS_REFERENCIA}
          autoComplete="off"
          maxLength={LARGO_REFERENCIA}
          value={referenciaInvitacion}
          onValor={cambiar(claveInvitar, setReferenciaInvitacion, "referencia")}
        />
        <ErrorDeCampo idCampo={`${titulo}-referencia`} mensaje={errorEn("invitar", "referencia")} />
      </VentanaDeFormulario>
    );
  }

  /** «Desasociar la cuenta», en su ventana (MAN-06): lo que pasa se dice antes de enviar (MAN-13). */
  function ventanaDesasociar(m: MandatoDetalle) {
    return (
      <VentanaDeFormulario
        titulo="Desasociar la cuenta"
        textoEnviar="Desasociar la cuenta"
        peligro
        alEnviar={() => desasociar(m)}
        alCerrar={cerrarAccionFicha}
      >
        <label htmlFor={`${id}-desasociar-referencia`}>Por qué esa cuenta no es la de la administración</label>
        <TextoDeUnaLinea
          id={`${id}-desasociar-referencia`}
          data-foco
          {...marcaDe(`${id}-desasociar-referencia`, errorEn("desasociar", "referencia"), `${id}-desasociar-ayuda`)}
          rows={FILAS_REFERENCIA}
          autoComplete="off"
          maxLength={LARGO_REFERENCIA}
          value={referenciaDesasociar}
          onValor={cambiar(claveDesasociar, setReferenciaDesasociar, "referencia")}
        />
        <ErrorDeCampo idCampo={`${id}-desasociar-referencia`} mensaje={errorEn("desasociar", "referencia")} />
        <p id={`${id}-desasociar-ayuda`}>{AYUDA_DESASOCIAR}</p>
      </VentanaDeFormulario>
    );
  }
}
