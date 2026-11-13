import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { TIPOS_DE_DOCUMENTO, TOPE_BYTES } from "./archivos";
import { encabezadoCsrf } from "./csrf";
import { mensajeDeError as mensajeDeCarga } from "./Documentos";
import { ENCABEZADO_IDEMPOTENCIA, LARGO_TITULO, mensajeDeActo, renovarClave } from "./egresos/comun";
import { exigirSesion, SinSesion, useConsultaDelAntivirus } from "./egresos/useConsorcio";
import { codigoDe, SIN_CONEXION } from "./fallas";
import { SubirArchivo } from "./SubirArchivo";
import { VisorDocumento } from "./VisorDocumento";

// «Donde sea input de datos de un documento debe realizarse la subida y después IA completa y humano
// confirma» (pedido del propietario, 08-11-2026; app 0088). Elegir el archivo es el único paso: se
// crea el documento para carga, se sube el archivo, el antivirus lo revisa y la IA lo lee. Lo leído
// solo llena campos del formulario de quien lo usa, que los marca para revisar; guardar es confirmar.
// Sin archivo, el formulario se completa a mano como siempre. Mientras un archivo se sube, se revisa
// o se lee, el formulario no se guarda (`alEsperar`): guardar sin el archivo lo perdería. El archivo
// anterior sigue valiendo hasta que el nuevo pasa el antivirus; «Seguir sin este archivo» deja de
// esperarlo y vuelve al anterior, si había.
//
// «Quitar el archivo» (app 0089): el archivo subido acá que resultó ser el equivocado se quita antes
// de guardar. El formulario lo suelta (vuelve el anterior, si había) y el servidor lo da de baja: deja
// de aparecer en Documentos, sin borrar nada. Solo el subido acá, y el servidor lo niega si ya respalda
// algo guardado. Pide confirmación: es un acto que no se deshace.

export type LecturaCarga = components["schemas"]["LecturaCarga"];
export type TipoCarga = components["schemas"]["TipoCarga"];

/** Un documento para carga cuyo archivo ya pasó el antivirus: lo que el formulario manda como
 * `documento_id`, y con qué se abre en el visor. */
export type Cargado = { documento: string; version: string; nombre: string };

type Fase =
  | { tipo: "nada" }
  | { tipo: "subiendo" }
  | { tipo: "revisando" }
  | { tipo: "leyendo" }
  | { tipo: "leida"; lectura: LecturaCarga }
  | { tipo: "no_leida" }
  | { tipo: "sin_lectura" }
  | { tipo: "rechazado" }
  | { tipo: "no_subio" }
  /** La persona dejó de esperar: sin el archivo nuevo (antivirus) o sin su lectura (a mano). */
  | { tipo: "dejado"; conArchivo: boolean }
  /** Se quitó el archivo subido acá (app 0089). */
  | { tipo: "quitado"; vuelveAnterior: boolean };

export const TEXTO_SUBIENDO = "Subiendo…";
export const TEXTO_REVISANDO = "El antivirus lo está revisando…";
export const TEXTO_LEYENDO = "Leyendo los datos…";
export const TEXTO_LEIDA = "Listo: se leyeron los datos del documento. Hay que revisar cada campo marcado antes de guardar.";
export const TEXTO_A_MANO = "No se pudieron leer los datos del documento: hay que completarlos a mano.";
export const TEXTO_SIN_LECTURA = "La lectura de documentos no está disponible: hay que completar los datos a mano.";
export const TEXTO_RECHAZADO = "Este archivo no se puede usar: no pasó el antivirus. Hay que elegir otro.";
export const TEXTO_ANTIVIRUS_TARDA = "El antivirus está tardando. Se puede completar el formulario a mano mientras tanto.";
export const TEXTO_CUIT_INVALIDO =
  "El CUIT que figura en el documento no es válido: hay que revisar el CUIT del proveedor antes de seguir.";
export const MARCA_LECTURA = "Lo completó la lectura: revisar";
export const TEXTO_SIGUE_ANTERIOR = "Sigue adjunto el archivo anterior.";
export const TEXTO_DEJADO_SIN_ARCHIVO = "Se dejó de esperar ese archivo: no queda adjunto.";
export const TEXTO_DEJADO_SIN_LECTURA = "Se dejó de esperar la lectura: el archivo queda adjunto y los datos se completan a mano.";
export const SEGUIR_SIN_ESPERAR = "Seguir sin este archivo";
export const SEGUIR_SIN_LECTURA = "Seguir sin la lectura";
export const QUITAR_ARCHIVO = "Quitar el archivo";
export const CONFIRMAR_QUITAR = "Sí, quitar el archivo";
export const NO_QUITAR = "No, dejarlo";
/** La pregunta antes de quitar. */
export const preguntaQuitar = (nombre: string) =>
  `¿Quitar «${nombre}»? Deja de estar adjunto y sale de Documentos. No se puede deshacer.`;
