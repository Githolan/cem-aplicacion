import { useEffect, useState } from "react";
import { useNavigate } from "react-router";

import { api } from "./api/cliente";
import { borrarCopias } from "./copiaContactos";
import { encabezadoCsrf } from "./csrf";
import { RUTA_INICIO } from "./enlace";
import { codigoDe } from "./fallas";
import { comprobarSesion } from "./sesionEncima";

// Si hay sesión, para decidir a qué pantalla va la persona (decisión del propietario del
// 05-11-2026, §2). Cada pantalla pregunta al montarse y no se guarda en caché: recién entrada,
// una respuesta vieja la mandaría de vuelta a /entrar. Sin conexión no se sabe, y se dice.

const ESTADO_SIN_SESION = 401;

export type EstadoSesion = "consultando" | "con_sesion" | "sin_sesion" | "sin_servicio";

interface Lectura {
  estado: EstadoSesion;
  correo: string | null;
  nombre: string | null;
}

const SIN_LEER: Lectura = { estado: "consultando", correo: null, nombre: null };

function useLectura(activa: boolean): Lectura {
  const [lectura, setLectura] = useState<Lectura>(SIN_LEER);

  useEffect(() => {
    // Lo leído no queda para quien entre después en este navegador.
    if (!activa) {
      setLectura(SIN_LEER);
      return;
    }
    let vigente = true;
    void api.GET("/api/v1/auth/sesion", { fetch: comprobarSesion }).then(
      ({ data, response }) => {
        if (!vigente) return;
        if (data !== undefined) setLectura({ estado: "con_sesion", correo: data.correo, nombre: data.nombre });
        else setLectura({ estado: response.status === ESTADO_SIN_SESION ? "sin_sesion" : "sin_servicio", correo: null, nombre: null });
      },
      () => {
        if (vigente) setLectura({ estado: "sin_servicio", correo: null, nombre: null });
      },
    );
    return () => {
      vigente = false;
    };
  }, [activa]);

  return lectura;
}

export function useSesion(activa = true): EstadoSesion {
  return useLectura(activa).estado;
}

/** La sesión con su nombre, para quien además lo muestra. */
export function useSesionConNombre(activa: boolean): Pick<Lectura, "estado" | "nombre"> {
  const { estado, nombre } = useLectura(activa);
  return { estado, nombre };
}

/** Terminó una acción que lleva al inicio, con el aviso del resultado. */
export function useIrAlInicio(): (aviso: string) => void {
  const navegar = useNavigate();
  return (aviso) => navegar(RUTA_INICIO, { replace: true, state: { aviso } });
}

/**
 * Cierra la sesión de este navegador y, como toda salida, borra las copias de los contactos
 * (H2b_7 §5). Devuelve null si salió bien y, si no, lo que hay que decirle a la persona.
 */
export async function salirDeLaCuenta(
  mensaje: (estado: number, codigo: string | undefined) => string,
): Promise<string | null> {
  try {
    const { error, response } = await api.POST("/api/v1/auth/salir", { headers: encabezadoCsrf() });
    // Un 401 es que ya estaba afuera: el resultado es el mismo.
    if (!response.ok && response.status !== ESTADO_SIN_SESION) return mensaje(response.status, codigoDe(error));
  } catch {
    // Sin respuesta: `mensaje` lo explica como conexión (estado 0, como en las otras páginas).
    return mensaje(0, undefined);
  }
  borrarCopias();
  return null;
}
