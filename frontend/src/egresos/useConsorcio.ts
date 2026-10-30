import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";

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

/** Los edificios de la persona y el elegido (el primero). Sin sesión, a /entrar. Las pantallas
 * de egresos nunca guardan nada en el dispositivo. */
export function useConsorcio() {
  const navegar = useNavigate();
  const [pantalla, setPantalla] = useState<PantallaConsorcio>({ tipo: "cargando" });
  const [consorcio, setConsorcio] = useState("");
  const consultado = useRef(false);

  const aEntrar = useCallback(() => navegar(RUTA_ENTRAR, { replace: true }), [navegar]);

  useEffect(() => {
    if (consultado.current) return;
    consultado.current = true;
    void api
      .GET("/api/v1/me/consorcios")
      .then(({ data, error, response }) => {
        exigirSesion(response);
        const primero = data?.[0];
        if (data === undefined)
          setPantalla({ tipo: "sin_servicio", deConexion: esDeConexion(response.status, codigoDe(error)) });
        else if (primero === undefined) setPantalla({ tipo: "sin_consorcios" });
        else {
          setPantalla({ tipo: "lista", consorcios: data });
          setConsorcio(primero.id);
        }
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion) aEntrar();
        else setPantalla({ tipo: "sin_servicio", deConexion: true });
      });
  }, [aEntrar]);

  return { pantalla, consorcio, setConsorcio, aEntrar };
}
