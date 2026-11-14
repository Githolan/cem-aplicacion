import { useCallback, useRef, useState } from "react";

import { api } from "../api/cliente";
import { useAvisoAlSalir } from "../avisoAlSalir";
import { TIPOS_DE_DOCUMENTO, tituloDeArchivo, TOPE_BYTES } from "../archivos";
import { TEXTO_SUBIENDO } from "../CargaConLectura";
import { encabezadoCsrf } from "../csrf";
import { mensajeDeError as mensajeDeCarga } from "../Documentos";
import { ENCABEZADO_IDEMPOTENCIA, LARGO_TITULO, mensajeDeActo, renovarClave } from "../egresos/comun";
import { exigirSesion, SinSesion, useConsultaDelAntivirus } from "../egresos/useConsorcio";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { Hoja } from "../Hoja";
import { SubirArchivo } from "../SubirArchivo";

// «Subir la factura» de una orden ya aprobada o pagada que no la tiene (decisión del propietario del
// 13-11-2026; app 0099; informe de fricción, AC-44). Elegir el archivo es el único paso: queda adjunto
// a la versión vigente, con quién y cuándo, sin versión nueva y sin volver a aprobación. Después, el
// «Listo» dice lo que pasó y la hoja pregunta hasta que el antivirus termina: si lo acepta, la orden
// sale de «Facturas faltantes»; si lo rechaza, lo dice y se elige otro archivo. Un error se dice dentro
// de la hoja: la pantalla de atrás está inerte.
//
// «Reemplazar la factura» (decisión del propietario del 13-11-2026) es la misma hoja con `reemplaza`:
// la anterior no se borra, queda como reemplazada; mientras la nueva está en el antivirus, o si la
// rechaza, vale la anterior. Nada más de la orden cambia.

export const SUBIR_FACTURA = "Subir la factura";
export const TEXTO_QUE_HACE =
  "La factura queda adjunta a la orden, con quién y cuándo. No cambia el importe, el proveedor ni la cuenta, y la orden no vuelve a aprobación.";
export const TEXTO_LISTO = "Listo: la factura quedó adjunta a la orden. El antivirus la está revisando.";
export const TEXTO_ACEPTADA = "El antivirus aceptó la factura: la orden ya no figura en «Facturas faltantes».";
export const TEXTO_RECHAZADA = "La factura no pasó el antivirus y no cuenta como adjunta. Hay que elegir otro archivo.";
export const TEXTO_TARDA = "El antivirus está tardando. Se puede cerrar: la orden sale de «Facturas faltantes» cuando acepte la factura.";
/** Los estados de una subida que son del archivo y no de la orden (Documentos los explica). */
const DEL_ARCHIVO = new Set([408, 413, 415, 503]);
const POR_OMISION = "Factura";

/** Lo que distingue un archivo elegido de otro: nombre, tipo, tamaño y fecha de modificación. */
function firmaDe(archivo: File): string {
  return JSON.stringify([archivo.name, archivo.type, archivo.size, archivo.lastModified]);
}

export const REEMPLAZAR_FACTURA = "Reemplazar la factura";
/** Lo que dice la hoja en cada modo: subir la que falta o reemplazar la adjunta. */
export const TEXTOS = {
  subir: {
    titulo: SUBIR_FACTURA,
    queHace: TEXTO_QUE_HACE,
    listo: TEXTO_LISTO,
    aceptada: TEXTO_ACEPTADA,
    rechazada: TEXTO_RECHAZADA,
    tarda: TEXTO_TARDA,
  },
  reemplazar: {
    titulo: REEMPLAZAR_FACTURA,
    queHace:
      "La factura nueva reemplaza a la adjunta, con quién y cuándo. La anterior no se borra: queda como reemplazada. No cambia el importe, el proveedor ni la cuenta, y la orden no vuelve a aprobación.",
    listo: "Listo: la factura nueva quedó adjunta. El antivirus la está revisando; mientras tanto vale la anterior.",
    aceptada: "El antivirus aceptó la factura nueva: es la que vale para la orden. La anterior quedó como reemplazada.",
    rechazada: "La factura nueva no pasó el antivirus: sigue valiendo la anterior. Se puede elegir otro archivo.",
    tarda: "El antivirus está tardando. Se puede cerrar: mientras tanto vale la anterior.",
  },
} as const;

type Fase =
  | { tipo: "elegir" }
  | { tipo: "subiendo" }
  | { tipo: "revisando"; documento: string; version: string }
  | { tipo: "aceptada" }
  | { tipo: "rechazada" };

type Props = {
  consorcio: string;
  orden: string;
  /** La versión vigente que se ve: si cambió, la base lo niega. */
  version: number;
  /** De qué orden se trata, en palabras: proveedor e importe. */
  descripcion: string;
  /** La constancia de la adjunta que vale, para reemplazarla; nulo para subir la que falta. */
  reemplaza: string | null;
  aEntrar: () => void;
  /** `cambio`: si quedó adjunta una factura (la pantalla de atrás se vuelve a leer). */
  alCerrar: (cambio: boolean) => void;
};

