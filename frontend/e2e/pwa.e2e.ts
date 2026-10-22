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

  test("una navegación a /entrar nunca sale de la caché del service worker", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => navigator.serviceWorker.ready);
    // Control: con el service worker al mando, las demás rutas sí salen de su caché.
    const inicio = await page.reload();
    expect(inicio?.fromServiceWorker()).toBe(true);
    const otra = await page.goto("/una-ruta-cualquiera");
    expect(otra?.fromServiceWorker()).toBe(true);

    for (const ruta of ["/entrar", "/entrar?x=1", "/entrar/"]) {
      const respuesta = await page.goto(ruta);
      expect(respuesta?.fromServiceWorker(), ruta).toBe(false);
      expect(respuesta?.status(), ruta).toBe(200);
      expect(respuesta?.headers()["cache-control"], ruta).toBe("no-cache");
    }
    // /entrarx no es /entrar: sigue saliendo de la caché.
    expect((await page.goto("/entrarx"))?.fromServiceWorker()).toBe(true);
  });

  test("con red, una navegación pasa por la red aunque el service worker esté al mando", async ({ page, context }) => {
    await page.goto("/");
    await page.evaluate(() => navigator.serviceWorker.ready);
    await page.reload();
    expect(await page.evaluate(() => navigator.serviceWorker.controller !== null)).toBe(true);

    const deLaRed: string[] = [];
    context.on("request", (pedido) => {
      if (pedido.serviceWorker() !== null) deLaRed.push(new URL(pedido.url()).pathname);
    });
    const respuesta = await page.goto("/una-ruta-cualquiera");
    // La respondió el service worker, pero yendo a buscarla a la red, no a su caché.
    expect(respuesta?.fromServiceWorker()).toBe(true);
    expect(deLaRed).toContain("/una-ruta-cualquiera");
  });

  test("una redirección de la navegación a otro origen llega al navegador (Cloudflare Access)", async ({
    page,
    context,
  }) => {
    await page.goto("/");
    await page.evaluate(() => navigator.serviceWorker.ready);
    await page.reload();
    expect(await page.evaluate(() => navigator.serviceWorker.controller !== null)).toBe(true);

    // Como Access con la sesión vencida: la página responde una redirección a su login.
    const login = "https://acceso.ejemplo.invalid/cdn-cgi/access/login";
    await context.route(
      (url) => url.pathname === "/" && url.origin === new URL(page.url()).origin,
      (ruta) => ruta.fulfill({ status: 302, headers: { location: login } }),
    );
    await context.route(`${login}**`, (ruta) =>
      ruta.fulfill({ status: 200, contentType: "text/html", body: "<h1>Login de Access</h1>" }),
    );
    await page.reload();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Login de Access");
    expect(page.url()).toBe(login);
  });

  test("sin conexión, una ruta que nunca se abrió carga la aplicación de la precaché", async ({ page, context }) => {
    await page.goto("/");
    await page.evaluate(() => navigator.serviceWorker.ready);
    await page.reload();

    await context.setOffline(true);
    await page.goto("/ruta-nunca-abierta");
    await expect(page.getByRole("heading", { name: "No encontramos esa página" })).toBeVisible();
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
