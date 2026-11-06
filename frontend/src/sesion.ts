import { useEffect, useState } from "react";
import { useNavigate } from "react-router";

import { api } from "./api/cliente";
import { borrarCopias } from "./copiaContactos";
import { encabezadoCsrf } from "./csrf";
import { RUTA_INICIO, VIGENCIA_ENLACE_MS } from "./enlace";
import { codigoDe } from "./fallas";

// Si hay sesión, para decidir a qué pantalla va la persona (decisión del propietario del
// 05-11-2026, §2). Cada pantalla pregunta al montarse y no se guarda en caché: recién entrada,
// una respuesta vieja la mandaría de vuelta a /entrar. Sin conexión no se sabe, y se dice.

const ESTADO_SIN_SESION = 401;

// Quien vuelve a entrar por seguridad sigue en la pantalla de la que salió. Va en localStorage y no
// en sessionStorage porque el enlace del correo se abre en otra pestaña, que no ve el de esta. Es
// solo una ruta, sin datos de la persona, y vale lo que vale el enlace.
const CLAVE_ORIGEN = "cem.entrar.origen";

export type EstadoSesion = "consultando" | "con_sesion" | "sin_sesion" | "sin_servicio";

interface Lectura {
  estado: EstadoSesion;
  correo: string | null;
}

interface Origen {
  ruta: string;
  /** Milisegundos desde la época: después de esto el origen ya no se usa. */
  venceEn: number;
}

function useLectura(activa: boolean): Lectura {
  const [lectura, setLectura] = useState<Lectura>({ estado: "consultando", correo: null });

  useEffect(() => {
    if (!activa) return;
    let vigente = true;
    void api.GET("/api/v1/auth/sesion").then(
      ({ data, response }) => {
        if (!vigente) return;
        if (data !== undefined) setLectura({ estado: "con_sesion", correo: data.correo });
        else setLectura({ estado: response.status === ESTADO_SIN_SESION ? "sin_sesion" : "sin_servicio", correo: null });
      },
      () => {
        if (vigente) setLectura({ estado: "sin_servicio", correo: null });
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

/** El correo de la sesión abierta; null mientras no se sabe o si no hay sesión. */
export function useCorreoDeSesion(activa: boolean): string | null {
  return useLectura(activa).correo;
}

export function recordarOrigen(ruta: string, ahora: number): void {
  const origen: Origen = { ruta, venceEn: ahora + VIGENCIA_ENLACE_MS };
  try {
    window.localStorage.setItem(CLAVE_ORIGEN, JSON.stringify(origen));
  } catch {
    // Safari privado o datos del sitio bloqueados: sin dónde guardarlo, la entrada termina en el inicio.
  }
}

function leerYBorrarOrigen(): string | null {
  try {
    const texto = window.localStorage.getItem(CLAVE_ORIGEN);
    window.localStorage.removeItem(CLAVE_ORIGEN);
    return texto;
  } catch {
    // Sin acceso al almacenamiento no pudo guardarse ningún origen.
    return null;
  }
}

function tomarOrigen(ahora: number): string | null {
  const texto = leerYBorrarOrigen();
  if (texto === null) return null;
  const { ruta, venceEn } = JSON.parse(texto) as Origen;
  return venceEn > ahora ? ruta : null;
}

/** Terminó una acción que lleva al inicio, con el aviso del resultado. */
export function useIrAlInicio(): (aviso: string) => void {
  const navegar = useNavigate();
  return (aviso) => navegar(RUTA_INICIO, { replace: true, state: { aviso } });
}

/** Terminó la entrada: la pantalla siguiente es el inicio, o la de origen si volvió a entrar. */
export function useTerminarEntrada(): (aviso: string) => void {
  const navegar = useNavigate();
  return (aviso) => navegar(tomarOrigen(Date.now()) ?? RUTA_INICIO, { replace: true, state: { aviso } });
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