export const TEXTO_QUITADO = "Se quitó el archivo: no queda adjunto ni aparece en Documentos.";
export const TEXTO_QUITADO_VUELVE_ANTERIOR = "Se quitó el archivo: vuelve a estar adjunto el anterior.";
/** Lo que dice el formulario mientras no se puede guardar. */
export const TEXTO_ESPERA_PARA_GUARDAR = "Hay que esperar a que termine el archivo, o elegir seguir sin él, para guardar.";

const hoy = () => {
  const ahora = new Date();
  const dos = (n: number) => String(n).padStart(2, "0");
  return `${ahora.getFullYear()}-${dos(ahora.getMonth() + 1)}-${dos(ahora.getDate())}`;
};

/** El título del documento: el nombre del archivo sin la extensión o, si no sirve, el de siempre. */
function tituloDe(archivo: File, porOmision: string): string {
  const sinExtension = archivo.name.replace(/\.[^.]+$/, "").trim();
  return (sinExtension === "" ? porOmision : sinExtension).slice(0, LARGO_TITULO);
}

type Props = {
  consorcio: string;
  tipo: TipoCarga;
  /** «Factura», «Documento del proveedor». */
  titulo: string;
  /** «de la factura»: para nombrar los botones. */
  de: string;
  /** «Ver la factura». */
  textoVer: string;
  deshabilitada?: boolean;
  /** Un documento ya cargado (la versión de la orden, o el que trae otra pantalla). */
  inicial?: Cargado | null;
  aEntrar: () => void;
  /** El documento que se manda al guardar; nulo si no hay uno que sirva. */
  alCargar: (cargado: Cargado | null) => void;
  /** Una sola vez por versión, cuando la lectura terminó con datos. */
  alLeer: (lectura: LecturaCarga) => void;
  /** Si hay un archivo subiéndose, en el antivirus o leyéndose: mientras tanto no se guarda. */
  alEsperar: (esperando: boolean) => void;
};

