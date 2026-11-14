import { useCallback, useRef, useState } from "react";

import { api } from "../api/cliente";
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
  aEntrar: () => void;
  /** `cambio`: si quedó adjunta una factura (la pantalla de atrás se vuelve a leer). */
  alCerrar: (cambio: boolean) => void;
};

export function SubirFactura({ consorcio, orden, version, descripcion, aEntrar, alCerrar }: Props) {
  const [fase, setFase] = useState<Fase>({ tipo: "elegir" });
  const [falla, setFalla] = useState<string | null>(null);
  const clave = useRef<string | null>(null);
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
          query: { version_esperada: version, titulo: tituloDeArchivo(archivo, POR_OMISION, LARGO_TITULO) },
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
    // Otro archivo es otro pedido: no repite la clave del anterior.
    clave.current = null;
    void subir(archivo);
  }

  const cerrar = () => alCerrar(cambio.current);
  const ofreceElegir = fase.tipo === "elegir" || fase.tipo === "subiendo" || fase.tipo === "rechazada";

  return (
    <Hoja titulo={SUBIR_FACTURA} alEscapar={cerrar}>
      <p>{descripcion}</p>
      {fase.tipo === "revisando" && (
        <p role="status" className="aviso-ok">
          {TEXTO_LISTO}
        </p>
      )}
      {fase.tipo === "revisando" && tarda && <p className="aviso-info">{TEXTO_TARDA}</p>}
      {fase.tipo === "aceptada" && (
        <p role="status" className="aviso-ok">
          {TEXTO_ACEPTADA}
        </p>
      )}
      {fase.tipo === "rechazada" && (
        <p role="alert" className="aviso-error">
          {TEXTO_RECHAZADA}
        </p>
      )}
      {ofreceElegir && (
        <div className="formulario">
          <p>{TEXTO_QUE_HACE}</p>
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
        <button type="button" className="secundario" onClick={cerrar}>
          Cerrar
        </button>
      </div>
    </Hoja>
  );
}
