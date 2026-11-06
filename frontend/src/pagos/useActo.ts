import { useRef, useState } from "react";

import { codigoDe, SIN_CONEXION } from "../fallas";
import { type Aviso, mensajeDeActo, renovarClave, SEGUNDO_FACTOR_REQUERIDO, soltarClaveSiCambio } from "../egresos/comun";
import { exigirSesion, SinSesion, useMontado } from "../egresos/useConsorcio";

// Un acto sobre una orden, igual para todos los botones del circuito. Un error se dice en la misma
// pantalla y lo escrito queda (regla de fricción, 05-11-2026). Ante el segundo factor se pide el
// código y se repite el mismo acto con la misma clave. Repetirlo a mano después de un 5xx o de una
// caída de la red también lleva la misma clave: es el mismo pedido.

type Respuesta<T> = { data?: T; error?: unknown; response: Response };

export type Acto<T> = {
  /** Lo que distingue este acto de otro: otros datos llevan otra clave. */
  quePide: readonly unknown[];
  /** Cómo se nombra en la pantalla del código: «Para aprobar la orden…». */
  nombre: string;
  /** `clave` va en `Idempotency-Key`; las rutas que no la piden la ignoran. */
  pedir: (clave: string) => Promise<Respuesta<T>>;
  alTerminar: (data: T) => void;
  alFallar?: (estado: number, codigo: string | undefined) => void;
};

export type Pendiente = { acto: string; reintentar: () => void };

/** `alDescartar`: el acto salió bien pero su pantalla ya se cerró, y nada más se entera. */
export function useActo(aEntrar: () => void, alDescartar?: () => void) {
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [pendiente, setPendiente] = useState<Pendiente | null>(null);
  const clave = useRef<string | null>(null);
  const ultimoActo = useRef<string | null>(null);
  const montado = useMontado();

  async function hacer<T>(acto: Acto<T>, reintento = false): Promise<void> {
    if (!montado.current) return;
    if (!reintento) soltarClaveSiCambio(clave, ultimoActo, JSON.stringify(acto.quePide));
    const usada = (clave.current ??= crypto.randomUUID());
    setEnviando(true);
    setAviso(null);
    let respuesta: Respuesta<T>;
    try {
      respuesta = await acto.pedir(usada);
      exigirSesion(respuesta.response);
    } catch (e: unknown) {
      setEnviando(false);
      if (e instanceof SinSesion) aEntrar();
      else setAviso({ texto: SIN_CONEXION, ok: false });
      return;
    }
    setEnviando(false);
    // La ficha ya se cerró: su respuesta no mueve la pantalla que se abrió después.
    const { data, error, response } = respuesta;
    if (!montado.current) {
      if (data !== undefined) alDescartar?.();
      return;
    }
    if (data !== undefined) {
      clave.current = null;
      acto.alTerminar(data);
      return;
    }
    const codigo = codigoDe(error);
    if (codigo === SEGUNDO_FACTOR_REQUERIDO) {
      setPendiente({
        acto: acto.nombre,
        reintentar: () => {
          clave.current = usada;
          void hacer(acto, true);
        },
      });
      return;
    }
    if (renovarClave(response.status, codigo)) clave.current = null;
    setAviso({ texto: mensajeDeActo(response.status, codigo), ok: false });
    acto.alFallar?.(response.status, codigo);
  }

  return { enviando, setEnviando, aviso, setAviso, pendiente, setPendiente, hacer, montado };
}

export type ActoEnCurso = ReturnType<typeof useActo>;
