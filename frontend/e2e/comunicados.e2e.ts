import { expect, type Page, test } from "@playwright/test";

import { simularInicioSinPendientes, simularPermisos, simularSesion, sinFaltasDeAccesibilidad } from "./entorno";

// «Comunicados» (RF28, app 0122) servida por el nginx de la imagen, con su CSP real; la API se simula
// desde el navegador. Quién lee y quién emite lo decide la API y lo prueba el backend
// (`test_comunicados_0122`). Acá se ve lo que solo un navegador real muestra: que la lista, la ficha
// y la ventana de emitir entran en un teléfono de 360 px sin desplazar la página, `axe` con contraste
// y que la CSP no frena nada. Datos SINTETICOS / NO REAL; los textos, de los más largos que admite.
const C1 = "11111111-1111-4111-8111-111111111111";
const A = "22222222-2222-4222-8222-222222222222";
const B = "33333333-3333-4333-8333-333333333333";
const ANCHO_TELEFONO_PX = 360;
const TITULO_LARGO = "Corte programado del suministro de agua por el reemplazo del tanque de reserva y las bombas del edificio";
const TEXTO_LARGO = Array.from({ length: 12 }, (_, i) => `Renglón ${i + 1}: la empresa contratista trabaja de 9 a 13 en la terraza y en la sala de máquinas.`).join("\n");

const REF_A = { id: A, titulo: TITULO_LARGO, emitido_en: "2026-11-20T13:00:00Z" };
const DE_A = {
  ...REF_A, emitido_por_nombre: "María de los Ángeles Fernández Rodríguez", corrige_a: null, corregido: true, texto: TEXTO_LARGO,
  corregido_por: [{ id: B, titulo: "Corrección: el corte es el miércoles y no el martes", emitido_en: "2026-11-21T13:00:00Z" }],
  envios: { destinatarios: 128, enviados: 128 },
};  // prettier-ignore
const DE_B = {
  id: B, titulo: "Corrección: el corte es el miércoles y no el martes", emitido_en: "2026-11-21T13:00:00Z",
  emitido_por_nombre: DE_A.emitido_por_nombre, corrige_a: REF_A, corregido: false,
};  // prettier-ignore
const resumen = ({ id, titulo, emitido_en, emitido_por_nombre, corrige_a, corregido }: typeof DE_A | typeof DE_B) =>
  ({ id, titulo, emitido_en, emitido_por_nombre, corrige_a, corregido });  // prettier-ignore

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

async function simularApi(pagina: Page, acciones: readonly string[], puedeEmitir: boolean): Promise<string[]> {
  const imprevistos: string[] = [];
  await pagina.route("**/api/v1/**", async (ruta) => {
    const pedido = ruta.request();
    const camino = new URL(pedido.url()).pathname;
    if (camino === "/api/v1/auth/sesion") return ruta.fallback();
    const permisos = simularPermisos(ruta, camino, acciones);
    if (permisos !== null) return permisos;
    if (pedido.method() === "GET") {
      if (camino === "/api/v1/me/consorcios") return ruta.fulfill({ json: [{ id: C1, nombre: "Edificio Azul", vinculos: [], monedas: ["ARS"] }] });
      if (camino === `/api/v1/consorcios/${C1}/comunicados`)
        return ruta.fulfill({
          json: {
            elementos: [resumen(DE_B), resumen(DE_A)], puede_emitir: puedeEmitir, destinatarios_posibles: puedeEmitir ? 128 : null,
            largo_titulo: 120, largo_texto: 4000,
          },
        });  // prettier-ignore
      if (camino === `/api/v1/consorcios/${C1}/comunicados/${A}`) return ruta.fulfill({ json: puedeEmitir ? DE_A : { ...DE_A, envios: null } });
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

test.describe("comunicados (app 0122)", () => {
  test("quien lee: la lista y la ficha entran en un teléfono de 360 px", async ({ page }) => {
    const errores = vigilarErrores(page);
    await page.setViewportSize({ width: ANCHO_TELEFONO_PX, height: 740 });
    const imprevistos = await simularApi(page, ["comunicados.ver", "cuenta.ver_propia", "fiscalizacion.ver_resumen", "documentos.ver", "contactos.ver"], false);
    await simularSesion(page, "con_sesion");
    await page.goto("/");
    await page.getByRole("navigation", { name: "Secciones" }).getByRole("link", { name: "Comunicados" }).click();
    await expect(page).toHaveURL(/\/comunicados$/);

    const lista = page.getByRole("list", { name: "Comunicados" });
    await expect(lista.getByRole("button", { name: /Corregido/ })).toBeVisible();
    await expect(page.getByRole("button", { name: "Nuevo comunicado" })).toHaveCount(0);
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);
    await lista.getByRole("button", { name: /Corregido/ }).click();
    const ficha = page.getByRole("dialog");
    await expect(ficha.getByText("Renglón 12:", { exact: false })).toBeVisible();
    await expect(ficha.getByText("Envíos")).toHaveCount(0);
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);
    expect(errores).toEqual([]);
    expect(imprevistos).toEqual([]);
  });

  test("quien emite: escribir y confirmar entran en un teléfono de 360 px", async ({ page }) => {
    const errores = vigilarErrores(page);
    await page.setViewportSize({ width: ANCHO_TELEFONO_PX, height: 740 });
    const imprevistos = await simularApi(page, ["comunicados.ver", "comunicados.emitir", "padron.ver", "documentos.ver", "contactos.ver"], true);
    await simularSesion(page, "con_sesion");
    await page.goto("/comunicados");

    await page.getByRole("button", { name: "Nuevo comunicado" }).click();
    const ventana = page.getByRole("dialog", { name: "Nuevo comunicado" });
    await ventana.getByLabel("Título").fill(TITULO_LARGO.slice(0, 120));
    await ventana.getByLabel("Texto").fill(TEXTO_LARGO);
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);
    await ventana.getByRole("button", { name: "Seguir" }).click();
    await expect(ventana.getByText("Lo van a recibir 128 personas", { exact: false })).toBeVisible();
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);
    await ventana.getByRole("button", { name: "Cancelar" }).click();
    // La ficha de quien emite: cuántos lo recibieron, nunca quiénes.
    await page.getByRole("list", { name: "Comunicados" }).getByRole("button", { name: /Corregido/ }).click();
    await expect(page.getByRole("dialog").getByText("Se dirigió a 128 personas; salieron 128 correos.")).toBeVisible();
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);
    expect(errores).toEqual([]);
    expect(imprevistos).toEqual([]);
  });
});
