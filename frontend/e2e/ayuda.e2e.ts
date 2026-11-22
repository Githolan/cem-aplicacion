import { expect, type Page, test } from "@playwright/test";

import { accionesDeLasPoliticas } from "../src/accionesDePrueba";
import { simularInicioSinPendientes, simularPermisos, simularSesion, sinFaltasDeAccesibilidad } from "./entorno";

// Ayuda por una persona autorizada (RF53, app 0127) servida por el nginx de la imagen, con su CSP
// real; la API se simula desde el navegador. Que el evaluador y la base rechacen todo lo decisorio,
// el alcance y la revocación lo prueba el backend (`test_ayuda_autorizada_0127`). Acá, en un teléfono
// de 360 px, con `axe` y sin desplazar la página: el propietario autoriza; quien recibió la
// autorización empieza a ayudar, ve el aviso, cada pedido al edificio de la ayuda lleva el encabezado
// y lo decisorio (Pagos) no se ofrece; deja de ayudar; el propietario revoca. Datos SINTETICOS.
const C1 = "11111111-1111-4111-8111-111111111111";
const C2 = "22222222-2222-4222-8222-222222222222";
const A1 = "55555555-5555-4555-8555-555555555555";
const ANCHO_TELEFONO_PX = 360;
const ENCABEZADO = "x-cem-ayuda";
const PROPIA = "Dora María de los Ángeles Fernández SINTETICA";
const QUIEN_AYUDA = "Beto Roberto Gutiérrez Larralde SINTETICO";
const EDIFICIO_AYUDA = "Torre de la Avenida Libertador 4500 SINTETICA";
/** Lo que el servidor deja ayudando con «consultar» y «preparar»: ninguna acción decisoria. */
const ACCIONES_AYUDANDO = ["consorcios.mis_acciones", "cuenta.ver_propia", "fiscalizacion.ver_resumen", "reclamos_liquidacion.ver", "reclamos_liquidacion.presentar"];

type Estado = { otorgada: boolean; revocada: boolean };

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

/** La API: lo de la propia cuenta en C1, y lo de la ayuda en C2, solo con el encabezado. Un pedido
 * a C2 sin el encabezado, o uno a otro lado con él, queda en `imprevistos`. */
