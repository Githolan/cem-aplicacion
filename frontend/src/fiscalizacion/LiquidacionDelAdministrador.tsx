import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { CabeceraInterna } from "../CabeceraInterna";
import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { hoyDelDispositivo, JPEG, PDF, PNG, TOPE_BYTES } from "../archivos";
import { encabezadoCsrf } from "../csrf";
import { ErrorDeCampo, type ErroresDeCampo, faltaCompletar, hayErrores, marcaDe, SIN_ERRORES, sinElDe } from "../ErrorDeCampo";
import { codigoDe, esPasajera, SIN_CONEXION } from "../fallas";
import { FichaDeRegistro, FilaDeRegistro, useFichas } from "../FichaDeRegistro";
import { RUTA_INICIO } from "../enlace";
import { fechaCorta } from "../pagos/textos";
import { REQUISITOS, usePuede } from "../permisos";
import { SubirArchivo } from "../SubirArchivo";
import { type FinDeEnvio, VentanaDeFormulario } from "../VentanaDeFormulario";
import { type Aviso, cambioLoVisto, ENCABEZADO_IDEMPOTENCIA, ESTADO, mensajeDeActo, mensajeDeError, renovarClave, sinLaFalla, sinPisarLoHecho } from "../egresos/comun";
import { ElegirConsorcio } from "../egresos/ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "../egresos/EstadoPantalla";
import { exigirSesion, SinSesion, useConsultaDelAntivirus, useConsorcio } from "../egresos/useConsorcio";
import { type Propuesta, RUTA_SOLICITUDES } from "./Solicitudes";

// Página /importar-liquidacion (H5-3, app 0115; diseño H5 §5): en Modo Consejo, quien tiene
// `liquidacion.importar` sube el PDF que manda la administración, o una foto (H5-5, app 0117), la
// aplicación lo lee (sin IA el formato Mis Expensas; con IA, dentro del tope del mes, lo demás, y
// una foto o un PDF sin texto como imágenes, de las que la IA transcribe cada renglón) y una persona confirma cada dato
// antes de que cuente (RN30). Cada dato muestra su valor, su confianza y el renglón del documento de
// donde salió; se confirma, se corrige o se descarta, y cuando no queda ninguno pendiente se confirma
// la liquidación entera. Nada se borra: lo descartado queda con su motivo.

type Resumen = components["schemas"]["ImportacionResumen"];
type Importacion = components["schemas"]["ImportacionLiquidacion"];
type Campo = components["schemas"]["CampoPreliminar"];
type Estado = Importacion["estado"];

export const RUTA_LIQUIDACION_ADMINISTRADOR = "/importar-liquidacion";
export const TITULO_LIQUIDACION_ADMINISTRADOR = "Liquidación del administrador";
export const CATEGORIA = "liquidaciones_del_administrador";
export const SUBIR = "Subir la liquidación";
export const SIN_IMPORTACIONES = "Todavía no se subió ninguna liquidación del administrador.";
export const IMPORTAR_DE_NUEVO = "Importar el archivo subido";
/** Cómo se leyó la importación, junto al estado. */
export const TEXTO_FORMATO: Record<string, string> = {
  mis_expensas: "Formato Mis Expensas",
  lectura_ia: "Leída con IA",
  lectura_ia_imagen: "Leída con IA de una imagen",
};
/** De una imagen, el fragmento es lo que transcribió la IA, no el texto del archivo. */
export const TRANSCRITO = "transcripción de la IA";
export const ENLACES = "Enlaces del documento";
export const TRAER = "Traer el archivo";
/** Solo se traen archivos de enlaces https (la descarga acotada del servidor, H5-5). */
export const SOLO_HTTPS = "Sin https: no se trae desde la aplicación.";
export const NO_SE_TRAJO = "No se trajo el archivo del enlace.";
const TEXTO_ORIGEN_ENLACE: Record<string, string> = { anotacion: "enlace", texto: "escrito", qr: "QR" };

/** El nombre del sitio del enlace, para leerlo sin la dirección entera. */
export function sitioDe(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}
export const EN_ANTIVIRUS = "El archivo está en la revisión del antivirus; cuando pase, se lee solo.";
export const LEYENDO = "Se está leyendo la liquidación.";
export const CONFIRMAR_DATO = "Confirmar";
export const CORREGIR_DATO = "Corregir";
export const DESCARTAR_DATO = "Descartar";
export const CONFIRMAR_LIQUIDACION = "Confirmar la liquidación";
export const DESCARTAR_LIQUIDACION = "Descartar la liquidación";
export const VOLVER_A_LA_LISTA = "Volver a la lista";
export const FALTA_ARCHIVO = "Falta elegir el archivo.";
export const FALTA_MOTIVO = "Falta escribir el motivo.";
export const SIN_CAMBIOS = "El valor corregido es igual al leído: para dejarlo así, se confirma.";
export const NUMERO_MAL = "Va un número entero, por ejemplo 2.";
export const IMPORTE_MAL = "Un importe va con dos decimales, por ejemplo 1.234,50.";
export const LARGO_MOTIVO = 500;
const ESPERA_LECTURA_MS = 3000;

export const TEXTO_ESTADO: Record<Estado, string> = {
  leyendo: "Leyendo",
  para_confirmar: "Para confirmar",
  no_reconocida: "No se pudo leer",
  confirmada: "Confirmada",
  descartada: "Descartada",
  falla_de_lectura: "Falló la lectura",
};
const TEXTO_TIPO: Record<Campo["tipo"], string> = {
  total: "Total",
  rubro: "Rubro",
  gasto: "Gasto",
  unidad: "Unidad",
  importe_unidad: "Lo que paga la unidad",
  saldo_unidad: "Saldo de la unidad",
};
const TEXTO_ESTADO_CAMPO: Record<Campo["estado"], string> = {
  pendiente: "Pendiente",
  confirmado: "Confirmado",
  corregido: "Corregido",
  descartado: "Descartado",
};
const TEXTO_CLAVE: Record<string, string> = {
  total: "Total",
  nombre: "Nombre",
  concepto: "Concepto",
  uf: "Unidad funcional",
  depto: "Departamento",
  saldo_anterior: "Saldo anterior",
  pagos: "Pagos",
  deuda: "Deuda",
  interes: "Interés",
  disponible: "Disponible",
  porcentaje: "Porcentaje",
  monto: "Monto",
  otros_gastos: "Otros gastos",
  redondeo: "Redondeo",
  importes: "Importes",
  grupos: "Grupos",
  rubro: "Número de rubro",
  numero: "Número",
  vencimientos: "Vencimientos",
};
const IMPORTE_LEIDO = /^-?\d{1,16}\.\d{2}$/;

