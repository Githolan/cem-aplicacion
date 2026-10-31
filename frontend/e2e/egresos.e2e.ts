import { expect, type Page, test } from "@playwright/test";

import { sinFaltasDeAccesibilidad } from "./entorno";

// Las páginas de egresos servidas por el nginx de la imagen, con su CSP real y la API simulada
// desde el navegador, como en padron.e2e.ts. Mensaje del propietario del 30-10-2026, puntos 31 a
// 33: que entren en un teléfono de 360 px, WCAG AA, que pasar del edificio A al B y volver al A
// nunca muestre datos de otro edificio, y que la cuenta completa no esté en el documento salvo en
// la revisión del Consejo (la privacidad la hace cumplir el backend; acá se ve que la página no
// agrega nada).
const CSRF = "e2e-csrf-egresos";
const A = "a1111111-1111-4111-8111-111111111111";
const B = "b2222222-2222-4222-8222-222222222222";
const PROV_A = "a3333333-3333-4333-8333-333333333333";
const PROV_B = "b4444444-4444-4444-8444-444444444444";
const ORDEN = "c5555555-5555-4555-8555-555555555555";
const ADM = "d6666666-6666-4666-8666-666666666666";
const CUENTA = "2850590940090418135201"; // sintética, de formato válido
const ANCHO_TELEFONO_PX = 360;
// Una razón social del largo que admite la base: la tarjeta no puede ensanchar la página.
const RAZON_LARGA = "Mantenimiento Integral de Ascensores y Equipos Electromecánicos del Río de la Plata S.R.L.";

const proveedor = (id: string, razon: string, ultimos: string) => ({
  id, cuit: "30712345679", version: 1, razon_social: razon, rubro: "Ascensores", contacto: null,
  estado: "activo", documento_id: null, cuenta_ultimos_digitos: ultimos, huella: "ab".repeat(32),
  alta_por: ADM, creado_en: "2026-10-30T12:00:00Z", actualizado_en: "2026-10-30T12:00:00Z",
});

const ORDEN_A = {
  id: ORDEN, estado: "en_revision", version_vigente: 1, preparada_por: ADM, creada_en: "2026-10-30T12:00:00Z",
  actualizada_en: "2026-10-30T12:00:00Z", motivo_rechazo: null, importe_centavos: 12_000_000, moneda: "ARS",
  concepto: "SINTETICO: reparación del ascensor", proveedor_id: PROV_A, razon_social: RAZON_LARGA,
  condicion_beneficiario: "conocido", usa_fondo_reserva: false, aprobaciones_requeridas: 1, aprobaciones: 0,
  franja: "sobre_umbral", alerta_fraccionamiento: true, aprobada_en: null, aprobacion_vence_en: null,
};

const version = (cuentaDestino: string | null) => ({
  numero: 1, importe_centavos: 12_000_000, moneda: "ARS", concepto: ORDEN_A.concepto, comprobante: "Factura A 0001-00000042",
  usa_fondo_reserva: false, huella: "cd".repeat(32), preparada_por: ADM, creada_en: "2026-10-30T12:00:00Z",
  aprobaciones_requeridas: 1, proveedor_id: PROV_A, proveedor_version: 1, condicion_beneficiario: "conocido",
  razon_social: RAZON_LARGA, cuit: "30712345679", cuenta_ultimos_digitos: CUENTA.slice(-4), aprobada_por: [],
  franja: "sobre_umbral", alerta_fraccionamiento: true, umbral_importe_centavos: 10_000_000, umbral_moneda: "ARS",
  contrato_id: null, cuenta_destino: cuentaDestino,
});

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

/** La API de egresos de dos edificios. `revisa` decide qué devuelve la revisión: la cuenta
 * completa (un consejero que no preparó) o nula (quien preparó). Anota cada lectura. */