async function simularApi(pagina: Page, estado: Estado): Promise<string[]> {
  const imprevistos: string[] = [];
  await pagina.route("**/api/v1/**", async (ruta) => {
    const pedido = ruta.request();
    const camino = new URL(pedido.url()).pathname;
    const ayuda = (await pedido.allHeaders())[ENCABEZADO];
    const aC2 = camino.startsWith(`/api/v1/consorcios/${C2}/`);
    const admite = aC2 || camino === "/api/v1/me/consorcios";
    if ((aC2 && ayuda !== A1) || (ayuda !== undefined && !admite)) imprevistos.push(`encabezado ${ayuda ?? "-"} en ${pedido.method()} ${camino}`);
    if (ayuda === A1 && estado.revocada)
      return ruta.fulfill({ status: 403, json: { codigo: "ayuda_revocada", mensaje: "La persona asistida dejó de autorizar esta ayuda.", detalle: null, id_correlacion: "e2e" } });
    if (camino === "/api/v1/me/consorcios")
      return ruta.fulfill({
        json: ayuda === A1 ? [{ id: C2, nombre: EDIFICIO_AYUDA, vinculos: [], monedas: ["ARS"] }] : [{ id: C1, nombre: "Edificio Azul", vinculos: [], monedas: ["ARS"] }],
      });
    if (camino === `/api/v1/consorcios/${C2}/mis-acciones`) return ruta.fulfill({ json: { acciones: ACCIONES_AYUDANDO } });
    const permisos = simularPermisos(ruta, camino, accionesDeLasPoliticas());
    if (permisos !== null) return permisos;
    const otorgada = {
      id: A1, autorizado_nombre: QUIEN_AYUDA, alcances: ["consultar_expensas", "preparar_reclamos"], otorgada_en: "2026-11-22T10:00:00Z",
      revocada_en: estado.revocada ? "2026-11-22T11:00:00Z" : null,
    };  // prettier-ignore
    const ayudas = `/api/v1/consorcios/${C1}/ayudas`;
    if (pedido.method() === "GET" && camino === ayudas)
      return ruta.fulfill({ json: { elementos: estado.otorgada ? [otorgada] : [], alcances_otorgables: ["consultar_expensas", "preparar_reclamos"] } });
    if (pedido.method() === "POST" && camino === ayudas) {
      expect(pedido.postDataJSON()).toEqual({ correo: "beto@ejemplo.com", alcances: ["preparar_reclamos", "consultar_expensas"] });
      estado.otorgada = true;
      return ruta.fulfill({ status: 201, json: otorgada });
    }
    if (pedido.method() === "POST" && camino === `${ayudas}/${A1}/revocacion`) {
      estado.revocada = true;
      return ruta.fulfill({ json: { ...otorgada, revocada_en: "2026-11-22T11:00:00Z" } });
    }
    if (camino === "/api/v1/me/ayudas") {
      const recibida = { id: A1, consorcio_id: C2, consorcio_nombre: EDIFICIO_AYUDA, otorgante_nombre: PROPIA, alcances: otorgada.alcances, otorgada_en: otorgada.otorgada_en };
      return ruta.fulfill({ json: { elementos: estado.otorgada && !estado.revocada ? [recibida] : [] } });
    }
    // El inicio de la propia cuenta (con todo el catálogo): sin pendientes.
    if (camino === `/api/v1/consorcios/${C1}/padron`) return ruta.fulfill({ json: { elementos: [] } });
    if (camino === `/api/v1/consorcios/${C1}/pagos/bandeja`)
      return ruta.fulfill({ json: { elementos: [], avisos_rechazo: [], por_cotejar: 0, sin_explicar: 0 } });
    const inicio = simularInicioSinPendientes(ruta, camino);
    if (inicio !== null) return inicio;
    imprevistos.push(`${pedido.method()} ${camino}`);
    return ruta.abort();
  });
  return imprevistos;
}

const sinDesplazamiento = (pagina: Page) =>
  pagina.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth);

async function controlar(pagina: Page) {
  expect(await sinDesplazamiento(pagina)).toBe(true);
  await sinFaltasDeAccesibilidad(pagina);
}

