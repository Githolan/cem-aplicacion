import { describe, expect, it } from "vitest";

import { NAVEGACION_ENTRAR, NAVEGACION_INVITACION, NAVEGACION_INVITACION_MANDATO } from "./enlace";
import { esNavegacionDeLaApp } from "./swNavegacion";

// Las rutas que nunca atiende el service worker, desde sus constantes: la función de
// swNavegacion.ts las copia a mano porque Workbox la serializa sin lo que importa.
const EXCLUIDAS = [/^\/api\//, /^\/health/, NAVEGACION_ENTRAR, NAVEGACION_INVITACION, NAVEGACION_INVITACION_MANDATO];

const RUTAS = [
  "/",
  "/mandatos",
  "/una-ruta-cualquiera",
  "/api/v1/me/consorcios",
  "/apix",
  "/health",
  "/healthz",
  "/entrar",
  "/entrar/",
  "/entrar?x=1",
  "/entrarx",
  "/invitacion",
  "/invitacion?x=1",
  "/invitacionx",
  "/invitacion-mandato",
  "/invitacion-mandato/",
  "/invitacion-mandatox",
];

const pedido = (modo: RequestMode) => ({ mode: modo }) as Request;

describe("esNavegacionDeLaApp", () => {
  it.each(RUTAS)("%s: la atiende si y solo si no es una ruta excluida", (ruta) => {
    const url = new URL(ruta, "https://cem.ejemplo.com");
    const excluida = EXCLUIDAS.some((r) => r.test(url.pathname + url.search));
    expect(esNavegacionDeLaApp({ request: pedido("navigate"), url })).toBe(!excluida);
  });

  it("el control tiene de los dos casos", () => {
    const atendidas = RUTAS.filter((r) => esNavegacionDeLaApp({ request: pedido("navigate"), url: new URL(r, "https://x") }));
    expect(atendidas).toEqual(["/", "/mandatos", "/una-ruta-cualquiera", "/apix", "/entrarx", "/invitacionx", "/invitacion-mandatox"]);
  });

  it("un pedido que no es una navegación nunca lo atiende", () => {
    expect(esNavegacionDeLaApp({ request: pedido("cors"), url: new URL("https://x/") })).toBe(false);
    expect(esNavegacionDeLaApp({ request: pedido("no-cors"), url: new URL("https://x/mandatos") })).toBe(false);
  });

  it("se puede copiar como texto: no nombra nada de afuera", () => {
    const texto = esNavegacionDeLaApp.toString();
    for (const nombre of ["NAVEGACION_", "RUTA_", "import"]) expect(texto).not.toContain(nombre);
  });
});
