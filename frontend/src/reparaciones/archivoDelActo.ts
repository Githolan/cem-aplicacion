import { useRef, useState } from "react";

import { api } from "../api/cliente";
import { encabezadoCsrf } from "../csrf";
import { mensajeDeError as mensajeDeCarga } from "../Documentos";
import { ENCABEZADO_IDEMPOTENCIA, mensajeDeActo, renovarClave } from "../egresos/comun";
import { exigirSesion, SinSesion } from "../egresos/useConsorcio";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { RECHAZADO, TODAVIA_REVISANDO, type TipoAdjunto } from "./textos";

// Un archivo de la reparación (RF19, app 0124) para un acto: se sube (entra en cuarentena), se
// espera a que el antivirus lo acepte y recién ahí el acto lo cita. El servidor no acepta citar uno
// que todavía se revisa: la ventana espera en lugar de ofrecer un envío que va a fallar.

export const ESPERA_ENTRE_CONSULTAS_MS = 2000;
export const CONSULTAS_MAXIMAS = 15;
/** Los códigos de una subida que dicen algo del archivo (tiempo, tamaño, tipo, turno). */
const DEL_ARCHIVO = new Set([408, 413, 415, 503]);

export type Preparado = { adjunto_id: string } | { rechazo: string } | null;

const esperar = (ms: number) => new Promise((listo) => setTimeout(listo, ms));

/** Sube el archivo; devuelve su `adjunto_id`, la falla dicha, o null si hay que volver a entrar. */
export async function subirArchivo(
  consorcio: string,
  reparacion: string,
  tipo: TipoAdjunto,
  archivo: File,
  clave: string,
): Promise<{ adjunto_id: string } | { falla: string; renovar: boolean }> {
  const { data, error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/reparaciones/{reparacion_id}/adjuntos", {
    headers: { ...encabezadoCsrf(), "Content-Type": archivo.type },
    params: { path: { consorcio_id: consorcio, reparacion_id: reparacion }, query: { tipo }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
    // El archivo crudo, como en Documentos: `bodySerializer` lo deja pasar sin JSON.
    body: archivo as unknown as string,
    bodySerializer: (cuerpo) => cuerpo,
  });
  exigirSesion(response);
  if (data !== undefined) return { adjunto_id: data.adjunto_id };
  const codigo = codigoDe(error);
  const motivo = DEL_ARCHIVO.has(response.status)
    ? mensajeDeCarga(response.status, codigo, "subir", [], response.headers.has("Retry-After"))
    : mensajeDeActo(response.status, error);
  return { falla: `No se cargó el archivo. ${motivo}`, renovar: renovarClave(response.status, codigo) };
}

/** Pregunta por la ficha hasta que el antivirus termina con el archivo. */
export async function esperarAnalisis(consorcio: string, reparacion: string, adjunto: string): Promise<"disponible" | "rechazado" | "revisando" | { falla: string }> {
  for (let vuelta = 0; vuelta < CONSULTAS_MAXIMAS; vuelta++) {
    const { data, error, response } = await api.GET("/api/v1/consorcios/{consorcio_id}/reparaciones/{reparacion_id}", {
      params: { path: { consorcio_id: consorcio, reparacion_id: reparacion } },
    });
    exigirSesion(response);
    if (data === undefined) return { falla: mensajeDeActo(response.status, error) };
    const estado = data.adjuntos.find((a) => a.id === adjunto)?.estado;
    if (estado === "disponible" || estado === "rechazado") return estado;
    await esperar(ESPERA_ENTRE_CONSULTAS_MS);
  }
  return "revisando";
}

/**
 * El archivo elegido en la ventana de un acto. `preparar` lo sube una sola vez (si el acto falla y
 * se reintenta con el mismo archivo, lo vuelve a citar) y espera al antivirus.
 */
export function useArchivoDelActo(consorcio: string, reparacion: string, tipo: TipoAdjunto, aEntrar: () => void) {
  const [archivo, setArchivo] = useState<File | null>(null);
  const [revisando, setRevisando] = useState(false);
  const clave = useRef<string | null>(null);
  const subido = useRef<{ archivo: File; adjunto_id: string } | null>(null);

  async function preparar(elegido: File): Promise<Preparado> {
    try {
      let adjunto: string;
      if (subido.current?.archivo === elegido) adjunto = subido.current.adjunto_id;
      else {
        clave.current ??= crypto.randomUUID();
        const r = await subirArchivo(consorcio, reparacion, tipo, elegido, clave.current);
        if ("falla" in r) {
          if (r.renovar) clave.current = null;
          return { rechazo: r.falla };
        }
        subido.current = { archivo: elegido, adjunto_id: r.adjunto_id };
        clave.current = null;
        adjunto = r.adjunto_id;
      }
      setRevisando(true);
      const analisis = await esperarAnalisis(consorcio, reparacion, adjunto);
      setRevisando(false);
      if (analisis === "disponible") return { adjunto_id: adjunto };
      if (analisis === "revisando") return { rechazo: TODAVIA_REVISANDO };
      if (analisis === "rechazado") {
        subido.current = null;
        setArchivo(null);
        return { rechazo: RECHAZADO };
      }
      return { rechazo: analisis.falla };
    } catch (e: unknown) {
      setRevisando(false);
      if (!(e instanceof SinSesion)) return { rechazo: SIN_CONEXION };
      aEntrar();
      return null;
    }
  }

  return { archivo, setArchivo, revisando, preparar };
}
