import { useCallback, useSyncExternalStore } from "react";
import { useNavigate } from "react-router";

import { guardarAdondeIba } from "./adondeIba";
import { borrarCopias } from "./copiaContactos";
import { ENCABEZADO_CSRF, encabezadoCsrf } from "./csrf";
import { RUTA_ENTRAR, RUTA_INICIO } from "./enlace";

// Entrar encima (decisión del propietario del 05-11-2026, §10; rediseño de pagos, §12): si la sesión
// vence, la entrada se pide sobre la misma pantalla, que sigue montada con lo escrito.
//
// Todo pedido pasa por `pedirConSesion`, así que ninguna pantalla tiene que acordarse. Un 401 de una
// sesión que este navegador conocía, o una sesión que ya no sirve para el acto, abre la hoja. Una
// pedido, lectura o escritura, espera a que la persona entre y se repite solo: la pantalla nunca se
// entera (decisión del propietario del 18-11-2026, pregunta 11; FL-ACC-01). El servidor no lo hizo
// (respondió 401 o 403), así que repetirlo no lo duplica; va con la misma clave de idempotencia y con
// el CSRF de la sesión nueva.
//
// La hoja dice por qué se abrió (FL-ACC-02): con un 401 la sesión venció; con un 403 de entrada
// reciente la sesión sigue abierta, pero el acto pide haber entrado hace poco.
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

/** Por qué se pide entrar: la sesión venció (401) o el acto pide una entrada reciente (403). */
export type MotivoEncima = "vencida" | "reciente";

export type EstadoEncima = { pidiendo: boolean; correo: string; recuperada: boolean; motivo: MotivoEncima };

let activa = false;
let conocida: Sesion | null = null;
let pedido: Pedido | null = null;
const SIN_PEDIDO: EstadoEncima = { pidiendo: false, correo: "", recuperada: false, motivo: "vencida" };
let foto: EstadoEncima = SIN_PEDIDO;
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
    foto = SIN_PEDIDO;
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

function abrir(motivo: MotivoEncima) {
  if (pedido !== null) return;
  let soltar = () => {};
  const seguir = new Promise<void>((resolver) => {
    soltar = resolver;
  });
  pedido = { antes: conocida, seguir, soltar };
  publicar({ pidiendo: true, correo: conocida?.correo ?? "", recuperada: false, motivo });
}

/** FL-CTA-01 (tanda 9): abre la hoja de entrar de nuevo sin esperar un 403, para un acto que ya se
 * sabe que pide una entrada reciente. Se resuelve al entrar con la misma persona. */
export function pedirEntrarDeNuevo(): Promise<void> {
  abrir("reciente");
  return (pedido as Pedido).seguir;
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

/** Si la respuesta pide entrar de nuevo, y por qué; null si no. */
async function pideEntrar(ruta: string, respuesta: Response): Promise<MotivoEncima | null> {
  if (respuesta.status === ESTADO_SIN_SESION) {
    if (conocida === null || RUTAS_DE_ENTRADA.has(ruta)) return null;
    return (await cuerpoDe(respuesta)).codigo === "no_autenticado" ? "vencida" : null;
  }
  if (respuesta.status !== ESTADO_SIN_PERMISO) return null;
  const { codigo, detalle } = await cuerpoDe(respuesta);
  if (codigo === "sesion_reciente_requerida") return "reciente";
  return codigo === "reautenticacion_requerida" && (detalle?.falta ?? FALTA_ENTRAR) === FALTA_ENTRAR ? "reciente" : null;
}

/** El mismo pedido para la sesión nueva: el CSRF está ligado a la sesión, así que va el de ahora. */
function paraLaSesionNueva(pedir: Request): Request {
  if (!pedir.headers.has(ENCABEZADO_CSRF)) return pedir;
  const encabezados = new Headers(pedir.headers);
  const csrf = encabezadoCsrf()[ENCABEZADO_CSRF];
  if (csrf === undefined) encabezados.delete(ENCABEZADO_CSRF);
  else encabezados.set(ENCABEZADO_CSRF, csrf);
  return new Request(pedir, { headers: encabezados });
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
  // El cuerpo se lee al mandarlo: la copia para repetirlo se hace antes. Se lee entero primero, no con
  // `clone()`: la copia de un cuerpo es un flujo, y el navegador lo manda como flujo (sin poder verlo
  // las herramientas del navegador ni las pruebas e2e). Los cuerpos de la API son JSON chicos o un
  // adjunto de hasta 20 MB, que queda en memoria mientras dura el pedido.
  const cuerpo = activa && pedir.body !== null ? await pedir.arrayBuffer() : null;
  const mandar = cuerpo === null ? pedir : new Request(pedir, { body: cuerpo });
  const repetible = activa ? (cuerpo === null ? pedir.clone() : new Request(pedir, { body: cuerpo })) : null;
  const respuesta = await globalThis.fetch(mandar);
  if (repetible === null) return respuesta;
  const ruta = new URL(pedir.url).pathname;
  if (ruta === RUTA_SESION) await anotarSesion(respuesta);
  // Si salir falló, la sesión sigue y puede vencer con algo escrito.
  if (ruta === RUTA_SALIR && (respuesta.ok || respuesta.status === ESTADO_SIN_SESION)) conocida = null;
  const motivo = await pideEntrar(ruta, respuesta);
  if (motivo === null) return respuesta;
  abrir(motivo);
  if (pedido === null) return respuesta;
  await pedido.seguir;
  return pedirConSesion(paraLaSesionNueva(repetible));
}
