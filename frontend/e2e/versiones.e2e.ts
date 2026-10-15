import { expect, test, type Page } from "@playwright/test";

import { sinFaltasDeAccesibilidad } from "./entorno";
import {
  IMAGEN_ACTUAL,
  IMAGEN_ANTERIOR,
  IMAGEN_SIGUIENTE,
  bajarTodos,
  levantar,
  reemplazar,
} from "./versiones";

// El navegador tiene en caché una versión y el servidor ya sirve otra (medido en staging el
// 14-10-2026: con la versión anterior a /entrar en caché, el enlace del correo terminaba en "No
// encontramos esa página" y el token vencía sin canjearse). Cada prueba levanta sus propios
// contenedores en un origen propio y los reemplaza en el mismo puerto.

const TOKEN = "Z".repeat(43);
const RUTA_CANJE = "/api/v1/auth/canje";
const CLAVE_GUARDADO = "cem.entrar.enlace";
// Para afirmar que algo NO pasa (una recarga sin pedirla), hace falta dejarle tiempo a que pase.
const ESPERA_NEGATIVA_MS = 3_000;

async function instalarServiceWorker(pagina: Page, origen: string) {
  await pagina.goto(`${origen}/`);
  await pagina.evaluate(() => navigator.serviceWorker.ready);
  await pagina.reload();
  expect(await pagina.evaluate(() => navigator.serviceWorker.controller !== null)).toBe(true);
}

function marcarPestana(pagina: Page) {
  return pagina.evaluate(() => {
    (window as unknown as { marca: number }).marca = 1;
  });
}

function sigueSinRecargar(pagina: Page) {
  return pagina.evaluate(() => (window as unknown as { marca?: number }).marca === 1);
}

