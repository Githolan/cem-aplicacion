import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { RUTA_ENTRAR, RUTA_INICIO } from "../enlace";
import { codigoDe, esDeConexion } from "../fallas";
import { type Requisito, useEdificioElegido } from "../permisos";
import { ESTADO } from "./comun";

type Consorcio = components["schemas"]["ConsorcioResumen"];

export type PantallaConsorcio =
  | { tipo: "cargando" }
  | { tipo: "sin_servicio"; deConexion: boolean }
  | { tipo: "sin_consorcios" }
  | { tipo: "lista"; consorcios: readonly Consorcio[] };

const SIN_EDIFICIOS: readonly Consorcio[] = [];

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

/** Mientras `activa`, llama a `mirar` cada tanto y no se rinde: el antivirus puede tardar. Devuelve si
 * ya pasó el tiempo de la espera, para avisar que se puede salir. */
export function useConsultaDelAntivirus(activa: boolean, mirar: () => void): boolean {
  const [tarda, setTarda] = useState(false);
  const ultima = useRef(mirar);
  ultima.current = mirar;
  useEffect(() => {
    if (!activa) return;
    let vueltas = 0;
    const temporizador = setInterval(() => {
      vueltas += 1;
      if (vueltas >= VUELTAS_ANTIVIRUS) setTarda(true);
      ultima.current();
    }, ESPERA_ANTIVIRUS_MS);
    return () => {
      clearInterval(temporizador);
      setTarda(false);
    };
  }, [activa]);
  return tarda;
}

/** Mientras `activa`, repite `leer` cuando el navegador avisa que volvió la red. Solo para lecturas:
 * un pedido que escribe sigue esperando el toque de la persona. */
export function useAlVolverLaRed(activa: boolean, leer: () => void) {
  const ultima = useRef(leer);
  ultima.current = leer;
  useEffect(() => {
    if (!activa) return;
    const alVolver = () => ultima.current();
    window.addEventListener("online", alVolver);
    return () => window.removeEventListener("online", alVolver);
  }, [activa]);
}

async function personaDeLaSesion(): Promise<string> {
  const { data } = await api.GET("/api/v1/auth/sesion");
  if (data === undefined) throw new Error("no se pudo leer la sesión");
  return data.persona_id;
}

/** La persona de la sesión, o null mientras no se sabe. Como con `mis-acciones`, sirve solo para
 * mostrar: sin el dato no se oculta nada y decide el servidor. Compara cuentas, no seres humanos, y
 * no queda guardada para quien entre después en este navegador. */
export function useMiPersona(activa: boolean): string | null {
  const consulta = useQuery({
    queryKey: ["mi-persona"],
    queryFn: personaDeLaSesion,
    enabled: activa,
    gcTime: 0,
    retry: false,
    retryOnMount: false,
    networkMode: "always",
  });
  return consulta.data ?? null;
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

/** Los edificios donde la pantalla sirve y el elegido: el que trae la navegación o, si no, el
 * primero. Sin sesión, a /entrar; si la API niega la lectura, al inicio. Las pantallas de egresos
 * nunca guardan nada en el dispositivo. */
export function useConsorcio(requisito: Requisito) {
  const navegar = useNavigate();
  // Se lee una vez, al llegar: `AvisoLlegada` borra el `state` apenas lo ve.
  const preferido = useRef(consorcioDe(useLocation().state));
  const [pantalla, setPantalla] = useState<PantallaConsorcio>({ tipo: "cargando" });
  const { edificios, consorcio, setConsorcio } = useEdificioElegido(
    pantalla.tipo === "lista" ? pantalla.consorcios : SIN_EDIFICIOS,
    requisito,
    preferido.current,
  );
  const consultado = useRef(false);

  const aEntrar = useCallback(() => navegar(RUTA_ENTRAR, { replace: true }), [navegar]);
  const aInicio = useCallback(() => navegar(RUTA_INICIO, { replace: true }), [navegar]);

  const consultar = useCallback(() => {
    void api
      .GET("/api/v1/me/consorcios")
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data === undefined)
          setPantalla({ tipo: "sin_servicio", deConexion: esDeConexion(response.status, codigoDe(error)) });
        else if (data.length === 0) setPantalla({ tipo: "sin_consorcios" });
        else setPantalla({ tipo: "lista", consorcios: data });
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

  const ofrecida: PantallaConsorcio = pantalla.tipo === "lista" ? { tipo: "lista", consorcios: edificios } : pantalla;
  return { pantalla: ofrecida, consorcio, setConsorcio, aEntrar, aInicio, reintentar };
}
