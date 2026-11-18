import { useEffect, useRef, useState } from "react";

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
  /** `error` es el cuerpo del error: de ahí salen los campos a marcar (`erroresDe`). */
  alFallar?: (estado: number, codigo: string | undefined, error: unknown) => void;
  /** El pedido no llegó al servidor (sin red): el aviso ya lo dice; esto, si hay algo más que hacer. */
  alNoLlegar?: () => void;
  /** El acto sale de una ventana (`VentanaDeFormulario`): el rechazo lo dice la ventana, no el aviso
   * de la pantalla de atrás (informe de fricción, tanda 1, punto 7). */
  enVentana?: boolean;
};

export type Pendiente = { acto: string; reintentar: () => void };

/** Cómo terminó un acto, para quien lo espera (una ventana): hecho, sigue la pantalla (se pidió el
 * código y se canceló, o no hay sesión) o por qué no se hizo, con el cuerpo del error. */
export type FinDelActo = "hecho" | "seguir" | { rechazo: string; estado: number | null; error: unknown };

/** `alDescartar`: el acto salió bien pero su pantalla ya se cerró, y nada más se entera. */
export function useActo(aEntrar: () => void, alDescartar?: () => void) {
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [pendiente, setPendiente] = useState<Pendiente | null>(null);
  const clave = useRef<string | null>(null);
  const ultimoActo = useRef<string | null>(null);
  const montado = useMontado();
  /** Quien espera el acto mientras se pide el código: si se cancela, sigue sin él. */
  const esperaElCodigo = useRef<((fin: FinDelActo) => void) | null>(null);
  useEffect(() => {
    if (pendiente !== null || esperaElCodigo.current === null) return;
    const seguir = esperaElCodigo.current;
    esperaElCodigo.current = null;
    seguir("seguir");
  }, [pendiente]);

  /** El acto, para quien lo espera: dice cómo terminó (una ventana, `VentanaDeFormulario`). */
  async function hacerYEsperar<T>(acto: Acto<T>, reintento = false): Promise<FinDelActo> {
    if (!montado.current) return "seguir";
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
      if (e instanceof SinSesion) {
        aEntrar();
        return "seguir";
      }
      // La pantalla ya se cerró: la caída de la red no mueve la que se abrió después.
      if (!montado.current) return "seguir";
      if (acto.enVentana !== true) setAviso({ texto: SIN_CONEXION, ok: false });
      acto.alNoLlegar?.();
      return { rechazo: SIN_CONEXION, estado: null, error: e };
    }
    setEnviando(false);
    // La ficha ya se cerró: su respuesta no mueve la pantalla que se abrió después.
    const { data, error, response } = respuesta;
    if (!montado.current) {
      if (data !== undefined) alDescartar?.();
      return "seguir";
    }
    if (data !== undefined) {
      clave.current = null;
      acto.alTerminar(data);
      return "hecho";
    }
    const codigo = codigoDe(error);
    if (codigo === SEGUNDO_FACTOR_REQUERIDO) {
      // Quien lo espera sigue esperando: el acto termina con el reintento, o sin él si se cancela.
      return new Promise<FinDelActo>((terminar) => {
        esperaElCodigo.current = terminar;
        setPendiente({
          acto: acto.nombre,
          reintentar: () => {
            esperaElCodigo.current = null;
            clave.current = usada;
            void hacerYEsperar(acto, true).then(terminar);
          },
        });
      });
    }
    if (renovarClave(response.status, codigo)) clave.current = null;
    const texto = mensajeDeActo(response.status, error);
    if (acto.enVentana !== true) setAviso({ texto, ok: false });
    acto.alFallar?.(response.status, codigo, error);
    return { rechazo: texto, estado: response.status, error };
  }

  /** El acto, para quien no espera el resultado: lo dicen sus `alTerminar` y `alFallar`. */
  async function hacer<T>(acto: Acto<T>): Promise<void> {
    await hacerYEsperar(acto);
  }

  return { enviando, setEnviando, aviso, setAviso, pendiente, setPendiente, hacer, hacerYEsperar, montado };
}

export type ActoEnCurso = ReturnType<typeof useActo>;