test.describe("ayuda por una persona autorizada (app 0127)", () => {
  test("autorizar, ayudar sin lo decisorio, dejar de ayudar y revocar entran en 360 px", async ({ page }) => {
    const errores = vigilarErrores(page);
    await page.setViewportSize({ width: ANCHO_TELEFONO_PX, height: 740 });
    const estado: Estado = { otorgada: false, revocada: false };
    const imprevistos = await simularApi(page, estado);
    await simularSesion(page, "con_sesion");
    const navegacion = page.getByRole("navigation", { name: "Secciones" });

    // 1. El propietario autoriza.
    await page.goto("/ayuda");
    await expect(page.getByText("Nadie tiene autorización para ayudar en este edificio.")).toBeVisible();
    await controlar(page);
    await page.getByRole("button", { name: "Autorizar a una persona" }).click();
    const ventana = page.getByRole("dialog", { name: "Autorizar a una persona" });
    await ventana.getByLabel("Correo de la persona").fill("beto@ejemplo.com");
    await ventana.getByRole("checkbox", { name: "Presentar y retirar reclamos sobre la liquidación" }).check();
    await expect(ventana.getByRole("checkbox", { name: "Consultar las expensas y los reclamos" })).toBeChecked();
    await controlar(page);
    await ventana.getByRole("button", { name: "Autorizar", exact: true }).click();
    await expect(page.getByText(`Se autorizó a ${QUIEN_AYUDA} a ayudar.`)).toBeVisible();
    await controlar(page);

    // 2. Quien recibió la autorización empieza a ayudar. Antes, con su propia cuenta, ve Pagos.
    await page.reload();
    await expect(navegacion.locator('a[href="/pagos"]')).toBeVisible();
    await page.getByRole("button", { name: `Ayudar a ${PROPIA}` }).click();
    await page.waitForURL((url) => url.pathname === "/");
    const aviso = page.getByRole("region", { name: "Ayuda en curso" });
    await expect(aviso.getByText(`Se está ayudando a ${PROPIA} en ${EDIFICIO_AYUDA}.`)).toBeVisible();
    await expect(aviso.getByText("Ayudando no se vota, firma, aprueba ni paga.")).toBeVisible();

    // 3. Lo decisorio no se ofrece: el menú sale de lo que el servidor deja ayudando.
    await expect(navegacion.locator('a[href="/mis-expensas"]')).toBeVisible();
    await expect(navegacion.locator('a[href="/pagos"]')).toHaveCount(0);
    expect((await aviso.getByRole("button", { name: "Dejar de ayudar" }).boundingBox())?.height).toBeGreaterThanOrEqual(44);
    await controlar(page);

    // 4. Deja de ayudar: el aviso se va y vuelve lo de su propia cuenta.
    await aviso.getByRole("button", { name: "Dejar de ayudar" }).click();
    await expect(aviso).toHaveCount(0);
    await expect(navegacion.locator('a[href="/pagos"]')).toBeVisible();
    expect(await page.evaluate(() => sessionStorage.getItem("cem:ayuda"))).toBeNull();

    // 5. El propietario revoca.
    await page.goto("/ayuda");
    await page.getByRole("list", { name: "Autorizaciones para ayudar" }).getByRole("button", { name: new RegExp(QUIEN_AYUDA) }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Revocar la autorización" }).click();
    const revocar = page.getByRole("dialog", { name: `Revocar la autorización de ${QUIEN_AYUDA}` });
    await controlar(page);
    await revocar.getByRole("button", { name: "Sí, revocar" }).click();
    await expect(page.getByText(`Se revocó la autorización de ${QUIEN_AYUDA}.`)).toBeVisible();
    await controlar(page);
    // Revocada, ya no se ofrece ayudar (la lista de lo recibido se lee al abrir la página).
    await page.reload();
    await expect(page.getByRole("list", { name: "Autorizaciones para ayudar" })).toBeVisible();
    await expect(page.getByRole("button", { name: `Ayudar a ${PROPIA}` })).toHaveCount(0);
    expect(estado).toEqual({ otorgada: true, revocada: true });
    expect(imprevistos).toEqual([]);
    expect(errores).toEqual([]);
  });

  test("si la persona asistida revoca mientras se ayuda, la pestaña sale sola y lo dice, en 360 px", async ({ page }) => {
    const errores: string[] = [];
    page.on("pageerror", (e) => errores.push(e.message));
    page.on("console", (m) => {
      // El 403 `ayuda_revocada` es la respuesta esperada de esta prueba, no una falla de la página.
      if (m.type() === "error" && !m.text().includes("403")) errores.push(m.text());
    });
    await page.setViewportSize({ width: ANCHO_TELEFONO_PX, height: 740 });
    const estado: Estado = { otorgada: true, revocada: false };
    const imprevistos = await simularApi(page, estado);
    await simularSesion(page, "con_sesion");
    await page.goto("/ayuda");
    await page.getByRole("button", { name: `Ayudar a ${PROPIA}` }).click();
    await page.waitForURL((url) => url.pathname === "/");
    await expect(page.getByRole("region", { name: "Ayuda en curso" })).toBeVisible();

    estado.revocada = true;
    await page.goto("/mis-expensas");
    await expect(page.getByText(`${PROPIA} dejó de autorizar la ayuda. Esta pestaña volvió a la cuenta propia.`)).toBeVisible();
    await expect(page.getByRole("region", { name: "Ayuda en curso" })).toHaveCount(0);
    await expect(page).toHaveURL((url) => url.pathname === "/");
    expect(await page.evaluate(() => sessionStorage.getItem("cem:ayuda"))).toBeNull();
    await controlar(page);
    expect(imprevistos).toEqual([]);
    expect(errores).toEqual([]);
  });
});
