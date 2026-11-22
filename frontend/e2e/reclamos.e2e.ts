import { expect, type Page, test } from "@playwright/test";

import { simularInicioSinPendientes, simularPermisos, simularSesion, sinFaltasDeAccesibilidad } from "./entorno";

// Reclamos sobre la liquidación (RF34 y RF38, app 0123) servidos por el nginx de la imagen, con su
// CSP real; la API se simula desde el navegador. Quién presenta, quién atiende y la revalidación del
// borrador los decide la API y los prueba el backend (`test_reclamos_liquidacion_0123`, con
// `test_pa16_…`). Acá se ve lo que solo un navegador real muestra: que la ventana del reclamo, el
// borrador sin enviar, el rechazo por el permiso vencido y la pantalla de quien atiende entran en un
// teléfono de 360 px sin desplazar la página, `axe` con contraste, que la CSP no frena nada y que el
// borrador queda en el almacenamiento del navegador. Datos SINTETICOS / NO REAL; los textos, de los
// más largos que admite.
const C1 = "11111111-1111-4111-8111-111111111111";
const B = "33333333-3333-4333-8333-333333333333";
const U1 = "44444444-4444-4444-8444-444444444444";
const R1 = "77777777-7777-4777-8777-777777777777";
const ANCHO_TELEFONO_PX = 360;
const UNIDAD_LARGA = "Piso 12 Departamento B (con cochera 4)";
const TEXTO_LARGO = Array.from({ length: 10 }, (_, i) => `Renglón ${i + 1}: el saldo anterior no incluye el pago del 2 de septiembre hecho por transferencia.`).join("\n");

const MI_CUENTA = {
  periodos: [{ periodo: "2026-10-01", confirmada_en: "2026-11-05T15:00:00Z" }],
  liquidacion: {
    importacion_id: B, periodo: "2026-10-01", confirmada_en: "2026-11-05T15:00:00Z", vencimientos: ["13/11/2026"],
    unidades: [
      { unidad_id: U1, designacion: UNIDAD_LARGA, figura: true, saldo_anterior: "123456789.00", pagos: "123456789.00",
        deuda: "0.00", interes: "0.00", disponible: null, grupos: [], otros_gastos: null, redondeo: null, total: "98765432.10" },
    ],
  },
};  // prettier-ignore
const RECLAMO = {
  id: R1, liquidacion_id: B, periodo: "2026-10-01", liquidacion_vigente: true, unidad_id: U1, designacion: UNIDAD_LARGA,
  campo: "saldo_unidad", presentado_por_nombre: "María de los Ángeles Fernández Rodríguez", presentado_en: "2026-11-22T10:00:00Z",
  estado: "presentado", propio: false, texto: TEXTO_LARGO, acciones: ["responder", "pasar_a_solicitud"],
  historia: [{ estado: "presentado", texto: null, solicitud_id: null, solicitud_numero: null, registrado_por_nombre: "María de los Ángeles Fernández Rodríguez", registrado_en: "2026-11-22T10:00:00Z" }],
};  // prettier-ignore
const RESUMEN = {
  id: R1, liquidacion_id: B, periodo: RECLAMO.periodo, liquidacion_vigente: true, unidad_id: U1, designacion: UNIDAD_LARGA, campo: RECLAMO.campo,
  presentado_por_nombre: RECLAMO.presentado_por_nombre, presentado_en: RECLAMO.presentado_en, estado: RECLAMO.estado, propio: false,
};  // prettier-ignore

function vigilarErrores(pagina: Page): string[] {
  const errores: string[] = [];
  pagina.on("pageerror", (e) => errores.push(e.message));
  pagina.on("console", (m) => {
    // El 403 del envío rechazado es la respuesta esperada de esta prueba, no una falla de la página.
    if (m.type() === "error" && !m.text().includes("403")) errores.push(m.text());
  });
  void pagina.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", (e) => console.error(`CSP: ${e.violatedDirective} ${e.blockedURI}`));
  });
  return errores;
}