/** Un importe leído («1234.50») como se lee en la Argentina («$ 1.234,50»). */
export function enPesos(texto: string): string {
  const negativo = texto.startsWith("-");
  const [enteros = "0", decimales = "00"] = texto.replace("-", "").split(".");
  const conMiles = enteros.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return `${negativo ? "-" : ""}$ ${conMiles},${decimales}`;
}

/** Lo escrito por la persona («1.234,50», «1234,5», «-10») a texto decimal; null si no es un importe. */
export function aImporte(texto: string): string | null {
  const limpio = texto.trim().replace(/\s/g, "");
  const negativo = limpio.startsWith("-");
  const sinSigno = negativo ? limpio.slice(1) : limpio;
  const partes = /^(\d{1,3}(?:\.\d{3})+)(?:,(\d{1,2}))?$/.exec(sinSigno) ?? /^(\d+)(?:[.,](\d{1,2}))?$/.exec(sinSigno);
  if (partes === null) return null;
  const [, enteros = "", decimales = ""] = partes;
  // Sin pasar por `Number`: un entero de más de 15 cifras perdería precisión.
  const sinCeros = enteros.replace(/\./g, "").replace(/^0+(?=\d)/, "");
  return `${negativo ? "-" : ""}${sinCeros}.${decimales.padEnd(2, "0")}`;
}

const texto = (v: unknown) => (typeof v === "string" ? (IMPORTE_LEIDO.test(v) ? enPesos(v) : v) : v === null || v === undefined ? "—" : String(v));

/** Una línea con lo esencial del dato: lo que la persona compara con el documento. */
export function resumenDe(c: Campo): string {
  const v = (c.valor_corregido ?? c.valor) as Record<string, unknown>;
  const unidad = [v.uf ? `UF ${String(v.uf)}` : "", v.depto ? String(v.depto) : ""].filter(Boolean).join(" ");
  switch (c.tipo) {
    case "rubro":
      return `${String(v.numero ?? "")}. ${String(v.nombre ?? "")}: ${texto(v.total)}`;
    case "gasto":
      return `${String(v.concepto ?? "")}: ${texto(v.total)}`;
    case "unidad":
      return [unidad, v.nombre ? String(v.nombre) : ""].filter(Boolean).join(" · ");
    case "importe_unidad":
      return `${unidad}: ${texto(v.total)}`;
    case "saldo_unidad":
      return `${unidad}: saldo anterior ${texto(v.saldo_anterior)}`;
    case "total":
      return `Total: ${texto(v.total)}`;
  }
}

// Como `forma_igual` del backend: un importe se reconoce por su clave (o lo que va dentro de
// `importes` y `grupos`), aunque lo leído esté vacío, o porque lo leído tiene forma de importe.
const CLAVES_DE_IMPORTE = new Set(["total", "saldo_anterior", "pagos", "deuda", "interes", "disponible", "otros_gastos", "redondeo", "porcentaje", "monto"]);
const CLAVES_DE_GRUPOS = new Set(["importes", "grupos"]);

/** Cada valor del dato que se corrige, con su camino, su etiqueta, lo leído como se escribe y su forma. */
type Forma = "importe" | "texto" | "numero" | "lista";
type Hoja = { camino: string[]; etiqueta: string; leido: string | null; forma: Forma };
export function hojasDe(valor: Record<string, unknown>, camino: string[] = []): Hoja[] {
  return Object.entries(valor).flatMap(([clave, v]): Hoja[] => {
    const aca = [...camino, clave];
    if (v !== null && typeof v === "object" && !Array.isArray(v)) return hojasDe(v as Record<string, unknown>, aca);
    const etiqueta = aca.map((k) => TEXTO_CLAVE[k] ?? k).join(" · ");
    if (Array.isArray(v)) return [{ camino: aca, etiqueta, leido: v.map(String).join(", "), forma: "lista" }];
    if (typeof v === "number") return [{ camino: aca, etiqueta, leido: String(v), forma: "numero" }];
    if (v !== null && typeof v !== "string") return [];
    const importe = CLAVES_DE_IMPORTE.has(clave) || CLAVES_DE_GRUPOS.has(camino.at(-1) ?? "") || (v !== null && IMPORTE_LEIDO.test(v));
    return [{ camino: aca, etiqueta, leido: v, forma: importe ? "importe" : "texto" }];
  });
}

/** Lo escrito en una hoja, en la forma del dato; `undefined` si no la tiene. */
function enSuForma(h: Hoja, crudo: string): string | number | string[] | null | undefined {
  switch (h.forma) {
    case "importe":
      return crudo === "" ? null : (aImporte(crudo) ?? undefined);
    case "numero":
      return /^-?\d{1,9}$/.test(crudo) ? Number(crudo) : undefined;
    case "lista":
      return crudo.split(",").map((x) => x.trim()).filter((x) => x !== "");
    case "texto":
      return crudo === "" ? null : crudo;
  }
}

/** Cada valor del dato, con su etiqueta (también los números): lo que se confirma entero. */
export function valoresDe(valor: Record<string, unknown>, camino: string[] = []): { clave: string; etiqueta: string; valor: string }[] {
  return Object.entries(valor).flatMap(([clave, v]) => {
    const aca = [...camino, clave];
    if (v !== null && typeof v === "object" && !Array.isArray(v)) return valoresDe(v as Record<string, unknown>, aca);
    return [{ clave: aca.join("."), etiqueta: aca.map((k) => TEXTO_CLAVE[k] ?? k).join(" · "), valor: texto(Array.isArray(v) ? v.join(", ") : v) }];
  });
}

/** Lo leído de un dato y, si se corrigió, cada valor que cambió con su corrección al lado. */
function datosDelValor(c: Campo): { etiqueta: string; valor: string }[] {
  const corregido = c.valor_corregido === null ? null : new Map(valoresDe(c.valor_corregido as Record<string, unknown>).map((v) => [v.clave, v.valor]));
  return valoresDe(c.valor as Record<string, unknown>).map((v) => {
    const nuevo = corregido?.get(v.clave);
    return { etiqueta: v.etiqueta, valor: nuevo === undefined || nuevo === v.valor ? v.valor : `${v.valor} → corregido: ${nuevo}` };
  });
}

function conHoja(valor: Record<string, unknown>, camino: string[], nuevo: unknown): Record<string, unknown> {
  const [primera, ...resto] = camino;
  if (primera === undefined) return valor;
  return { ...valor, [primera]: resto.length === 0 ? nuevo : conHoja(valor[primera] as Record<string, unknown>, resto, nuevo) };
}

type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; elementos: Resumen[] };
type Detalle = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; importacion: Importacion };
type Subida = { consorcio: string; documento: string; version: string };
type Ventana = { tipo: "subir" } | { tipo: "corregir"; campo: Campo } | { tipo: "descartar" };

