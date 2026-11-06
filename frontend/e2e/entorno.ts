import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, type Route } from "@playwright/test";

import { accionesDeLasPoliticas } from "../src/accionesDePrueba";

// Datos de la corrida que deja scripts/probar-extremo-a-extremo.sh. Sin ellos no hay contra qué
// probar: se falla en vez de suponer una URL.
function requerida(nombre: string): string {
  const valor = process.env[nombre];
  if (!valor) throw new Error(`falta ${nombre}: correr con "pnpm e2e" (scripts/probar-extremo-a-extremo.sh)`);
  return valor;
}

/** El mismo cem-frontend apuntando a un backend que no existe: /health da 502. */
export const URL_SIN_BACKEND = requerida("CEM_E2E_URL_SIN_BACKEND");
/** SHA que el cem-backend de la corrida informa en /health (CEM_SHA_IMAGEN). */
export const SHA_BACKEND = requerida("CEM_E2E_SHA");

// La página muestra los primeros 7 caracteres de la SHA, como git.
export const LARGO_SHA_CORTA = 7;

// El backend limita /api/v1/auth/ a 20 pedidos por minuto por IP (backend/app/limite.py), y toda
// la corrida sale de la misma IP: la suite de main ya pasa cerca de ese cupo (medido el
// 16-10-2026: 69 pruebas en 1,0 min, y con 8 pedidos de más fallaron 7 por 429). Las pruebas que
// necesitan más pedidos reales se presentan como otro cliente, como en producción: nginx toma la IP
// de CF-Connecting-IP (nginx/cem-frontend.conf.template). Direcciones de documentación (RFC 5737).
export function otroCliente(n: number): { extraHTTPHeaders: Record<string, string> } {
  return { extraHTTPHeaders: { "CF-Connecting-IP": `198.51.100.${n}` } };
}

const RUTA_SESION = "**/api/v1/auth/sesion";
const RUTA_EDIFICIOS = "**/api/v1/me/consorcios";
const RUTA_MIS_INVITACIONES = "**/api/v1/me/invitaciones";
const RUTA_MIS_ACCIONES = "**/api/v1/consorcios/*/mis-acciones";
const RUTA_POR_ACLARAR = "**/api/v1/pagos-por-aclarar";

const SESION_SIMULADA = {
  persona_id: "99999999-9999-4999-8999-999999999999",
  nombre: "Ana Pérez",
  correo: "ana@ejemplo.com",
  sesion_id: "11111111-1111-4111-8111-111111111111",
  vence_en: "2026-11-15T20:00:00Z",
};

export type Sesion = "con_sesion" | "sin_sesion";

/**
 * Lo que `/` y `Exige` piden para saber qué mostrar, para las pruebas que simulan toda la API en un
 * `page.route`: lo responde (todo el catálogo, salvo que digan otra cosa) o devuelve null. Sin esto
 * el pedido se corta, la consola lo informa y el inicio no ofrece ningún enlace.
 */
export function simularPermisos(ruta: Route, camino: string, acciones: readonly string[] = accionesDeLasPoliticas()) {
  if (camino.endsWith("/mis-acciones")) return ruta.fulfill({ json: { acciones } });
  if (camino === "/api/v1/pagos-por-aclarar" || camino === "/api/v1/me/invitaciones")
    return ruta.fulfill({ json: { elementos: [] } });
  return null;
}

/**
 * Responde `GET /api/v1/auth/sesion` desde el navegador, y con sesión también lo que pide el inicio
 * (sin edificios, si la prueba no simula otros). El backend de la corrida no tiene base de datos:
 * sin esto `/` lleva siempre a /entrar, y cada consulta real gasta el cupo de /api/v1/auth/ (ver
 * otroCliente). `sesion` puede ser una función, para cuando cambia a mitad de la prueba. Va en el
 * contexto porque page.route no ve lo que pasa por un service worker. Con "sin_sesion" la consola
 * informa el 401 del recurso, igual que con el backend de verdad.
 */
export async function simularSesion(pagina: Page, sesion: Sesion | (() => Sesion)) {
  const vigente = () => (typeof sesion === "function" ? sesion() : sesion);
  await pagina.context().route(RUTA_SESION, (ruta) => {
    if (vigente() === "con_sesion") return ruta.fulfill({ json: SESION_SIMULADA });
    return ruta.fulfill({ status: 401, json: { codigo: "no_autenticado", mensaje: "x", id_correlacion: "c" } });
  });
  const conSesion = (json: unknown) => (ruta: Route) => (vigente() === "con_sesion" ? ruta.fulfill({ json }) : ruta.fallback());
  await pagina.context().route(RUTA_EDIFICIOS, conSesion([]));
  await pagina.context().route(RUTA_MIS_INVITACIONES, conSesion({ elementos: [] }));
  await pagina.context().route(RUTA_MIS_ACCIONES, conSesion({ acciones: accionesDeLasPoliticas() }));
  await pagina.context().route(RUTA_POR_ACLARAR, conSesion({ elementos: [] }));
}

/** Deja de simular: la consulta vuelve a salir a la red (o a fallar, si la prueba la cortó). */
export async function dejarDeSimularSesion(pagina: Page) {
  for (const ruta of [RUTA_SESION, RUTA_EDIFICIOS, RUTA_MIS_INVITACIONES, RUTA_MIS_ACCIONES, RUTA_POR_ACLARAR])
    await pagina.context().unroute(ruta);
}

export const AGENTE_IPHONE =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1";

// Documento 13, §14 y RNF07: WCAG 2.2 AA. axe solo evalúa las reglas con estas etiquetas.
const ETIQUETAS_WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

export async function sinFaltasDeAccesibilidad(pagina: Page) {
  const resultado = await new AxeBuilder({ page: pagina }).withTags(ETIQUETAS_WCAG).analyze();
  const faltas = resultado.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
  expect(faltas).toEqual([]);
}

// Chromium no emula `display-mode` (ni con Emulation.setEmulatedMedia de CDP: medido con Chromium
// 143, la consulta sigue en false), y una instalación de verdad (CDP PWA.install) crea accesos
// directos en el sistema. Así que solo esa consulta se responde desde un script de inicio; todas
// las demás siguen yendo al matchMedia real del navegador.
export async function simularStandalone(pagina: Page) {
  await pagina.addInitScript(() => {
    const original = window.matchMedia.bind(window);
    window.matchMedia = (consulta: string) => {
      const real = original(consulta);
      if (consulta.replace(/\s/g, "") !== "(display-mode:standalone)") return real;
      return Object.create(real, { matches: { value: true } }) as MediaQueryList;
    };
  });
}

// El enlace y el título de la instalación cambian en una computadora (`pointer: fine`). Se fija
// acá para que la prueba no dependa de qué informa cada proyecto de Playwright sin pantalla.
export async function simularPuntero(pagina: Page, fino: boolean) {
  await pagina.addInitScript((respuesta) => {
    const original = window.matchMedia.bind(window);
    window.matchMedia = (consulta: string) => {
      const real = original(consulta);
      if (consulta.replace(/\s/g, "") !== "(pointer:fine)") return real;
      return Object.create(real, { matches: { value: respuesta } }) as MediaQueryList;
    };
  }, fino);
}
