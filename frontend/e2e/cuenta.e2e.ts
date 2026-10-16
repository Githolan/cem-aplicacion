import { expect, type Page, test } from "@playwright/test";

import { sinFaltasDeAccesibilidad } from "./entorno";

// La página /cuenta servida por el nginx de la imagen, con su CSP real. El backend de la corrida
// no tiene base de datos: sin sesión responde el 401 de verdad, y con eso se prueba que la página
// manda a /entrar. La cuenta con sesiones se simula desde el navegador (la lógica está en Vitest)
// para ver lo que solo un navegador real muestra: que no hay violaciones de la CSP, que la lista
// entra en un teléfono, el contraste de WCAG AA y que la cookie CSRF viaja como encabezado.
const ACTUAL = "11111111-1111-4111-8111-111111111111";
const OTRA = "22222222-2222-4222-8222-222222222222";
const CSRF = "e2e-csrf-cuenta";
const YO = {
  persona_id: "99999999-9999-4999-8999-999999999999",
  nombre: "Ana Pérez",
  correo: "ana.perez.con.un.correo.largo@ejemplo.com",
  sesion_id: ACTUAL,
  vence_en: "2026-11-15T20:00:00Z",
};
const SESIONES = [
  { id: ACTUAL, dispositivo: "Chrome en Android", actual: true },
  { id: OTRA, dispositivo: "Firefox en Windows", actual: false },
].map((s) => ({
  ...s,
  creada_en: "2026-10-01T12:00:00Z",
  ultimo_uso_en: "2026-10-16T12:00:00Z",
  vence_en: "2026-11-15T12:00:00Z",
}));
const CONTRASENA = { definida: false, cambiada_en: null };
const TOTP = { activo: true, activado_en: "2026-10-15T20:00:00Z", codigos_recuperacion_restantes: 10, verificado_en: null };

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

/**
 * Responde la API de la cuenta desde el navegador. Los POST sin el encabezado CSRF reciben 403,
 * como en el backend; devuelve el encabezado que trajo cada POST.
 */
async function simularApi(pagina: Page, baseURL: string): Promise<Record<string, string | null>> {
  const csrf: Record<string, string | null> = {};
  await pagina.route("**/api/v1/auth/**", async (ruta) => {
    const pedido = ruta.request();
    const camino = new URL(pedido.url()).pathname;
    if (pedido.method() === "GET") {
      if (camino === "/api/v1/auth/sesion") return ruta.fulfill({ json: YO });
      if (camino === "/api/v1/auth/sesiones") return ruta.fulfill({ json: { elementos: SESIONES } });
      if (camino === "/api/v1/auth/totp") return ruta.fulfill({ json: TOTP });
      if (camino === "/api/v1/auth/contrasena") return ruta.fulfill({ json: CONTRASENA });
      return ruta.abort();
    }
    const encabezado = (await pedido.headerValue("x-csrf-token")) ?? null;
    csrf[camino] = encabezado;
    if (encabezado !== CSRF)
      return ruta.fulfill({ status: 403, json: { codigo: "csrf_invalido", mensaje: "x", id_correlacion: "c" } });
    if (camino === `/api/v1/auth/sesiones/${OTRA}/revocar` || camino === "/api/v1/auth/salir")
      return ruta.fulfill({ status: 204 });
    return ruta.abort();
  });
  // La cookie que el backend deja al entrar (__Host-: Secure, en /). Chromium considera seguro a
  // 127.0.0.1 y la guarda aunque la página sea http, pero solo si se pasa con domain y path: con
  // `url`, CDP la rechaza ("Invalid cookie fields", medido con Playwright 1.57).
  await pagina.context().addCookies([
    { name: "__Host-cem_csrf", value: CSRF, domain: new URL(baseURL).hostname, path: "/", secure: true },
  ]);
  return csrf;
}

test.describe("mi cuenta", () => {
  test("sin sesión, el backend real responde 401 y la página manda a /entrar", async ({ page }) => {
    const errores = vigilarErrores(page);
    const consulta = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/v1/auth/sesion");
    const contrasena = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/v1/auth/contrasena");
    await page.goto("/cuenta");

    expect((await consulta).status()).toBe(401);
    // El estado de la contraseña tampoco se muestra sin sesión (medido: 401 no_autenticado).
    const respuesta = await contrasena;
    expect(respuesta.status()).toBe(401);
    expect(((await respuesta.json()) as { codigo: string }).codigo).toBe("no_autenticado");
    await expect(page).toHaveURL(/\/entrar$/);
    await expect(page.getByRole("heading", { level: 1, name: "Entrar a Consorcio en Mano" })).toBeVisible();
    // La consola informa los 401 de los recursos; violaciones de la CSP o errores, ninguno.
    expect(errores.filter((e) => !e.includes("401"))).toEqual([]);
  });

  test("en un teléfono: la cuenta entra entera, cierra otra sesión y sale, con CSRF y sin violar la CSP", async ({
    page,
    baseURL,
  }) => {
    const errores = vigilarErrores(page);
    await page.setViewportSize({ width: 360, height: 740 });
    const csrf = await simularApi(page, baseURL!);
    await page.goto("/");
    await page.getByRole("link", { name: "Mi cuenta" }).click();

    await expect(page.getByText("Ana Pérez")).toBeVisible();
    await expect(page.getByText(/Está activado/)).toBeVisible();
    await expect(page.locator(".sesiones li[aria-current=true]")).toContainText("(este dispositivo)");
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
    await sinFaltasDeAccesibilidad(page);

    const cerrarOtra = page
      .locator(".sesiones li", { hasText: "Firefox en Windows" })
      .getByRole("button", { name: "Cerrar esta sesión" });
    await expect(cerrarOtra).toHaveAccessibleDescription("Firefox en Windows");
    await cerrarOtra.click();
    await expect(page.getByText("Listo: cerramos la sesión de Firefox en Windows.")).toBeVisible();
    expect(csrf[`/api/v1/auth/sesiones/${OTRA}/revocar`]).toBe(CSRF);
    await sinFaltasDeAccesibilidad(page);

    await page.getByRole("button", { name: "Salir" }).click();
    await expect(page).toHaveURL(/\/entrar$/);
    expect(csrf["/api/v1/auth/salir"]).toBe(CSRF);
    expect(errores).toEqual([]);
  });

  test("control: sin la cookie CSRF, el backend simulado rechaza y la página lo dice", async ({ page, baseURL }) => {
    const csrf = await simularApi(page, baseURL!);
    await page.context().clearCookies();
    await page.goto("/cuenta");

    await page.getByRole("button", { name: "Salir" }).click();
    await expect(page.getByText(/No pudimos completar el pedido/)).toBeVisible();
    expect(csrf["/api/v1/auth/salir"]).toBeNull();
    await expect(page).toHaveURL(/\/cuenta$/);
  });
});
