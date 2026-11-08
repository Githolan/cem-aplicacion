import { type FormEvent, type MouseEvent, useCallback, useEffect, useId, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { TIPOS_DE_DOCUMENTO, TOPE_BYTES } from "./archivos";
import { borrarCopias } from "./copiaContactos";
import { encabezadoCsrf } from "./csrf";
import { RUTA_INICIO } from "./enlace";
import { useAEntrar } from "./sesionEncima";
import { codigoDe, esDeConexion, LIMITE_DE_PEDIDOS, mensajeDeFalla, SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "./fallas";
import { AlInicioSinEdificios, REQUISITOS, useAcciones, useEdificioElegido, usePuede } from "./permisos";
import { SubirArchivo } from "./SubirArchivo";
import { VisorDocumento } from "./VisorDocumento";
import { FichaDeRegistro, FilaDeRegistro, useFichas } from "./FichaDeRegistro";

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

const TEXTO_CAMPO: Record<string, string> = {
  categoria: "la categoría",
  titulo: "el título",
  fecha: "la fecha",
};

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

/** Los campos que la API marcó en un 422 (`detalle.campos`, p. ej. `body.titulo`). */
function camposDe(error: unknown): string[] {
  const campos = (error as { detalle?: { campos?: unknown } } | undefined)?.detalle?.campos;
  return Array.isArray(campos) ? campos.filter((c): c is string => typeof c === "string") : [];
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
  if (estado === ESTADO.sinPermiso && codigo === "csrf_invalido") return "No pudimos verificar el pedido. Recargá la página.";
  if (estado === ESTADO.sinPermiso) return "No tenés permiso para cargar documentos de esa categoría.";
  if (estado === ESTADO.noEncontrado) return "Ese documento ya no está en este edificio.";
  if (estado === ESTADO.demasiadoLento) return "El archivo tardó demasiado en llegar y no se guardó. Probá de nuevo con una conexión más firme.";
  if (estado === ESTADO.demasiadoGrande) return "El archivo pasa de 20 MB y no se guardó. Hay que elegir uno más chico.";
  if (estado === ESTADO.tipoNoAdmitido)
    return "Solo se aceptan archivos PDF, JPEG o PNG, y el contenido tiene que coincidir con el tipo. El archivo no se guardó.";
  if (estado === ESTADO.conflicto && codigo === "idempotencia_en_curso")
    return "El pedido se está procesando. Probá de nuevo en unos segundos.";
  if (estado === ESTADO.invalido && codigo === "idempotencia_reutilizada")
    return "Cambiaste los datos de un intento anterior. Volvé a enviar el formulario.";
  if (estado === ESTADO.invalido && paso === "crear") {
    const nombres = [...new Set(campos.map((c) => TEXTO_CAMPO[c.split(".").pop() ?? ""]).filter((t) => t !== undefined))];
    const que = nombres.length > 0 ? nombres.join(", ") : "los datos";
    return `Revisá ${que}. El título no puede llevar teléfonos ni correos.`;
  }
  if (estado === ESTADO.limite) return LIMITE_DE_PEDIDOS;
  // Un 503 con `Retry-After` es el tope de cargas a la vez; sin él, el servicio no está.
  if (estado === ESTADO.noDisponible && conEspera) return "Hay muchas cargas en curso. Probá de nuevo en un momento.";
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

/** Si el intento terminó y el próximo lleva otra clave (igual que en /contactos/gestion). */
function renovarClave(estado: number, codigo: string | undefined): boolean {
  if (codigo === "idempotencia_reutilizada") return true;
  return estado < ESTADO.errorDelServidor && codigo !== "idempotencia_en_curso";
}

type Datos = { categoria: Categoria; titulo: string; fecha: string };

const VACIO: Datos = { categoria: "actas_y_decisiones", titulo: "", fecha: "" };

type Pantalla =
  | { tipo: "cargando" }
  | { tipo: "sin_servicio"; deConexion: boolean }
  | { tipo: "sin_consorcios" }
  | { tipo: "lista"; consorcios: Consorcio[] };

const SIN_EDIFICIOS: readonly Consorcio[] = [];

/** `solo_carga`: quien carga sin acceso a la lista (el administrador externo, documento 08 §4). */
type Lectura = { tipo: "cargando" } | { tipo: "solo_carga" } | { tipo: "sin_servicio" } | { tipo: "ok"; documentos: Documento[] };

/** El formulario abierto: el de un documento nuevo, o el de subir el archivo de uno que ya existe. */
type Abierto = { tipo: "nuevo" } | { tipo: "subir"; documento: string; titulo: string };

const NUEVO: Abierto = { tipo: "nuevo" };

type Aviso = { texto: string; ok: boolean };

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
  // Quien solo carga no tiene otra cosa que hacer en la pantalla: el formulario ya está abierto y no
  // hay a qué volver. No toma el foco al llegar: eso queda para cuando la persona abre uno.
  const soloCarga = lectura.tipo === "solo_carga";
  const formulario = abierto ?? (soloCarga ? NUEVO : null);
  const [datos, setDatos] = useState<Datos>(VACIO);
  const [archivo, setArchivo] = useState<File | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
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
  const primerCampo = useRef<HTMLElement | null>(null);
  const textoAviso = useRef<HTMLParagraphElement | null>(null);
  const alAviso = useRef(false);
  // El próximo pedido de la lista es el refresco automático: si falla, la lista a la vista no se pierde.
  const refrescoAutomatico = useRef(false);

  useEffect(() => {
    if (abierto !== null) primerCampo.current?.focus();
  }, [abierto]);

  // El foco va al aviso cuando termina un envío, o cuando el archivo se rechaza sin enviar nada.
  useEffect(() => {
    if (enviando || !alAviso.current || aviso === null) return;
    alAviso.current = false;
    textoAviso.current?.focus();
  }, [enviando, aviso]);

  // Sin sesión no queda ninguna copia de /contactos, igual que en las demás páginas con sesión.
  function bajar(evento: MouseEvent<HTMLAnchorElement>, documento: string, version: string) {
    evento.preventDefault();
    setAviso(null);
    // El aviso queda al final de la página: sin el foco, tocar «Descargar» parece no hacer nada. Se
    // marca recién con la falla, para que una descarga buena no le deje el foco a un aviso posterior.
    const fallo = (texto: string) => {
      alAviso.current = true;
      setAviso({ texto, ok: false });
      // El aviso está en la página, debajo de la ficha: la ficha se cierra para que lo tome el foco.
      cerrarFichaDe(false);
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
    // El refresco que falla deja la lista y un formulario abierto como están, dice que puede estar
    // vieja y no sigue solo (`setLectura` no cambia, así que no se programa otro): queda «Actualizar».
    const fallaAutomatica = (falla: string) =>
      setAviso((actual) =>
        actual !== null && !actual.ok ? actual : { texto: `${falla} La lista puede no estar al día: tocá «Actualizar».`, ok: false },
      );
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/documentos", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        // Una lista pedida antes de un cambio, o de otro edificio, puede llegar después.
        if (!vigente) return;
        if (data !== undefined) setLectura({ tipo: "ok", documentos: data.documentos });
        else if (response.status === ESTADO.sinPermiso) {
          if (quedaLaCarga) setLectura({ tipo: "solo_carga" });
          else navegar(RUTA_INICIO, { replace: true });
        } else if (automatico) fallaAutomatica(mensajeDeFalla(response.status, codigoDe(error)));
        else {
          setLectura({ tipo: "sin_servicio" });
          const falla = mensajeDeFalla(response.status, codigoDe(error));
          setAviso((actual) => (actual === null ? { texto: falla, ok: false } : actual));
        }
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else if (automatico) fallaAutomatica(SIN_CONEXION);
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso((actual) => (actual === null ? { texto: SIN_CONEXION, ok: false } : actual));
        }
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

  function volverAPedir() {
    setLectura({ tipo: "cargando" });
    setVuelta((v) => v + 1);
  }

  /** Después de una carga, lo que se ve lo decide la API. Quien solo carga no tiene lista que pedir. */
  function actualizarLista() {
    if (!soloCarga) volverAPedir();
  }

  function abrir(nuevo: Abierto) {
    claveCrear.current = null;
    claveSubir.current = null;
    if (nuevo.tipo === "nuevo") setDatos(VACIO);
    setArchivo(null);
    setAviso(null);
    setAbierto(nuevo);
  }

  function cerrar() {
    claveCrear.current = null;
    claveSubir.current = null;
    setDatos(VACIO);
    setArchivo(null);
    setAbierto(null);
  }

  function cambiar<K extends keyof Datos>(campo: K, valor: Datos[K]) {
    claveCrear.current = null;
    setDatos((d) => ({ ...d, [campo]: valor }));
  }

  function elegirArchivo(elegido: File) {
    claveSubir.current = null;
    setArchivo(elegido);
  }

  function enviar(evento: FormEvent) {
    evento.preventDefault();
    if (formulario === null) return;
    // El tipo y el tamaño ya los controló `SubirArchivo` al elegirlo: un archivo que no va a entrar
    // no llega acá ni deja un documento vacío. El servidor vuelve a controlar el tipo real y el tamaño.
    if (archivo === null) {
      alAviso.current = true;
      setAviso({ texto: "Hay que elegir el archivo.", ok: false });
      return;
    }
    alAviso.current = true;
    setEnviando(true);
    setAviso(null);
    if (formulario.tipo === "subir") {
      void subir(formulario.documento, formulario.titulo, archivo, false).finally(() => setEnviando(false));
      return;
    }
    const titulo = datos.titulo.trim();
    void crear(titulo)
      .then((documento) => (documento === null ? undefined : subir(documento, titulo, archivo, true)))
      .finally(() => setEnviando(false));
  }

  /** Primer paso: crea el documento. Devuelve su id, o nulo si no se creó (el aviso ya lo dice). */
  async function crear(titulo: string): Promise<string | null> {
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
        setAviso({ texto: mensajeDeError(response.status, codigo, "crear", camposDe(error)), ok: false });
        return null;
      }
      claveCrear.current = null;
      return data.id;
    } catch (e: unknown) {
      if (e instanceof SinSesion) aEntrar();
      else setAviso({ texto: SIN_CONEXION, ok: false });
      return null;
    }
  }

  /** Segundo paso: sube el archivo crudo, sin JSON ni multipart, con su tipo como `Content-Type`. */
  async function subir(documento: string, titulo: string, elegido: File, recienCreado: boolean) {
    claveSubir.current ??= crypto.randomUUID();
    // Si el documento se acaba de crear y el archivo no entra, el documento queda sin archivo:
    // se dice y el formulario pasa a subir solo el archivo de ese documento.
    const fallo = (texto: string) => {
      setAviso({
        texto: recienCreado ? `Creamos el documento «${titulo}», pero el archivo no se subió. ${texto}` : texto,
        ok: false,
      });
      if (recienCreado) {
        claveCrear.current = null;
        setAbierto({ tipo: "subir", documento, titulo });
        actualizarLista();
      }
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
        fallo(mensajeDeError(response.status, codigo, "subir", [], response.headers.has("Retry-After")));
        return;
      }
      cerrar();
      cerrarFichaDe(false);
      // Sin acceso a la lista, quien solo carga no ve el resultado del análisis. Con la lista caída
      // no hay refresco automático: queda «Actualizar».
      const despues = soloCarga
        ? "Queda «En revisión» mientras lo analizamos."
        : lectura.tipo === "sin_servicio"
          ? "Queda «En revisión» mientras lo analizamos, de unos segundos a unos minutos; tocá «Actualizar» para ver si ya está disponible."
          : "Queda «En revisión» mientras lo analizamos, de unos segundos a unos minutos; la lista se actualiza sola.";
      setAviso({ texto: `Listo: subimos el archivo de «${titulo}». ${despues}`, ok: true });
      actualizarLista();
    } catch (e: unknown) {
      if (e instanceof SinSesion) aEntrar();
      else fallo(SIN_CONEXION);
    }
  }

  const conMensaje = aviso !== null || pantalla.tipo !== "lista";

  if (pantalla.tipo === "sin_consorcios") return <AlInicioSinEdificios />;

  return (
    <>
      <h1>Documentos del edificio</h1>

      {pantalla.tipo === "lista" && (
        <>
          {edificios.length > 1 && (
            <div className="formulario formulario-movil tarjeta">
              <label htmlFor={`${id}-consorcio`}>Edificio</label>
              <select
                id={`${id}-consorcio`}
                value={consorcio}
                disabled={enviando}
                onChange={(e) => {
                  cerrar();
                  cerrarFichaDe(false);
                  setAviso(null);
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

          {!soloCarga && (
            <section aria-labelledby={`${id}-lista`} className="tarjeta">
              <h2 id={`${id}-lista`}>Documentos</h2>
              {lectura.tipo === "cargando" && <p>Consultando…</p>}
              {lectura.tipo === "sin_servicio" && <p>No pudimos mostrar la lista.</p>}
              {lectura.tipo === "ok" && (
                <>
                  {lectura.documentos.length === 0 && <p>El edificio todavía no tiene documentos cargados.</p>}
                  {(Object.keys(TEXTO_CATEGORIA) as Categoria[]).map((c) => categoria(c, lectura.documentos))}
                </>
              )}
              {lectura.tipo !== "cargando" && (
                <p>
                  <button type="button" className="secundario" onClick={volverAPedir} disabled={enviando}>
                    Actualizar
                  </button>
                </p>
              )}
            </section>
          )}

          {/* También sin acceso a la lista: el administrador externo solo carga (documento 08 §4). Con la
              lista caída sigue a la vista, porque cargar no depende de ella, y un formulario nuevo abierto
              no se va mientras llega la lista. */}
          {carga && (lectura.tipo !== "cargando" || abierto?.tipo === "nuevo") && (
            <section aria-labelledby={`${id}-carga`} className="tarjeta">
              <h2 id={`${id}-carga`}>Cargar un documento</h2>
              {formulario?.tipo === "nuevo" ? (
                formularioNuevo()
              ) : formulario?.tipo === "subir" && !enSuFicha(formulario.documento) ? (
                // El reintento del archivo de un documento cuya ficha no está abierta: el que se acaba
                // de crear, el de quien solo carga (sin acceso a la lista), una categoría que la persona
                // carga pero no ve, o la lista caída.
                formularioArchivo(`${id}-reintento`, `Subir el archivo de «${formulario.titulo}»`, "h3")
              ) : (
                <p>
                  <button type="button" onClick={() => abrir({ tipo: "nuevo" })} disabled={enviando}>
                    Cargar un documento
                  </button>
                </p>
              )}
            </section>
          )}
        </>
      )}

      {fichaDelDocumento()}

      {viendo !== null && (
        <VisorDocumento consorcio={consorcio} documento={viendo.documento} version={viendo.version} nombre={viendo.titulo}
          alCerrar={() => setViendo(null)} aEntrar={aEntrar} />
      )}

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
        {aviso && (
          <p ref={textoAviso} tabIndex={-1} className={aviso.ok ? "aviso-ok" : "aviso-error"}>
            {aviso.texto}
          </p>
        )}
      </div>
    </>
  );

  /** Los documentos de una categoría; una categoría sin documentos no se muestra. */
  function categoria(c: Categoria, documentos: Documento[]) {
    const deEsta = documentos.filter((d) => d.categoria === c);
    if (deEsta.length === 0) return null;
    const titulo = `${id}-${c}`;
    return (
      <section key={c} aria-labelledby={titulo}>
        <h3 id={titulo}>{TEXTO_CATEGORIA[c]}</h3>
        <ul className="filas-registro" aria-labelledby={titulo}>
          {deEsta.map((d) => (
            <li key={d.id}>
              <FilaDeRegistro titulo={d.titulo} resumen={resumenDe(d)} alAbrir={() => abrirFicha(d.id)} refFila={refFila(d.id)}
                abierta={fichaAbierta === d.id} deshabilitada={enviando} />
            </li>
          ))}
        </ul>
      </section>
    );
  }

  /** Cerrar la ficha también deja el archivo a medio elegir de ese documento. */
  function cerrarFicha() {
    if (abierto?.tipo === "subir" && abierto.documento === fichaAbierta) cerrar();
    cerrarFichaDe();
  }

  /** Si la ficha de ese documento está a la vista (ahí se dibuja su formulario de archivo). */
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
            valor:
              d.versiones.length === 0 ? (
                "Todavía no tiene un archivo."
              ) : (
                <ul className="versiones" aria-label={`Archivos de ${d.titulo}`}>
                  {d.versiones.map((v) => (
                    <li key={v.id}>
                      <strong>{estadoEnPalabras(v)}</strong>
                      <span>
                        {" "}
                        · {TEXTO_TIPO[v.tipo]} de {tamano(v.tamano)} · subido el {momento(v.subida_en)}
                      </span>
                      {v.estado === "disponible" && (
                        <>
                          {" "}
                          <button type="button" className="secundario"
                            onClick={() => setViendo({ documento: d.id, version: v.id, titulo: d.titulo })}>
                            Ver
                          </button>{" "}
                          <a className="enlace-boton" href={descarga(consorcio, d.id, v.id)} onClick={(e) => bajar(e, d.id, v.id)}>
                            Descargar
                          </a>
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              ),
          },
        ]}
      >
        {subiendo
          ? formularioArchivo(`${id}-${d.id}`, `Subir el archivo de «${d.titulo}»`, "h3")
          : subir !== null &&
            puede(...accionesDeCarga(d.categoria)) && (
              <p>
                <button type="button" className="secundario" onClick={() => abrir({ tipo: "subir", documento: d.id, titulo: d.titulo })}
                  disabled={enviando}>
                  {subir}
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
        nota="Antes de mostrarlo lo revisa el antivirus."
        elegido={archivo === null ? null : `${archivo.name} (${tamano(archivo.size)})`}
        refPrimero={
          abierto?.tipo === "subir"
            ? (e) => {
                primerCampo.current = e;
              }
            : undefined
        }
        alElegir={elegirArchivo}
      />
    );
  }

  /** Subir el archivo de un documento que ya existe (o reintentar el segundo paso). */
  function formularioArchivo(titulo: string, encabezado: string, Nivel: "h3" | "h5" = "h5") {
    return (
      <form className="formulario formulario-movil" onSubmit={enviar} aria-labelledby={`${titulo}-accion`}>
        <Nivel id={`${titulo}-accion`}>{encabezado}</Nivel>
        {campoArchivo()}
        <p>
          <button type="submit" disabled={enviando}>
            Subir el archivo
          </button>{" "}
          <button type="button" className="secundario peligro" onClick={cerrar} disabled={enviando}>
            Cancelar
          </button>
        </p>
      </form>
    );
  }

  /** Un documento nuevo: categoría, título, fecha y archivo, en un solo envío de dos pasos. */
  function formularioNuevo() {
    const titulo = `${id}-nuevo`;
    const ayudaTitulo = `${titulo}-ayuda-titulo`;
    return (
      <form className="formulario formulario-movil" onSubmit={enviar} aria-labelledby={`${titulo}-accion`}>
        <h3 id={`${titulo}-accion`}>Datos del documento</h3>
        <label htmlFor={`${titulo}-categoria`}>Categoría</label>
        <select
          id={`${titulo}-categoria`}
          ref={(e) => {
            primerCampo.current = e;
          }}
          value={datos.categoria}
          disabled={enviando}
          onChange={(e) => cambiar("categoria", e.target.value as Categoria)}
        >
          {(Object.keys(TEXTO_CATEGORIA) as Categoria[]).filter((c) => puede(...accionesDeCarga(c))).map((c) => (
            <option key={c} value={c}>
              {TEXTO_CATEGORIA[c]}
            </option>
          ))}
        </select>
        <label htmlFor={`${titulo}-titulo`}>Título</label>
        <input
          id={`${titulo}-titulo`}
          type="text"
          autoComplete="off"
          maxLength={LARGO_TITULO}
          pattern={PATRON_TEXTO}
          required
          disabled={enviando}
          aria-describedby={ayudaTitulo}
          value={datos.titulo}
          onChange={(e) => cambiar("titulo", e.target.value)}
        />
        <p id={ayudaTitulo}>Sin teléfonos ni correos en el título.</p>
        <label htmlFor={`${titulo}-fecha`}>Fecha del documento</label>
        <input
          id={`${titulo}-fecha`}
          type="date"
          required
          disabled={enviando}
          value={datos.fecha}
          onChange={(e) => cambiar("fecha", e.target.value)}
        />
        {campoArchivo()}
        <p>
          <button type="submit" disabled={enviando}>
            Cargar el documento
          </button>
          {!soloCarga && (
            <>
              {" "}
              <button type="button" className="secundario peligro" onClick={cerrar} disabled={enviando}>
                Cancelar
              </button>
            </>
          )}
        </p>
      </form>
    );
  }
}