export function CargaConLectura({ consorcio, tipo, titulo, de, textoVer, deshabilitada = false, inicial = null, aEntrar, alCargar, alLeer, alEsperar }: Props) {
  const [fase, setFase] = useState<Fase>({ tipo: "nada" });
  const [texto, setTexto] = useState<string | null>(null);
  /** El subido acá; mientras no se subió nada, vale el inicial (que puede llegar después). */
  const [propio, setPropio] = useState<Cargado | null>(null);
  const [tocado, setTocado] = useState(false);
  const cargado = tocado ? propio : inicial;
  const [viendo, setViendo] = useState(false);
  /** Preguntando si se quita el archivo subido acá, o quitándolo. */
  const [quitar, setQuitar] = useState<"nada" | "preguntando" | "quitando">("nada");
  const claveQuitar = useRef<string | null>(null);
  /** Por qué no se pudo quitar; lo demás (la lectura, sus avisos) sigue como estaba. */
  const [falloQuitar, setFalloQuitar] = useState<string | null>(null);
  /** La versión que se sube o se espera. */
  const enEspera = useRef<{ documento: string; version: string; nombre: string } | null>(null);
  /** El documento creado cuyo archivo todavía no entró: el reintento sube solo el archivo. */
  const sinArchivo = useRef<string | null>(null);
  const claveDocumento = useRef<string | null>(null);
  const claveArchivo = useRef<string | null>(null);
  const leidas = useRef(new Set<string>());
  const ultimas = useRef({ alCargar, alLeer, alEsperar });
  ultimas.current = { alCargar, alLeer, alEsperar };

  const ponerCargado = useCallback((nuevo: Cargado | null) => {
    setTocado(true);
    setPropio(nuevo);
    ultimas.current.alCargar(nuevo);
  }, []);

  const mirar = useCallback(() => {
    const cual = enEspera.current;
    if (cual === null) return;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/documentos/{documento_id}/versiones/{version_id}/lectura-carga", {
        params: { path: { consorcio_id: consorcio, documento_id: cual.documento, version_id: cual.version } },
      })
      .then(({ data, response }) => {
        exigirSesion(response);
        // Otro archivo ya reemplazó a este: su respuesta no cuenta.
        if (enEspera.current !== cual || data === undefined) return;
        if (data.estado_version === "rechazado") {
          enEspera.current = null;
          setFase({ tipo: "rechazado" });
          return;
        }
        if (data.estado_version === "en_cuarentena") return;
        ponerCargado(cual);
        if (data.estado === "leyendo") setFase({ tipo: "leyendo" });
        else {
          enEspera.current = null;
          if (data.estado === "leida") {
            setFase({ tipo: "leida", lectura: data });
            if (!leidas.current.has(cual.version)) {
              leidas.current.add(cual.version);
              ultimas.current.alLeer(data);
            }
          } else setFase({ tipo: data.estado === "no_leida" ? "no_leida" : "sin_lectura" });
        }
      })
      .catch((e: unknown) => {
        // Una consulta que no llega se repite en la vuelta siguiente; sin sesión, a entrar.
        if (e instanceof SinSesion) aEntrar();
      });
  }, [consorcio, aEntrar, ponerCargado]);

  const antivirusTarda = useConsultaDelAntivirus(fase.tipo === "revisando", mirar);
  const lecturaTarda = useConsultaDelAntivirus(fase.tipo === "leyendo", mirar);
  useEffect(() => {
    // La lectura que no termina se deja: el formulario se completa a mano.
    if (lecturaTarda && fase.tipo === "leyendo") {
      enEspera.current = null;
      setFase({ tipo: "no_leida" });
    }
  }, [lecturaTarda, fase.tipo]);

  async function subir(archivo: File) {
    setFase({ tipo: "subiendo" });
    setTexto(null);
    enEspera.current = null;
    // El archivo anterior sigue adjunto hasta que el nuevo pase el antivirus.
    const nombre = tituloDe(archivo, titulo);
    try {
      if (sinArchivo.current === null) {
        claveDocumento.current ??= crypto.randomUUID();
        const creado = await api.POST("/api/v1/consorcios/{consorcio_id}/documentos/para-carga", {
          headers: encabezadoCsrf(),
          params: { path: { consorcio_id: consorcio }, header: { [ENCABEZADO_IDEMPOTENCIA]: claveDocumento.current } },
          body: { tipo, titulo: nombre, fecha: hoy() },
        });
        exigirSesion(creado.response);
        if (creado.data === undefined) {
          const codigo = codigoDe(creado.error);
          if (renovarClave(creado.response.status, codigo)) claveDocumento.current = null;
          setFase({ tipo: "no_subio" });
          setTexto(`El archivo no se subió. ${mensajeDeActo(creado.response.status, creado.error)}`);
          return;
        }
        claveDocumento.current = null;
        sinArchivo.current = creado.data.id;
      }
      claveArchivo.current ??= crypto.randomUUID();
      const documento = sinArchivo.current;
      const subida = await api.POST("/api/v1/consorcios/{consorcio_id}/documentos/{documento_id}/versiones", {
        headers: { ...encabezadoCsrf(), "Content-Type": archivo.type },
        params: { path: { consorcio_id: consorcio, documento_id: documento }, header: { [ENCABEZADO_IDEMPOTENCIA]: claveArchivo.current } },
        // El archivo crudo, como en Documentos: `bodySerializer` lo deja pasar sin JSON.
        body: archivo as unknown as string,
        bodySerializer: (cuerpo) => cuerpo,
      });
      exigirSesion(subida.response);
      if (subida.data === undefined) {
        const codigo = codigoDe(subida.error);
        if (renovarClave(subida.response.status, codigo)) claveArchivo.current = null;
        setFase({ tipo: "no_subio" });
        setTexto(`El archivo no se subió. ${mensajeDeCarga(subida.response.status, codigo, "subir", [], subida.response.headers.has("Retry-After"))}`);
        return;
      }
      claveArchivo.current = null;
      sinArchivo.current = null;
      enEspera.current = { documento, version: subida.data.id, nombre };
      setFase({ tipo: "revisando" });
    } catch (e: unknown) {
      if (e instanceof SinSesion) aEntrar();
      else {
        setFase({ tipo: "no_subio" });
        setTexto(SIN_CONEXION);
      }
    }
  }

  /** Quitar el archivo subido acá: el servidor lo da de baja y el formulario vuelve al anterior. */
  async function quitarArchivo() {
    // Nunca con otro archivo subiéndose, en el antivirus o leyéndose: quitar soltaría su espera.
    // Ni con el formulario guardando (`deshabilitada`): mandaría el archivo que se está quitando.
    if (propio === null || subiendoOtro || deshabilitada) return;
    setQuitar("quitando");
    setFalloQuitar(null);
    claveQuitar.current ??= crypto.randomUUID();
    try {
      const { error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/documentos/{documento_id}/quitar", {
        headers: encabezadoCsrf(),
        params: { path: { consorcio_id: consorcio, documento_id: propio.documento }, header: { [ENCABEZADO_IDEMPOTENCIA]: claveQuitar.current } },
      });
      exigirSesion(response);
      if (!response.ok) {
        const codigo = codigoDe(error);
        if (renovarClave(response.status, codigo)) claveQuitar.current = null;
        setQuitar("nada");
        setFalloQuitar(`El archivo no se quitó. ${mensajeDeActo(response.status, error)}`);
        return;
      }
      claveQuitar.current = null;
      setQuitar("nada");
      setTocado(false);
      setPropio(null);
      ultimas.current.alCargar(inicial);
      setFase({ tipo: "quitado", vuelveAnterior: inicial !== null });
    } catch (e: unknown) {
      setQuitar("nada");
      if (e instanceof SinSesion) aEntrar();
      else setFalloQuitar(`El archivo no se quitó. ${SIN_CONEXION}`);
    }
  }

  function elegir(archivo: File) {
    // Otro archivo es otro pedido: si el anterior no entró, sirve su documento, no su clave.
    claveArchivo.current = null;
    claveQuitar.current = null;
    setFalloQuitar(null);
    // Otro archivo cancela una pregunta de quitar abierta: se preguntaba por el anterior.
    setQuitar("nada");
    void subir(archivo);
  }

  // Mientras se quita, tampoco se guarda: el formulario mandaría el archivo que se está quitando.
  // Con la pregunta de quitar abierta no se elige otro archivo (`SubirArchivo` deshabilitada).
  // Si el formulario empieza a guardar con la pregunta abierta, la pregunta se cierra.
  useEffect(() => {
    if (deshabilitada) setQuitar((actual) => (actual === "preguntando" ? "nada" : actual));
  }, [deshabilitada]);
  /** Un archivo subiéndose, en el antivirus o leyéndose. */
  const subiendoOtro = fase.tipo === "subiendo" || fase.tipo === "revisando" || fase.tipo === "leyendo";
  const ocupada = subiendoOtro || quitar === "quitando";
  useEffect(() => {
    ultimas.current.alEsperar(ocupada);
  }, [ocupada]);
  // Si el formulario se cierra en medio de la espera, ya no hay nada que esperar.
  useEffect(() => () => ultimas.current.alEsperar(false), []);

  /** Dejar de esperar: sin el archivo nuevo (vuelve el anterior, si había) o sin su lectura. */
  function dejarDeEsperar() {
    enEspera.current = null;
    setFase({ tipo: "dejado", conArchivo: fase.tipo === "leyendo" });
  }
  const progreso = fase.tipo === "subiendo" ? TEXTO_SUBIENDO : fase.tipo === "revisando" ? TEXTO_REVISANDO : fase.tipo === "leyendo" ? TEXTO_LEYENDO : null;
  const resultado =
    fase.tipo === "leida" ? TEXTO_LEIDA
    : fase.tipo === "no_leida" ? TEXTO_A_MANO
    : fase.tipo === "sin_lectura" ? TEXTO_SIN_LECTURA
    : null;

  return (
    <section className="carga-con-lectura" aria-label={titulo}>
      <SubirArchivo titulo={titulo} de={de} tipos={TIPOS_DE_DOCUMENTO} tope={TOPE_BYTES} reemplaza={cargado !== null}
        deshabilitada={deshabilitada || ocupada || quitar !== "nada"}
        nota="Los datos que se lean completan el formulario: hay que revisarlos antes de guardar." alElegir={elegir} />
      {progreso !== null && (
        <p role="status" className="aviso-info">
          {progreso}
        </p>
      )}
      {fase.tipo === "revisando" && antivirusTarda && <p className="aviso-info">{TEXTO_ANTIVIRUS_TARDA}</p>}
      {(fase.tipo === "revisando" || fase.tipo === "leyendo") && (
        <div className="acciones">
          <button type="button" className="secundario" onClick={dejarDeEsperar}>
            {fase.tipo === "leyendo" ? SEGUIR_SIN_LECTURA : SEGUIR_SIN_ESPERAR}
          </button>
        </div>
      )}
      {fase.tipo === "dejado" && (
        <p role="status" className="aviso-info">
          {fase.conArchivo ? TEXTO_DEJADO_SIN_LECTURA : TEXTO_DEJADO_SIN_ARCHIVO}
        </p>
      )}
      {resultado !== null && (
        <p role="status" className={fase.tipo === "leida" ? "aviso-info" : "aviso-error"}>
          {resultado}
        </p>
      )}
      {fase.tipo === "leida" && fase.lectura.cuit_invalido && (
        <p className="aviso-error" role="alert">
          {TEXTO_CUIT_INVALIDO}
        </p>
      )}
      {fase.tipo === "rechazado" && (
        <p className="aviso-error" role="alert">
          {TEXTO_RECHAZADO}
        </p>
      )}
      {(fase.tipo === "rechazado" || fase.tipo === "no_subio" || (fase.tipo === "dejado" && !fase.conArchivo) ||
        fase.tipo === "subiendo" || fase.tipo === "revisando") &&
        cargado !== null && <p className="aviso-info">{TEXTO_SIGUE_ANTERIOR}</p>}
      {fase.tipo === "no_subio" && texto !== null && (
        <p className="aviso-error" role="alert">
          {texto}
        </p>
      )}
      {fase.tipo === "quitado" && (
        <p role="status" className="aviso-info">
          {fase.vuelveAnterior ? TEXTO_QUITADO_VUELVE_ANTERIOR : TEXTO_QUITADO}
        </p>
      )}
      {falloQuitar !== null && (
        <p className="aviso-error" role="alert">
          {falloQuitar}
        </p>
      )}
      {cargado !== null && (
        <div className="acciones">
          <button type="button" className="secundario" onClick={() => setViendo(true)}>
            {textoVer}
          </button>
          {/* Solo el subido acá: el que trae la orden o el proveedor ya respalda algo guardado. */}
          {tocado && propio !== null && quitar === "nada" && (
            <button type="button" className="secundario peligro" disabled={deshabilitada || ocupada} onClick={() => setQuitar("preguntando")}>
              {QUITAR_ARCHIVO}
            </button>
          )}
        </div>
      )}
      {tocado && propio !== null && quitar !== "nada" && (
        <div role="group" aria-label={QUITAR_ARCHIVO} className="confirmar-quitar">
          <p>{preguntaQuitar(propio.nombre)}</p>
          <div className="acciones">
            <button type="button" className="secundario peligro" disabled={quitar === "quitando" || subiendoOtro || deshabilitada} onClick={() => void quitarArchivo()}>
              {quitar === "quitando" ? "Quitando…" : CONFIRMAR_QUITAR}
            </button>
            {/* Cerrar la pregunta se lleva también el rechazo anterior de quitar (G-20). */}
            <button type="button" className="secundario" disabled={quitar === "quitando"} onClick={() => {
              setQuitar("nada");
              setFalloQuitar(null);
            }}>
              {NO_QUITAR}
            </button>
          </div>
        </div>
      )}
      {viendo && cargado !== null && (
        <VisorDocumento consorcio={consorcio} documento={cargado.documento} version={cargado.version} nombre={cargado.nombre}
          alCerrar={() => setViendo(false)} aEntrar={aEntrar} />
      )}
    </section>
  );
}

