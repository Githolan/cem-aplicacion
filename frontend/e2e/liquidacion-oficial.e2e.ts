import { expect, type Page, test } from "@playwright/test";

import { simularInicioSinPendientes, simularPermisos, simularSesion, sinFaltasDeAccesibilidad } from "./entorno";

// «Mis expensas» y «Liquidación oficial» (H5 D3-A, app 0119) servidas por el nginx de la imagen, con
// su CSP real; la API se simula desde el navegador. Quién lee qué lo decide la API y lo prueba el
// backend (`test_liquidacion_oficial_0119`). Acá se ve lo que solo un navegador real muestra: que
// las dos pantallas entran en un teléfono de 360 px sin desplazar la página, `axe` con contraste y
// que la CSP no frena nada. Datos SINTETICOS / NO REAL; los textos, los más largos que puede traer.
const C1 = "11111111-1111-4111-8111-111111111111";
const A = "22222222-2222-4222-8222-222222222222";
const B = "33333333-3333-4333-8333-333333333333";
const U1 = "44444444-4444-4444-8444-444444444444";
const PERSONA = "88888888-8888-4888-8888-888888888888";
const ANCHO_TELEFONO_PX = 360;
const RUBRO_LARGO = "Mantenimiento de ascensores, montacargas y escaleras mecánicas del edificio";

const MI_CUENTA = {
  periodos: [
    { periodo: "2026-10-01", confirmada_en: "2026-11-05T15:00:00Z" },
    { periodo: "2026-09-01", confirmada_en: "2026-10-05T15:00:00Z" },
  ],
  liquidacion: {
    periodo: "2026-10-01",
    confirmada_en: "2026-11-05T15:00:00Z",
    vencimientos: ["13/11/2026", "23/11/2026"],
    unidades: [
      { unidad_id: U1, designacion: "Piso 12 Departamento B (con cochera 4)", figura: true, saldo_anterior: "123456789.00", pagos: "123456789.00",
        deuda: "0.00", interes: "0.00", disponible: null, grupos: [], otros_gastos: null, redondeo: null, total: "98765432.10" },
      { unidad_id: B, designacion: "Local 1", figura: false, saldo_anterior: null, pagos: null, deuda: null, interes: null, disponible: null,
        grupos: [], otros_gastos: null, redondeo: null, total: null },
    ],
  },
};  // prettier-ignore
const RESUMEN = {
  periodos: MI_CUENTA.periodos,
  liquidacion: {
    periodo: "2026-10-01",
    confirmada_en: "2026-11-05T15:00:00Z",
    rubros: [
      { numero: 1, nombre: "Servicios públicos", total: "1500.50" },
      { numero: 2, nombre: RUBRO_LARGO, total: "98765432.10" },
    ],
    grupos: [{ nombre: "Gastos Comunes", total: "98766932.60" }],
    total: "98766932.60",
    vencimientos: ["13/11/2026", "23/11/2026"],
  },
  // App 0120: el total de cada período, con importes de los más largos.
  gasto_por_periodo: [
    { periodo: "2026-09-01", total: "123456789012.34" },
    { periodo: "2026-10-01", total: "98766932.60" },
  ],
};
const VIEJA = {
  id: A, periodo: "2026-10-01", formato: "mis_expensas", decidida_por: PERSONA, decidida_en: "2026-11-01T15:00:00Z", vigente: false,
  reemplaza_a: null, motivo_reemplazo: null, reemplazada_por: B, diferencias: 1,
};  // prettier-ignore
const NUEVA = {
  ...VIEJA, id: B, decidida_en: "2026-11-05T15:00:00Z", vigente: true, reemplaza_a: A, reemplazada_por: null, diferencias: 0,
  motivo_reemplazo: "La administración mandó otra versión con el rubro de ascensores corregido después del reclamo del Consejo",
};  // prettier-ignore
const DETALLE = {
  ...NUEVA,
  diferencias: [{ regla: "totales_gastos", detalle: "total de gastos", pagina: 1, renglon: "295.0", esperado: "98766932.60", informado: "98766932.65" }],
  campos: [
    { orden: 0, tipo: "total", clave: "total:gastos", valor: { grupos: {}, total: "98766932.60", vencimientos: ["13/11/2026"] }, unidad_id: null, designacion: null },
    { orden: 1, tipo: "rubro", clave: "rubro:2", valor: { numero: 2, nombre: RUBRO_LARGO, total: "98765432.10" }, unidad_id: null, designacion: null },
    { orden: 2, tipo: "gasto", clave: "gasto:2:1", valor: { rubro: 2, concepto: "Ascensores, Montacargas y Escaleras Mecánicas del Río de la Plata Sociedad Anónima - Abono", total: "98765432.10" }, unidad_id: null, designacion: null },
    { orden: 3, tipo: "saldo_unidad", clave: "saldo_unidad:012", valor: { uf: "012", depto: "12-B", saldo_anterior: "123456789.00", pagos: "123456789.00" }, unidad_id: U1, designacion: "Piso 12 Departamento B (con cochera 4)" },
    { orden: 4, tipo: "importe_unidad", clave: "importe_unidad:012", valor: { uf: "012", depto: "12-B", total: "98765432.10" }, unidad_id: U1, designacion: "Piso 12 Departamento B (con cochera 4)" },
  ],
};  // prettier-ignore

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