async function simularApi(pagina: Page, acciones: readonly string[], atiende: boolean): Promise<string[]> {
  const imprevistos: string[] = [];
  await pagina.route("**/api/v1/**", async (ruta) => {
    const pedido = ruta.request();
    const camino = new URL(pedido.url()).pathname;
    if (camino === "/api/v1/auth/sesion") return ruta.fallback();
    const permisos = simularPermisos(ruta, camino, acciones);
    if (permisos !== null) return permisos;
    const reclamos = `/api/v1/consorcios/${C1}/reclamos-liquidacion`;
    if (pedido.method() === "GET") {
      if (camino === "/api/v1/me/consorcios") return ruta.fulfill({ json: [{ id: C1, nombre: "Edificio Azul", vinculos: [], monedas: ["ARS"] }] });
      if (camino === `/api/v1/consorcios/${C1}/mi-cuenta`) return ruta.fulfill({ json: MI_CUENTA });
      if (camino === reclamos)
        return ruta.fulfill({ json: { elementos: atiende ? [RESUMEN] : [], atiende, largo_texto: 2000, largo_motivo: 500 } });
      if (camino === `${reclamos}/${R1}`) return ruta.fulfill({ json: RECLAMO });
    }
    // PA16: el servidor revalida el borrador y el vínculo ya no alcanza.
    if (pedido.method() === "POST" && camino === reclamos)
      return ruta.fulfill({ status: 403, json: { codigo: "sin_permiso", mensaje: "Esta cuenta no tiene permiso para hacer esto.", detalle: null, id_correlacion: "e2e" } });
    const inicio = simularInicioSinPendientes(ruta, camino);
    if (inicio !== null) return inicio;
    imprevistos.push(`${pedido.method()} ${camino}`);
    return ruta.abort();
  });
  return imprevistos;
}

const sinDesplazamiento = (pagina: Page) =>
  pagina.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth);

test.describe("reclamos sobre la liquidación (app 0123)", () => {
  test("quien presenta: la ventana, el borrador y el rechazo por el permiso vencido entran en 360 px", async ({ page }) => {
    const errores = vigilarErrores(page);
    await page.setViewportSize({ width: ANCHO_TELEFONO_PX, height: 740 });
    const imprevistos = await simularApi(
      page,
      ["cuenta.ver_propia", "reclamos_liquidacion.presentar", "reclamos_liquidacion.ver", "documentos.ver", "contactos.ver"],
      false,
    );
    await simularSesion(page, "con_sesion");
    await page.goto("/mis-expensas");

    const unidad = page.getByRole("region", { name: `Unidad ${UNIDAD_LARGA}` });
    await unidad.getByRole("button", { name: "Reclamar el saldo" }).click();
    const ventana = page.getByRole("dialog", { name: "Hacer un reclamo" });
    await expect(ventana.getByLabel("Dato que se cita")).toHaveValue("saldo_unidad");
    await ventana.getByLabel("Reclamo").fill(TEXTO_LARGO);
    await expect(ventana.getByText("Borrador sin enviar: queda guardado en este dispositivo hasta enviarlo.")).toBeVisible();
    expect(await page.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith("cem-reclamo:")).length)).toBe(1);
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);

    await ventana.getByRole("button", { name: "Enviar el reclamo" }).click();
    await expect(page.getByText(/ya no figura como propietaria confirmada de esa unidad/).first()).toBeVisible();
    const borradores = page.getByRole("list", { name: "Borradores sin enviar" });
    await expect(borradores.getByText("No se pudo enviar")).toBeVisible();
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);
    await borradores.getByRole("button").click();
    const ficha = page.getByRole("dialog");
    await expect(ficha.getByRole("button", { name: "Descartar el borrador" })).toBeVisible();
    await expect(ficha.getByRole("button", { name: "Enviar ahora" })).toHaveCount(0);
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);
    await ficha.getByRole("button", { name: "Descartar el borrador" }).click();
    await expect(borradores).toHaveCount(0);
    expect(await page.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith("cem-reclamo:")).length)).toBe(0);
    expect(errores).toEqual([]);
    expect(imprevistos).toEqual([]);
  });

  test("quien atiende: la lista y la ficha con «Responder» entran en 360 px", async ({ page }) => {
    const errores = vigilarErrores(page);
    await page.setViewportSize({ width: ANCHO_TELEFONO_PX, height: 740 });
    const imprevistos = await simularApi(
      page,
      ["reclamos_liquidacion.atender", "reclamos_liquidacion.ver", "solicitud_formal.preparar", "fiscalizacion.ver_panel", "documentos.ver", "contactos.ver"],
      true,
    );
    await simularSesion(page, "con_sesion");
    await page.goto("/reclamos");

    const lista = page.getByRole("list", { name: "Reclamos sobre la liquidación" });
    await expect(lista.getByRole("button", { name: /María de los Ángeles/ })).toBeVisible();
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);
    await lista.getByRole("button").click();
    const ficha = page.getByRole("dialog");
    await expect(ficha.getByText("Renglón 10:", { exact: false })).toBeVisible();
    await expect(ficha.getByRole("button", { name: "Pasar a solicitud formal" })).toBeVisible();
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);
    await ficha.getByRole("button", { name: "Responder" }).click();
    const ventana = page.getByRole("dialog", { name: "Responder" });
    await ventana.getByLabel("Respuesta").fill(TEXTO_LARGO);
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);
    expect(errores).toEqual([]);
    expect(imprevistos).toEqual([]);
  });
});
