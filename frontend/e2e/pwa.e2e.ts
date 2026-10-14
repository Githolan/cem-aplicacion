import { expect, test } from "@playwright/test";

import type { ManifestOptions } from "vite-plugin-pwa";

test.describe("PWA servida por la imagen", () => {
  test("el manifiesto enlazado es instalable y sus iconos existen", async ({ page, request }) => {
    await page.goto("/");
    const href = await page.locator('link[rel="manifest"]').getAttribute("href");
    expect(href).toBeTruthy();

    const respuesta = await request.get(new URL(href!, page.url()).toString());
    expect(respuesta.status()).toBe(200);
    expect(respuesta.headers()["content-type"]).toBe("application/manifest+json");
    const manifiesto = (await respuesta.json()) as ManifestOptions;
    expect(manifiesto.display).toBe("standalone");
    expect(manifiesto.start_url).toBe("/");
    expect(manifiesto.icons.some((i) => i.purpose === "maskable")).toBe(true);
    for (const icono of manifiesto.icons) {
      const imagen = await request.get(`/${icono.src}`);
      expect(imagen.status(), icono.src).toBe(200);
      expect(imagen.headers()["content-type"], icono.src).toBe(icono.type);
    }
  });

  test("el service worker se registra, controla la página y Chromium la da por instalable", async ({
    page,
  }) => {
    await page.goto("/");
    const registro = await page.evaluate(async () => {
      const r = await navigator.serviceWorker.ready;
      return { alcance: r.scope, guion: r.active?.scriptURL ?? "" };
    });
    expect(new URL(registro.alcance).pathname).toBe("/");
    expect(new URL(registro.guion).pathname).toBe("/sw.js");

    await page.reload();
    expect(await page.evaluate(() => navigator.serviceWorker.controller !== null)).toBe(true);

    // El mismo chequeo que hace Chrome antes de ofrecer "Instalar": manifiesto, iconos y SW.
    const cdp = await page.context().newCDPSession(page);
    const { installabilityErrors } = await cdp.send("Page.getInstallabilityErrors");
    expect(installabilityErrors).toEqual([]);
  });

  test("sin conexión el shell carga desde caché y /health no sale de caché", async ({ page, context }) => {
    await page.goto("/");
    await page.evaluate(() => navigator.serviceWorker.ready);
    await page.reload();
    await expect(page.getByText("El servicio funciona.")).toBeVisible();

    await context.setOffline(true);
    await page.reload();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Tu consorcio, en tu mano");
    await expect(page.getByText(/No pudimos contactar al servicio/)).toBeVisible();
  });
});
