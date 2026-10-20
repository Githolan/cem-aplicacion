import { expect, type Page, type Route, test } from "@playwright/test";

import { otroCliente, sinFaltasDeAccesibilidad } from "./entorno";

// La página /invitacion (RF03) servida por el nginx de la imagen, con su CSP real. El backend de
// la corrida no tiene base de datos, así que consultar y aceptar se simulan desde el navegador.
// Qué invitación sirve y quién puede aceptarla los decide la base y los prueban las pruebas del
// backend (test_invitaciones). Acá se ve lo que solo un navegador real muestra: que el token sale
// de la URL, que viaja en el cuerpo y no en la dirección, que la cookie CSRF va como encabezado,
// que la página entra en un teléfono de 360 px, WCAG AA, que el estado de carga se ve y que sin
// red lo dice, sin violaciones de la CSP.
const CSRF = "e2e-csrf-invitacion";
const TOKEN = "I".repeat(21) + "_-" + "v".repeat(20);
const ANCHO_TELEFONO_PX = 360;
const ALTO_TELEFONO_PX = 740;
const RECIBIDA = {
  consorcio: "Edificio Azul",
  unidad: "1° A",
  tipo: "inquilino",
  correo: "beto.perez.con.un.correo.largo@ejemplo.com",
  nombre: "Beto Pérez",
  invitada_por: "Ana",
  vence_en: "2026-10-27T12:00:00Z",
};
const ACEPTADA = {
  consorcio_id: "11111111-1111-4111-8111-111111111111",
  persona_id: "22222222-2222-4222-8222-222222222222",
  cuenta_nueva: true,
};

type Pedido = { camino: string; url: string; csrf: string | null; cuerpo: unknown };

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

/** Simula consultar y aceptar; `responder` decide qué pasa con cada pedido (por defecto, sale bien). */
async function simularApi(
  pagina: Page,
  baseURL: string,
  responder: (camino: string, ruta: Route) => Promise<void> = async (camino, ruta) =>
    ruta.fulfill({ json: camino.endsWith("/consultar") ? RECIBIDA : ACEPTADA }),
): Promise<Pedido[]> {
  const pedidos: Pedido[] = [];
  await pagina.route("**/api/v1/auth/invitacion/**", async (ruta) => {
    const pedido = ruta.request();
    const camino = new URL(pedido.url()).pathname;
    pedidos.push({
      camino,
      url: pedido.url(),
      csrf: (await pedido.headerValue("x-csrf-token")) ?? null,
      cuerpo: pedido.postDataJSON(),
    });
    await responder(camino, ruta);
  });
  await pagina.context().addCookies([
    { name: "__Host-cem_csrf", value: CSRF, domain: new URL(baseURL).hostname, path: "/", secure: true },
  ]);
  return pedidos;
}

async function anchoDePagina(pagina: Page): Promise<number> {
  return pagina.evaluate(() => document.documentElement.scrollWidth);
}

test.describe("invitación", () => {
  test.use(otroCliente(6));

  test("en un teléfono, consulta y acepta con el token en el cuerpo y el CSRF, sin salirse de 360 px", async ({
    page,
    baseURL,
  }) => {
    const errores = vigilarErrores(page);
    await page.setViewportSize({ width: ANCHO_TELEFONO_PX, height: ALTO_TELEFONO_PX });
    const pedidos = await simularApi(page, baseURL!);
    await page.goto(`/invitacion#token=${TOKEN}`);

    await expect(page.getByRole("heading", { level: 2, name: "Edificio Azul" })).toBeVisible();
    await expect(page).toHaveURL(/\/invitacion$/);
    expect(await anchoDePagina(page)).toBeLessThanOrEqual(ANCHO_TELEFONO_PX);
    await sinFaltasDeAccesibilidad(page);

    await page.getByLabel("Tu nombre").fill("  Alberto ");
    await page.getByRole("button", { name: "Aceptar la invitación" }).click();
    await expect(page.getByText("Listo: ya sos parte de Edificio Azul.")).toBeVisible();
    expect(await anchoDePagina(page)).toBeLessThanOrEqual(ANCHO_TELEFONO_PX);
    await sinFaltasDeAccesibilidad(page);

    expect(pedidos.map((p) => p.camino)).toEqual([
      "/api/v1/auth/invitacion/consultar",
      "/api/v1/auth/invitacion/aceptar",
    ]);
    for (const p of pedidos) expect(p.url).not.toContain(TOKEN);
    expect(pedidos[0]).toMatchObject({ cuerpo: { token: TOKEN } });
    expect(pedidos[1]).toMatchObject({ csrf: CSRF, cuerpo: { token: TOKEN, nombre: "Alberto" } });
    expect(errores).toEqual([]);
  });

  test("carga: mientras la consulta no responde dice «Buscando la invitación…», accesible", async ({ page, baseURL }) => {
    const errores = vigilarErrores(page);
    await page.setViewportSize({ width: ANCHO_TELEFONO_PX, height: ALTO_TELEFONO_PX });
    let soltar: () => void = () => undefined;
    const listo = new Promise<void>((r) => {
      soltar = r;
    });
    await simularApi(page, baseURL!, async (camino, ruta) => {
      await listo;
      await ruta.fulfill({ json: camino.endsWith("/consultar") ? RECIBIDA : ACEPTADA });
    });
    await page.goto(`/invitacion#token=${TOKEN}`);

    await expect(page.getByRole("status")).toHaveText("Buscando la invitación…");
    await expect(page.getByRole("button", { name: "Aceptar la invitación" })).toHaveCount(0);
    await sinFaltasDeAccesibilidad(page);
    soltar();

    await expect(page.getByRole("button", { name: "Aceptar la invitación" })).toBeVisible();
    await expect(page.getByText("Buscando la invitación…")).toHaveCount(0);
    expect(errores).toEqual([]);
  });

  test("sin red: la consulta cortada se explica, sin botón de aceptar y sin salirse de 360 px", async ({
    page,
    baseURL,
  }) => {
    const errores = vigilarErrores(page);
    await page.setViewportSize({ width: ANCHO_TELEFONO_PX, height: ALTO_TELEFONO_PX });
    const pedidos = await simularApi(page, baseURL!, async (_camino, ruta) => ruta.abort("internetdisconnected"));
    await page.goto(`/invitacion#token=${TOKEN}`);

    await expect(page.getByText(/No pudimos completar el pedido. Revisá la conexión/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Aceptar la invitación" })).toHaveCount(0);
    expect(pedidos.map((p) => p.camino)).toEqual(["/api/v1/auth/invitacion/consultar"]);
    expect(await anchoDePagina(page)).toBeLessThanOrEqual(ANCHO_TELEFONO_PX);
    await sinFaltasDeAccesibilidad(page);
    // La consola informa el pedido cortado; violaciones de la CSP o errores de la página, ninguno.
    expect(errores.filter((e) => !e.includes("ERR_INTERNET_DISCONNECTED"))).toEqual([]);
  });
});