/** La marca de un campo que completó la lectura, con la forma de darlo por revisado. */
export function MarcaDeLectura({ alRevisar }: { alRevisar: () => void }) {
  return (
    <p className="marca-lectura">
      {MARCA_LECTURA}{" "}
      <button type="button" className="sutil" onClick={alRevisar}>
        Está bien
      </button>
    </p>
  );
}

/** Esa versión exacta del archivo, si quien mira puede abrirla, para «Ver la factura»: la que se
 * aprobó con la orden, nunca la más nueva del documento. Nula si no está disponible o si no la ve
 * (la lista de documentos solo trae lo que puede ver). */
export function useVersionVisible(consorcio: string, documento: string | null, version: string | null, aEntrar: () => void): Cargado | null {
  const [cargado, setCargado] = useState<Cargado | null>(null);
  useEffect(() => {
    setCargado(null);
    if (documento === null || version === null) return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/documentos", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, response }) => {
        exigirSesion(response);
        if (!vigente || data === undefined) return;
        const d = data.documentos.find((x) => x.id === documento);
        const esa = d?.versiones.find((v) => v.id === version && v.estado === "disponible");
        if (d !== undefined && esa !== undefined) setCargado({ documento: d.id, version: esa.id, nombre: d.titulo });
      })
      .catch((e: unknown) => {
        if (vigente && e instanceof SinSesion) aEntrar();
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, documento, version, aEntrar]);
  return cargado;
}
