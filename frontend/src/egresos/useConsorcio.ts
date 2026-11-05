import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { RUTA_ENTRAR } from "../enlace";
import { codigoDe, esDeConexion } from "../fallas";
import { ESTADO } from "./comun";

type Consorcio = components["schemas"]["ConsorcioResumen"];

export type PantallaConsorcio =
  | { tipo: "cargando" }
  | { tipo: "sin_servicio"; deConexion: boolean }
  | { tipo: "sin_consorcios" }
  | { tipo: "lista"; consorcios: Consorcio[] };

export class SinSesion extends Error {}

/** Cada cuánto se mira si el antivirus terminó y cuántas veces, antes de dejarlo a mano. */
const ESPERA_ANTIVIRUS_MS = 3000;
const VUELTAS_ANTIVIRUS = 20;

/** Mientras `activa`, llama a `mirar` cada tanto; agotadas las vueltas, a `alAgotar` y se detiene. */
export function useEsperaAntivirus(activa: boolean, mirar: () => void, alAgotar: () => void) {
  const ultimas = useRef({ mirar, alAgotar });
  ultimas.current = { mirar, alAgotar };
  useEffect(() => {
    if (!activa) return;
    let vueltas = 0;
    const temporizador = setInterval(() => {
      vueltas += 1;
      if (vueltas <= VUELTAS_ANTIVIRUS) ultimas.current.mirar();
      else {
        clearInterval(temporizador);
        ultimas.current.alAgotar();
      }
    }, ESPERA_ANTIVIRUS_MS);
    return () => clearInterval(temporizador);
  }, [activa]);
}

/** Si la pantalla sigue montada: una escritura nunca sale de una pantalla descartada. */
export function useMontado() {
  const montado = useRef(true);
  useEffect(() => {
    montado.current = true;
    return () => {
      montado.current = false;
    };
  }, []);
  return montado;
}

export function exigirSesion(response: Response) {
  if (response.status === ESTADO.sinSesion) throw new SinSesion();
}

/** El edificio con el que llega la persona desde otra pantalla (`state.consorcio`). */
function consorcioDe(estado: unknown): string | null {
  if (typeof estado !== "object" || estado === null || !("consorcio" in estado)) return null;
  return typeof estado.consorcio === "string" ? estado.consorcio : null;
}

/** Los edificios de la persona y el elegido: el que trae la navegación o, si no, el primero. Sin
 * sesión, a /entrar. Las pantallas de egresos nunca guardan nada en el dispositivo. */
export function useConsorcio() {
  const navegar = useNavigate();
  // Se lee una vez, al llegar: `AvisoLlegada` borra el `state` apenas lo ve.
  const preferido = useRef(consorcioDe(useLocation().state));
  const [pantalla, setPantalla] = useState<PantallaConsorcio>({ tipo: "cargando" });
  const [consorcio, setConsorcio] = useState("");
  const consultado = useRef(false);

  const aEntrar = useCallback(() => navegar(RUTA_ENTRAR, { replace: true }), [navegar]);

  const consultar = useCallback(() => {
    void api
      .GET("/api/v1/me/consorcios")
      .then(({ data, error, response }) => {
        exigirSesion(response);
        const elegido = data?.find((c) => c.id === preferido.current) ?? data?.[0];
        if (data === undefined)
          setPantalla({ tipo: "sin_servicio", deConexion: esDeConexion(response.status, codigoDe(error)) });
        else if (elegido === undefined) setPantalla({ tipo: "sin_consorcios" });
        else {
          setPantalla({ tipo: "lista", consorcios: data });
          setConsorcio(elegido.id);
        }
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion) aEntrar();
        else setPantalla({ tipo: "sin_servicio", deConexion: true });
      });
  }, [aEntrar]);

  useEffect(() => {
    if (consultado.current) return;
    consultado.current = true;
    consultar();
  }, [consultar]);

  const reintentar = useCallback(() => {
    setPantalla({ tipo: "cargando" });
    consultar();
  }, [consultar]);

  return { pantalla, consorcio, setConsorcio, aEntrar, reintentar };
}
