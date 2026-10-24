import { expect, type Page, test } from "@playwright/test";

import { otroCliente, sinFaltasDeAccesibilidad } from "./entorno";

// La página /padron servida por el nginx de la imagen, con su CSP real. Como en mandatos.e2e.ts,
// sin sesión responde el backend de verdad (401) y, con sesión, la API se simula desde el
// navegador. Quién ve el padrón, quién confirma y quién finaliza lo deciden la política
// (politicas_datos/padron.json) y sus pruebas de backend. Acá se ve lo que solo un navegador real
// muestra: que la página entra en un teléfono, el contraste de WCAG AA, que la cookie CSRF y la
// clave de reintento viajan como encabezados, que correo y teléfono no llegan al documento aunque
// la API los mandara y que no hay violaciones de la CSP.
const CSRF = "e2e-csrf-padron";
const C1 = "11111111-1111-4111-8111-111111111111";
const U1 = "22222222-2222-4222-8222-222222222222";
const U2 = "33333333-3333-4333-8333-333333333333";
const V_PROP = "44444444-4444-4444-8444-444444444444";
const V_INQ = "55555555-5555-4555-8555-555555555555";
const V_FUTURO = "66666666-6666-4666-8666-666666666666";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const BASE = `/api/v1/consorcios/${C1}`;

// Un nombre del largo que admite la base: la tarjeta no puede ensanchar la página.
const NOMBRE_LARGO = "Anahí Florencia de los Santos Echeverría Bustamante";

const PADRON = {
  elementos: [
    { vinculo_id: V_PROP, unidad_id: U1, designacion: "1° A", nombre: NOMBRE_LARGO, tipo: "propietario", vigente_desde: "2025-03-01", vigente_hasta: null, confirmado: true },
    { vinculo_id: V_INQ, unidad_id: U1, designacion: "1° A", nombre: "Beto Pérez", tipo: "inquilino", vigente_desde: "2026-01-01", vigente_hasta: "2027-01-01", confirmado: false },
    { vinculo_id: V_FUTURO, unidad_id: U2, designacion: "2° B", nombre: "Carla Gómez", tipo: "propietario", vigente_desde: "2099-01-01", vigente_hasta: null, confirmado: false },
  ],
};

const error = (status: number, codigo: string) => ({ status, json: { codigo, mensaje: "x", id_correlacion: "c" } });

type Envio = { camino: string; csrf: string | null; clave: string | null; cuerpo: Record<string, unknown> };

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
 * Responde la API del padrón desde el navegador. La vecina sin `padron.ver` recibe 403. Las filas
 * llevan además correo y teléfono, que la API real no manda: la página no tiene que mostrarlos.
 * Sin CSRF, 403; sin clave, 422. El 403 al finalizar el primer vínculo lo inventa el simulador para
 * ver cómo la página muestra un rechazo de la API (la política real decide en el backend).
 */
async function simularApi(pagina: Page, baseURL: string, quien: "consejo" | "vecina"): Promise<Envio[]> {
  const envios: Envio[] = [];
  await pagina.route("**/api/v1/**", async (ruta) => {
    const pedido = ruta.request();
    const camino = new URL(pedido.url()).pathname;
    if (pedido.method() === "GET") {
      if (camino === "/api/v1/me/consorcios") return ruta.fulfill({ json: [{ id: C1, nombre: "Edificio Azul", vinculos: [] }] });
      if (quien === "vecina") return ruta.fulfill(error(403, "sin_permiso"));
      if (camino === `${BASE}/padron`)
        return ruta.fulfill({
          json: { elementos: PADRON.elementos.map((f) => ({ ...f, correo: "privado@ejemplo.com", telefono: "+54 11 4321-9876" })) },
        });
      return ruta.abort();
    }
    const csrf = (await pedido.headerValue("x-csrf-token")) ?? null;
    const clave = (await pedido.headerValue("idempotency-key")) ?? null;
    const cuerpo = pedido.postDataJSON() as Record<string, unknown>;
    envios.push({ camino, csrf, clave, cuerpo });
    if (csrf !== CSRF) return ruta.fulfill(error(403, "csrf_invalido"));
    if (clave === null) return ruta.fulfill(error(422, "peticion_invalida"));
    if (camino === `${BASE}/vinculos/${V_INQ}/confirmar`)
      return ruta.fulfill({ json: { vinculo_id: V_INQ, unidad_id: U1, tipo: "inquilino", confirmado: true, confirmado_en: "2026-10-24T12:00:00Z" } });
    if (camino === `${BASE}/vinculos/${V_PROP}/finalizar`) return ruta.fulfill(error(403, "sin_permiso"));
    if (camino === `${BASE}/vinculos/${V_INQ}/finalizar`)
      return ruta.fulfill({ json: { vinculo_id: V_INQ, unidad_id: U1, tipo: "inquilino", vigente_hasta: "2026-10-24", finalizado_en: "2026-10-24T12:00:00Z" } });
    return ruta.abort();
  });
  await pagina.context().addCookies([
    { name: "__Host-cem_csrf", value: CSRF, domain: new URL(baseURL).hostname, path: "/", secure: true },
  ]);
  return envios;
}

test.describe("padrón sin sesión", () => {
  test.use(otroCliente(7));

  test("el backend real responde 401 y la página manda a /entrar", async ({ page }) => {
    const errores = vigilarErrores(page);
    const consulta = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/v1/me/consorcios");
    await page.goto("/padron");
    const respuesta = await consulta;
    expect(respuesta.status()).toBe(401);
    expect(((await respuesta.json()) as { codigo: string }).codigo).toBe("no_autenticado");
    await expect(page).toHaveURL(/\/entrar$/);
    expect(errores.filter((e) => !e.includes("401"))).toEqual([]);
  });
});

