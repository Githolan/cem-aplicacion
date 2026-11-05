import { useEffect, useState } from "react";
import { useNavigate } from "react-router";

import { api } from "./api/cliente";

// Si hay sesión, para decidir a qué pantalla va la persona (decisión del propietario del
// 05-11-2026, §2). Cada pantalla pregunta al montarse y no se guarda en caché: recién entrada,
// una respuesta vieja la mandaría de vuelta a /entrar. Sin conexión no se sabe, y se dice.

const ESTADO_SIN_SESION = 401;

export type EstadoSesion = "consultando" | "con_sesion" | "sin_sesion" | "sin_servicio";

export function useSesion(activa = true): EstadoSesion {
  const [estado, setEstado] = useState<EstadoSesion>("consultando");

  useEffect(() => {
    if (!activa) return;
    let vigente = true;
    void api.GET("/api/v1/auth/sesion").then(
      ({ data, response }) => {
        if (!vigente) return;
        if (data !== undefined) setEstado("con_sesion");
        else setEstado(response.status === ESTADO_SIN_SESION ? "sin_sesion" : "sin_servicio");
      },
      () => {
        if (vigente) setEstado("sin_servicio");
      },
    );
    return () => {
      vigente = false;
    };
  }, [activa]);

  return estado;
}

/** Terminó la entrada: la pantalla siguiente es el inicio, con el aviso del resultado. */
export function useIrAlInicio(): (aviso: string) => void {
  const navegar = useNavigate();
  return (aviso) => navegar("/", { replace: true, state: { aviso } });
}
