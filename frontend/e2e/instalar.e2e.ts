import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

import { AGENTE_IPHONE, simularStandalone, sinFaltasDeAccesibilidad } from "./entorno";

const TITULO = "Instalá Consorcio en Mano en tu teléfono";
const BOTON = "Instalar la aplicación";

// Chrome solo dispara `beforeinstallprompt` fuera de incógnito y cuando juzga que hubo uso
// suficiente del sitio; esta opción de línea de comandos saltea esa heurística.
const SIN_HEURISTICA_DE_USO = "--bypass-app-banner-engagement-checks";

test.describe("página /instalar", () => {
  test("Chromium ofrece beforeinstallprompt de verdad y aparece el botón propio", async ({
    playwright,
    baseURL,
  }, info) => {
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
      await expect(pagina.getByText("Tocá el botón y confirmá.")).toBeVisible();
      await sinFaltasDeAccesibilidad(pagina);
    } finally {
      await contexto.close();
      await rm(perfil, { recursive: true, force: true });
    }
  });

  test("sin beforeinstallprompt explica cómo instalar desde el menú", async ({ page }) => {
    await page.goto("/instalar");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(TITULO);
    await expect(page.getByText(/Tocá el menú \(los tres puntos\)/)).toBeVisible();
    await expect(page.getByRole("button", { name: BOTON })).toHaveCount(0);
    await sinFaltasDeAccesibilidad(page);
  });

  test("en modo standalone avisa que ya está instalada y el inicio no la enlaza", async ({ page }) => {
    await simularStandalone(page);
    await page.goto("/instalar");
    await expect(page.getByText("Ya está instalada.", { exact: false })).toBeVisible();
    await expect(page.getByRole("button", { name: BOTON })).toHaveCount(0);
    await expect(page.locator("ol.pasos")).toHaveCount(0);
    await sinFaltasDeAccesibilidad(page);

    await page.getByRole("link", { name: "Volver al inicio" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Tu consorcio, en tu mano");
    await expect(page.getByRole("link", { name: "Instalá la aplicación en tu teléfono" })).toHaveCount(0);
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
    await expect(page.getByText(/Tocá el menú/)).toHaveCount(0);
    await sinFaltasDeAccesibilidad(page);
  });
});
