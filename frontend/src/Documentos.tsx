import { type FormEvent, useCallback, useEffect, useId, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { borrarCopias } from "./copiaContactos";
import { encabezadoCsrf } from "./csrf";
import { RUTA_ENTRAR } from "./enlace";
import { codigoDe, esDeConexion, LIMITE_DE_PEDIDOS, mensajeDeFalla, SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "./fallas";

// Página /documentos (H2c 2c-2; doc/diseno/H2c_Diseno.md §2 a §4 del repositorio CEM): los
// documentos del edificio por categoría, con sus versiones, y la carga en dos pasos (se crea el
// documento y después se sube el archivo crudo, que entra en cuarentena hasta que el antivirus lo
// aprueba). La interfaz nunca concede ni deduce permisos: la lista ya llega filtrada por lo que la
// persona puede ver, y un 403 al cargar es la respuesta a quien no puede cargar esa categoría. La
// descarga es una navegación común: el navegador sigue el 302 a la URL firmada de 5 minutos.

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
/** Los mismos topes que el contrato (`LARGO_MAXIMO_TITULO`, `TOPE_BYTES`). */
const LARGO_TITULO = 200;
export const TOPE_BYTES = 20 * 1024 * 1024;
const BYTES_POR_KB = 1024;
const BYTES_POR_MB = 1024 * 1024;
/** Un texto con algo más que espacios. */
const PATRON_TEXTO = ".*\\S.*";
export const ACEPTA = ".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png";

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

function esTipoAdmitido(tipo: string): tipo is TipoArchivo {
  return tipo in TEXTO_TIPO;
}

/** Por qué el archivo no sale, antes de mandarlo; nulo si se puede mandar (el servidor decide igual). */
export function problemaDelArchivo(archivo: File): string | null {
  if (!esTipoAdmitido(archivo.type)) return "Solo se aceptan archivos PDF, JPEG o PNG. Elegí otro archivo.";
  if (archivo.size > TOPE_BYTES) return "El archivo pasa de 20 MB, el máximo. Elegí uno más chico.";
  return null;
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
  if (estado === ESTADO.demasiadoGrande) return "El archivo pasa de 20 MB y no se guardó. Elegí uno más chico.";
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

type Lectura = { tipo: "cargando" } | { tipo: "sin_permiso" } | { tipo: "sin_servicio" } | { tipo: "ok"; documentos: Documento[] };

/** El formulario abierto: el de un documento nuevo, o el de subir el archivo de uno que ya existe. */
type Abierto = { tipo: "nuevo" } | { tipo: "subir"; documento: string; titulo: string };

type Aviso = { texto: string; ok: boolean };

class SinSesion extends Error {}

function exigirSesion(response: Response) {
  if (response.status === ESTADO.sinSesion) throw new SinSesion();
}

/** La dirección de descarga: una navegación que el servidor responde con el 302 a la URL firmada. */
function descarga(consorcio: string, documento: string, version: string): string {
  return `/api/v1/consorcios/${consorcio}/documentos/${documento}/versiones/${version}/descargar`;
}

/** Sin una versión disponible ni en revisión (ninguna, o todas rechazadas), el documento espera su archivo. */
function esperaArchivo(d: Documento): boolean {
  return !d.versiones.some((v) => v.estado !== "rechazado");
}

export function Documentos() {
  const id = useId();
  const navegar = useNavigate();
  const [pantalla, setPantalla] = useState<Pantalla>({ tipo: "cargando" });
  const [consorcio, setConsorcio] = useState("");
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [abierto, setAbierto] = useState<Abierto | null>(null);
  const [datos, setDatos] = useState<Datos>(VACIO);
  const [archivo, setArchivo] = useState<File | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [vuelta, setVuelta] = useState(0);
  const consultado = useRef(false);
  // Una clave por intento lógico de cada paso: se conserva si el pedido se cae y se renueva al
  // cambiar los datos o el archivo.
  const claveCrear = useRef<string | null>(null);
  const claveSubir = useRef<string | null>(null);
  const primerCampo = useRef<HTMLElement | null>(null);
  const textoAviso = useRef<HTMLParagraphElement | null>(null);
  const alAviso = useRef(false);

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
  const aEntrar = useCallback(() => {
    borrarCopias();
    navegar(RUTA_ENTRAR, { replace: true });
  }, [navegar]);

  useEffect(() => {
    if (consultado.current) return;
    consultado.current = true;
    void api
      .GET("/api/v1/me/consorcios")
      .then(({ data, error, response }) => {
        exigirSesion(response);
        const primero = data?.[0];
        if (data === undefined)
          setPantalla({ tipo: "sin_servicio", deConexion: esDeConexion(response.status, codigoDe(error)) });
        else if (primero === undefined) setPantalla({ tipo: "sin_consorcios" });
        else {
          setPantalla({ tipo: "lista", consorcios: data });
          setConsorcio(primero.id);
        }
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion) aEntrar();
        else setPantalla({ tipo: "sin_servicio", deConexion: true });
      });
  }, [aEntrar]);

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/documentos", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        // Una lista pedida antes de un cambio, o de otro edificio, puede llegar después.
        if (!vigente) return;
        if (data !== undefined) setLectura({ tipo: "ok", documentos: data.documentos });
        else if (response.status === ESTADO.sinPermiso) setLectura({ tipo: "sin_permiso" });
        else {
          setLectura({ tipo: "sin_servicio" });
          const falla = mensajeDeFalla(response.status, codigoDe(error));
          setAviso((actual) => (actual === null ? { texto: falla, ok: false } : actual));
        }
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso((actual) => (actual === null ? { texto: SIN_CONEXION, ok: false } : actual));
        }
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, vuelta, aEntrar]);

  function volverAPedir() {
    setLectura({ tipo: "cargando" });
    setVuelta((v) => v + 1);
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
    setArchivo(null);
    setAbierto(null);
  }

  function cambiar<K extends keyof Datos>(campo: K, valor: Datos[K]) {
    claveCrear.current = null;
    setDatos((d) => ({ ...d, [campo]: valor }));
  }

  function elegirArchivo(elegido: File | null) {
    claveSubir.current = null;
    setArchivo(elegido);
  }

  function enviar(evento: FormEvent) {
    evento.preventDefault();
    if (abierto === null) return;
    // El archivo se revisa antes de crear nada: un archivo que no va a entrar no deja un documento
    // vacío. El servidor vuelve a controlar el tipo real y el tamaño.
    const problema = archivo === null ? "Elegí el archivo." : problemaDelArchivo(archivo);
    if (archivo === null || problema !== null) {
      alAviso.current = true;
      setAviso({ texto: problema ?? "Elegí el archivo.", ok: false });
      return;
    }
    alAviso.current = true;
    setEnviando(true);
    setAviso(null);
    if (abierto.tipo === "subir") {
      void subir(abierto.documento, abierto.titulo, archivo, false).finally(() => setEnviando(false));
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
        volverAPedir();
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
      setAviso({
        texto: `Listo: subimos el archivo de «${titulo}». Queda «En revisión» mientras lo analizamos, de unos segundos a unos minutos; tocá «Actualizar» para ver si ya está disponible.`,
        ok: true,
      });
      // Lo que se ve lo decide la API: la lista se vuelve a pedir.
      volverAPedir();
    } catch (e: unknown) {
      if (e instanceof SinSesion) aEntrar();
      else fallo(SIN_CONEXION);
    }
  }

  const conMensaje = aviso !== null || pantalla.tipo !== "lista";

  return (
    <>
      <h1>Documentos del edificio</h1>

      {pantalla.tipo === "lista" && (
        <>
          {pantalla.consorcios.length > 1 && (
            <div className="formulario formulario-movil tarjeta">
              <label htmlFor={`${id}-consorcio`}>Edificio</label>
              <select
                id={`${id}-consorcio`}
                value={consorcio}
                disabled={enviando}
                onChange={(e) => {
                  cerrar();
                  setAviso(null);
                  setLectura({ tipo: "cargando" });
                  setConsorcio(e.target.value);
                }}
              >
                {pantalla.consorcios.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.nombre}
                  </option>
                ))}
              </select>
            </div>
          )}

          <section aria-labelledby={`${id}-lista`} className="tarjeta">
            <h2 id={`${id}-lista`}>Documentos</h2>
            {lectura.tipo === "cargando" && <p>Consultando…</p>}
            {lectura.tipo === "sin_permiso" && (
              <p>No tenés acceso a los documentos de este edificio: los ven los propietarios, la administración y el Consejo.</p>
            )}
            {lectura.tipo === "sin_servicio" && <p>No pudimos mostrar la lista.</p>}
            {lectura.tipo === "ok" && (
              <>
                {lectura.documentos.length === 0 && <p>El edificio todavía no tiene documentos cargados.</p>}
                {(Object.keys(TEXTO_CATEGORIA) as Categoria[]).map((c) => categoria(c, lectura.documentos))}
              </>
            )}
            {lectura.tipo !== "cargando" && lectura.tipo !== "sin_permiso" && (
              <p>
                <button type="button" onClick={volverAPedir} disabled={enviando}>
                  Actualizar
                </button>
              </p>
            )}
          </section>

          {lectura.tipo === "ok" && (
            <section aria-labelledby={`${id}-carga`} className="tarjeta">
              <h2 id={`${id}-carga`}>Cargar un documento</h2>
              {abierto?.tipo === "nuevo" ? (
                formularioNuevo()
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

      <div role="status" aria-live="polite" className={conMensaje ? "tarjeta" : undefined}>
        {pantalla.tipo === "cargando" && <p>Consultando…</p>}
        {pantalla.tipo === "sin_servicio" && (
          <p className="aviso-error">
            {pantalla.deConexion
              ? "No pudimos consultar tus edificios. Revisá la conexión y volvé en un rato."
              : `No pudimos consultar tus edificios. ${SERVICIO_NO_DISPONIBLE}`}
          </p>
        )}
        {pantalla.tipo === "sin_consorcios" && <p>Todavía no participás de ningún edificio.</p>}
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
        <ul className="sesiones" aria-labelledby={titulo}>
          {deEsta.map((d) => tarjeta(d))}
        </ul>
      </section>
    );
  }

  /** Un documento con su fecha y sus versiones, de la más nueva a la más vieja. */
  function tarjeta(d: Documento) {
    const titulo = `${id}-${d.id}`;
    const subiendo = abierto?.tipo === "subir" && abierto.documento === d.id;
    return (
      <li key={d.id}>
        <h4 id={titulo}>{d.titulo}</h4>
        <dl className="detalles">
          <dt>Fecha del documento</dt>
          <dd>{dia(d.fecha)}</dd>
        </dl>
        {d.versiones.length === 0 ? (
          <p>Todavía no tiene un archivo.</p>
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
                    <a className="enlace-boton" href={descarga(consorcio, d.id, v.id)} aria-describedby={titulo}>
                      Descargar
                    </a>
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
        {subiendo
          ? formularioArchivo(titulo, `Subir el archivo de «${d.titulo}»`)
          : esperaArchivo(d) && (
              <p>
                <button
                  type="button"
                  onClick={() => abrir({ tipo: "subir", documento: d.id, titulo: d.titulo })}
                  disabled={enviando}
                  aria-describedby={titulo}
                >
                  Subir archivo
                </button>
              </p>
            )}
      </li>
    );
  }

  /** El campo del archivo, con la ayuda de qué se acepta. */
  function campoArchivo(titulo: string) {
    const ayuda = `${titulo}-ayuda-archivo`;
    return (
      <>
        <label htmlFor={`${titulo}-archivo`}>Archivo</label>
        <input
          id={`${titulo}-archivo`}
          type="file"
          accept={ACEPTA}
          required={archivo === null}
          disabled={enviando}
          aria-describedby={ayuda}
          ref={
            abierto?.tipo === "subir"
              ? (e) => {
                  primerCampo.current = e;
                }
              : undefined
          }
          onChange={(e) => elegirArchivo(e.target.files?.[0] ?? null)}
        />
        <p id={ayuda}>
          PDF, JPEG o PNG de hasta 20 MB. Antes de mostrarlo lo revisa el antivirus.
          {archivo !== null && ` Elegido: ${archivo.name} (${tamano(archivo.size)}).`}
        </p>
      </>
    );
  }

  /** Subir el archivo de un documento que ya existe (o reintentar el segundo paso). */
  function formularioArchivo(titulo: string, encabezado: string) {
    return (
      <form className="formulario formulario-movil" onSubmit={enviar} aria-labelledby={`${titulo}-accion`}>
        <h5 id={`${titulo}-accion`}>{encabezado}</h5>
        {campoArchivo(titulo)}
        <p>
          <button type="submit" disabled={enviando}>
            Subir el archivo
          </button>{" "}
          <button type="button" onClick={cerrar} disabled={enviando}>
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
          {(Object.keys(TEXTO_CATEGORIA) as Categoria[]).map((c) => (
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
        {campoArchivo(titulo)}
        <p>
          <button type="submit" disabled={enviando}>
            Cargar el documento
          </button>{" "}
          <button type="button" onClick={cerrar} disabled={enviando}>
            Cancelar
          </button>
        </p>
      </form>
    );
  }
}
