import { expect, type Page, test } from "@playwright/test";

import { otroCliente, simularPermisos, simularSesion, sinFaltasDeAccesibilidad } from "./entorno";

// La página /panel servida por el nginx de la imagen, con su CSP real. Sin sesión responde el
// backend de verdad (401); con sesión la API se simula desde el navegador. Quién ve el panel lo
// decide la API (`fiscalizacion.ver_panel`) y lo prueba el backend (`test_panel_del_consejo_0094`).
// Acá se ve lo que solo un navegador real muestra: que los gráficos entran en un teléfono de 360 px
// sin desplazar la página, `axe` con contraste y que no hay violaciones de la CSP.
const C1 = "11111111-1111-4111-8111-111111111111";
const O1 = "33333333-3333-4333-8333-333333333333";
const ANCHO_TELEFONO_PX = 360;

// Los textos más largos que puede traer la API: el teléfono no puede ensancharse por ellos.
const PANEL = {
  ordenes_por_estado: [
    { estado: "en_verificacion", moneda: "ARS", cantidad: 12, importe_centavos: 123_456_789_00 },
    { estado: "pagada", moneda: "ARS", cantidad: 3, importe_centavos: 300_000 },
  ],
  gasto_pagado: [
    { periodo: "2026-10-01", rubro: "Mantenimiento de ascensores y montacargas del edificio", fondo_reserva: true, moneda: "ARS", cantidad: 1, importe_centavos: 98_765_432_10 },
    { periodo: "2026-11-01", rubro: null, fondo_reserva: false, moneda: "ARS", cantidad: 2, importe_centavos: 150_000 },
  ],
  comprobantes_faltantes: [
    {
      orden_id: O1,
      estado: "en_verificacion",
      proveedor: "Ascensores, Montacargas y Escaleras Mecánicas del Río de la Plata Sociedad Anónima",
      concepto: "Abono mensual de mantenimiento preventivo y correctivo de noviembre",
      importe_centavos: 98_765_432_10,
      moneda: "ARS",
      actualizada_en: "2026-11-05T12:00:00Z",
      version: 1,
      factura_subida: null,
    },
  ],
};
const D1 = "55555555-5555-4555-8555-555555555555";
const V1 = "66666666-6666-4666-8666-666666666666";

function vigilarErrores(pagina: Page): string[] {
  const errores: string[] = [];
  pagina.on("pageerror", (e) => errores.push(e.message));
  pagina.on("console", (m) => {
    if (m.type() === "error") errores.push(m.text());
  });
  void pagina.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", (e) => console.error(`CSP: ${e.violatedDirective} ${e.blockedURI}`));
  });
  return errores;
}

async function simularApi(pagina: Page, subidas: string[] = []): Promise<string[]> {
  const imprevistos: string[] = [];
  await pagina.route("**/api/v1/**", async (ruta) => {
    const pedido = ruta.request();
    const camino = new URL(pedido.url()).pathname;
    if (camino === "/api/v1/auth/sesion") return ruta.fallback();
    const permisos = simularPermisos(ruta, camino);
    if (permisos !== null) return permisos;
    // Subir la factura que falta (app 0099): el archivo crudo, con la versión y el título en la consulta.
    if (pedido.method() === "POST" && camino === `/api/v1/consorcios/${C1}/ordenes-pago/${O1}/factura`) {
      subidas.push(`${new URL(pedido.url()).search} ${pedido.headers()["content-type"]}`);
      return ruta.fulfill({
        status: 201,
        json: { orden_id: O1, version: 1, documento_id: D1,
                archivo: { id: V1, estado: "en_cuarentena", tipo: "image/png", tamano: 68, sha256: "a".repeat(64) } },
      });
    }
    if (pedido.method() === "GET" && camino === `/api/v1/consorcios/${C1}/documentos/${D1}/versiones/${V1}/estado`)
      return ruta.fulfill({ json: { estado: "disponible", motivo_rechazo: null } });
    if (pedido.method() === "GET") {
      if (camino === "/api/v1/me/consorcios") return ruta.fulfill({ json: [{ id: C1, nombre: "Edificio Azul", vinculos: [] }] });
      if (camino === `/api/v1/consorcios/${C1}/panel`) return ruta.fulfill({ json: PANEL });
      if (camino === `/api/v1/consorcios/${C1}/pagos/bandeja`) return ruta.fulfill({ json: { elementos: [], avisos_rechazo: [], por_cotejar: 0, sin_explicar: 0 } });
      if (camino === "/api/v1/pagos-por-aclarar" || camino === "/api/v1/me/invitaciones")
        return ruta.fulfill({ json: { elementos: [], avisos_rechazo: [], por_cotejar: 0, sin_explicar: 0 } });
    }
    imprevistos.push(`${pedido.method()} ${camino}`);
    return ruta.abort();
  });
  return imprevistos;
}

