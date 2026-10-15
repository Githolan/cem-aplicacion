import { expect, type Page, test } from "@playwright/test";

import { sinFaltasDeAccesibilidad } from "./entorno";

// La página /cuenta/segundo-factor servida por el nginx de la imagen, con su CSP real. El backend
// de la corrida no tiene base de datos, así que no hay sesión con qué dar de alta un factor: el
// flujo contra PostgreSQL se prueba en el backend (tests/test_segundo_factor.py) y la lógica de la
// página en Vitest. Aquí se comprueba lo que solo un navegador de verdad muestra: que el QR se
// dibuja sin violar la CSP (`style-src 'self'`, `img-src 'self' data:`), que se ve entero en un
// teléfono y que los colores pasan el contraste de WCAG AA, que jsdom no mide.
const SECRETO = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP";
const URI = `otpauth://totp/Consorcio%20en%20Mano:ana%40ejemplo.com?secret=${SECRETO}&issuer=Consorcio%20en%20Mano`;
const CODIGOS = Array.from({ length: 10 }, (_, i) => `abcd${i + 2}-fgh${i + 2}k`);
const INACTIVO = { activo: false, activado_en: null, codigos_recuperacion_restantes: 0, verificado_en: null };

/** Errores de la página y violaciones de la CSP, que el navegador informa como eventos. */
function vigilarErrores(pagina: Page): string[] {
  const errores: string[] = [];
  pagina.on("pageerror", (e) => errores.push(e.message));
  pagina.on("console", (m) => {
    if (m.type() === "error") errores.push(m.text());
  });
  void pagina.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", (e) =>
      console.error(`CSP: ${e.violatedDirective} ${e.blockedURI}`),
    );
  });
  return errores;
}

/** Responde la API del segundo factor desde el navegador: la página y su CSP siguen siendo reales. */
async function simularApi(pagina: Page) {
  await pagina.route("**/api/v1/auth/totp**", async (ruta) => {
    const camino = new URL(ruta.request().url()).pathname;
    if (camino === "/api/v1/auth/totp") return ruta.fulfill({ json: INACTIVO });
    if (camino === "/api/v1/auth/totp/alta") return ruta.fulfill({ json: { secreto: SECRETO, uri: URI } });
    if (camino === "/api/v1/auth/totp/confirmar") return ruta.fulfill({ json: { codigos: CODIGOS } });
    return ruta.abort();
  });
}

test.describe("segundo factor", () => {
  test("sin sesión, el backend real no muestra nada del factor y la página lo explica", async ({ page }) => {
    const errores = vigilarErrores(page);
    const consulta = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/v1/auth/totp");
    await page.goto("/cuenta/segundo-factor");

    const respuesta = await consulta;
    expect(respuesta.status()).toBe(401);
    await expect(page.getByRole("link", { name: "entrá a tu cuenta" })).toHaveAttribute("href", "/entrar");
    await sinFaltasDeAccesibilidad(page);
    // La consola informa el 401 del recurso; violaciones de la CSP o errores de la página, ninguno.
    expect(errores.filter((e) => !e.includes("401"))).toEqual([]);
  });

  test("el QR y los códigos se ven enteros en un teléfono, sin violar la CSP", async ({ page }) => {
    const errores = vigilarErrores(page);
    await page.setViewportSize({ width: 360, height: 740 });
    await simularApi(page);
    await page.goto("/cuenta/segundo-factor");

    await page.getByRole("button", { name: "Activar el segundo factor" }).click();
    const qr = page.getByRole("img", { name: /Código QR/ });
    await expect(qr).toBeVisible();
    const caja = await qr.boundingBox();
    expect(caja).not.toBeNull();
    expect(caja!.width).toBeGreaterThan(150);
    expect(caja!.x + caja!.width).toBeLessThanOrEqual(360);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
    await sinFaltasDeAccesibilidad(page);

    await page.getByLabel("Código de la aplicación").fill("042917");
    await page.getByRole("button", { name: "Confirmar" }).click();
    await expect(page.getByRole("list", { name: "Códigos de recuperación" }).getByRole("listitem")).toHaveCount(10);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
    await sinFaltasDeAccesibilidad(page);
    expect(errores).toEqual([]);
  });

  test("control: la vigilancia de la CSP sí ve un estilo en línea", async ({ page }) => {
    const errores = vigilarErrores(page);
    await simularApi(page);
    await page.goto("/cuenta/segundo-factor");
    await expect(page.getByRole("button", { name: "Activar el segundo factor" })).toBeVisible();

    await page.evaluate(() => {
      const elemento = document.createElement("style");
      elemento.textContent = "body { color: red }";
      document.head.append(elemento);
    });
    await expect.poll(() => errores.some((e) => e.startsWith("CSP: style-src"))).toBe(true);
  });
});
