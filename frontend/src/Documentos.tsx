import { type MouseEvent, useCallback, useEffect, useId, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { hoyDelDispositivo, TIPOS_DE_DOCUMENTO, tituloDeArchivo, TOPE_BYTES } from "./archivos";
import { ComandoNoDisponible } from "./ComandoNoDisponible";
import { borrarCopias } from "./copiaContactos";
import { encabezadoCsrf } from "./csrf";
import {
  ErrorDeCampo,
  type ErroresDeCampo,
  erroresDe,
  faltaCompletar,
  hayErrores,
  marcaDe,
  resumenDe as resumenDeErrores,
  REVISAR_DATO,
  SIN_ERRORES,
  sinElDe,
  soloDe,
} from "./ErrorDeCampo";
import { RUTA_INICIO } from "./enlace";
import { useAEntrar } from "./sesionEncima";
import {
  codigoDe,
  esDeConexion,
  esPasajera,
  LIMITE_DE_PEDIDOS,
  mensajeDeFalla,
  SERVICIO_NO_DISPONIBLE,
  SIN_CONEXION,
  SIN_RESPUESTA,
} from "./fallas";
import { AlInicioSinEdificios, REQUISITOS, useAcciones, useEdificioElegido, usePuede } from "./permisos";
import { useConsultaDelAntivirus } from "./egresos/useConsorcio";
import { SubirArchivo } from "./SubirArchivo";
import { VisorDocumento } from "./VisorDocumento";
import { FichaDeRegistro, FilaDeRegistro, useFichas } from "./FichaDeRegistro";
import { type FinDeEnvio, VentanaDeFormulario } from "./VentanaDeFormulario";

// Página /documentos (H2c 2c-2; doc/diseno/H2c_Diseno.md §2 a §4 del repositorio CEM): los
// documentos del edificio por categoría, con sus versiones, y la carga en dos pasos (se crea el
// documento y después se sube el archivo crudo, que entra en cuarentena hasta que el antivirus lo
// aprueba). La lista ya llega filtrada por lo que la persona puede ver, y cada parte aparece solo
// si la alcanza: la lista con `documentos.ver`, la carga y sus categorías con la acción de cada una
// (`CATEGORIAS_OPERATIVAS`). Quien no puede ni ver ni cargar va al inicio. Es solo interfaz: la API
// decide cada pedido, y un 403 al cargar se dice en la pantalla. La descarga termina en una
// navegación común, porque el 302 lleva a una URL firmada de otro origen
// que un `fetch` no puede seguir (CSP `connect-src 'self'`); antes se pregunta con `redirect:
// "manual"` para decir un error acá, en vez de dejar a la persona frente al JSON.
//
// «Retirar el documento» (app 0089): en la ficha, para quien carga esa categoría y además tiene la
// facultad `documentos.ver` (`documentos.ver_restringidos`), con un motivo obligatorio. No se borra
// nada: el documento sale de la lista y queda en la exportación y en la auditoría. El servidor lo
// niega si el documento respalda algo guardado.
//
// Informe de fricción (tandas 1 y 7, 17-11-2026): toda carga va en su ventana, también la de quien
// solo carga (DOC-01, DOC-02); los avisos van arriba (DOC-09); la lista no tiene «Actualizar»: se
// vuelve a pedir sola y, si eso falla, lo dice con «Consultar de nuevo» (DOC-06, DOC-14); después de
// una carga o un retiro queda a la vista mientras llega la nueva (DOC-13).

type Consorcio = components["schemas"]["ConsorcioResumen"];
type Documento = components["schemas"]["DocumentoEnLista"];
type Version = components["schemas"]["VersionEnLista"];
type Categoria = components["schemas"]["CategoriaDocumento"];
type Motivo = components["schemas"]["MotivoRechazo"];
type TipoArchivo = components["schemas"]["TipoArchivo"];

export const RUTA_DOCUMENTOS = "/documentos";

const ESTADO = {
  sinSesion: 401,
  sinPermiso: 403,
  noEncontrado: 404,
  demasiadoLento: 408,
  conflicto: 409,
  demasiadoGrande: 413,
  tipoNoAdmitido: 415,
  invalido: 422,
  limite: 429,
  errorDelServidor: 500,
  noDisponible: 503,
} as const;
const ENCABEZADO_IDEMPOTENCIA = "Idempotency-Key" as const;
/** Cada cuánto se vuelve a pedir la lista mientras un archivo sigue en revisión. */
export const REFRESCO_CUARENTENA_MS = 10_000;
/** El mismo tope que el contrato (`LARGO_MAXIMO_TITULO`); el del archivo está en `archivos.ts`. */
const LARGO_TITULO = 200;
/** El tope del motivo de un retiro (`LARGO_MAXIMO_MOTIVO_RETIRO` del contrato). */
export const LARGO_MOTIVO_RETIRO = 300;
export const RETIRAR_DOCUMENTO = "Retirar el documento";
/** Una sola línea (DOC-04): que no respalde nada ya lo controló la ficha, que no ofrece el retiro. */
export const AYUDA_RETIRO = "No se borra: queda en la exportación y en la auditoría.";
const BYTES_POR_KB = 1024;
const BYTES_POR_MB = 1024 * 1024;
/** Un texto con algo más que espacios. */
const PATRON_TEXTO = ".*\\S.*";

export const TEXTO_CATEGORIA: Record<Categoria, string> = {
  institucional: "Institucional",
  actas_y_decisiones: "Actas y decisiones",
  seguros: "Seguros",
  contratos_proveedores: "Contratos con proveedores",
  facturas_y_comprobantes: "Facturas y comprobantes",
  presupuestos: "Presupuestos",
  mantenimiento_y_certificados: "Mantenimiento y certificados",
  otros: "Otros",
};

const TEXTO_MOTIVO: Record<Motivo, string> = {
  virus: "el análisis encontró un virus",
  contenido_activo: "el PDF trae contenido activo (programas o adjuntos)",
  pdf_cifrado: "el PDF tiene contraseña",
  pdf_mal_formado: "el PDF está dañado o mal armado",
  no_coincide: "el contenido no coincide con el tipo de archivo",
};

const TEXTO_TIPO: Record<TipoArchivo, string> = {
  "application/pdf": "PDF",
  "image/jpeg": "JPEG",
  "image/png": "PNG",
};

/** Cómo se nombra cada campo del formulario en el resumen del error (tanda 3). */
const TEXTO_CAMPO: Record<string, string> = {
  categoria: "la categoría",
  titulo: "el título",
  fecha: "la fecha",
};
/** El largo y el blanco los frena el propio campo: lo único que la API puede rechazar del título
 *  son los teléfonos y correos (G-08). */
export const MENSAJES_PROPIOS: Record<string, string> = { titulo: "El título no puede llevar teléfonos ni correos." };
const FALTA_ARCHIVO = "Falta elegir el archivo.";
/** C8G-08: con un archivo en revisión no se ofrece subir otro; se dice por qué y que la ficha sigue sola. */
export const ARCHIVO_EN_REVISION =
  "Hay un archivo en revisión del antivirus: cuando termine se puede subir otra versión. La ficha se actualiza sola.";

const FORMATO_DIA = new Intl.DateTimeFormat("es-AR", { dateStyle: "long", timeZone: "UTC" });
const FORMATO_MOMENTO = new Intl.DateTimeFormat("es-AR", { dateStyle: "long", timeStyle: "short" });
const FORMATO_NUMERO = new Intl.NumberFormat("es-AR", { maximumFractionDigits: 1 });
const dia = (iso: string) => FORMATO_DIA.format(new Date(`${iso}T00:00:00Z`));
const momento = (iso: string) => FORMATO_MOMENTO.format(new Date(iso));

/** El tamaño en palabras: en KB por debajo de 1 MB (nunca «0 KB»), y en MB con un decimal. */
export function tamano(bytes: number): string {
  if (bytes < BYTES_POR_MB) return `${Math.max(1, Math.ceil(bytes / BYTES_POR_KB))} KB`;
  return `${FORMATO_NUMERO.format(bytes / BYTES_POR_MB)} MB`;
}

function estadoEnPalabras(v: Version): string {
  if (v.estado === "disponible") return "Disponible";
  if (v.estado === "en_cuarentena") return "En revisión";
  return `Rechazado: ${v.motivo_rechazo ? TEXTO_MOTIVO[v.motivo_rechazo] : "sin el motivo"}`;
}

type Paso = "crear" | "subir";

/** Qué decir ante un error de un paso de la carga, según el estado, el código y los campos del 422. */
export function mensajeDeError(
  estado: number,
  codigo: string | undefined,
  paso: Paso,
  campos: string[] = [],
  conEspera = false,
): string {
  if (estado === ESTADO.sinPermiso && codigo === "csrf_invalido") return "La página quedó abierta mucho tiempo. Hay que recargarla y repetir el paso.";
  if (estado === ESTADO.sinPermiso) return "Esta cuenta no tiene permiso para cargar documentos de esa categoría.";
  if (estado === ESTADO.noEncontrado) return "Ese documento ya no está en este edificio.";
  if (estado === ESTADO.demasiadoLento) return "El archivo tardó demasiado en llegar y no se guardó. Reintentar con una conexión más firme.";
  if (estado === ESTADO.demasiadoGrande) return "El archivo pasa de 20 MB y no se guardó. Hay que elegir uno más chico.";
  if (estado === ESTADO.tipoNoAdmitido)
    return "Solo se aceptan archivos PDF, JPEG o PNG, y el contenido tiene que coincidir con el tipo. El archivo no se guardó.";
  if (estado === ESTADO.conflicto && codigo === "idempotencia_en_curso")
    return "El pedido se está procesando. Reintentar en unos segundos.";
  if (estado === ESTADO.invalido && codigo === "idempotencia_reutilizada")
    return "Los datos cambiaron desde un intento anterior. Hay que enviar el formulario de nuevo.";
  // Cada campo marcado lleva su motivo al lado (`ErrorDeCampo`); el resumen solo los nombra (G-08).
  if (estado === ESTADO.invalido && paso === "crear")
    return resumenDeErrores(Object.fromEntries(campos.map((c) => [c.split(".").pop() ?? "", REVISAR_DATO])), TEXTO_CAMPO);
  if (estado === ESTADO.limite) return LIMITE_DE_PEDIDOS;
  // Un 503 con `Retry-After` es el tope de cargas a la vez; sin él, el servicio no está.
  if (estado === ESTADO.noDisponible && conEspera) return "Hay muchas cargas en curso. Reintentar en un momento.";
  return mensajeDeFalla(estado, codigo);
}

/** Qué decir si el documento no se pudo retirar. */
export function mensajeDeRetiro(estado: number, codigo: string | undefined): string {
  if (codigo === "documento_referenciado") return RESPALDA_ALGO;
  if (codigo === "motivo_con_cuenta") return "El motivo no puede llevar el número de una cuenta.";
  if (estado === ESTADO.sinPermiso && codigo === "csrf_invalido") return "No se pudo verificar el pedido. Hay que recargar la página.";
  if (estado === ESTADO.sinPermiso) return "No hay permiso para retirar documentos de esa categoría.";
  if (estado === ESTADO.noEncontrado) return "Ese documento ya no está en este edificio.";
  if (estado === ESTADO.invalido) return "Hay que escribir el motivo, sin teléfonos ni correos.";
  if (estado === ESTADO.limite) return LIMITE_DE_PEDIDOS;
  return mensajeDeFalla(estado, codigo);
}

/** Las que también se cargan con `documentos.cargar_operativos` (`CATEGORIAS_OPERATIVAS` del contrato). */
export const CATEGORIAS_OPERATIVAS: readonly Categoria[] = ["actas_y_decisiones", "presupuestos", "mantenimiento_y_certificados"];

/** Las acciones que cargan una categoría (`ACCIONES_DE_CARGA` del contrato). */
function accionesDeCarga(categoria: Categoria): string[] {
  return CATEGORIAS_OPERATIVAS.includes(categoria)
    ? ["documentos.cargar", "documentos.cargar_operativos"]
    : ["documentos.cargar"];
}

/** Si el intento terminó y el próximo lleva otra clave (igual que en la gestión de /contactos). */
function renovarClave(estado: number, codigo: string | undefined): boolean {
  if (codigo === "idempotencia_reutilizada") return true;
  return estado < ESTADO.errorDelServidor && codigo !== "idempotencia_en_curso";
}

type Datos = { categoria: Categoria; titulo: string; fecha: string };

/** Un documento nuevo empieza con la fecha de hoy: casi siempre se carga el día que llega (G-06). */
const vacio = (): Datos => ({ categoria: "actas_y_decisiones", titulo: "", fecha: hoyDelDispositivo() });

/** El rechazo de la API si algo guardado cita el documento (`documento_referenciado`). */
export const RESPALDA_ALGO =
  "Este documento respalda algo guardado (una orden, un pago, un proveedor, un contrato o el acta de una decisión): no se puede retirar.";

type Pantalla =
  | { tipo: "cargando" }
  | { tipo: "sin_servicio"; deConexion: boolean }
  | { tipo: "sin_consorcios" }
  | { tipo: "lista"; consorcios: Consorcio[] };

const SIN_EDIFICIOS: readonly Consorcio[] = [];

/** `solo_carga`: quien carga sin acceso a la lista (el administrador externo, documento 08 §4).
 * `sin_servicio` lleva el motivo, que se dice una vez, junto a «Consultar de nuevo» (DOC-14). */
type Lectura = { tipo: "cargando" } | { tipo: "solo_carga" } | { tipo: "sin_servicio"; motivo: string } | { tipo: "ok"; documentos: Documento[] };

/** La ventana abierta: la de un documento nuevo, o la de subir el archivo de uno que ya existe. */
type Abierto = { tipo: "nuevo" } | { tipo: "subir"; documento: string; titulo: string; vacio?: boolean };

type Aviso = { texto: string; ok: boolean };
/** Un archivo subido por quien solo carga, a la espera del antivirus (G-09), con las consultas
 * seguidas que fallaron por algo pasajero y el estado de la última. */
type Pendiente = { consorcio: string; documento: string; version: string; titulo: string; fallas: number; estadoFalla: number };
/** Un documento que quedó esperando un archivo, o un archivo cuyo análisis no se puede saber:
 * rechazado (`subir` dice el botón que lo resuelve), creado sin archivo porque la carga se canceló
 * (FL-DOC-03), o imposible de consultar (404, 403: ya no hay nada que esperar; `subir` null). Uno
 * por documento. */
type Rechazo = { documento: string; titulo: string; texto: string; subir: string | null };

/** El rechazo del antivirus, el mismo para quien ve la lista y para quien solo carga (FL-DOC-01). */
export function textoDeRechazo(titulo: string, motivo: Motivo | null | undefined): string {
  return `«Rechazado»: el archivo de «${titulo}» no pasó la revisión (${motivo ? TEXTO_MOTIVO[motivo] : "sin el motivo"}). No se va a mostrar: hay que subir otro.`;
}

/** FL-DOC-03: el documento quedó creado y la carga de su archivo se canceló. */
export const textoSinArchivo = (titulo: string) => `El documento «${titulo}» quedó creado sin archivo.`;
export const subirOtro = (titulo: string) => `Subir otro archivo para «${titulo}»`;
export const subirElArchivo = (titulo: string) => `Subir el archivo de «${titulo}»`;

/** Suma lo que espera un archivo, uno por documento: el más nuevo reemplaza al anterior. */
const conRechazo = (nuevo: Rechazo) => (lista: Rechazo[]) => [...lista.filter((r) => r.documento !== nuevo.documento), nuevo];

/** Consultas seguidas con una falla pasajera antes de decirlo: tres vueltas de la espera del
 * antivirus, unos 9 s. Con menos, un corte de un instante ya alarma; la consulta sigue igual. */
const FALLAS_PARA_AVISAR = 3;

/** Por qué no se pudo consultar, cuando la falla es pasajera. */
function causaPasajera(estado: number): string {
  if (estado === ESTADO.limite) return "se llegó al límite de pedidos";
  if (estado >= ESTADO.errorDelServidor) return "el servicio no está disponible";
  return "no hay conexión";
}

/** Por qué ya no se puede saber cómo terminó: la respuesta es definitiva. */
function sinResultado(titulo: string, estado: number, codigo: string | undefined): string {
  const porque =
    estado === ESTADO.noEncontrado
      ? "ese documento o ese archivo ya no está en el edificio."
      : estado === ESTADO.sinPermiso
        ? "esta cuenta ya no puede cargar documentos en el edificio."
        : mensajeDeFalla(estado, codigo);
  return `No se puede saber cómo terminó la revisión del archivo de «${titulo}»: ${porque}`;
}

class SinSesion extends Error {}

function exigirSesion(response: Response) {
  if (response.status === ESTADO.sinSesion) throw new SinSesion();
}

/** Qué decir si el archivo no se puede bajar. */
function mensajeDeDescarga(estado: number, codigo: string | undefined): string {
  if (estado === ESTADO.noEncontrado) return "Ese documento ya no está en este edificio.";
  if (codigo === "version_no_disponible") return "Ese archivo todavía no se puede bajar.";
  if (codigo === "archivo_vencido") return "Ese archivo ya no está disponible.";
  return mensajeDeFalla(estado, codigo);
}

/** La dirección de descarga: una navegación que el servidor responde con el 302 a la URL firmada. */
function descarga(consorcio: string, documento: string, version: string): string {
  return `/api/v1/consorcios/${consorcio}/documentos/${documento}/versiones/${version}/descargar`;
}

/** Si algún archivo de la lista sigue en el análisis: es lo único que justifica volver a pedirla sola. */
function hayEnCuarentena(documentos: Documento[]): boolean {
  return documentos.some((d) => d.versiones.some((v) => v.estado === "en_cuarentena"));
}

/** Sin una versión disponible ni en revisión (ninguna, o todas rechazadas), el documento espera su archivo. */
function esperaArchivo(d: Documento): boolean {
  return !d.versiones.some((v) => v.estado !== "rechazado");
}

/** Con un archivo disponible y ninguno en revisión se puede subir otra versión; con uno en revisión
 * se espera a que termine. */
function admiteOtraVersion(d: Documento): boolean {
  return d.versiones.some((v) => v.estado === "disponible") && !d.versiones.some((v) => v.estado === "en_cuarentena");
}

/**
 * Los archivos de la ficha (DOC-11): a la vista, el vigente con lo que vino después (uno en revisión o
 * un rechazo que todavía no se reemplazó); plegados, los anteriores y los rechazos ya reemplazados.
 * Sin ninguno disponible, a la vista queda el más nuevo.
 */
export function archivosDeLaFicha(versiones: readonly Version[]): { aLaVista: Version[]; anteriores: Version[] } {
  const vigente = versiones.findIndex((v) => v.estado === "disponible");
  const corte = vigente === -1 ? Math.min(1, versiones.length) : vigente + 1;
  return { aLaVista: versiones.slice(0, corte), anteriores: versiones.slice(corte) };
}

/** El resumen de la fila: la fecha y el estado del archivo más nuevo; el detalle va en la ficha. */
function resumenDe(d: Documento): string {
  const [ultima] = d.versiones;
  const estado =
    ultima === undefined ? "Sin archivo"
    : ultima.estado === "disponible" ? "Disponible"
    : ultima.estado === "en_cuarentena" ? "En revisión"
    : "Rechazado";
  return `${dia(d.fecha)} · ${estado}`;
}

export function Documentos() {
  const id = useId();
  const navegar = useNavigate();
  const [pantalla, setPantalla] = useState<Pantalla>({ tipo: "cargando" });
  const { edificios, consorcio, setConsorcio } = useEdificioElegido(
    pantalla.tipo === "lista" ? pantalla.consorcios : SIN_EDIFICIOS,
    REQUISITOS.documentos,
  );
  const sabeQueAlcanza = useAcciones(consorcio) !== null;
  const puede = usePuede(consorcio);
  const veLaLista = puede("documentos.ver");
  const carga = puede("documentos.cargar", "documentos.cargar_operativos");
  // Mientras no se sabe se muestra, pero un 403 de la lista no deja a nadie en una pantalla vacía.
  const quedaLaCarga = sabeQueAlcanza && carga;
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [abierto, setAbierto] = useState<Abierto | null>(null);
  // Quien solo carga ve el botón y el estado de lo que subió; la carga, como para todos, en su ventana (DOC-02).
  const soloCarga = lectura.tipo === "solo_carga";
  const formulario = abierto;
  const [datos, setDatos] = useState<Datos>(vacio);
  const [archivo, setArchivo] = useState<File | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  /** El refresco de la lista falló: la lista a la vista puede estar vieja (DOC-06). */
  const [refrescoFallido, setRefrescoFallido] = useState<string | null>(null);
  /** Por qué no se pudo bajar un archivo: se dice en su ficha, que no se cierra (DOC-05). */
  const [fallaDescarga, setFallaDescarga] = useState<{ documento: string; texto: string } | null>(null);
  /** El «Listo» de una carga y su archivo: se va cuando la lista lo muestra fuera de revisión (DOC-08). */
  const listoDeLaCarga = useRef<{ version: string; texto: string; documento: string; titulo: string } | null>(null);
  /** Los campos del documento nuevo que rechazó la API, con su motivo (G-08). */
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  /** La versión que se está mirando en la hoja de «Ver». */
  const [viendo, setViendo] = useState<{ documento: string; version: string; titulo: string } | null>(null);
  const [vuelta, setVuelta] = useState(0);
  /** La ficha del documento abierta (comentario 21): la fila entera la abre. */
  const { abierta: fichaAbierta, abrir: abrirFicha, cerrar: cerrarFichaDe, refFila } = useFichas<string>();
  const consultado = useRef(false);
  // Una clave por intento lógico de cada paso: se conserva si el pedido se cae y se renueva al
  // cambiar los datos o el archivo.
  const claveCrear = useRef<string | null>(null);
  const claveSubir = useRef<string | null>(null);
  /** El retiro abierto en su ventana, sobre la ficha: el documento y el motivo escrito. */
  const [retiro, setRetiro] = useState<{ documento: string; motivo: string } | null>(null);
  /** El motivo del retiro, si falta (decisión 4: el botón queda activo y marca el campo). */
  const [erroresRetiro, setErroresRetiro] = useState<ErroresDeCampo>(SIN_ERRORES);
  const claveRetiro = useRef<string | null>(null);
  const primerCampo = useRef<HTMLElement | null>(null);
  const textoAviso = useRef<HTMLParagraphElement | null>(null);
  const alAviso = useRef(false);
  /** El último título que propuso un archivo: si la persona no lo cambió, otro archivo lo reemplaza. */
  const tituloPropuesto = useRef("");
  /** Los archivos que subió quien solo carga y el antivirus todavía revisa: todos, no solo el último,
   * porque puede subir otro antes de que termine el anterior (G-09). */
  const [esperando, setEsperando] = useState<Pendiente[]>([]);
  /** Los archivos rechazados, cada uno con su documento esperando otro archivo. Quedan a la vista hasta
   * que ese documento recibe uno: el formulario a medio llenar no cambia de destino por esto. */
  const [rechazados, setRechazados] = useState<Rechazo[]>([]);
  /** El edificio elegido, para las respuestas que llegan tarde: una pedida para otro se ignora. Se
   * fija también al cambiarlo, antes de que la pantalla se vuelva a dibujar. */
  const consorcioVigente = useRef(consorcio);
  consorcioVigente.current = consorcio;
  // El próximo pedido de la lista es el refresco automático: si falla, la lista a la vista no se pierde.
  const refrescoAutomatico = useRef(false);

  // El foco va al aviso cuando termina un acto. Con una ventana abierta se espera a que se cierre: al
  // cerrarse, `Hoja` devuelve el foco a lo que la abrió, y recién entonces pasa al aviso.
  useEffect(() => {
    if (enviando || abierto !== null || !alAviso.current || aviso === null) return;
    alAviso.current = false;
    textoAviso.current?.focus();
  }, [enviando, aviso, abierto]);

  // Sin sesión no queda ninguna copia de /contactos, igual que en las demás páginas con sesión.
  function bajar(evento: MouseEvent<HTMLAnchorElement>, documento: string, version: string) {
    evento.preventDefault();
    setFallaDescarga(null);
    // DOC-05: la falla se dice en la ficha, que sigue abierta. Una falla que llega con otro edificio ya
    // elegido es de un documento que ya no está a la vista.
    const pedidoEn = consorcio;
    const fallo = (texto: string) => {
      if (consorcioVigente.current !== pedidoEn) return;
      setFallaDescarga({ documento, texto });
    };
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/documentos/{documento_id}/versiones/{version_id}/descargar", {
        params: { path: { consorcio_id: consorcio, documento_id: documento, version_id: version } },
        redirect: "manual",
      })
      .then(({ error, response }) => {
        exigirSesion(response);
        if (response.type === "opaqueredirect") {
          const enlace = window.document.createElement("a");
          enlace.href = descarga(consorcio, documento, version);
          enlace.click();
        } else fallo(mensajeDeDescarga(response.status, codigoDe(error)));
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion) aEntrar();
        else fallo(SIN_CONEXION);
      });
  }

  const aEntrar = useAEntrar(borrarCopias);

  /** Cómo va el archivo que subió quien solo carga: disponible, rechazado o todavía en revisión (G-09). */
  const mirarMiArchivo = useCallback(() => {
    for (const cual of esperando) mirarUno(cual);

    function mirarUno(cual: Pendiente) {
      const esta = (p: Pendiente) => p.version === cual.version;
      const sinEsperar = (lista: Pendiente[]) => lista.filter((p) => !esta(p));
      // Solo un resultado definitivo saca el archivo de la espera; una falla pasajera (sin red, 429,
      // 5xx) se cuenta y se vuelve a consultar en la vuelta siguiente (revisión del PR #241).
      const pasajera = (estado: number) =>
        setEsperando((lista) => lista.map((p) => (esta(p) ? { ...p, fallas: p.fallas + 1, estadoFalla: estado } : p)));
      const terminado = (texto: string, ofreceOtro: boolean) => {
        setEsperando(sinEsperar);
        setRechazados(conRechazo({ documento: cual.documento, titulo: cual.titulo, texto, subir: ofreceOtro ? subirOtro(cual.titulo) : null }));
      };
      void api
        .GET("/api/v1/consorcios/{consorcio_id}/documentos/{documento_id}/versiones/{version_id}/estado", {
          params: { path: { consorcio_id: cual.consorcio, documento_id: cual.documento, version_id: cual.version } },
        })
        .then(({ data, error, response }) => {
          exigirSesion(response);
          // Una respuesta que llega con otro edificio ya elegido no toca nada: su espera ya se descartó
          // y su documento no está en el formulario de este edificio (revisión del PR #241).
          if (consorcioVigente.current !== cual.consorcio) return;
          if (data === undefined) {
            if (esPasajera(response.status)) pasajera(response.status);
            else terminado(sinResultado(cual.titulo, response.status, codigoDe(error)), false);
            return;
          }
          if (data.estado === "en_cuarentena") {
            setEsperando((lista) => lista.map((p) => (esta(p) ? { ...p, fallas: 0 } : p)));
            return;
          }
          if (data.estado === "disponible") {
            setEsperando(sinEsperar);
            setAviso({ texto: `Listo: el archivo de «${cual.titulo}» pasó la revisión del antivirus y quedó «Disponible».`, ok: true });
            return;
          }
          // Rechazado: aparte del aviso, para que otro resultado no lo tape, y sin tocar el formulario:
          // subir otro archivo para ese documento es un paso que la persona elige (revisión del PR #241).
          terminado(textoDeRechazo(cual.titulo, data.motivo_rechazo), true);
        })
        .catch((e: unknown) => {
          // Sin sesión, a entrar; una consulta que no llegó es una falla pasajera más.
          if (e instanceof SinSesion) aEntrar();
          else if (consorcioVigente.current === cual.consorcio) pasajera(SIN_RESPUESTA);
        });
    }
  }, [esperando, aEntrar]);
  const antivirusTarda = useConsultaDelAntivirus(esperando.length > 0, mirarMiArchivo);

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
    if (!veLaLista) {
      setLectura({ tipo: "solo_carga" });
      return;
    }
    let vigente = true;
    const automatico = refrescoAutomatico.current;
    refrescoAutomatico.current = false;
    // El refresco que falla deja la lista y una ventana abierta como están, dice en la lista que puede
    // estar vieja y no sigue solo (`setLectura` no cambia, así que no se programa otro): queda
    // «Consultar de nuevo» junto a ese aviso (DOC-06).
    const fallaAutomatica = (falla: string) => setRefrescoFallido(falla);
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/documentos", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        // Una lista pedida antes de un cambio, o de otro edificio, puede llegar después.
        if (!vigente) return;
        if (data !== undefined) {
          setRefrescoFallido(null);
          setLectura({ tipo: "ok", documentos: data.documentos });
        } else if (response.status === ESTADO.sinPermiso) {
          if (quedaLaCarga) setLectura({ tipo: "solo_carga" });
          else navegar(RUTA_INICIO, { replace: true });
        } else if (automatico) fallaAutomatica(mensajeDeFalla(response.status, codigoDe(error)));
        // DOC-14: el motivo, una vez, en la lista y junto a «Consultar de nuevo».
        else setLectura({ tipo: "sin_servicio", motivo: mensajeDeFalla(response.status, codigoDe(error)) });
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else if (automatico) fallaAutomatica(SIN_CONEXION);
        else setLectura({ tipo: "sin_servicio", motivo: SIN_CONEXION });
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, vuelta, aEntrar, navegar, veLaLista, quedaLaCarga]);

  // Un solo pedido por vez: el próximo se programa cuando llega la lista, y se cancela al salir de
  // la pantalla, al cambiar de edificio o cuando ya ningún archivo está en revisión.
  useEffect(() => {
    if (lectura.tipo !== "ok" || !hayEnCuarentena(lectura.documentos)) return;
    const espera = setTimeout(() => {
      refrescoAutomatico.current = true;
      setVuelta((v) => v + 1);
    }, REFRESCO_CUARENTENA_MS);
    return () => clearTimeout(espera);
  }, [lectura]);

  // DOC-08: el «Listo» de una carga se va cuando la lista ya muestra ese archivo fuera de revisión. Si
  // el antivirus lo rechazó, en su lugar queda el rechazo con su botón, como para quien solo carga
  // (FL-DOC-01): la fila sola cambiaba en silencio.
  useEffect(() => {
    const listo = listoDeLaCarga.current;
    if (listo === null || lectura.tipo !== "ok") return;
    const version = lectura.documentos.flatMap((d) => d.versiones).find((v) => v.id === listo.version);
    if (version === undefined || version.estado === "en_cuarentena") return;
    listoDeLaCarga.current = null;
    setAviso((actual) => (actual?.texto === listo.texto ? null : actual));
    if (version.estado === "rechazado")
      setRechazados(conRechazo({
        documento: listo.documento,
        titulo: listo.titulo,
        texto: textoDeRechazo(listo.titulo, version.motivo_rechazo),
        subir: subirOtro(listo.titulo),
      }));
  }, [lectura]);

  /**
   * Vuelve a pedir la lista. Con la lista a la vista, como el refresco automático: queda mientras
   * llega la nueva y, si falla, sigue con el aviso de que puede estar vieja (DOC-13).
   */
  function volverAPedir() {
    if (lectura.tipo === "ok") refrescoAutomatico.current = true;
    else setLectura({ tipo: "cargando" });
    setRefrescoFallido(null);
    setVuelta((v) => v + 1);
  }

  /** Después de una carga, lo que se ve lo decide la API. Quien solo carga no tiene lista que pedir. */
  function actualizarLista() {
    if (!soloCarga) volverAPedir();
  }

  function abrir(nuevo: Abierto) {
    tituloPropuesto.current = "";
    claveCrear.current = null;
    claveSubir.current = null;
    if (nuevo.tipo === "nuevo") setDatos(vacio());
    setArchivo(null);
    setAviso(null);
    setErrores(SIN_ERRORES);
    setAbierto(nuevo);
  }

  function cerrar() {
    tituloPropuesto.current = "";
    claveCrear.current = null;
    claveSubir.current = null;
    setDatos(vacio());
    setArchivo(null);
    setErrores(SIN_ERRORES);
    setAbierto(null);
  }

  /** «Cancelar»: el error era del formulario y se va con él; un «Listo» anterior queda (G-13). Si el
   * documento se acababa de crear sin su archivo, queda arriba con su botón para subirlo: quien
   * solo carga no tiene otro camino para volver a él, y cargarlo de nuevo lo duplicaría (FL-DOC-03). */
  function cancelar() {
    setAviso((actual) => (actual?.ok === true ? actual : null));
    if (abierto?.tipo === "subir" && abierto.vacio === true)
      setRechazados(conRechazo({
        documento: abierto.documento,
        titulo: abierto.titulo,
        texto: textoSinArchivo(abierto.titulo),
        subir: subirElArchivo(abierto.titulo),
      }));
    cerrar();
  }

  function cambiar<K extends keyof Datos>(campo: K, valor: Datos[K]) {
    claveCrear.current = null;
    setErrores((e) => sinElDe(e, campo));
    setDatos((d) => ({ ...d, [campo]: valor }));
  }

  function elegirArchivo(elegido: File) {
    claveSubir.current = null;
    setArchivo(elegido);
    // El nombre del archivo propone el título (G-06), sin pisar uno que la persona escribió.
    const propuesto = tituloDeArchivo(elegido, "", LARGO_TITULO);
    if (propuesto !== "" && (datos.titulo === "" || datos.titulo === tituloPropuesto.current)) {
      tituloPropuesto.current = propuesto;
      cambiar("titulo", propuesto);
    }
  }

  /**
   * El botón de la ventana de carga (DOC-01): lo que falta se marca sin enviar (decisión 4); un
   * documento nuevo se crea y después se sube su archivo, en un solo envío. El rechazo queda en la
   * ventana; si el documento se creó y el archivo no entró, la misma ventana pasa a subir solo el
   * archivo de ese documento.
   */
  async function enviarCarga(): Promise<FinDeEnvio> {
    if (formulario === null) return "cerrar";
    const faltan =
      formulario.tipo === "nuevo"
        ? faltaCompletar({ titulo: [datos.titulo, "Falta escribir el título."], fecha: [datos.fecha, "Falta la fecha del documento."] })
        : SIN_ERRORES;
    setErrores(faltan);
    // El tipo y el tamaño ya los controló `SubirArchivo` al elegirlo: un archivo que no va a entrar
    // no llega acá ni deja un documento vacío. El servidor vuelve a controlar el tipo real y el tamaño.
    if (archivo === null) return { rechazo: FALTA_ARCHIVO };
    if (hayErrores(faltan)) return "seguir";
    setEnviando(true);
    setAviso(null);
    try {
      if (formulario.tipo === "subir") return await subir(formulario.documento, formulario.titulo, archivo, false);
      const titulo = datos.titulo.trim();
      const resultado = await crear(titulo);
      if ("fin" in resultado) return resultado.fin;
      return await subir(resultado.creado, titulo, archivo, true);
    } finally {
      setEnviando(false);
    }
  }

  /** Subir el archivo desde la ficha, en su ventana: el rechazo queda en la ventana. */
  async function subirDesdeLaFicha(documento: string, titulo: string): Promise<FinDeEnvio> {
    if (archivo === null) return { rechazo: FALTA_ARCHIVO };
    setEnviando(true);
    setAviso(null);
    try {
      return await subir(documento, titulo, archivo, false);
    } finally {
      setEnviando(false);
    }
  }

  /** Primer paso: crea el documento. Devuelve su id o, si no se creó, cómo sigue la ventana. */
  /** El id del documento creado, o cómo termina la ventana (Codex en #257: "seguir" no es un id). */
  async function crear(titulo: string): Promise<{ creado: string } | { fin: FinDeEnvio }> {
    claveCrear.current ??= crypto.randomUUID();
    try {
      const { data, error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/documentos", {
        headers: encabezadoCsrf(),
        params: { path: { consorcio_id: consorcio }, header: { [ENCABEZADO_IDEMPOTENCIA]: claveCrear.current } },
        body: { categoria: datos.categoria, titulo, fecha: datos.fecha },
      });
      exigirSesion(response);
      if (data === undefined) {
        const codigo = codigoDe(error);
        if (renovarClave(response.status, codigo)) claveCrear.current = null;
        const marcados = soloDe(erroresDe(error, REVISAR_DATO, MENSAJES_PROPIOS), TEXTO_CAMPO);
        setErrores(marcados);
        return { fin: { rechazo: mensajeDeError(response.status, codigo, "crear", Object.keys(marcados).map((c) => `body.${c}`)) } };
      }
      claveCrear.current = null;
      return { creado: data.id };
    } catch (e: unknown) {
      if (!(e instanceof SinSesion)) return { fin: { rechazo: SIN_CONEXION } };
      // La sesión se pide encima: lo escrito queda en la ventana para repetir el paso.
      aEntrar();
      return { fin: "seguir" };
    }
  }

  /**
   * Segundo paso: sube el archivo crudo, sin JSON ni multipart, con su tipo como `Content-Type`.
   * Devuelve cómo terminó: el rechazo lo dice la ventana desde la que se subió.
   */
  async function subir(documento: string, titulo: string, elegido: File, recienCreado: boolean): Promise<FinDeEnvio> {
    claveSubir.current ??= crypto.randomUUID();
    // Si el documento se acaba de crear y el archivo no entra, el documento queda sin archivo:
    // se dice y el formulario pasa a subir solo el archivo de ese documento.
    const fallo = (texto: string): FinDeEnvio => {
      if (recienCreado) {
        claveCrear.current = null;
        setAbierto({ tipo: "subir", documento, titulo, vacio: true });
        actualizarLista();
      }
      return { rechazo: recienCreado ? `El documento «${titulo}» quedó creado, pero el archivo no se subió. ${texto}` : texto };
    };
    try {
      const { data, error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/documentos/{documento_id}/versiones", {
        headers: { ...encabezadoCsrf(), "Content-Type": elegido.type },
        params: {
          path: { consorcio_id: consorcio, documento_id: documento },
          header: { [ENCABEZADO_IDEMPOTENCIA]: claveSubir.current },
        },
        // El generador tipa `format: binary` como texto; el cuerpo es el archivo tal cual, y
        // `bodySerializer` lo deja pasar sin convertirlo a JSON.
        body: elegido as unknown as string,
        bodySerializer: (cuerpo) => cuerpo,
      });
      exigirSesion(response);
      if (data === undefined) {
        const codigo = codigoDe(error);
        if (renovarClave(response.status, codigo)) claveSubir.current = null;
        return fallo(mensajeDeError(response.status, codigo, "subir", [], response.headers.has("Retry-After")));
      }
      cerrar();
      cerrarFichaDe(false);
      // Sin acceso a la lista, quien solo carga pregunta por su archivo hasta que el antivirus
      // termina (G-09). Con la lista caída no hay refresco automático: queda «Actualizar».
      if (soloCarga)
        setEsperando((lista) => [...lista, { consorcio, documento, version: data.id, titulo, fallas: 0, estadoFalla: SIN_RESPUESTA }]);
      // Ese documento ya tiene otro archivo: su rechazo anterior está atendido.
      setRechazados((lista) => lista.filter((r) => r.documento !== documento));
      // DOC-08: con la lista, la fila ya dice «En revisión»; sin ella (quien solo carga), el estado de lo
      // que subió lo dice esta pantalla.
      const texto = soloCarga
        ? `Listo: el archivo de «${titulo}» se subió. Queda «En revisión» mientras el antivirus lo analiza; esta pantalla avisa cuando termina.`
        : `Listo: el archivo de «${titulo}» se subió.`;
      listoDeLaCarga.current = soloCarga ? null : { version: data.id, texto, documento, titulo };
      alAviso.current = true;
      setAviso({ texto, ok: true });
      actualizarLista();
      return "cerrar";
    } catch (e: unknown) {
      if (!(e instanceof SinSesion)) return fallo(SIN_CONEXION);
      // La sesión se pide encima: el archivo elegido queda para repetir el paso.
      aEntrar();
      return "seguir";
    }
  }

  // El error de una carga o de un retiro se dice en su ventana: la página de atrás queda inerte
  // mientras está abierta (G-01).
  const conMensaje = aviso !== null || rechazados.length > 0 || esperando.some((p) => p.fallas >= FALLAS_PARA_AVISAR) || pantalla.tipo !== "lista";

  if (pantalla.tipo === "sin_consorcios") return <AlInicioSinEdificios />;

  return (
    <>
      <h1>Documentos del edificio</h1>

      {pantalla.tipo === "lista" && edificios.length > 1 && (
        <div className="formulario formulario-movil tarjeta">
          <label htmlFor={`${id}-consorcio`}>Edificio</label>
          <select
            id={`${id}-consorcio`}
            value={consorcio}
            disabled={enviando}
            onChange={(e) => {
              // Desde ya, lo que responda el edificio anterior se ignora (ver `consorcioVigente`).
              consorcioVigente.current = e.target.value;
              cerrar();
              cerrarFichaDe(false);
              setEsperando([]);
              setRechazados([]);
              setRetiro(null);
              setViendo(null);
              setAviso(null);
              setRefrescoFallido(null);
              setFallaDescarga(null);
              listoDeLaCarga.current = null;
              setLectura({ tipo: "cargando" });
              setConsorcio(e.target.value);
            }}
          >
            {edificios.map((c) => (
              <option key={c.id} value={c.id}>
                {c.nombre}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* DOC-09: los avisos arriba, debajo del título, como en el Padrón y en Contactos. */}
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
        {aviso && (
          <p ref={textoAviso} tabIndex={-1} className={aviso.ok ? "aviso-ok" : "aviso-error"}>
            {aviso.texto}
          </p>
        )}
        {esperando.length > 0 && antivirusTarda && (
          <p>
            El antivirus está tardando más que de costumbre con {esperando.map((p) => `«${p.titulo}»`).join(", ")}: esta
            pantalla sigue esperando.
          </p>
        )}
        {esperando
          .filter((p) => p.fallas >= FALLAS_PARA_AVISAR)
          .map((p) => (
            <p key={p.version} className="aviso-error">
              No se pudo consultar cómo va el archivo de «{p.titulo}» ({causaPasajera(p.estadoFalla)}); esta pantalla lo sigue
              intentando.
            </p>
          ))}
        {rechazados.map((r) => (
          <div key={r.documento}>
            <p className="aviso-error">{r.texto}</p>
            {r.subir !== null && !(formulario?.tipo === "subir" && formulario.documento === r.documento) && (
              <button type="button" className="secundario" disabled={enviando}
                onClick={() => abrir({ tipo: "subir", documento: r.documento, titulo: r.titulo })}>
                {r.subir}
              </button>
            )}
          </div>
        ))}
      </div>

      {pantalla.tipo === "lista" && (
        <>
          {/* G-05: la carga va arriba, antes de la lista. También sin acceso a la lista: el administrador
              externo solo carga (documento 08 §4). Con la lista caída o mientras llega la nueva sigue a
              la vista, porque cargar no depende de ella (DOC-13). */}
          {carga && lectura.tipo !== "cargando" && (
            <p className="acciones-pantalla">
              <button type="button" onClick={() => abrir({ tipo: "nuevo" })} disabled={enviando}>
                Cargar un documento
              </button>
            </p>
          )}

          {!soloCarga && (
            <section aria-label="Documentos" className="tarjeta">
              {lectura.tipo === "cargando" && <p>Consultando…</p>}
              {lectura.tipo === "sin_servicio" && (
                <>
                  <p className="aviso-error">No se pudo mostrar la lista. {lectura.motivo}</p>
                  <button type="button" className="secundario" onClick={volverAPedir} disabled={enviando}>
                    Consultar de nuevo
                  </button>
                </>
              )}
              {lectura.tipo === "ok" && (
                <>
                  {refrescoFallido !== null && (
                    <>
                      <p className="aviso-error">{refrescoFallido} La lista puede no estar al día.</p>
                      <button type="button" className="secundario" onClick={volverAPedir} disabled={enviando}>
                        Consultar de nuevo
                      </button>
                    </>
                  )}
                  {aLaVista(lectura.documentos).length === 0 && <p>El edificio todavía no tiene documentos cargados.</p>}
                  {(Object.keys(TEXTO_CATEGORIA) as Categoria[]).map((c) => categoria(c, aLaVista(lectura.documentos)))}
                </>
              )}
            </section>
          )}
        </>
      )}

      {/* La carga en su ventana (DOC-01, DOC-02): la de un documento nuevo, o la de subir el archivo de
          uno cuya ficha no está abierta (el que se acaba de crear, el de quien solo carga, una categoría
          que la persona carga pero no ve, o la lista caída). La del archivo desde la ficha va sobre ella. */}
      {formulario !== null && (formulario.tipo === "nuevo" || !enSuFicha(formulario.documento)) && ventanaCarga(formulario)}

      {fichaDelDocumento()}

      {viendo !== null && (
        <VisorDocumento consorcio={consorcio} documento={viendo.documento} version={viendo.version} nombre={viendo.titulo}
          alCerrar={() => setViendo(null)} aEntrar={aEntrar} />
      )}
    </>
  );

  /** FL-DOC-02: a quien no carga la categoría, solo los documentos con un archivo para abrir. Uno sin
   * archivo, con el suyo en revisión o rechazado no tiene nada que mostrarle ni nada que pueda hacer:
   * lo completa quien carga, que sí lo ve. */
  function aLaVista(documentos: Documento[]): Documento[] {
    return documentos.filter((d) => puede(...accionesDeCarga(d.categoria)) || d.versiones.some((v) => v.estado === "disponible"));
  }

  /** Los documentos de una categoría; una categoría sin documentos no se muestra. */
  function categoria(c: Categoria, documentos: Documento[]) {
    const deEsta = documentos.filter((d) => d.categoria === c);
    if (deEsta.length === 0) return null;
    const titulo = `${id}-${c}`;
    return (
      <section key={c} aria-labelledby={titulo}>
        <h2 id={titulo}>{TEXTO_CATEGORIA[c]}</h2>
        <ul className="filas-registro" aria-labelledby={titulo}>
          {deEsta.map((d) => (
            <li key={d.id}>
              <FilaDeRegistro titulo={d.titulo} resumen={resumenDe(d)} alAbrir={() => {
                setFallaDescarga(null);
                abrirFicha(d.id);
              }} refFila={refFila(d.id)}
                abierta={fichaAbierta === d.id} deshabilitada={enviando} />
            </li>
          ))}
        </ul>
      </section>
    );
  }

  /** Retirar el documento: con el motivo, por la API, que lo niega si algo guardado lo cita. El
   * rechazo queda en la ventana del retiro, sobre la ficha, para corregir o cancelar. */
  async function retirar(documento: string, titulo: string): Promise<FinDeEnvio> {
    if (retiro === null) return "cerrar";
    const faltan = faltaCompletar({ motivo: [retiro.motivo, "Falta escribir el motivo del retiro."] });
    if (hayErrores(faltan)) {
      setErroresRetiro(faltan);
      return "seguir";
    }
    claveRetiro.current ??= crypto.randomUUID();
    setEnviando(true);
    setAviso(null);
    try {
      const { error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/documentos/{documento_id}/retiro", {
        headers: encabezadoCsrf(),
        params: { path: { consorcio_id: consorcio, documento_id: documento }, header: { [ENCABEZADO_IDEMPOTENCIA]: claveRetiro.current } },
        body: { motivo: retiro.motivo.trim() },
      });
      exigirSesion(response);
      if (!response.ok) {
        const codigo = codigoDe(error);
        if (renovarClave(response.status, codigo)) claveRetiro.current = null;
        return { rechazo: `El documento no se retiró. ${mensajeDeRetiro(response.status, codigo)}` };
      }
      claveRetiro.current = null;
      alAviso.current = true;
      setRetiro(null);
      cerrarFichaDe(false);
      // Sale de la lista ya, sin esperar la nueva (DOC-13).
      setLectura((actual) => (actual.tipo === "ok" ? { tipo: "ok", documentos: actual.documentos.filter((x) => x.id !== documento) } : actual));
      setAviso({ texto: `Listo: se retiró «${titulo}». Ya no aparece en la lista.`, ok: true });
      actualizarLista();
      return "cerrar";
    } catch (e: unknown) {
      if (!(e instanceof SinSesion)) return { rechazo: `El documento no se retiró. ${SIN_CONEXION}` };
      aEntrar();
      return "seguir";
    } finally {
      setEnviando(false);
    }
  }

  /** El retiro, en su ventana sobre la ficha: el motivo y la confirmación. */
  function ventanaRetiro(d: Documento) {
    if (retiro?.documento !== d.id) return null;
    const base = `${id}-retiro-${d.id}`;
    return (
      <VentanaDeFormulario
        titulo={RETIRAR_DOCUMENTO}
        textoEnviar="Retirar"
        peligro
        alEnviar={() => retirar(d.id, d.titulo)}
        alCerrar={() => {
          setErroresRetiro(SIN_ERRORES);
          setRetiro(null);
        }}
      >
        <p id={`${base}-ayuda`}>{AYUDA_RETIRO}</p>
        <label htmlFor={`${base}-motivo`}>Motivo del retiro</label>
        <textarea
          id={`${base}-motivo`}
          data-foco
          required
          maxLength={LARGO_MOTIVO_RETIRO}
          {...marcaDe(`${base}-motivo`, erroresRetiro.motivo, `${base}-ayuda`)}
          value={retiro.motivo}
          onChange={(e) => {
            claveRetiro.current = null;
            setErroresRetiro((actuales) => sinElDe(actuales, "motivo"));
            setRetiro({ documento: d.id, motivo: e.target.value });
          }}
        />
        <ErrorDeCampo idCampo={`${base}-motivo`} mensaje={erroresRetiro.motivo} />
      </VentanaDeFormulario>
    );
  }

  /** Cerrar la ficha también deja el archivo a medio elegir de ese documento. */
  function cerrarFicha() {
    if (abierto?.tipo === "subir" && abierto.documento === fichaAbierta) cancelar();
    setFallaDescarga(null);
    setRetiro(null);
    setErroresRetiro(SIN_ERRORES);
    cerrarFichaDe();
  }

  /** Si la ficha de ese documento está a la vista (ahí se abre la ventana de su archivo). */
  function enSuFicha(documento: string) {
    return fichaAbierta === documento && lectura.tipo === "ok" && lectura.documentos.some((d) => d.id === documento);
  }

  /** La ficha del documento: categoría, fecha y sus versiones, de la más nueva a la más vieja, con
   * «Ver» y «Descargar» en las disponibles y, si el rol carga esa categoría, subir un archivo. */
  function fichaDelDocumento() {
    if (fichaAbierta === null || lectura.tipo !== "ok") return null;
    const d = lectura.documentos.find((x) => x.id === fichaAbierta);
    if (d === undefined) return null;
    const subiendo = abierto?.tipo === "subir" && abierto.documento === d.id;
    const subir = esperaArchivo(d) ? "Subir archivo" : admiteOtraVersion(d) ? "Subir otra versión" : null;
    return (
      <FichaDeRegistro
        titulo={d.titulo}
        alCerrar={cerrarFicha}
        ocupada={enviando}
        datos={[
          { etiqueta: "Categoría", valor: TEXTO_CATEGORIA[d.categoria] },
          { etiqueta: "Fecha del documento", valor: dia(d.fecha) },
          {
            etiqueta: "Archivos",
            valor: d.versiones.length === 0 ? "Todavía no tiene un archivo." : archivos(d),
          },
        ]}
        ventana={
          subiendo ? (
            <VentanaDeFormulario
              titulo={`Subir el archivo de «${d.titulo}»`}
              textoEnviar="Subir el archivo"
              alEnviar={() => subirDesdeLaFicha(d.id, d.titulo)}
              alCerrar={cancelar}
            >
              {campoArchivo()}
            </VentanaDeFormulario>
          ) : (
            ventanaRetiro(d)
          )
        }
      >
        {fallaDescarga?.documento === d.id && (
          <p className="aviso-error" role="alert">
            {fallaDescarga.texto}
          </p>
        )}
        {/* C8G-08: con un archivo en revisión no se ofrece subir otro; se dice por qué. */}
        {subir === null && puede(...accionesDeCarga(d.categoria)) && d.versiones.some((v) => v.estado === "en_cuarentena") && (
          <ComandoNoDisponible motivo={ARCHIVO_EN_REVISION} />
        )}
        {subir !== null && puede(...accionesDeCarga(d.categoria)) && (
          <p>
            {/* G-04: sin archivo, subirlo es lo que hace avanzar: el principal (pregunta 13). */}
            <button type="button" className={esperaArchivo(d) ? undefined : "secundario"}
              onClick={() => {
                setRetiro(null);
                setFallaDescarga(null);
                abrir({ tipo: "subir", documento: d.id, titulo: d.titulo });
              }} disabled={enviando}>
              {subir}
            </button>
          </p>
        )}
        {/* Retirar: quien carga la categoría y además tiene la facultad de ver (`0089`). */}
        {/* DOC-12: si algo guardado lo cita, ni el botón ni un párrafo que lo explique (decisión del
            propietario, 18-11-2026; deja de lado G-02 en esto). */}
        {puede(...accionesDeCarga(d.categoria)) && puede("documentos.ver_restringidos") && d.respalda !== true && (
          <p>
            <button type="button" className="secundario peligro" disabled={enviando}
              onClick={() => {
                if (subiendo) cancelar();
                setFallaDescarga(null);
                claveRetiro.current = null;
                setErroresRetiro(SIN_ERRORES);
                setRetiro({ documento: d.id, motivo: "" });
              }}>
              {RETIRAR_DOCUMENTO}
            </button>
          </p>
        )}
      </FichaDeRegistro>
    );
  }

  /** Elegir el archivo: se manda con el botón del formulario, que también reintenta con la misma clave. */
  function campoArchivo() {
    return (
      <SubirArchivo
        titulo="Archivo"
        de="del documento"
        tipos={TIPOS_DE_DOCUMENTO}
        tope={TOPE_BYTES}
        reemplaza={archivo !== null}
        deshabilitada={enviando}
        elegido={archivo === null ? null : `${archivo.name} (${tamano(archivo.size)})`}
        refPrimero={(e) => {
          primerCampo.current = e;
        }}
        alElegir={elegirArchivo}
      />
    );
  }

  /** Los archivos de la ficha (DOC-11): el vigente a la vista, con «Ver» y «Descargar»; los anteriores, plegados. */
  function archivos(d: Documento) {
    const { aLaVista, anteriores } = archivosDeLaFicha(d.versiones);
    return (
      <>
        <ul className="versiones" aria-label={`Archivos de ${d.titulo}`}>
          {aLaVista.map((v) => version(d, v))}
        </ul>
        {anteriores.length > 0 && (
          <details className="historial">
            <summary>Versiones anteriores ({anteriores.length})</summary>
            <ul className="versiones" aria-label={`Versiones anteriores de ${d.titulo}`}>
              {anteriores.map((v) => version(d, v))}
            </ul>
          </details>
        )}
      </>
    );
  }

  function version(d: Documento, v: Version) {
    return (
      <li key={v.id}>
        <strong>{estadoEnPalabras(v)}</strong>
        <span>
          {" "}
          · {TEXTO_TIPO[v.tipo]} de {tamano(v.tamano)} · subido el {momento(v.subida_en)}
        </span>
        {v.estado === "disponible" && (
          <>
            {" "}
            <button type="button" className="secundario" onClick={() => setViendo({ documento: d.id, version: v.id, titulo: d.titulo })}>
              Ver
            </button>{" "}
            <a className="enlace-boton" href={descarga(consorcio, d.id, v.id)} onClick={(e) => bajar(e, d.id, v.id)}>
              Descargar
            </a>
          </>
        )}
      </li>
    );
  }

  /**
   * La carga en su ventana (DOC-01): un documento nuevo (el archivo primero, que propone el título,
   * G-06; después la categoría, el título y la fecha, hoy por omisión) o el archivo de uno que ya
   * existe. El nombre de la acción va una vez, en el título; el botón dice el verbo (DOC-10).
   */
  function ventanaCarga(f: Abierto) {
    return (
      <VentanaDeFormulario
        titulo={f.tipo === "nuevo" ? "Cargar un documento" : `Subir el archivo de «${f.titulo}»`}
        textoEnviar={f.tipo === "nuevo" ? "Cargar" : "Subir el archivo"}
        alEnviar={enviarCarga}
        alCerrar={cancelar}
      >
        {campoArchivo()}
        {f.tipo === "nuevo" && camposNuevo()}
      </VentanaDeFormulario>
    );
  }

  /** La categoría, el título y la fecha de un documento nuevo, sin ayudas fijas (DOC-10). */
  function camposNuevo() {
    const titulo = `${id}-nuevo`;
    return (
      <>
        <label htmlFor={`${titulo}-categoria`}>Categoría</label>
        <select
          id={`${titulo}-categoria`}
          {...marcaDe(`${titulo}-categoria`, errores.categoria)}
          value={datos.categoria}
          onChange={(e) => cambiar("categoria", e.target.value as Categoria)}
        >
          {(Object.keys(TEXTO_CATEGORIA) as Categoria[]).filter((c) => puede(...accionesDeCarga(c))).map((c) => (
            <option key={c} value={c}>
              {TEXTO_CATEGORIA[c]}
            </option>
          ))}
        </select>
        <ErrorDeCampo idCampo={`${titulo}-categoria`} mensaje={errores.categoria} />
        <label htmlFor={`${titulo}-titulo`}>Título</label>
        <input
          id={`${titulo}-titulo`}
          type="text"
          autoComplete="off"
          maxLength={LARGO_TITULO}
          pattern={PATRON_TEXTO}
          required
          {...marcaDe(`${titulo}-titulo`, errores.titulo)}
          value={datos.titulo}
          onChange={(e) => cambiar("titulo", e.target.value)}
        />
        <ErrorDeCampo idCampo={`${titulo}-titulo`} mensaje={errores.titulo} />
        <label htmlFor={`${titulo}-fecha`}>Fecha del documento</label>
        <input
          id={`${titulo}-fecha`}
          type="date"
          required
          {...marcaDe(`${titulo}-fecha`, errores.fecha)}
          value={datos.fecha}
          onChange={(e) => cambiar("fecha", e.target.value)}
        />
        <ErrorDeCampo idCampo={`${titulo}-fecha`} mensaje={errores.fecha} />
      </>
    );
  }
}
