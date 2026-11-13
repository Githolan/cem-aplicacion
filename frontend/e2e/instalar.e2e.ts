import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { expect, type Page, test } from "@playwright/test";

import { AGENTE_IPHONE, simularPuntero, simularSesion, simularStandalone, sinFaltasDeAccesibilidad } from "./entorno";

const TITULO = "Instalar Consorcio en Mano en este dispositivo";
const BOTON = "Instalar la aplicación";

// Chrome solo dispara `beforeinstallprompt` fuera de incógnito y cuando juzga que hubo uso
// suficiente del sitio; esta opción de línea de comandos saltea esa heurística.
const SIN_HEURISTICA_DE_USO = "--bypass-app-banner-engagement-checks";

const TITULO_ENTRAR = "Entrar a Consorcio en Mano";

/**
 * Ofrece la instalación como lo haría Chromium, para decidir qué responde. Que el evento de verdad
 * llega lo prueba «Chromium ofrece beforeinstallprompt de verdad»; acá importa qué hace la página
 * con la elección, y la de Chromium no se puede contestar desde una prueba.
 */
async function ofrecerInstalacion(pagina: Page, respuesta: "accepted" | "dismissed" | "falla") {
  await expect(pagina.getByRole("heading", { level: 1 })).toHaveText(TITULO);
  await pagina.evaluate((eleccion) => {
    const evento = new Event("beforeinstallprompt", { cancelable: true });
    Object.assign(evento, {
      prompt: () => (eleccion === "falla" ? Promise.reject(new Error("el navegador no pudo")) : Promise.resolve()),
      userChoice: Promise.resolve({ outcome: eleccion }),
    });
    window.dispatchEvent(evento);
  }, respuesta);
}

