import { useCallback, useEffect, useState } from "react";

import { api } from "../api/cliente";
import { encabezadoCsrf } from "../csrf";
import { mensajeDeError as mensajeDeCarga } from "../Documentos";
import { ENCABEZADO_IDEMPOTENCIA, mensajeDeError, renovarClave } from "../egresos/comun";
import { exigirSesion, SinSesion } from "../egresos/useConsorcio";
import { codigoDe, SIN_CONEXION } from "../fallas";
import type { LecturaDelArchivo } from "../VisorDocumento";
import { mensajeDePago } from "./textos";

// El comprobante de un pago informado (app 0129): se sube para una unidad y entra en cuarentena; el
// pago lo cita enseguida (el antivirus solo frena la confirmación, no el informe). Ver un archivo
// pide el contenido por la ruta del pago o del correo recibido, con el control de acceso de cada una.

/** Los códigos de una subida que dicen algo del archivo (tiempo, tamaño, tipo, turno). */
const DEL_ARCHIVO = new Set([408, 413, 415, 503]);

/** Sube el comprobante; devuelve la versión, o la falla dicha y si hay que cambiar la clave. */
export async function subirComprobante(
  consorcio: string,
  unidad: string,
  archivo: File,
  clave: string,
): Promise<{ version: string } | { falla: string; renovar: boolean }> {
  const { data, error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/pagos-informados/comprobantes", {
    headers: { ...encabezadoCsrf(), "Content-Type": archivo.type },
    params: { path: { consorcio_id: consorcio }, query: { unidad_id: unidad }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
    // El archivo crudo, como en Documentos: `bodySerializer` lo deja pasar sin JSON.
    body: archivo as unknown as string,
    bodySerializer: (cuerpo) => cuerpo,
  });
  exigirSesion(response);
  if (data !== undefined) return { version: data.archivo.id };
  const codigo = codigoDe(error);
  const motivo = DEL_ARCHIVO.has(response.status)
    ? mensajeDeCarga(response.status, codigo, "subir", [], response.headers.has("Retry-After"))
    : mensajeDePago(response.status, error);
  return { falla: `No se cargó el comprobante. ${motivo}`, renovar: renovarClave(response.status, codigo) };
}

export type DeDonde = { tipo: "pago"; consorcio: string; pago: string } | { tipo: "correo"; consorcio: string; recibido: string };

/** El archivo de un pago informado o de un correo recibido. */
export function useArchivoDelPago(de: DeDonde, aEntrar: () => void) {
  const [lectura, setLectura] = useState<LecturaDelArchivo>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const clave = de.tipo === "pago" ? de.pago : de.recibido;
  useEffect(() => {
    let vigente = true;
    setLectura({ tipo: "cargando" });
    const pedido =
      de.tipo === "pago"
        ? api.GET("/api/v1/consorcios/{consorcio_id}/pagos-informados/{pago_id}/comprobante", {
            params: { path: { consorcio_id: de.consorcio, pago_id: de.pago } },
            parseAs: "blob",
          })
        : api.GET("/api/v1/consorcios/{consorcio_id}/pagos-informados/por-correo/{recibido_id}/comprobante", {
            params: { path: { consorcio_id: de.consorcio, recibido_id: de.recibido } },
            parseAs: "blob",
          });
    void pedido
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        if (data === undefined) setLectura({ tipo: "falla", texto: mensajeDeError(response.status, codigoDe(error)) });
        else setLectura({ tipo: "ok", archivo: data as Blob });
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else setLectura({ tipo: "falla", texto: SIN_CONEXION });
      });
    return () => {
      vigente = false;
    };
    // `de` cambia de identidad en cada dibujo: lo que cuenta es a qué archivo apunta.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [de.tipo, de.consorcio, clave, vuelta, aEntrar]);
  const reintentar = useCallback(() => setVuelta((n) => n + 1), []);
  return { lectura, reintentar };
}