test.describe("padrón", () => {
  test("en un teléfono, el Consejo ve el padrón sin datos de contacto y confirma con CSRF y clave", async ({ page, baseURL }) => {
    const errores = vigilarErrores(page);
    await page.setViewportSize({ width: 360, height: 740 });
    const envios = await simularApi(page, baseURL!, "consejo");
    await page.goto("/");
    await page.getByRole("link", { name: "Padrón del edificio" }).click();

    const beto = page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: "1° A: Beto Pérez" }) });
    await expect(beto).toBeVisible();
    await expect(page.getByRole("heading", { name: `1° A: ${NOMBRE_LARGO}` })).toBeVisible();
    await expect(page.getByText("(todavía no empezó)")).toBeVisible();
    // Ni el texto ni los atributos del documento llevan correo o teléfono.
    const html = await page.content();
    expect(html).not.toMatch(/privado@ejemplo\.com|4321-9876/);
    await expect(page.locator('a[href^="mailto:"], a[href^="tel:"], input[type="email"], input[type="tel"]')).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
    await sinFaltasDeAccesibilidad(page);

    await beto.getByRole("button", { name: "Confirmar" }).click();
    await page.getByLabel("Referencia del título").fill("  Contrato de locación 77  ");
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
    await sinFaltasDeAccesibilidad(page);
    const enviar = await page.getByRole("button", { name: "Confirmar el vínculo" }).boundingBox();
    expect(enviar?.height).toBeGreaterThanOrEqual(44);
    await page.getByRole("button", { name: "Confirmar el vínculo" }).click();
    await expect(page.getByText("Listo: confirmamos el vínculo de Beto Pérez con la unidad 1° A.")).toBeFocused();
    expect(envios).toHaveLength(1);
    expect(envios[0]?.camino).toBe(`${BASE}/vinculos/${V_INQ}/confirmar`);
    expect(envios[0]?.csrf).toBe(CSRF);
    expect(envios[0]?.clave).toMatch(UUID);
    expect(envios[0]?.cuerpo).toEqual({ referencia: "Contrato de locación 77" });
    expect(errores).toEqual([]);
  });

  test("finalizar pide la confirmación; un rechazo de la API se muestra con foco y otro vínculo se finaliza", async ({ page, baseURL }) => {
    const envios = await simularApi(page, baseURL!, "consejo");
    await page.setViewportSize({ width: 360, height: 740 });
    await page.goto("/padron");
    const propia = page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: `1° A: ${NOMBRE_LARGO}` }) });
    await propia.getByRole("button", { name: "Finalizar" }).click();
    await page.getByLabel("Motivo").fill("Venta de la unidad");
    await page.getByRole("button", { name: "Finalizar el vínculo" }).click();
    await expect(page.getByRole("button", { name: "Sí, finalizar" })).toBeFocused();
    expect(envios).toHaveLength(0);
    await sinFaltasDeAccesibilidad(page);
    await page.getByRole("button", { name: "Sí, finalizar" }).click();
    await expect(page.getByText(/No tenés permiso para finalizar/)).toBeFocused();
    await sinFaltasDeAccesibilidad(page);

    const beto = page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: "1° A: Beto Pérez" }) });
    await beto.getByRole("button", { name: "Finalizar" }).click();
    await page.getByLabel("Motivo").fill("Fin del contrato");
    await page.getByRole("button", { name: "Finalizar el vínculo" }).click();
    await page.getByRole("button", { name: "Sí, finalizar" }).click();
    await expect(page.getByText("Listo: finalizamos el vínculo de Beto Pérez con la unidad 1° A. Desde el 24 de octubre de 2026 ya no vale.")).toBeFocused();
    expect(envios.map((e) => e.camino)).toEqual([`${BASE}/vinculos/${V_PROP}/finalizar`, `${BASE}/vinculos/${V_INQ}/finalizar`]);
    expect(envios[1]?.cuerpo).toEqual({ motivo: "Fin del contrato" });
  });

  test("la vecina sin permiso no ve el padrón ni sus acciones", async ({ page, baseURL }) => {
    const errores = vigilarErrores(page);
    await simularApi(page, baseURL!, "vecina");
    await page.goto("/padron");
    await expect(page.getByText(/No tenés permiso para ver el padrón/)).toBeVisible();
    await expect(page.getByRole("button", { name: /Confirmar|Finalizar/ })).toHaveCount(0);
    await sinFaltasDeAccesibilidad(page);
    expect(errores.filter((e) => !e.includes("403"))).toEqual([]);
  });

  test("control: sin la cookie CSRF, el simulador rechaza y la página lo dice", async ({ page, baseURL }) => {
    const envios = await simularApi(page, baseURL!, "consejo");
    await page.context().clearCookies();
    await page.goto("/padron");
    const beto = page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: "1° A: Beto Pérez" }) });
    await beto.getByRole("button", { name: "Confirmar" }).click();
    await page.getByLabel("Referencia del título").fill("Contrato 77");
    await page.getByRole("button", { name: "Confirmar el vínculo" }).click();
    await expect(page.getByText("No pudimos verificar el pedido. Recargá la página.")).toBeVisible();
    expect(envios[0]?.csrf).toBeNull();
  });
});
