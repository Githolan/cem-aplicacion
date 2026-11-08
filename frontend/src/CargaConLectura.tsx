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
// Sin archivo, el formulario se completa a mano como siempre.

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
  | { tipo: "no_subio" };

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
};

export function CargaConLectura({ consorcio, tipo, titulo, de, textoVer, deshabilitada = false, inicial = null, aEntrar, alCargar, alLeer }: Props) {
  const [fase, setFase] = useState<Fase>({ tipo: "nada" });
  const [texto, setTexto] = useState<string | null>(null);
  /** El subido acá; mientras no se subió nada, vale el inicial (que puede llegar después). */
  const [propio, setPropio] = useState<Cargado | null>(null);
  const [tocado, setTocado] = useState(false);
  const cargado = tocado ? propio : inicial;
  const [viendo, setViendo] = useState(false);
  /** La versión que se sube o se espera. */
  const enEspera = useRef<{ documento: string; version: string; nombre: string } | null>(null);
  /** El documento creado cuyo archivo todavía no entró: el reintento sube solo el archivo. */
  const sinArchivo = useRef<string | null>(null);
  const claveDocumento = useRef<string | null>(null);
  const claveArchivo = useRef<string | null>(null);
  const leidas = useRef(new Set<string>());
  const ultimas = useRef({ alCargar, alLeer });
  ultimas.current = { alCargar, alLeer };

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
    ponerCargado(null);
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
          setTexto(`El archivo no se subió. ${mensajeDeActo(creado.response.status, codigo)}`);
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

  function elegir(archivo: File) {
    // Otro archivo es otro pedido: si el anterior no entró, sirve su documento, no su clave.
    claveArchivo.current = null;
    void subir(archivo);
  }

  const ocupada = fase.tipo === "subiendo" || fase.tipo === "revisando" || fase.tipo === "leyendo";
  const progreso = fase.tipo === "subiendo" ? TEXTO_SUBIENDO : fase.tipo === "revisando" ? TEXTO_REVISANDO : fase.tipo === "leyendo" ? TEXTO_LEYENDO : null;
  const resultado =
    fase.tipo === "leida" ? TEXTO_LEIDA
    : fase.tipo === "no_leida" ? TEXTO_A_MANO
    : fase.tipo === "sin_lectura" ? TEXTO_SIN_LECTURA
    : null;

  return (
    <section className="carga-con-lectura" aria-label={titulo}>
      <SubirArchivo titulo={titulo} de={de} tipos={TIPOS_DE_DOCUMENTO} tope={TOPE_BYTES} reemplaza={cargado !== null}
        deshabilitada={deshabilitada || ocupada}
        nota="Los datos que se lean completan el formulario: hay que revisarlos antes de guardar." alElegir={elegir} />
      {progreso !== null && (
        <p role="status" className="aviso-info">
          {progreso}
        </p>
      )}
      {fase.tipo === "revisando" && antivirusTarda && <p className="aviso-info">{TEXTO_ANTIVIRUS_TARDA}</p>}
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
      {fase.tipo === "no_subio" && texto !== null && (
        <p className="aviso-error" role="alert">
          {texto}
        </p>
      )}
      {cargado !== null && (
        <div className="acciones">
          <button type="button" className="secundario" onClick={() => setViendo(true)}>
            {textoVer}
          </button>
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

/** La versión disponible de un documento que quien mira puede abrir, para «Ver la factura»; nula si
 * no hay o si no la ve (la lista de documentos solo trae lo que puede ver). */
export function useVersionVisible(consorcio: string, documento: string | null, aEntrar: () => void): Cargado | null {
  const [cargado, setCargado] = useState<Cargado | null>(null);
  useEffect(() => {
    setCargado(null);
    if (documento === null) return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/documentos", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, response }) => {
        exigirSesion(response);
        if (!vigente || data === undefined) return;
        const d = data.documentos.find((x) => x.id === documento);
        const disponible = d?.versiones.find((v) => v.estado === "disponible");
        if (d !== undefined && disponible !== undefined) setCargado({ documento: d.id, version: disponible.id, nombre: d.titulo });
      })
      .catch((e: unknown) => {
        if (vigente && e instanceof SinSesion) aEntrar();
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, documento, aEntrar]);
  return cargado;
}