async function simularApi(pagina: Page, baseURL: string, revisa: "consejero" | "quien_preparo"): Promise<string[]> {
  const lecturas: string[] = [];
  await pagina.route("**/api/v1/**", async (ruta) => {
    const pedido = ruta.request();
    const camino = new URL(pedido.url()).pathname;
    if (pedido.method() !== "GET") return ruta.abort();
    lecturas.push(camino);
    if (camino === "/api/v1/me/consorcios")
      return ruta.fulfill({ json: [{ id: A, nombre: "Edificio Azul", vinculos: [] }, { id: B, nombre: "Edificio Bordó", vinculos: [] }] });
    if (camino === `/api/v1/consorcios/${A}/proveedores`)
      return ruta.fulfill({ json: { elementos: [proveedor(PROV_A, RAZON_LARGA, CUENTA.slice(-4))] } });
    if (camino === `/api/v1/consorcios/${B}/proveedores`)
      return ruta.fulfill({ json: { elementos: [proveedor(PROV_B, "Limpieza Bordó SINTETICO", "9876")] } });
    if (camino === `/api/v1/consorcios/${A}/ordenes-pago/revision`) return ruta.fulfill({ json: { elementos: [ORDEN_A] } });
    if (camino === `/api/v1/consorcios/${A}/ordenes-pago/${ORDEN}/revision`)
      return ruta.fulfill({ json: { orden: ORDEN_A, versiones: [version(revisa === "consejero" ? CUENTA : null)] } });
    return ruta.abort();
  });
  await pagina.context().addCookies([
    { name: "__Host-cem_csrf", value: CSRF, domain: new URL(baseURL).hostname, path: "/", secure: true },
  ]);
  return lecturas;
}

async function entraEnElTelefono(pagina: Page) {
  expect(await pagina.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(ANCHO_TELEFONO_PX);
}

test.describe("egresos en un teléfono", () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: ANCHO_TELEFONO_PX, height: 740 });
  });

  test("proveedores: A, B y de nuevo A, cada uno con sus datos y la cuenta enmascarada", async ({ page, baseURL }) => {
    const errores = vigilarErrores(page);
    const lecturas = await simularApi(page, baseURL!, "consejero");
    await page.goto("/egresos/proveedores");
    const edificio = page.getByLabel("Edificio");

    await expect(page.getByRole("heading", { name: RAZON_LARGA })).toBeVisible();
    await expect(page.getByText(`…${CUENTA.slice(-4)}`)).toBeVisible();
    await entraEnElTelefono(page);
    await sinFaltasDeAccesibilidad(page);

    await edificio.selectOption(B);
    await expect(page.getByRole("heading", { name: "Limpieza Bordó SINTETICO" })).toBeVisible();
    await expect(page.getByRole("heading", { name: RAZON_LARGA })).toHaveCount(0);
    await entraEnElTelefono(page);

    await edificio.selectOption(A);
    await expect(page.getByRole("heading", { name: RAZON_LARGA })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Limpieza Bordó SINTETICO" })).toHaveCount(0);

    const deProveedores = lecturas.filter((l) => l.endsWith("/proveedores"));
    expect(deProveedores).toEqual([A, B, A].map((c) => `/api/v1/consorcios/${c}/proveedores`));
    expect(await page.content()).not.toContain(CUENTA);
    expect(errores).toEqual([]);
  });

  test("revisión: el consejero ve la cuenta completa, la alerta de fraccionamiento y entra en 360 px", async ({ page, baseURL }) => {
    const errores = vigilarErrores(page);
    await simularApi(page, baseURL!, "consejero");
    await page.goto("/egresos/revision");
    await expect(page.getByText("Posible fraccionamiento: existen órdenes recientes relacionadas")).toBeVisible();
    await entraEnElTelefono(page);
    await sinFaltasDeAccesibilidad(page);

    const revisar = page.getByRole("button", { name: `Revisar la orden de ${RAZON_LARGA}` });
    expect((await revisar.boundingBox())?.height).toBeGreaterThanOrEqual(44);
    await revisar.click();
    await expect(page.getByText(CUENTA)).toBeVisible();
    await entraEnElTelefono(page);
    await sinFaltasDeAccesibilidad(page);
    expect(errores).toEqual([]);
  });

  test("revisión: a quien preparó la orden el backend no le da la cuenta y la página muestra los últimos dígitos", async ({ page, baseURL }) => {
    await simularApi(page, baseURL!, "quien_preparo");
    await page.goto("/egresos/revision");
    await page.getByRole("button", { name: `Revisar la orden de ${RAZON_LARGA}` }).click();
    await expect(page.getByText(`…${CUENTA.slice(-4)}`)).toBeVisible();
    expect(await page.content()).not.toContain(CUENTA);
    await entraEnElTelefono(page);
  });
});