async function simularApi(pagina: Page, acciones: readonly string[]): Promise<string[]> {
  const imprevistos: string[] = [];
  await pagina.route("**/api/v1/**", async (ruta) => {
    const pedido = ruta.request();
    const camino = new URL(pedido.url()).pathname;
    if (camino === "/api/v1/auth/sesion") return ruta.fallback();
    const permisos = simularPermisos(ruta, camino, acciones);
    if (permisos !== null) return permisos;
    if (pedido.method() === "GET") {
      if (camino === "/api/v1/me/consorcios") return ruta.fulfill({ json: [{ id: C1, nombre: "Edificio Azul", vinculos: [], monedas: ["ARS"] }] });
      if (camino === `/api/v1/consorcios/${C1}/mi-cuenta`) return ruta.fulfill({ json: MI_CUENTA });
      if (camino === `/api/v1/consorcios/${C1}/resumen`) return ruta.fulfill({ json: RESUMEN });
      if (camino === `/api/v1/consorcios/${C1}/liquidacion-oficial`) return ruta.fulfill({ json: [NUEVA, VIEJA] });
      if (camino === `/api/v1/consorcios/${C1}/liquidacion-oficial/${B}`) return ruta.fulfill({ json: DETALLE });
    }
    const inicio = simularInicioSinPendientes(ruta, camino);
    if (inicio !== null) return inicio;
    imprevistos.push(`${pedido.method()} ${camino}`);
    return ruta.abort();
  });
  return imprevistos;
}

const sinDesplazamiento = (pagina: Page) =>
  pagina.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth);

test.describe("liquidación oficial (app 0119)", () => {
  test("«Mis expensas» entra en un teléfono de 360 px, con el gráfico y su base", async ({ page }) => {
    const errores = vigilarErrores(page);
    await page.setViewportSize({ width: ANCHO_TELEFONO_PX, height: 740 });
    const imprevistos = await simularApi(page, ["cuenta.ver_propia", "fiscalizacion.ver_resumen", "documentos.ver", "contactos.ver"]);
    await simularSesion(page, "con_sesion");
    await page.goto("/mis-expensas");

    await expect(page.getByText(/según la liquidación de la administración, confirmada el/)).toBeVisible();
    await expect(page.getByRole("region", { name: "Unidad Piso 12 Departamento B (con cochera 4)" })).toBeVisible();
    await expect(page.getByText("Esta unidad no figura en la liquidación de la administración.")).toBeVisible();
    await expect(page.getByRole("region", { name: "Gasto del período por rubro" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Gasto por período" })).toBeVisible();
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);
    for (const resumen of await page.getByText("Cómo se calcula").all()) await resumen.click();
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);
    // Las barras son SVG en línea: la CSP no tiene que frenar nada. Dos rubros y dos períodos.
    expect(await page.locator("svg.grafico__dibujo").count()).toBe(4);
    expect(errores).toEqual([]);
    expect(imprevistos).toEqual([]);
  });

  test("«Liquidación oficial» entra en un teléfono de 360 px, con sus tablas y la historia", async ({ page }) => {
    const errores = vigilarErrores(page);
    await page.setViewportSize({ width: ANCHO_TELEFONO_PX, height: 740 });
    const imprevistos = await simularApi(page, ["liquidacion.ver_importada", "fiscalizacion.ver_panel", "documentos.ver", "contactos.ver"]);
    await simularSesion(page, "con_sesion");
    await page.goto("/");
    await page.getByRole("link", { name: "Liquidación oficial" }).click();
    await expect(page).toHaveURL(/\/liquidacion-oficial$/);

    const lista = page.getByRole("list", { name: "Liquidaciones oficiales" });
    await expect(lista.getByRole("button", { name: /Reemplazada/ })).toBeVisible();
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);
    await lista.getByRole("button", { name: /Vigente/ }).click();
    await expect(page.getByRole("table", { name: "Unidades" })).toBeVisible();
    await expect(page.getByText(/Reemplazó a la liquidación confirmada el .*Motivo: La administración mandó otra versión/)).toBeVisible();
    await expect(page.getByRole("list", { name: "Lo que no cierra" })).toBeVisible();
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);
    expect(errores).toEqual([]);
    expect(imprevistos).toEqual([]);
  });
});