test.describe("página /instalar", () => {
  test("Chromium ofrece beforeinstallprompt de verdad y aparece el botón propio", async ({
    playwright,
    baseURL,
  }, info) => {
    // Lanza su propio Chromium completo con perfil nuevo y espera a que el service worker se
    // active: con la PC cargada (CI con 8 navegadores) no entra en 30 s sin que nada esté mal
    // (medido el 17-10-2026). test.slow() triplica el plazo solo para esta prueba.
    test.slow();
    // Un perfil persistente y Chromium completo (no el headless shell): en incógnito el navegador
    // no ofrece instalar (Page.getInstallabilityErrors da "in-incognito"). El evento no se fabrica.
    // El perfil va en la carpeta temporal del sistema: con la ruta de test-results (lleva el
    // título de la prueba, con tildes) el service worker de Chromium no llega a activarse.
    const perfil = await mkdtemp(join(tmpdir(), "cem-e2e-perfil-"));
    const { viewport, userAgent, deviceScaleFactor, isMobile, hasTouch } = info.project.use;
    const contexto = await playwright.chromium.launchPersistentContext(perfil, {
      channel: "chromium",
      args: [SIN_HEURISTICA_DE_USO],
      viewport,
      userAgent,
      deviceScaleFactor,
      isMobile,
      hasTouch,
    });
    try {
      const pagina = await contexto.newPage();
      await pagina.goto(`${baseURL}/instalar`);
      // El navegador exige un service worker activo antes de ofrecer la instalación.
      await pagina.evaluate(() => navigator.serviceWorker.ready);
      await pagina.reload();
      await expect(pagina.getByRole("button", { name: BOTON })).toBeVisible();
      await expect(pagina.getByText("Tocar el botón y confirmar.")).toBeVisible();
      await sinFaltasDeAccesibilidad(pagina);
    } finally {
      await contexto.close();
      await rm(perfil, { recursive: true, force: true });
    }
  });

  test("sin beforeinstallprompt explica cómo instalar desde el menú", async ({ page }) => {
    await page.goto("/instalar");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(TITULO);
    await expect(page.getByText(/Tocar el menú \(los tres puntos\)/)).toBeVisible();
    await expect(page.locator("ol.pasos li")).toHaveCount(2);
    await expect(page.getByRole("button", { name: BOTON })).toHaveCount(0);
    await sinFaltasDeAccesibilidad(page);
  });

  test("en una computadora, con la oferta del navegador, habla de esta computadora", async ({ page }) => {
    await simularPuntero(page, true);
    await page.goto("/instalar");
    await ofrecerInstalacion(page, "accepted");

    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Instalar Consorcio en Mano en esta computadora");
    await sinFaltasDeAccesibilidad(page);
  });

  test("en modo standalone no hay nada que instalar: va al inicio, que no la enlaza", async ({ page }) => {
    await simularStandalone(page);
    await simularSesion(page, "con_sesion");
    await page.goto("/instalar");

    await expect(page.getByRole("heading", { level: 1 })).toHaveText("El consorcio, en la mano");
    expect(new URL(page.url()).pathname).toBe("/");
    await expect(page.getByRole("button", { name: BOTON })).toHaveCount(0);
    await expect(page.locator("ol.pasos")).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Instalar la aplicación en este dispositivo" })).toHaveCount(0);
    await sinFaltasDeAccesibilidad(page);
  });

  test("«Seguir sin instalar» lleva a /entrar", async ({ page }) => {
    await simularSesion(page, "sin_sesion");
    await page.goto("/instalar");
    await page.getByRole("link", { name: "Seguir sin instalar" }).click();

    await expect(page).toHaveURL(/\/entrar$/);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(TITULO_ENTRAR);
  });

  for (const respuesta of ["dismissed", "falla"] as const) {
    test(`si la instalación no se hace (${respuesta}), lo dice, sigue en /instalar y deja reintentar`, async ({ page }) => {
      await simularPuntero(page, false);
      await page.goto("/instalar");
      await ofrecerInstalacion(page, respuesta);
      await page.getByRole("button", { name: BOTON }).click();

      await expect(page.getByText(/No se instaló/)).toBeVisible();
      await expect(page).toHaveURL(/\/instalar$/);
      await expect(page.getByRole("heading", { level: 1 })).toHaveText(TITULO);
      await expect(page.getByRole("button", { name: "Reintentar" })).toBeVisible();
      await expect(page.locator("ol.pasos")).toHaveCount(0);
      await sinFaltasDeAccesibilidad(page);
    });
  }

  test("Reintentar recarga la página: sin el evento quedan los pasos del menú", async ({ page }) => {
    await simularPuntero(page, false);
    await page.goto("/instalar");
    await ofrecerInstalacion(page, "dismissed");
    await page.getByRole("button", { name: BOTON }).click();

    const recarga = page.waitForEvent("load");
    await page.getByRole("button", { name: "Reintentar" }).click();
    await recarga;

    await expect(page.getByText(/No se instaló/)).toHaveCount(0);
    await expect(page.getByText(/Tocar el menú/)).toBeVisible();
  });

  test("terminada la instalación sigue a /entrar con el aviso", async ({ page }) => {
    await simularSesion(page, "sin_sesion");
    await page.goto("/instalar");
    await ofrecerInstalacion(page, "accepted");
    await page.getByRole("button", { name: BOTON }).click();

    // Control: aceptó pero el navegador todavía no terminó; no se avanza ni hay error.
    await expect(page.getByText("Instalando…")).toBeVisible();
    await expect(page.getByText(/No se instaló/)).toHaveCount(0);
    await expect(page).toHaveURL(/\/instalar$/);

    await page.evaluate(() => window.dispatchEvent(new Event("appinstalled")));

    await expect(page).toHaveURL(/\/entrar$/);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(TITULO_ENTRAR);
    await expect(page.getByText(/Ya está instalada. Se puede seguir acá/)).toBeVisible();
    await sinFaltasDeAccesibilidad(page);
  });
});

test.describe("página /instalar en iPhone", () => {
  // Safari en iOS no tiene beforeinstallprompt; se emula su user agent en un viewport de teléfono.
  test.use({ userAgent: AGENTE_IPHONE, viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

  test("explica Safari → Compartir → Agregar a inicio", async ({ page }, info) => {
    test.skip(info.project.name !== "movil", "el iPhone se emula una sola vez, en el proyecto móvil");
    await page.goto("/instalar");
    const pasos = page.locator("ol.pasos li");
    await expect(pasos).toHaveCount(3);
    await expect(pasos.nth(0)).toContainText("Safari");
    await expect(pasos.nth(1)).toContainText("Compartir");
    await expect(pasos.nth(2)).toContainText("Agregar a inicio");
    await expect(page.getByText(/Tocar el menú/)).toHaveCount(0);
    await sinFaltasDeAccesibilidad(page);
  });
});