export function LiquidacionDelAdministrador() {
  const elegido = useConsorcio(REQUISITOS.importarLiquidacion);
  return <DelEdificio key={elegido.consorcio} {...elegido} />;
}

function DelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar }: ReturnType<typeof useConsorcio>) {
  const id = useId();
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [elegida, setElegida] = useState<string | null>(null);
  const [detalle, setDetalle] = useState<Detalle>({ tipo: "cargando" });
  const [vueltaDetalle, setVueltaDetalle] = useState(0);
  const [subida, setSubida] = useState<Subida | null>(null);
  const [ventana, setVentana] = useState<Ventana | null>(null);
  const [archivo, setArchivo] = useState<File | null>(null);
  const [motivo, setMotivo] = useState("");
  const [escrito, setEscrito] = useState<Record<string, string>>({});
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  const [enviando, setEnviando] = useState(false);
  const [fallaEnFicha, setFallaEnFicha] = useState<string | null>(null);
  const fichas = useFichas<string>();
  const clave = useRef<string | null>(null);
  const claveImportar = useRef<string | null>(null);
  // El archivo que el antivirus aprobó y todavía no se pudo importar: se reintenta con la misma clave.
  const [porImportar, setPorImportar] = useState<Subida | null>(null);

  const path = { consorcio_id: consorcio };

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/liquidaciones-importadas", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        const codigo = codigoDe(error);
        if (data !== undefined) setLectura({ tipo: "ok", elementos: data });
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

  useEffect(() => {
    if (consorcio === "" || elegida === null) return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/liquidaciones-importadas/{importacion_id}", {
        params: { path: { consorcio_id: consorcio, importacion_id: elegida } },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        if (data !== undefined) setDetalle({ tipo: "ok", importacion: data });
        else {
          setDetalle({ tipo: "sin_servicio" });
          setAviso(sinPisarLoHecho(mensajeDeError(response.status, codigoDe(error))));
        }
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else {
          setDetalle({ tipo: "sin_servicio" });
          setAviso(sinPisarLoHecho(SIN_CONEXION));
        }
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, elegida, vueltaDetalle, aEntrar]);

  // Mientras se lee, la importación elegida se vuelve a pedir sola.
  const leyendo = detalle.tipo === "ok" && detalle.importacion.estado === "leyendo";
  useEffect(() => {
    if (!leyendo) return;
    const reloj = setInterval(() => setVueltaDetalle((v) => v + 1), ESPERA_LECTURA_MS);
    return () => clearInterval(reloj);
  }, [leyendo]);

  /** El archivo subido, en el antivirus: cuando pasa, se pide importarlo; si lo rechaza, se dice. */
  const mirarSubida = useCallback(() => {
    if (subida === null) return;
    const s = subida;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/documentos/{documento_id}/versiones/{version_id}/estado", {
        params: { path: { consorcio_id: s.consorcio, documento_id: s.documento, version_id: s.version } },
      })
      .then(async ({ data, error, response }) => {
        exigirSesion(response);
        if (data === undefined) {
          if (!esPasajera(response.status)) {
            setSubida(null);
            setAviso({ texto: mensajeDeError(response.status, codigoDe(error)), ok: false });
          }
          return;
        }
        if (data.estado === "en_cuarentena") return;
        setSubida(null);
        if (data.estado === "rechazado") {
          setAviso({ texto: `El antivirus rechazó el archivo${data.motivo_rechazo ? `: ${data.motivo_rechazo}` : ""}.`, ok: false });
          return;
        }
        setPorImportar(s);
        await importar(s);
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion) aEntrar();
      });
    // `importar` solo usa refs y setters estables.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subida, aEntrar]);
  const antivirusTarda = useConsultaDelAntivirus(subida !== null, mirarSubida);

  /** Pide importar el archivo aprobado. Si el pedido no llega o el servidor falla de paso, el archivo
   * queda para reintentar con la misma clave; un rechazo definitivo se dice y lo descarta. */
  async function importar(s: Subida) {
    claveImportar.current ??= crypto.randomUUID();
    setEnviando(true);
    try {
      const { data, error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/liquidaciones-importadas", {
        headers: encabezadoCsrf(),
        params: { path: { consorcio_id: s.consorcio }, header: { [ENCABEZADO_IDEMPOTENCIA]: claveImportar.current } },
        body: { documento_id: s.documento, version_documento_id: s.version },
      });
      exigirSesion(response);
      if (data === undefined) {
        const codigo = codigoDe(error);
        if (renovarClave(response.status, codigo)) claveImportar.current = null;
        if (!esPasajera(response.status) && codigo !== "idempotencia_en_curso") {
          claveImportar.current = null;
          setPorImportar(null);
        }
        setAviso({ texto: mensajeDeActo(response.status, error), ok: false });
        return;
      }
      claveImportar.current = null;
      setPorImportar(null);
      setAviso({ texto: LEYENDO, ok: true });
      setVuelta((v) => v + 1);
      abrir(data.id);
    } catch (e: unknown) {
      if (e instanceof SinSesion) {
        aEntrar();
        return;
      }
      setAviso({ texto: SIN_CONEXION, ok: false });
    } finally {
      setEnviando(false);
    }
  }

  function abrir(importacion: string) {
    setDetalle({ tipo: "cargando" });
    setElegida(importacion);
    setVentana(null);
    fichas.cerrar();
  }

  function volverALaLista() {
    setElegida(null);
    setVuelta((v) => v + 1);
  }

  function abrirVentana(v: Ventana) {
    clave.current = null;
    setErrores(SIN_ERRORES);
    setArchivo(null);
    setMotivo("");
    setEscrito(v.tipo === "corregir" ? Object.fromEntries(hojasDe((v.campo.valor_corregido ?? v.campo.valor) as Record<string, unknown>).map((h) => [h.camino.join("."), h.leido === null ? "" : h.forma === "importe" ? enPesos(h.leido).replace("$ ", "") : h.leido])) : {});
    setVentana(v);
  }

  /** Sube el PDF o la foto: crea el documento de la categoría y sube el archivo; el antivirus y la lectura siguen solos. */
  async function subir(): Promise<FinDeEnvio> {
    if (archivo === null) {
      setErrores({ archivo: FALTA_ARCHIVO });
      return "seguir";
    }
    clave.current ??= crypto.randomUUID();
    setEnviando(true);
    try {
      const creado = await api.POST("/api/v1/consorcios/{consorcio_id}/documentos", {
        headers: encabezadoCsrf(),
        params: { path, header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current } },
        body: { categoria: CATEGORIA, titulo: TITULO_LIQUIDACION_ADMINISTRADOR, fecha: hoyDelDispositivo() },
      });
      exigirSesion(creado.response);
      if (creado.data === undefined) {
        if (renovarClave(creado.response.status, codigoDe(creado.error))) clave.current = null;
        return { rechazo: mensajeDeActo(creado.response.status, creado.error) };
      }
      clave.current = null;
      const documento = creado.data.id;
      const version = await api.POST("/api/v1/consorcios/{consorcio_id}/documentos/{documento_id}/versiones", {
        headers: { ...encabezadoCsrf(), "Content-Type": archivo.type },
        params: { path: { consorcio_id: consorcio, documento_id: documento }, header: { [ENCABEZADO_IDEMPOTENCIA]: crypto.randomUUID() } },
        // El generador tipa `format: binary` como texto; el cuerpo es el archivo tal cual.
        body: archivo as unknown as string,
        bodySerializer: (cuerpo) => cuerpo,
      });
      exigirSesion(version.response);
      if (version.data === undefined) return { rechazo: `El archivo no se subió. ${mensajeDeActo(version.response.status, version.error)}` };
      setSubida({ consorcio, documento, version: version.data.id });
      setAviso({ texto: EN_ANTIVIRUS, ok: true });
      setVentana(null);
      return "cerrar";
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

  /** Un acto sobre la importación elegida; la respuesta es la importación como queda. */
  async function actuar(
    pedido: (claveActo: string) => Promise<{ data?: Importacion; error?: unknown; response: Response }>,
    hecho: string,
  ): Promise<FinDeEnvio> {
    clave.current ??= crypto.randomUUID();
    setEnviando(true);
    try {
      const { data, error, response } = await pedido(clave.current);
      exigirSesion(response);
      if (data === undefined) {
        const codigo = codigoDe(error);
        if (cambioLoVisto(response.status, codigo)) {
          setAviso({ texto: mensajeDeActo(response.status, error), ok: false });
          setVentana(null);
          fichas.cerrar();
          setVueltaDetalle((v) => v + 1);
          return "cerrar";
        }
        if (renovarClave(response.status, codigo)) clave.current = null;
        return { rechazo: mensajeDeActo(response.status, error) };
      }
      clave.current = null;
      setDetalle({ tipo: "ok", importacion: data });
      setAviso({ texto: hecho, ok: true });
      setVentana(null);
      fichas.cerrar();
      return "cerrar";
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

  /** Trae el archivo de un enlace: entra como documento en el antivirus y, cuando pasa, se importa como uno subido. */
  async function traerEnlace(orden: number) {
    // Como la carga: mientras hay un archivo aprobado esperando su importación, no se trae otro,
    // que la pisaría (Codex).
    if (elegida === null || enviando || subida !== null || porImportar !== null) return;
    clave.current ??= crypto.randomUUID();
    setEnviando(true);
    try {
      const { data, error, response } = await api.POST(
        "/api/v1/consorcios/{consorcio_id}/liquidaciones-importadas/{importacion_id}/enlaces/{numero}/descarga",
        {
          headers: encabezadoCsrf(),
          params: { path: { consorcio_id: consorcio, importacion_id: elegida, numero: orden }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current } },
        },
      );
      exigirSesion(response);
      if (data === undefined) {
        const codigo = codigoDe(error);
        if (renovarClave(response.status, codigo)) clave.current = null;
        // El servidor dice por qué no se trajo (el destino, el tamaño o el tipo): se muestra.
        const motivo = (error as { mensaje?: unknown } | undefined)?.mensaje;
        const texto = codigo === "enlace_no_traido" && typeof motivo === "string" ? `${NO_SE_TRAJO} ${motivo}` : mensajeDeActo(response.status, error);
        setAviso({ texto, ok: false });
        return;
      }
      clave.current = null;
      setSubida({ consorcio, documento: data.documento_id, version: data.version_documento_id });
      setAviso({ texto: EN_ANTIVIRUS, ok: true });
    } catch (e: unknown) {
      if (e instanceof SinSesion) aEntrar();
      else setAviso({ texto: SIN_CONEXION, ok: false });
    } finally {
      setEnviando(false);
    }
  }

  const decidir = (c: Campo, decision: "confirmado" | "corregido" | "descartado", corregido?: Record<string, unknown>) =>
    actuar(
      (k) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/liquidaciones-importadas/{importacion_id}/campos/{campo_id}/decision", {
          headers: encabezadoCsrf(),
          params: { path: { consorcio_id: consorcio, importacion_id: elegida ?? "", campo_id: c.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: k } },
          body: { decision, valor_corregido: corregido ?? null },
        }),
      `${TEXTO_TIPO[c.tipo]}: ${TEXTO_ESTADO_CAMPO[decision].toLowerCase()}.`,
    );

  async function enviarCorreccion(c: Campo): Promise<FinDeEnvio> {
    const leido = (c.valor_corregido ?? c.valor) as Record<string, unknown>;
    let nuevo: Record<string, unknown> = c.valor as Record<string, unknown>;
    const marcados: Record<string, string> = {};
    for (const h of hojasDe(c.valor as Record<string, unknown>)) {
      const llave = h.camino.join(".");
      const valor = enSuForma(h, (escrito[llave] ?? "").trim());
      if (valor === undefined) marcados[llave] = h.forma === "importe" ? IMPORTE_MAL : NUMERO_MAL;
      else nuevo = conHoja(nuevo, h.camino, valor);
    }
    if (Object.keys(marcados).length > 0) {
      setErrores(marcados);
      return "seguir";
    }
    if (JSON.stringify(nuevo) === JSON.stringify(c.valor) || JSON.stringify(nuevo) === JSON.stringify(leido)) return { rechazo: SIN_CAMBIOS };
    return decidir(c, "corregido", nuevo);
  }

  async function descartar(): Promise<FinDeEnvio> {
    const faltan = faltaCompletar({ motivo: [motivo, FALTA_MOTIVO] });
    if (hayErrores(faltan)) {
      setErrores(faltan);
      return "seguir";
    }
    return actuar(
      (k) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/liquidaciones-importadas/{importacion_id}/descarte", {
          headers: encabezadoCsrf(),
          params: { path: { consorcio_id: consorcio, importacion_id: elegida ?? "" }, header: { [ENCABEZADO_IDEMPOTENCIA]: k } },
          body: { motivo: motivo.trim() },
        }),
      "Se descartó la liquidación importada.",
    );
  }

  /** Un acto de un botón directo (sin ventana que muestre su rechazo): el rechazo queda a la vista. */
  const directo = (fin: Promise<FinDeEnvio>, mostrar: (texto: string) => void) =>
    void fin.then((r) => {
      if (typeof r === "object") mostrar(r.rechazo);
    });

  const decidirDirecto = (c: Campo, decision: "confirmado" | "descartado") => {
    setFallaEnFicha(null);
    directo(decidir(c, decision), setFallaEnFicha);
  };

  const confirmar = () =>
    directo(actuar(
      (k) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/liquidaciones-importadas/{importacion_id}/confirmacion", {
          headers: encabezadoCsrf(),
          params: { path: { consorcio_id: consorcio, importacion_id: elegida ?? "" }, header: { [ENCABEZADO_IDEMPOTENCIA]: k } },
        }),
      "Se confirmó la liquidación: sus datos confirmados y corregidos ya cuentan.",
    ), (texto) => setAviso({ texto, ok: false }));

  const elementos = lectura.tipo === "ok" ? lectura.elementos : [];
  const importacion = detalle.tipo === "ok" && elegida !== null ? detalle.importacion : null;
  const enFicha = importacion?.campos.find((c) => c.id === fichas.abierta);
  const pendientes = importacion?.campos.filter((c) => c.estado === "pendiente").length ?? 0;
  const seConfirma = importacion?.estado === "para_confirmar";

  return (
    <>
      <CabeceraInterna titulo={TITULO_LIQUIDACION_ADMINISTRADOR} volver={RUTA_INICIO} destino="Inicio" />
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={enviando} elegir={setConsorcio} />
      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">Esta cuenta no tiene edificios en Consorcio en Mano.</p>}
      <ConsorcioSinServicio pantalla={pantalla} reintentar={reintentar} />
      <AvisoPantalla aviso={aviso} />
      {subida !== null && antivirusTarda && <p role="status">El antivirus está tardando más de lo habitual; se sigue esperando.</p>}
      {porImportar !== null && !enviando && (
        <p>
          <button type="button" className="secundario" onClick={() => void importar(porImportar)}>
            {IMPORTAR_DE_NUEVO}
          </button>
        </p>
      )}

      {pantalla.tipo === "lista" && elegida === null && (
        <>
          {/* Con un archivo en el antivirus o sin importar todavía, no se sube otro: se perdería el primero. */}
          {subida === null && porImportar === null && (
            <p>
              <button type="button" onClick={() => abrirVentana({ tipo: "subir" })}>
                {SUBIR}
              </button>
            </p>
          )}
          {lectura.tipo === "cargando" && <p role="status">Cargando las liquidaciones…</p>}
          {lectura.tipo === "sin_servicio" && (
            <FallaDeLectura
              alReintentar={() => {
                setAviso(sinLaFalla);
                setLectura({ tipo: "cargando" });
                setVuelta((v) => v + 1);
              }}
              avisar={setAviso}
            />
          )}
          {lectura.tipo === "ok" &&
            (elementos.length === 0 ? (
              <p className="tarjeta">{SIN_IMPORTACIONES}</p>
            ) : (
              <ul className="filas-registro" aria-label="Liquidaciones importadas">
                {elementos.map((r) => (
                  <li key={r.id}>
                    <FilaDeRegistro
                      titulo={r.periodo ? `Período ${r.periodo.slice(0, 7)}` : `Subida el ${fechaCorta(r.creada_en)}`}
                      resumen={r.estado === "para_confirmar" ? `${r.pendientes} de ${r.campos} datos sin decidir` : `Subida el ${fechaCorta(r.creada_en)}`}
                      estado={TEXTO_ESTADO[r.estado]}
                      alAbrir={() => abrir(r.id)}
                      deshabilitada={enviando}
                    />
                  </li>
                ))}
              </ul>
            ))}
        </>
      )}

      {pantalla.tipo === "lista" && elegida !== null && (
        <section aria-labelledby={`${id}-titulo`}>
          <p>
            <button type="button" className="secundario" onClick={volverALaLista}>
              {VOLVER_A_LA_LISTA}
            </button>
          </p>
          {detalle.tipo === "cargando" && <p role="status">Cargando la liquidación…</p>}
          {detalle.tipo === "sin_servicio" && (
            <FallaDeLectura
              alReintentar={() => {
                setAviso(sinLaFalla);
                setDetalle({ tipo: "cargando" });
                setVueltaDetalle((v) => v + 1);
              }}
              avisar={setAviso}
            />
          )}
          {importacion !== null && (
            <>
              <h2 id={`${id}-titulo`}>{importacion.periodo ? `Período ${importacion.periodo.slice(0, 7)}` : TITULO_LIQUIDACION_ADMINISTRADOR}</h2>
              <p>
                {TEXTO_ESTADO[importacion.estado]}
                {importacion.formato ? ` · ${TEXTO_FORMATO[importacion.formato]}` : ""}
              </p>
              {importacion.estado === "leyendo" && <p role="status">{LEYENDO}</p>}
              {importacion.motivo && <p className="tarjeta">{importacion.motivo}</p>}
              {importacion.motivo_descarte && <p className="tarjeta">Motivo del descarte: {importacion.motivo_descarte}</p>}
              {importacion.diferencias.length > 0 && (
                <>
                  <h3>Lo que no cierra</h3>
                  <ul>
                    {importacion.diferencias.map((d, i) => {
                      const { esperado, informado } = d as Record<string, unknown>;
                      return (
                        <li key={`${d.regla}-${i}`}>
                          {d.detalle} (página {d.pagina})
                          {typeof esperado === "string" && typeof informado === "string" ? `: suma ${enPesos(esperado)}, dice ${enPesos(informado)}` : ""}
                        </li>
                      );
                    })}
                  </ul>
                </>
              )}
              {importacion.enlaces.length > 0 && (
                <>
                  <h3>{ENLACES}</h3>
                  <ul aria-label={ENLACES}>
                    {importacion.enlaces.map((e) => (
                      <li key={e.orden}>
                        {`${sitioDe(e.url)} (${TEXTO_ORIGEN_ENLACE[e.origen]}, página ${e.pagina})`}{" "}
                        {!e.url.toLowerCase().startsWith("https://") ? (
                          SOLO_HTTPS
                        ) : (
                          subida === null &&
                          porImportar === null && (
                            <button type="button" className="secundario" aria-label={`${TRAER} de ${sitioDe(e.url)}`} onClick={() => void traerEnlace(e.orden)}>
                              {TRAER}
                            </button>
                          )
                        )}
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {seConfirma && (
                <p className="ficha-registro__acciones">
                  {pendientes === 0 && (
                    <button type="button" onClick={confirmar}>
                      {CONFIRMAR_LIQUIDACION}
                    </button>
                  )}
                  <button type="button" className="secundario peligro" onClick={() => abrirVentana({ tipo: "descartar" })}>
                    {DESCARTAR_LIQUIDACION}
                  </button>
                </p>
              )}
              {importacion.estado === "no_reconocida" && (
                <p className="ficha-registro__acciones">
                  <button type="button" className="secundario peligro" onClick={() => abrirVentana({ tipo: "descartar" })}>
                    {DESCARTAR_LIQUIDACION}
                  </button>
                </p>
              )}
              {seConfirma && pendientes > 0 && <p>{`Quedan ${pendientes} datos sin decidir.`}</p>}
              {importacion.estado === "confirmada" && <RecalculoDeLaLiquidacion key={importacion.id} consorcio={consorcio} importacion={importacion.id} aEntrar={aEntrar} />}
              {importacion.campos.length > 0 && (
                <ul className="filas-registro" aria-label="Datos leídos">
                  {importacion.campos.map((c) => (
                    <li key={c.id}>
                      <FilaDeRegistro
                        titulo={`${TEXTO_TIPO[c.tipo]} · ${resumenDe(c)}`}
                        resumen={`Página ${c.pagina}${c.origen === "ia" ? " · Leído con IA" : ""}`}
                        estado={TEXTO_ESTADO_CAMPO[c.estado]}
                        alAbrir={() => {
                          setAviso(null);
                          setFallaEnFicha(null);
                          setVentana(null);
                          fichas.abrir(c.id);
                        }}
                        refFila={fichas.refFila(c.id)}
                        abierta={fichas.abierta === c.id}
                        deshabilitada={enviando}
                      />
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </section>
      )}

      {enFicha !== undefined && importacion !== null && (
        <FichaDeRegistro
          titulo={`${TEXTO_TIPO[enFicha.tipo]}`}
          datos={[
            ...datosDelValor(enFicha),
            {
              etiqueta: "En el documento",
              valor: `Página ${enFicha.pagina}${enFicha.fragmento ? `${importacion.formato === "lectura_ia_imagen" ? ` (${TRANSCRITO})` : ""}: «${enFicha.fragmento}»` : ""}`,
            },
            { etiqueta: "Confianza", valor: `${Math.round(Number(enFicha.confianza) * 100)} %${enFicha.origen === "ia" ? " (leído con IA)" : ""}` },
            { etiqueta: "Estado", valor: TEXTO_ESTADO_CAMPO[enFicha.estado] },
          ]}
          alCerrar={() => fichas.cerrar()}
          ocupada={enviando}
          ventana={ventana?.tipo === "corregir" && ventanaCorregir(ventana.campo)}
        >
          {fallaEnFicha !== null && (
            <p className="aviso-error" role="alert">
              {fallaEnFicha}
            </p>
          )}
          {seConfirma && (
            <p className="ficha-registro__acciones">
              {enFicha.estado !== "confirmado" && (
                <button type="button" onClick={() => decidirDirecto(enFicha, "confirmado")}>
                  {CONFIRMAR_DATO}
                </button>
              )}
              <button type="button" className="secundario" onClick={() => abrirVentana({ tipo: "corregir", campo: enFicha })}>
                {CORREGIR_DATO}
              </button>
              {enFicha.estado !== "descartado" && (
                <button type="button" className="secundario peligro" onClick={() => decidirDirecto(enFicha, "descartado")}>
                  {DESCARTAR_DATO}
                </button>
              )}
            </p>
          )}
        </FichaDeRegistro>
      )}

      {ventana?.tipo === "subir" && (
        <VentanaDeFormulario titulo={SUBIR} textoEnviar="Subir" alEnviar={subir} alCerrar={() => setVentana(null)}>
          <SubirArchivo
            de="de la liquidación"
            tipos={[PDF, JPEG, PNG]}
            tope={TOPE_BYTES}
            elegido={archivo?.name}
            conArchivo={archivo !== null}
            alElegir={(f) => {
              clave.current = null;
              setErrores((actuales) => sinElDe(actuales, "archivo"));
              setArchivo(f);
            }}
          />
          <ErrorDeCampo idCampo={`${id}-archivo`} mensaje={errores.archivo} />
        </VentanaDeFormulario>
      )}

      {ventana?.tipo === "descartar" && importacion !== null && (
        <VentanaDeFormulario titulo={DESCARTAR_LIQUIDACION} textoEnviar="Confirmar el descarte" peligro alEnviar={descartar} alCerrar={() => setVentana(null)}>
          <label htmlFor={`${id}-motivo`}>Motivo</label>
          <textarea
            id={`${id}-motivo`}
            data-foco
            value={motivo}
            maxLength={LARGO_MOTIVO}
            {...marcaDe(`${id}-motivo`, errores.motivo)}
            onChange={(ev) => {
              clave.current = null;
              setErrores((actuales) => sinElDe(actuales, "motivo"));
              setMotivo(ev.target.value);
            }}
          />
          <ErrorDeCampo idCampo={`${id}-motivo`} mensaje={errores.motivo} />
        </VentanaDeFormulario>
      )}
    </>
  );

  /** La corrección: cada texto o importe del dato, con lo leído ya escrito. */
  function ventanaCorregir(c: Campo) {
    return (
      <VentanaDeFormulario titulo={`Corregir: ${TEXTO_TIPO[c.tipo]}`} textoEnviar="Guardar la corrección" alEnviar={() => enviarCorreccion(c)} alCerrar={() => setVentana(null)}>
        {hojasDe(c.valor as Record<string, unknown>).map((h, i) => {
          const llave = h.camino.join(".");
          const idCampo = `${id}-corregir-${i}`;
          return (
            <div key={llave}>
              <label htmlFor={idCampo}>{h.forma === "lista" ? `${h.etiqueta} (separados por coma)` : h.etiqueta}</label>
              <input
                id={idCampo}
                {...(i === 0 ? { "data-foco": true } : {})}
                inputMode={h.forma === "importe" ? "decimal" : h.forma === "numero" ? "numeric" : undefined}
                value={escrito[llave] ?? ""}
                {...marcaDe(idCampo, errores[llave])}
                onChange={(ev) => {
                  clave.current = null;
                  setErrores((actuales) => sinElDe(actuales, llave));
                  setEscrito((actual) => ({ ...actual, [llave]: ev.target.value }));
                }}
              />
              <ErrorDeCampo idCampo={idCampo} mensaje={errores[llave]} />
            </div>
          );
        })}
      </VentanaDeFormulario>
    );
  }
}

// --- Recálculo con los coeficientes del edificio (H5-4, app 0116; diseño H5 §5) ---------------
// En una importación confirmada: el gasto de cada grupo de la liquidación repartido con los
// coeficientes del edificio vigentes en el período, frente a lo que informó la administración. Dice
// la diferencia y su base; no la califica (RN33). Una diferencia se convierte, si quien mira tiene
// `solicitud_formal.preparar`, en el borrador de una solicitud formal ya redactado (RF49).

type Recalculo = components["schemas"]["RecalculoLiquidacion"];
type GrupoRecalculado = components["schemas"]["GrupoRecalculado"];
type UnidadRecalculada = components["schemas"]["UnidadRecalculada"];

export const RECALCULO = "Recálculo con los coeficientes del edificio";
export const ELEGIR_COEFICIENTES = "Elegir los coeficientes";
export const CAMBIAR_COEFICIENTES = "Cambiar los coeficientes";
export const PREPARAR_SOLICITUD = "Preparar una solicitud formal";
export const SIN_GRUPOS = "Esta liquidación no informa el reparto por grupo: no hay nada que recalcular.";
export const FALTA_ESQUEMA = "Falta elegir los coeficientes.";
const LARGO_SOLICITUD = 2000;

const nombreDeEsquema = (grupo: string) => `«${grupo.replaceAll("_", " ")}»`;
/** Un día como «1 de enero de 2024»; al mediodía para que la zona horaria no lo corra. */
const fechaLarga = (dia: string) => new Intl.DateTimeFormat("es-AR", { day: "numeric", month: "long", year: "numeric" }).format(new Date(`${dia}T12:00:00`));
const mesDe = (periodo: string) => new Intl.DateTimeFormat("es-AR", { month: "long", year: "numeric" }).format(new Date(`${periodo}T12:00:00`));
const porcentajeDe = (texto: string | null | undefined) =>
  texto === null || texto === undefined ? "—" : `${Number(texto).toLocaleString("es-AR", { maximumFractionDigits: 6 })} %`;
const pesos = (texto: string | null | undefined) => (texto === null || texto === undefined ? "—" : enPesos(texto));

function situacionDelGrupo(g: GrupoRecalculado, periodo: string): string {
  switch (g.estado) {
    case "recalculado": {
      const base = `Repartido con los coeficientes ${nombreDeEsquema(g.grupo ?? "")} (versión ${g.esquema_version ?? "—"}, vigentes desde el ${fechaLarga(g.vigente_desde ?? periodo)}): total del grupo ${pesos(g.total)}.`;
      // Un monto vacío no se compara: se nombra la unidad, no se inventa una diferencia.
      const vacias = g.unidades.filter((u) => u.sin_monto).map((u) => u.designacion);
      const sinMonto = vacias.length === 0 ? "" : ` Sin monto informado, no se ${vacias.length === 1 ? "compara" : "comparan"}: ${vacias.join(", ")}.`;
      if (g.senaladas === 0) {
        const todas = vacias.length === 0 ? "todas las unidades" : "las demás unidades";
        return `${base} Coincide con lo informado en ${todas}, con hasta un centavo de diferencia.${sinMonto}`;
      }
      return `${base} ${g.senaladas === 1 ? "Una unidad difiere" : `${g.senaladas} unidades difieren`} en más de un centavo.${sinMonto}`;
    }
    case "sin_correspondencia":
      return "Falta indicar qué coeficientes del edificio corresponden a este grupo.";
    case "sin_esquema":
      return `Los coeficientes ${nombreDeEsquema(g.grupo ?? "")} no estaban vigentes en ${mesDe(periodo)}.`;
    case "sin_total":
      return "El total de este grupo no está entre los datos confirmados de la liquidación.";
    case "coeficientes_incompletos":
      return `Hay unidades sin coeficiente en ${nombreDeEsquema(g.grupo ?? "")}: se completan en la carga de coeficientes.`;
  }
}

/** Una unidad con diferencia, en una línea: lo informado y lo que da el reparto. */
function lineaDeUnidad(u: UnidadRecalculada): string {
  if (u.esperado === null) return `${u.designacion} · informado ${pesos(u.informado)} · no está en el padrón del edificio`;
  if (u.informado === null) return `${u.designacion} · no figura en la liquidación · según los coeficientes ${pesos(u.esperado)}`;
  return `${u.designacion} · informado ${pesos(u.informado)} · según los coeficientes ${pesos(u.esperado)}`;
}

/** El texto de la solicitud formal: cada diferencia con su base, sin calificarla (RN33), dentro del
 * largo que admite la solicitud. */
export function textoDeSolicitud(r: Recalculo): string {
  const encabezado = `Se pide la base de cálculo del reparto de la liquidación de ${mesDe(r.periodo)} en estos grupos y unidades, donde el monto informado no coincide con el reparto del gasto del grupo según los coeficientes cargados en el edificio:`;
  const cierre = "Si se aplicó un criterio distinto del coeficiente (por ejemplo, un gasto particular de una unidad), se pide indicar cuál.";
  const lineas = r.grupos.flatMap((g) =>
    g.unidades
      .filter((u) => u.senalada)
      .map((u) => {
        const base =
          u.esperado === null
            ? "no está en el padrón del edificio"
            : `según los coeficientes ${nombreDeEsquema(g.grupo ?? "")} ${pesos(u.esperado)} (coeficiente ${porcentajeDe(u.coeficiente)}; porcentaje informado ${porcentajeDe(u.porcentaje_informado)})`;
        const informado = u.informado === null ? "no figura en la liquidación" : `informado ${pesos(u.informado)}`;
        return `- ${g.nombre}, ${u.designacion}: ${informado}; ${base}.`;
      }),
  );
  const mas = (n: number) => `- Y ${n === 1 ? "una diferencia más" : `${n} diferencias más`}, en el recálculo de la aplicación.`;
  const partes = [encabezado];
  for (const [i, linea] of lineas.entries()) {
    const quedan = lineas.length - i - 1;
    if ([...partes, linea, ...(quedan > 0 ? [mas(quedan)] : []), cierre].join("\n").length > LARGO_SOLICITUD) {
      partes.push(mas(lineas.length - i));
      break;
    }
    partes.push(linea);
  }
  partes.push(cierre);
  return partes.join("\n");
}

type PropsRecalculo = { consorcio: string; importacion: string; aEntrar: () => void };

function RecalculoDeLaLiquidacion({ consorcio, importacion, aEntrar }: PropsRecalculo) {
  const id = useId();
  const [lectura, setLectura] = useState<{ tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; r: Recalculo }>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [eligiendo, setEligiendo] = useState<GrupoRecalculado | null>(null);
  const [elegido, setElegido] = useState("");
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  const clave = useRef<string | null>(null);
  const fichas = useFichas<string>();
  const puede = usePuede(consorcio);
  const navegar = useNavigate();

  useEffect(() => {
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/liquidaciones-importadas/{importacion_id}/recalculo", {
        params: { path: { consorcio_id: consorcio, importacion_id: importacion } },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        if (data === undefined) {
          setLectura({ tipo: "sin_servicio" });
          setAviso({ texto: mensajeDeError(response.status, codigoDe(error)), ok: false });
          return;
        }
        setLectura({ tipo: "ok", r: data });
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion) aEntrar();
        else if (vigente) {
          setLectura({ tipo: "sin_servicio" });
          setAviso({ texto: SIN_CONEXION, ok: false });
        }
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, importacion, vuelta, aEntrar]);

  async function guardarCorrespondencia(g: GrupoRecalculado): Promise<FinDeEnvio> {
    if (elegido === "") {
      setErrores({ esquema: FALTA_ESQUEMA });
      return "seguir";
    }
    clave.current ??= crypto.randomUUID();
    try {
      const { data, error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/liquidaciones-importadas/{importacion_id}/correspondencias", {
        headers: encabezadoCsrf(),
        params: { path: { consorcio_id: consorcio, importacion_id: importacion }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current } },
        body: { nombre: g.nombre, grupo: elegido },
      });
      exigirSesion(response);
      if (data === undefined) {
        if (renovarClave(response.status, codigoDe(error))) clave.current = null;
        return { rechazo: mensajeDeActo(response.status, error) };
      }
      clave.current = null;
      setLectura({ tipo: "ok", r: data });
      setAviso({ texto: `«${g.nombre}» se recalcula con los coeficientes ${nombreDeEsquema(elegido)}.`, ok: true });
      setEligiendo(null);
      return "cerrar";
    } catch (e: unknown) {
      if (e instanceof SinSesion) {
        aEntrar();
        return "seguir";
      }
      return { rechazo: SIN_CONEXION };
    }
  }

  const r = lectura.tipo === "ok" ? lectura.r : null;
  const senaladas = r?.grupos.reduce((n, g) => n + g.senaladas, 0) ?? 0;
  const llaveDe = (g: GrupoRecalculado, u: UnidadRecalculada) => `${g.nombre}|${u.unidad_id ?? u.designacion}`;
  const enFicha = r?.grupos.flatMap((g) => g.unidades.map((u) => ({ g, u }))).find(({ g, u }) => llaveDe(g, u) === fichas.abierta);

  function prepararSolicitud(recalculo: Recalculo) {
    const propuesta: Propuesta = { consorcio, objeto: "periodo", referencia: `Liquidación de ${mesDe(recalculo.periodo)}`, documentacion: textoDeSolicitud(recalculo) };
    void navegar(RUTA_SOLICITUDES, { state: { propuesta } });
  }

  return (
    <section aria-labelledby={`${id}-recalculo`}>
      <h3 id={`${id}-recalculo`}>{RECALCULO}</h3>
      <AvisoPantalla aviso={aviso} />
      {lectura.tipo === "cargando" && <p role="status">Recalculando…</p>}
      {lectura.tipo === "sin_servicio" && (
        <FallaDeLectura
          alReintentar={() => {
            setAviso(sinLaFalla);
            setLectura({ tipo: "cargando" });
            setVuelta((v) => v + 1);
          }}
          avisar={setAviso}
        />
      )}
      {r !== null && r.sin_grupos && <p className="tarjeta">{SIN_GRUPOS}</p>}
      {r !== null && !r.sin_grupos && r.esquemas.length === 0 && (
        <p className="tarjeta">{`El edificio no tiene coeficientes vigentes en ${mesDe(r.periodo)}: se cargan desde los coeficientes del padrón.`}</p>
      )}
      {r?.grupos.map((g) => {
        const conDiferencia = g.unidades.filter((u) => u.senalada);
        return (
          <div key={g.nombre}>
            <h4>{g.nombre}</h4>
            <p>{situacionDelGrupo(g, r.periodo)}</p>
            {conDiferencia.length > 0 && (
              <ul className="filas-registro" aria-label={`Diferencias en ${g.nombre}`}>
                {conDiferencia.map((u) => (
                  <li key={llaveDe(g, u)}>
                    <FilaDeRegistro
                      titulo={lineaDeUnidad(u)}
                      estado={u.diferencia === null ? undefined : `Diferencia ${enPesos(u.diferencia)}`}
                      alAbrir={() => fichas.abrir(llaveDe(g, u))}
                      refFila={fichas.refFila(llaveDe(g, u))}
                      abierta={fichas.abierta === llaveDe(g, u)}
                    />
                  </li>
                ))}
              </ul>
            )}
            {r.esquemas.length > 0 && g.estado !== "sin_total" && (
              <p>
                <button
                  type="button"
                  className="secundario"
                  onClick={() => {
                    clave.current = null;
                    setErrores(SIN_ERRORES);
                    setElegido(g.grupo !== null && r.esquemas.some((e) => e.grupo === g.grupo) ? g.grupo : "");
                    setEligiendo(g);
                  }}
                >
                  {g.estado === "sin_correspondencia" ? ELEGIR_COEFICIENTES : CAMBIAR_COEFICIENTES}
                </button>
              </p>
            )}
          </div>
        );
      })}
      {r !== null && senaladas > 0 && puede("solicitud_formal.preparar") && (
        <p>
          <button type="button" onClick={() => prepararSolicitud(r)}>
            {PREPARAR_SOLICITUD}
          </button>
        </p>
      )}
      {enFicha !== undefined && (
        <FichaDeRegistro
          titulo={`${enFicha.g.nombre} · ${enFicha.u.designacion}`}
          datos={[
            { etiqueta: "Coeficiente cargado", valor: porcentajeDe(enFicha.u.coeficiente) },
            { etiqueta: "Porcentaje informado", valor: porcentajeDe(enFicha.u.porcentaje_informado) },
            { etiqueta: "Según los coeficientes", valor: pesos(enFicha.u.esperado) },
            { etiqueta: "Informado", valor: pesos(enFicha.u.informado) },
            { etiqueta: "Diferencia", valor: pesos(enFicha.u.diferencia) },
          ]}
          alCerrar={() => fichas.cerrar()}
        />
      )}
      {eligiendo !== null && r !== null && (
        <VentanaDeFormulario titulo={`Coeficientes para «${eligiendo.nombre}»`} textoEnviar="Recalcular" alEnviar={() => guardarCorrespondencia(eligiendo)} alCerrar={() => setEligiendo(null)}>
          <p>El edificio recuerda la elección para las liquidaciones siguientes.</p>
          <label htmlFor={`${id}-esquema`}>Coeficientes del edificio</label>
          <select
            id={`${id}-esquema`}
            data-foco
            value={elegido}
            {...marcaDe(`${id}-esquema`, errores.esquema)}
            onChange={(ev) => {
              clave.current = null;
              setErrores(SIN_ERRORES);
              setElegido(ev.target.value);
            }}
          >
            <option value="">Elegir…</option>
            {r.esquemas.map((e) => (
              <option key={e.grupo} value={e.grupo}>
                {`${e.grupo.replaceAll("_", " ")} (versión ${e.esquema_version}, desde el ${fechaLarga(e.vigente_desde)})`}
              </option>
            ))}
          </select>
          <ErrorDeCampo idCampo={`${id}-esquema`} mensaje={errores.esquema} />
        </VentanaDeFormulario>
      )}
    </section>
  );
}
