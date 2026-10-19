import { expect, type Page, test } from "@playwright/test";

import { otroCliente, sinFaltasDeAccesibilidad } from "./entorno";

// La página /invitacion-mandato (A7) servida por el nginx de la imagen, con su CSP real. El
// backend de la corrida no tiene base de datos, así que consultar y aceptar se simulan desde el
// navegador. Qué enlace sirve, si la persona puede asociarse y la separación con el Consejo los
// decide la base y los prueban las pruebas del backend (test_mandatos_invitaciones). Acá se ve lo
// que solo un navegador real muestra: que el token sale de la URL antes de cualquier otra cosa,
// que viaja en el cuerpo y no en la dirección, que la cookie CSRF va como encabezado, que la
// página entra en un teléfono, WCAG AA y que no hay violaciones de la CSP.
const CSRF = "e2e-csrf-mandato";
const TOKEN = "M".repeat(21) + "_-" + "n".repeat(20);

type Pedido = { camino: string; url: string; csrf: string | null; cuerpo: unknown };

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

async function simularApi(pagina: Page, baseURL: string, aceptar: { status: number; json: unknown }): Promise<Pedido[]> {
  const pedidos: Pedido[] = [];
  await pagina.route("**/api/v1/auth/invitacion-mandato/**", async (ruta) => {
    const pedido = ruta.request();
    const camino = new URL(pedido.url()).pathname;
    pedidos.push({ camino, url: pedido.url(), csrf: (await pedido.headerValue("x-csrf-token")) ?? null, cuerpo: pedido.postDataJSON() });
    if (camino.endsWith("/consultar"))
      return ruta.fulfill({ json: { consorcio: "Edificio Azul", mandatario: "Estudio X", invitada_por: "Ana", vence_en: "2026-10-26T12:00:00Z" } });
    return ruta.fulfill(aceptar);
  });
  await pagina.context().addCookies([
    { name: "__Host-cem_csrf", value: CSRF, domain: new URL(baseURL).hostname, path: "/", secure: true },
  ]);
  return pedidos;
}

test.describe("invitación de mandato", () => {
  test.use(otroCliente(4));

  test("en un teléfono, consulta y acepta con el token en el cuerpo y el CSRF", async ({ page, baseURL }) => {
    const errores = vigilarErrores(page);
    await page.setViewportSize({ width: 360, height: 740 });
    const pedidos = await simularApi(page, baseURL!, {
      status: 200,
      json: {
        consorcio_id: "11111111-1111-4111-8111-111111111111",
        persona_id: "22222222-2222-4222-8222-222222222222",
        mandato_id: "33333333-3333-4333-8333-333333333333",
        cuenta_nueva: true,
        vigente_desde: "2026-01-01",
      },
    });
    await page.goto(`/invitacion-mandato#token=${TOKEN}`);

    await expect(page.getByText("Estudio X")).toBeVisible();
    await expect(page).toHaveURL(/\/invitacion-mandato$/);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
    await sinFaltasDeAccesibilidad(page);

    await page.getByRole("button", { name: "Aceptar y asociar mi cuenta" }).click();
    await expect(page.getByText(/tu cuenta quedó asociada a la administración de Edificio Azul/)).toBeVisible();
    expect(pedidos.map((p) => p.camino)).toEqual([
      "/api/v1/auth/invitacion-mandato/consultar",
      "/api/v1/auth/invitacion-mandato/aceptar",
    ]);
    for (const p of pedidos) expect(p.url).not.toContain(TOKEN);
    expect(pedidos[1]).toMatchObject({ csrf: CSRF, cuerpo: { token: TOKEN, nombre: "Estudio X" } });
    expect(errores).toEqual([]);
  });

  test("el rechazo por el Consejo se explica sin dejar la página en blanco", async ({ page, baseURL }) => {
    const errores = vigilarErrores(page);
    await simularApi(page, baseURL!, {
      status: 409,
      json: { codigo: "asociacion_incompatible_con_consejo", mensaje: "x", id_correlacion: "c" },
    });
    await page.goto(`/invitacion-mandato#token=${TOKEN}`);
    await page.getByRole("button", { name: "Aceptar y asociar mi cuenta" }).click();
    await expect(page.getByText(/el Consejo fiscaliza y no administra/)).toBeVisible();
    await sinFaltasDeAccesibilidad(page);
    expect(errores.filter((e) => !e.includes("409"))).toEqual([]);
  });
});