test.describe("versiones en caché", () => {
  test("con la versión anterior a /entrar en caché, el enlace del correo llega al canje", async ({ page }, info) => {
    const etiquetas = [`${info.project.name}-${info.repeatEachIndex}-entrar-anterior`, `${info.project.name}-${info.repeatEachIndex}-entrar-actual`];
    try {
      const anterior = await levantar(IMAGEN_ANTERIOR, etiquetas[0]!);
      await instalarServiceWorker(page, anterior.origen);
      // Control: esa versión, desde su caché, de verdad no conoce /entrar.
      await page.goto(`${anterior.origen}/entrar`);
      await expect(page.getByRole("heading", { level: 1 })).toHaveText("No encontramos esa página");
      await page.goto(`${anterior.origen}/`);

      const actual = await reemplazar(anterior, IMAGEN_ACTUAL, etiquetas[1]!);
      const navegaciones: boolean[] = [];
      page.on("response", (r) => {
        if (r.request().isNavigationRequest() && new URL(r.url()).pathname === "/entrar") {
          navegaciones.push(r.fromServiceWorker());
        }
      });
      const canje = page.waitForRequest((r) => new URL(r.url()).pathname === RUTA_CANJE);
      await page.goto(`${actual.origen}/entrar#token=${TOKEN}`);

      const pedido = await canje;
      expect(pedido.method()).toBe("POST");
      expect(pedido.postDataJSON()).toEqual({ token: TOKEN });
      const respuesta = await pedido.response();
      expect(respuesta?.status()).toBe(401);
      expect(((await respuesta?.json()) as { codigo: string }).codigo).toBe("enlace_invalido");
      await expect(page.getByText(/No pudimos hacerte entrar con este enlace/)).toBeVisible();
      // Primero respondió la caché de la versión anterior; después, la red (versión actual).
      expect(navegaciones).toEqual([true, false]);
      expect(page.url()).not.toContain(TOKEN);
      expect(new URL(page.url()).pathname).toBe("/entrar");
      expect(await page.evaluate((clave) => sessionStorage.getItem(clave), CLAVE_GUARDADO)).toBeNull();
      await sinFaltasDeAccesibilidad(page);
    } finally {
      bajarTodos(etiquetas);
    }
  });

  test("una ruta que la versión en caché no conoce aplica sola la nueva, en la misma URL; las demás pestañas esperan el botón", async ({
    page,
    context,
  }, info) => {
    const etiquetas = [`${info.project.name}-${info.repeatEachIndex}-ruta-actual`, `${info.project.name}-${info.repeatEachIndex}-ruta-siguiente`];
    try {
      const actual = await levantar(IMAGEN_ACTUAL, etiquetas[0]!);
      await instalarServiceWorker(page, actual.origen);
      const otra = await context.newPage();
      await otra.goto(`${actual.origen}/`);
      expect(await otra.evaluate(() => navigator.serviceWorker.controller !== null)).toBe(true);

      const siguiente = await reemplazar(actual, IMAGEN_SIGUIENTE, etiquetas[1]!);

      // En una ruta conocida la versión nueva espera el botón (documento 13, §14.4).
      await marcarPestana(otra);
      await otra.evaluate(async () => {
        const registro = await navigator.serviceWorker.getRegistration();
        await registro?.update();
      });
      await expect(otra.getByRole("alert")).toContainText("Hay una versión nueva");
      await otra.waitForTimeout(ESPERA_NEGATIVA_MS);
      expect(await sigueSinRecargar(otra)).toBe(true);
      expect(await otra.evaluate(async () => (await navigator.serviceWorker.getRegistration())?.waiting !== null)).toBe(true);

      // En una ruta que no conoce, la aplica sola y recarga la MISMA URL (ruta, consulta y fragmento).
      const url = `${siguiente.origen}/ruta-de-una-version-nueva?a=1#b=2`;
      await page.goto(url);
      await page.waitForFunction(
        () => (performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming).type === "reload",
      );
      expect(page.url()).toBe(url);
      await expect(page.getByRole("heading", { level: 1 })).toHaveText("No encontramos esa página");
      await expect(page.getByRole("alert")).toHaveCount(0);
      expect(await page.evaluate(async () => (await navigator.serviceWorker.getRegistration())?.waiting ?? null)).toBeNull();
      await sinFaltasDeAccesibilidad(page);

      // La otra pestaña no recargó sola: conserva lo que tenga a medio hacer y el aviso.
      await otra.waitForTimeout(ESPERA_NEGATIVA_MS);
      expect(await sigueSinRecargar(otra)).toBe(true);
      await expect(otra.getByRole("alert")).toContainText("Hay una versión nueva");
      // Su botón recarga (la versión nueva ya está activa) y el aviso desaparece.
      await otra.getByRole("button", { name: "Actualizar" }).click();
      await expect(otra.getByRole("alert")).toHaveCount(0);
      expect(await sigueSinRecargar(otra)).toBe(false);
    } finally {
      bajarTodos(etiquetas);
    }
  });
});

test.describe("recarga a mitad del canje", () => {
  // page.route no ve los pedidos que pasan por un service worker; acá lo que se prueba es la
  // página, así que se bloquea el service worker.
  test.use({ serviceWorkers: "block" });

  test("una recarga mientras el canje está en curso no pierde el token", async ({ page }) => {
    const pedidos: unknown[] = [];
    let primero = true;
    await page.route(`**${RUTA_CANJE}`, async (ruta) => {
      pedidos.push(ruta.request().postDataJSON());
      if (primero) {
        // El primero queda colgado: la recarga llega antes de la respuesta.
        primero = false;
        return;
      }
      await ruta.continue();
    });

    await page.goto(`/entrar#token=${TOKEN}`);
    await expect.poll(() => pedidos.length).toBe(1);
    expect(page.url()).not.toContain(TOKEN);
    expect(await page.evaluate((clave) => sessionStorage.getItem(clave), CLAVE_GUARDADO)).toContain(TOKEN);

    const respuesta = page.waitForResponse((r) => new URL(r.url()).pathname === RUTA_CANJE);
    await page.reload();
    expect((await respuesta).status()).toBe(401);
    expect(pedidos).toEqual([{ token: TOKEN }, { token: TOKEN }]);
    await expect(page.getByText(/No pudimos hacerte entrar con este enlace/)).toBeVisible();
    expect(await page.evaluate((clave) => sessionStorage.getItem(clave), CLAVE_GUARDADO)).toBeNull();
    expect(new URL(page.url()).hash).toBe("");
    await sinFaltasDeAccesibilidad(page);
  });
});
