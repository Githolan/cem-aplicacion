import { useCallback, useSyncExternalStore } from "react";
import { useNavigate } from "react-router";

import { guardarAdondeIba } from "./adondeIba";
import { borrarCopias } from "./copiaContactos";
import { RUTA_ENTRAR, RUTA_INICIO } from "./enlace";

// Entrar encima (decisión del propietario del 05-11-2026, §10; rediseño de pagos, §12): si la sesión
// vence, la entrada se pide sobre la misma pantalla, que sigue montada con lo escrito.
//
// Todo pedido pasa por `pedirConSesion`, así que ninguna pantalla tiene que acordarse. Un 401 de una
// sesión que este navegador conocía, o una sesión de más de 12 horas para el acto, abre la hoja. Una
// lectura espera a que la persona entre y se repite sola: la pantalla nunca se entera. Un pedido que
// escribe devuelve la respuesta y no se manda de nuevo: lo repite la persona, con la misma clave de
// idempotencia que ya tenía su pantalla.
//
// Quien entra puede ser otra persona. Lo que hay en la pantalla no es suyo: si cambió, la aplicación
// se carga de cero en el inicio, sin nada de la anterior.

const RUTA_SESION = "/api/v1/auth/sesion";
const RUTA_SALIR = "/api/v1/auth/salir";
/** Acá un 401 es la respuesta de entrar, no una sesión vencida. */
const RUTAS_DE_ENTRADA: ReadonlySet<string> = new Set([
  "/api/v1/auth/canje",
  "/api/v1/auth/codigo",
  "/api/v1/auth/contrasena/entrar",
  "/api/v1/auth/contrasena/segundo-factor",
  RUTA_SALIR,
]);
const ESTADO_SIN_SESION = 401;
const ESTADO_SIN_PERMISO = 403;
/** Las faltas de `reautenticacion_requerida` que se resuelven acá mismo no piden entrar de nuevo. */
const FALTA_ENTRAR = "entrar_de_nuevo";

type Sesion = { persona: string; correo: string; id: string };
type Pedido = { antes: Sesion | null; seguir: Promise<void>; soltar: () => void };
type Cuerpo = { codigo?: string; detalle?: { falta?: string } };

export type EstadoEncima = { pidiendo: boolean; correo: string; recuperada: boolean };

let activa = false;
let conocida: Sesion | null = null;
let pedido: Pedido | null = null;
let foto: EstadoEncima = { pidiendo: false, correo: "", recuperada: false };
const oyentes = new Set<() => void>();

/** Las salidas que recargan la página, aparte para poder observarlas en las pruebas. */
export const salidas = {
  aInicio: () => window.location.replace(RUTA_INICIO),
  aEntrar: () => window.location.replace(RUTA_ENTRAR),
};

function publicar(cambio: Partial<EstadoEncima>) {
  foto = { ...foto, ...cambio };
  oyentes.forEach((oyente) => oyente());
}

/** Empieza a mirar los pedidos. Lo hace `main.tsx`, antes de la primera pantalla. */
export function activarEntrarEncima(): () => void {
  activa = true;
  return () => {
    activa = false;
    conocida = null;
    pedido = null;
    foto = { pidiendo: false, correo: "", recuperada: false };
  };
}

function suscribir(oyente: () => void) {
  oyentes.add(oyente);
  return () => {
    oyentes.delete(oyente);
  };
}

export function useEstadoEncima(): EstadoEncima {
  return useSyncExternalStore(suscribir, () => foto);
}

/** Si hay una hoja pidiendo entrar: entonces nada se va a /entrar. */
export const pidiendoEntrar = () => pedido !== null;

/** Si todavía no se sabe de quién es la sesión de este navegador. */
export const faltaConocerSesion = () => activa && conocida === null;

/** En /entrar ya no hay nada que conservar: lo que siga no es una sesión que venció trabajando. */
export const olvidarSesion = () => {
  if (pedido === null) conocida = null;
};

export const olvidarRecuperada = () => {
  if (foto.recuperada) publicar({ recuperada: false });
};