export function SubirFactura({ consorcio, orden, version, descripcion, reemplaza, aEntrar, alCerrar }: Props) {
  const textos = reemplaza === null ? TEXTOS.subir : TEXTOS.reemplazar;
  const [fase, setFase] = useState<Fase>({ tipo: "elegir" });
  const [falla, setFalla] = useState<string | null>(null);
  const clave = useRef<string | null>(null);
  /** El archivo del último pedido sin respuesta clara: elegirlo otra vez es reintentar ese pedido. */
  const pedido = useRef<string | null>(null);
  const cambio = useRef(false);
  const enEspera = useRef<{ documento: string; version: string } | null>(null);

  const mirar = useCallback(() => {
    const cual = enEspera.current;
    if (cual === null) return;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/documentos/{documento_id}/versiones/{version_id}/estado", {
        params: { path: { consorcio_id: consorcio, documento_id: cual.documento, version_id: cual.version } },
      })
      .then(({ data, response }) => {
        exigirSesion(response);
        if (enEspera.current !== cual || data === undefined || data.estado === "en_cuarentena") return;
        enEspera.current = null;
        setFase({ tipo: data.estado === "disponible" ? "aceptada" : "rechazada" });
      })
      .catch((e: unknown) => {
        // Una consulta que no llega se repite en la vuelta siguiente; sin sesión, a entrar.
        if (e instanceof SinSesion) aEntrar();
      });
  }, [consorcio, aEntrar]);
  const tarda = useConsultaDelAntivirus(fase.tipo === "revisando", mirar);

  async function subir(archivo: File) {
    const antes = fase;
    setFase({ tipo: "subiendo" });
    setFalla(null);
    clave.current ??= crypto.randomUUID();
    try {
      const { data, error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/factura", {
        headers: { ...encabezadoCsrf(), "Content-Type": archivo.type },
        params: {
          path: { consorcio_id: consorcio, orden_id: orden },
          query: {
            version_esperada: version,
            titulo: tituloDeArchivo(archivo, POR_OMISION, LARGO_TITULO),
            ...(reemplaza === null ? {} : { reemplaza }),
          },
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
        setFase(antes);
        setFalla(
          `La factura no se subió. ${
            DEL_ARCHIVO.has(response.status)
              ? mensajeDeCarga(response.status, codigo, "subir", [], response.headers.has("Retry-After"))
              : mensajeDeActo(response.status, error)
          }`,
        );
        return;
      }
      clave.current = null;
      cambio.current = true;
      enEspera.current = { documento: data.documento_id, version: data.archivo.id };
      setFase({ tipo: "revisando", ...enEspera.current });
    } catch (e: unknown) {
      if (e instanceof SinSesion) aEntrar();
      else {
        setFase(antes);
        setFalla(`La factura no se subió. ${SIN_CONEXION}`);
      }
    }
  }

  function elegir(archivo: File) {
    // El mismo archivo es el mismo pedido (la orden, la versión y lo que reemplaza no cambian en la
    // hoja): si la respuesta anterior se perdió, la misma clave devuelve lo que ya quedó, sin subirlo
    // dos veces. Otro archivo es otro pedido: no repite la clave del anterior.
    const firma = firmaDe(archivo);
    if (pedido.current !== firma) clave.current = null;
    pedido.current = firma;
    void subir(archivo);
  }

  const cerrar = () => alCerrar(cambio.current);
  // Mientras el pedido viaja, la hoja no se cierra: si se cerrara, lo que el servidor confirme
  // después no llegaría a la pantalla de atrás, que seguiría ofreciendo subirla.
  const enviando = fase.tipo === "subiendo";
  useAvisoAlSalir(enviando);
  const ofreceElegir = fase.tipo === "elegir" || fase.tipo === "subiendo" || fase.tipo === "rechazada";

  return (
    <Hoja titulo={textos.titulo} alEscapar={enviando ? undefined : cerrar}>
      <p>{descripcion}</p>
      {fase.tipo === "revisando" && (
        <p role="status" className="aviso-ok">
          {textos.listo}
        </p>
      )}
      {fase.tipo === "revisando" && tarda && <p className="aviso-info">{textos.tarda}</p>}
      {fase.tipo === "aceptada" && (
        <p role="status" className="aviso-ok">
          {textos.aceptada}
        </p>
      )}
      {fase.tipo === "rechazada" && (
        <p role="alert" className="aviso-error">
          {textos.rechazada}
        </p>
      )}
      {ofreceElegir && (
        <div className="formulario">
          <p>{textos.queHace}</p>
          <SubirArchivo de="de la factura" tipos={TIPOS_DE_DOCUMENTO} tope={TOPE_BYTES} deshabilitada={fase.tipo === "subiendo"} alElegir={elegir} />
          {fase.tipo === "subiendo" && (
            <p role="status" className="aviso-info">
              {TEXTO_SUBIENDO}
            </p>
          )}
          {falla !== null && (
            <p role="alert" className="aviso-error">
              {falla}
            </p>
          )}
        </div>
      )}
      <div className="acciones">
        <button type="button" className="secundario" onClick={cerrar} disabled={enviando}>
          Cerrar
        </button>
      </div>
    </Hoja>
  );
}
