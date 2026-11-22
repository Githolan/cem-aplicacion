import { expect, type Page, test } from "@playwright/test";

import { simularInicioSinPendientes, simularPermisos, simularSesion, sinFaltasDeAccesibilidad } from "./entorno";

// Bandeja del Consejo (RF40, P0 S6): el inicio es la única bandeja (decisión 9 del 18-11-2026). Servida
// por el nginx de la imagen, con su CSP real; la API se simula desde el navegador. Qué cuenta y a quién
// lo deciden la API y el backend (`test_bandeja_consejo_0126`); acá se ve lo que solo un navegador real
// muestra: que las filas del Consejo con dos edificios de nombre largo entran en un teléfono de 360 px
// sin desplazar la página, que cada una lleva a su pantalla, `axe` con contraste, y que sin nada lo
// dice en una línea. Datos SINTETICOS / NO REAL.
const C1 = "11111111-1111-4111-8111-111111111111";
const C2 = "22222222-2222-4222-8222-222222222222";
const ANCHO_TELEFONO_PX = 360;
const NOMBRE_LARGO = "Consorcio de Propietarios Avenida Rivadavia 12345 Torre Norte";
const CONSEJO = [
  "fiscalizacion.ver_panel",
  "solicitud_formal.responder",
  "liquidacion.importar",
  "reclamos_liquidacion.atender",
  "reparaciones.ver",
  "reparaciones.gestionar",
  "contactos.ver",
];

// Una importación completa: la pantalla de destino la muestra entera al abrir la fila.
const importacion = (id: string) => ({
  id, documento_id: id, version_documento_id: id, subida_por: id, creada_en: "2026-11-20T10:00:00Z", estado: "para_confirmar",
  formato: "mis_expensas", periodo: "2026-10-01", motivo: null, diferencias: 0, campos: 12, pendientes: 3, decidida_por: null,
  decidida_en: null, motivo_descarte: null, reemplaza_a: null, motivo_reemplazo: null,
});  // prettier-ignore
const I1 = "33333333-3333-4333-8333-333333333333";
const I2 = "44444444-4444-4444-8444-444444444444";

const solicitud = (estado: string, vence: string, porVencer: boolean, acciones: string[] = []) => ({ estado, acciones, envio: { vence_el: vence }, por_vencer: porVencer });

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

async function simularApi(pagina: Page, conPendientes: boolean): Promise<string[]> {
  const imprevistos: string[] = [];
  await pagina.route("**/api/v1/**", async (ruta) => {
    const pedido = ruta.request();
    const camino = new URL(pedido.url()).pathname;
    if (camino === "/api/v1/auth/sesion") return ruta.fallback();
    const permisos = simularPermisos(ruta, camino, CONSEJO);
    if (permisos !== null) return permisos;
    if (pedido.method() === "GET") {
      if (camino === "/api/v1/me/consorcios")
        return ruta.fulfill({
          json: [
            { id: C1, nombre: NOMBRE_LARGO, vinculos: [], monedas: ["ARS"] },
            { id: C2, nombre: `${NOMBRE_LARGO} (anexo)`, vinculos: [], monedas: ["ARS"] },
          ],
        });
      if (conPendientes) {
        const de = (c: string, parte: string) => camino === `/api/v1/consorcios/${c}/${parte}`;
        for (const c of [C1, C2]) {
          if (de(c, "liquidaciones-importadas")) return ruta.fulfill({ json: [importacion(I1), importacion(I2)] });
          if (de(c, "solicitudes-formales"))
            return ruta.fulfill({ json: { elementos: [solicitud("enviada", "2026-11-25", true), solicitud("vencida", "2026-11-01", false, ["registrar_respuesta"])] } });
          if (de(c, "reclamos-liquidacion")) return ruta.fulfill({ json: { elementos: [{ estado: "presentado" }, { estado: "presentado" }, { estado: "presentado" }] } });
          if (de(c, "reparaciones")) return ruta.fulfill({ json: { elementos: [{ estado: "pedida" }, { estado: "en_gestion" }] } });
        }
      }
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

test.describe("bandeja del Consejo en el inicio (RF40, P0 S6)", () => {
  test("cada pendiente del Consejo, por edificio, entra en 360 px y lleva a su pantalla", async ({ page }) => {
    const errores = vigilarErrores(page);
    await page.setViewportSize({ width: ANCHO_TELEFONO_PX, height: 740 });
    const imprevistos = await simularApi(page, true);
    await simularSesion(page, "con_sesion");
    await page.goto("/");

    const paraHacer = page.getByRole("region", { name: "Para hacer" });
    const importar = paraHacer.getByRole("link", { name: `Liquidación del administrador · ${NOMBRE_LARGO}`, exact: true });
    await expect(importar).toHaveAccessibleDescription("2 liquidaciones esperan confirmación");
    await expect(paraHacer.getByRole("link", { name: `Solicitudes formales · ${NOMBRE_LARGO}`, exact: true })).toHaveAccessibleDescription(
      "1 con la respuesta por registrar · 1 por vencer, vence el 25/11/2026 · 1 vencida",
    );
    await expect(paraHacer.getByRole("link", { name: `Reclamos sobre la liquidación · ${NOMBRE_LARGO}`, exact: true })).toHaveAccessibleDescription(
      "3 reclamos esperan respuesta",
    );
    await expect(paraHacer.getByRole("link", { name: `Reparaciones · ${NOMBRE_LARGO} (anexo)`, exact: true })).toHaveAccessibleDescription(
      "1 pedido de reparación espera responsable · 1 reparación en curso",
    );
    await expect(paraHacer.getByRole("link")).toHaveCount(8);
    await expect(page.getByText("No hay nada pendiente.")).toHaveCount(0);
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);
    await importar.click();
    await expect(page).toHaveURL(/\/importar-liquidacion$/);
    expect(errores).toEqual([]);
    expect(imprevistos).toEqual([]);
  });

  test("sin nada para el Consejo, lo dice en una línea y sin «Para hacer»", async ({ page }) => {
    const errores = vigilarErrores(page);
    await page.setViewportSize({ width: ANCHO_TELEFONO_PX, height: 740 });
    const imprevistos = await simularApi(page, false);
    await simularSesion(page, "con_sesion");
    await page.goto("/");

    await expect(page.getByText("No hay nada pendiente.")).toBeVisible();
    await expect(page.getByRole("region", { name: "Para hacer" })).toHaveCount(0);
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);
    expect(errores).toEqual([]);
    expect(imprevistos).toEqual([]);
  });
});