/** Salir a /entrar por un 401, salvo que la hoja ya lo esté resolviendo sobre esta pantalla. La
 * sesión no se conocía: no hay hoja que conserve la pantalla, así que se guarda adónde iba. */
export function useAEntrar(antes?: () => void): () => void {
  const navegar = useNavigate();
  return useCallback(() => {
    if (pidiendoEntrar()) return;
    antes?.();
    guardarAdondeIba();
    navegar(RUTA_ENTRAR, { replace: true });
  }, [antes, navegar]);
}

function abrir() {
  if (pedido !== null) return;
  let soltar = () => {};
  const seguir = new Promise<void>((resolver) => {
    soltar = resolver;
  });
  pedido = { antes: conocida, seguir, soltar };
  publicar({ pidiendo: true, correo: conocida?.correo ?? "", recuperada: false });
}

function terminar({ antes, soltar }: Pedido, nueva: Sesion) {
  if (antes?.persona !== nueva.persona) {
    borrarCopias();
    salidas.aInicio();
    return;
  }
  pedido = null;
  publicar({ pidiendo: false, recuperada: true });
  soltar();
}

/** Un 401 acá no la olvida: una pantalla con algo escrito puede enterarse así, al volver a la ventana. */
async function anotarSesion(respuesta: Response) {
  if (!respuesta.ok) return;
  const { persona_id, correo, sesion_id } = (await respuesta.clone().json()) as {
    persona_id: string;
    correo: string;
    sesion_id: string;
  };
  conocida = { persona: persona_id, correo, id: sesion_id };
  if (pedido !== null && sesion_id !== pedido.antes?.id) terminar(pedido, conocida);
}

async function cuerpoDe(respuesta: Response): Promise<Cuerpo> {
  // Un error del borde (Cloudflare, nginx) no trae el cuerpo uniforme: no es de la aplicación.
  return (await respuesta.clone().json().catch(() => ({}))) as Cuerpo;
}

async function pideEntrar(ruta: string, respuesta: Response): Promise<boolean> {
  if (respuesta.status === ESTADO_SIN_SESION) {
    if (conocida === null || RUTAS_DE_ENTRADA.has(ruta)) return false;
    return (await cuerpoDe(respuesta)).codigo === "no_autenticado";
  }
  if (respuesta.status !== ESTADO_SIN_PERMISO) return false;
  const { codigo, detalle } = await cuerpoDe(respuesta);
  if (codigo === "sesion_reciente_requerida") return true;
  return codigo === "reautenticacion_requerida" && (detalle?.falta ?? FALTA_ENTRAR) === FALTA_ENTRAR;
}

/**
 * El `fetch` de quien solo pregunta si hay sesión: la hoja, el inicio y /entrar. Anota lo que responde
 * sin abrir la hoja ni esperarla: la hoja se cierra con esta respuesta, y en esas pantallas un 401 no
 * deja nada escrito atrás.
 */
export async function comprobarSesion(pedir: Request): Promise<Response> {
  const respuesta = await globalThis.fetch(pedir);
  if (activa) await anotarSesion(respuesta);
  return respuesta;
}

/** El `fetch` del cliente de la API. */
export async function pedirConSesion(pedir: Request): Promise<Response> {
  const repetible = activa && pedir.method === "GET" ? pedir.clone() : null;
  const respuesta = await globalThis.fetch(pedir);
  if (!activa) return respuesta;
  const ruta = new URL(pedir.url).pathname;
  if (ruta === RUTA_SESION) await anotarSesion(respuesta);
  // Si salir falló, la sesión sigue y puede vencer con algo escrito.
  if (ruta === RUTA_SALIR && (respuesta.ok || respuesta.status === ESTADO_SIN_SESION)) conocida = null;
  if (!(await pideEntrar(ruta, respuesta))) return respuesta;
  abrir();
  if (repetible === null || pedido === null) return respuesta;
  await pedido.seguir;
  return pedirConSesion(repetible);
}