const sinDesplazamiento = (pagina: Page) =>
  pagina.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth);

test.describe("panel sin sesión", () => {
  test.use(otroCliente(11));

  test("el backend real responde 401 y la página manda a /entrar", async ({ page }) => {
    const errores = vigilarErrores(page);
    const consulta = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/v1/me/consorcios");
    await page.goto("/panel");
    expect((await consulta).status()).toBe(401);
    await expect(page).toHaveURL(/\/entrar$/);
    expect(errores.filter((e) => !e.includes("401"))).toEqual([]);
  });
});

test.describe("panel", () => {
  test("en un teléfono de 360 px los gráficos y sus tablas entran sin desplazar la página", async ({ page }) => {
    const errores = vigilarErrores(page);
    await page.setViewportSize({ width: ANCHO_TELEFONO_PX, height: 740 });
    const imprevistos = await simularApi(page);
    await simularSesion(page, "con_sesion");
    await page.goto("/");
    await page.getByRole("link", { name: "Resumen de pagos del edificio" }).click();
    await expect(page).toHaveURL(/\/panel$/);

    await expect(page.getByRole("region", { name: "Órdenes de pago por estado" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Pagos con el fondo de reserva" })).toBeVisible();
    const faltantes = page.getByRole("region", { name: "Facturas faltantes" });
    await expect(faltantes.getByRole("link", { name: PANEL.comprobantes_faltantes[0]!.proveedor })).toHaveAttribute("href", `/pagos/${O1}`);
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);

    // Abiertas las tablas equivalentes, tampoco.
    for (const resumen of await page.getByText("Ver los datos en una tabla").all()) await resumen.click();
    await expect(page.getByRole("table", { name: "Gasto pagado por mes" })).toBeVisible();
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);
    // Las barras son SVG en línea: la CSP no tiene que frenar nada.
    expect(await page.locator("svg.grafico__dibujo").count()).toBeGreaterThan(0);
    expect(errores).toEqual([]);
    expect(imprevistos).toEqual([]);
  });
});

test.describe("subir la factura que falta (app 0099)", () => {
  // Un PNG de 1x1: SINTETICO.
  const PNG = Buffer.from(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000" + "1f15c4890000000d4944415478da63f8cfc0f01f0005000100ff899973" + "1d0000000049454e44ae426082",
    "hex",
  );

  test("en un teléfono de 360 px la hoja entra, sube el archivo y dice lo que pasó", async ({ page }) => {
    const errores = vigilarErrores(page);
    await page.setViewportSize({ width: ANCHO_TELEFONO_PX, height: 740 });
    const subidas: string[] = [];
    const imprevistos = await simularApi(page, subidas);
    await simularSesion(page, "con_sesion");
    await page.goto("/panel");

    const faltantes = page.getByRole("region", { name: "Facturas faltantes" });
    await faltantes.getByRole("button", { name: "Subir la factura", exact: true }).click();
    const hoja = page.getByRole("dialog", { name: "Subir la factura", exact: true });
    await expect(hoja).toBeVisible();
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);

    await hoja.getByLabel("Archivo de la factura", { exact: true }).setInputFiles({ name: "Factura 0001.png", mimeType: "image/png", buffer: PNG });
    await expect(hoja.getByText("Listo: la factura quedó adjunta a la orden. El antivirus la está revisando.")).toBeVisible();
    expect(subidas).toEqual(["?version_esperada=1&titulo=Factura%200001 image/png"]);
    // La hoja pregunta cada 3 s cómo terminó el antivirus.
    await expect(hoja.getByText(/^El antivirus aceptó la factura/)).toBeVisible({ timeout: 10_000 });
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);
    await hoja.getByRole("button", { name: "Cerrar", exact: true }).click();
    await expect(hoja).toBeHidden();
    expect(errores).toEqual([]);
    expect(imprevistos).toEqual([]);
  });
});
