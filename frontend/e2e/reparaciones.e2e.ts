import { expect, type Page, test } from "@playwright/test";

import { simularInicioSinPendientes, simularPermisos, simularSesion, sinFaltasDeAccesibilidad } from "./entorno";

// Reparaciones a cargo del consorcio (RF19, app 0124) servidas por el nginx de la imagen, con su
// CSP real; la API se simula desde el navegador. Quién pide, quién gestiona, las transiciones y el
// cierre con el gasto los decide la API y los prueba el backend (`test_reparaciones_0124`, con
// `test_pa11_…`). Acá se ve lo que solo un navegador real muestra: que «Nueva gestión», la lista y
// el expediente con su historia y las ventanas de quien gestiona entran en un teléfono de 360 px sin
// desplazar la página, `axe` con contraste y que la CSP no frena nada. Datos SINTETICOS / NO REAL;
// los textos, de los más largos que admite.
const C1 = "11111111-1111-4111-8111-111111111111";
const U1 = "44444444-4444-4444-8444-444444444444";
const MC = "55555555-5555-4555-8555-555555555555";
const OFICIAL = "66666666-6666-4666-8666-666666666666";
const R1 = "77777777-7777-4777-8777-777777777777";
const ANCHO_TELEFONO_PX = 360;
const UNIDAD_LARGA = "Piso 12 Departamento B (con cochera 4)";
const NOMBRE_LARGO = "María de los Ángeles Fernández Rodríguez";
const TEXTO_LARGO = Array.from({ length: 10 }, (_, i) => `Renglón ${i + 1}: pierde agua la canilla del baño y mancha el techo del departamento de abajo.`).join("\n");

const paso = (estado: string, cambios: Record<string, unknown> = {}) => ({
  estado, motivo: null, adjunto_id: null, proveedor_nombre: null, proveedor_cuit: null, aprobado_por: null, facultad: null,
  decision_id: null, decision_descripcion: null, decision_fecha: null, gasto: null, registrado_por_nombre: NOMBRE_LARGO,
  registrado_en: "2026-11-22T10:00:00Z", ...cambios,
});  // prettier-ignore
const GASTO = { liquidacion_id: OFICIAL, periodo: "2026-10-01", clave: "gasto:12:34", concepto: "Plomería y destapaciones del edificio SINTETICA", total: "123456789.00" };
const TERMINADA = {
  id: R1, numero: 1234, estado: "terminada_gasto_pendiente", unidad_id: U1, designacion: UNIDAD_LARGA, lugar: "Baño principal, al lado del lavarropas",
  gestion: { responsable_id: MC, responsable_nombre: NOMBRE_LARGO, prioridad: "urgente", fecha_comprometida: "2026-12-01" },
  pedida_por_nombre: NOMBRE_LARGO, pedida_en: "2026-11-22T10:00:00Z", propia: false, descripcion: TEXTO_LARGO,
  historia: [
    paso("pedida"), paso("en_gestion"),
    paso("presupuestada", { proveedor_nombre: "Plomería y destapaciones del edificio SINTETICA", proveedor_cuit: "20123456786" }),
    paso("aprobada", { aprobado_por: "Asamblea extraordinaria del 3 de noviembre", facultad: "reparaciones.gestionar", decision_id: OFICIAL, decision_fecha: "2026-11-03", decision_descripcion: "Aprueba el arreglo de la cañería del baño" }),
    paso("terminada_gasto_pendiente"),
  ],
  adjuntos: [
    { id: OFICIAL, tipo: "presupuesto", estado: "disponible", tipo_archivo: "application/pdf", subido_en: "2026-11-22T10:00:00Z", propio: true, usado: true },
    { id: MC, tipo: "foto", estado: "disponible", tipo_archivo: "image/jpeg", subido_en: "2026-11-22T10:00:00Z", propio: false, usado: false },
  ],
  acciones: ["enlazar_gasto", "gestionar", "no_realizada"],
  responsables_posibles: [{ id: MC, nombre: NOMBRE_LARGO }], decisiones_citables: [], gastos_enlazables: [GASTO],
};  // prettier-ignore
const resumen = { id: R1, numero: 1234, estado: TERMINADA.estado, unidad_id: U1, designacion: UNIDAD_LARGA, lugar: TERMINADA.lugar, gestion: TERMINADA.gestion, pedida_por_nombre: NOMBRE_LARGO, pedida_en: TERMINADA.pedida_en, propia: false };
const lista = (gestiona: boolean) => ({
  elementos: gestiona ? [resumen] : [], gestiona, pide: true, unidades: [{ id: U1, designacion: UNIDAD_LARGA }],
  largo_descripcion: 2000, largo_lugar: 120, largo_motivo: 500, largo_proveedor: 120, largo_aprobado_por: 120, fotos_maximo: 4,
});  // prettier-ignore

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

async function simularApi(pagina: Page, acciones: readonly string[], gestiona: boolean): Promise<string[]> {
  const imprevistos: string[] = [];
  await pagina.route("**/api/v1/**", async (ruta) => {
    const pedido = ruta.request();
    const camino = new URL(pedido.url()).pathname;
    if (camino === "/api/v1/auth/sesion") return ruta.fallback();
    const permisos = simularPermisos(ruta, camino, acciones);
    if (permisos !== null) return permisos;
    const reparaciones = `/api/v1/consorcios/${C1}/reparaciones`;
    if (pedido.method() === "GET") {
      if (camino === "/api/v1/me/consorcios") return ruta.fulfill({ json: [{ id: C1, nombre: "Edificio Azul", vinculos: [], monedas: ["ARS"] }] });
      if (camino === reparaciones) return ruta.fulfill({ json: lista(gestiona) });
      if (camino === `${reparaciones}/${R1}`) return ruta.fulfill({ json: TERMINADA });
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

test.describe("reparaciones a cargo del consorcio (app 0124)", () => {
  test("quien pide: «Nueva gestión» entra en 360 px", async ({ page }) => {
    const errores = vigilarErrores(page);
    await page.setViewportSize({ width: ANCHO_TELEFONO_PX, height: 740 });
    const imprevistos = await simularApi(page, ["reparaciones.ver", "reparaciones.pedir", "contactos.ver"], false);
    await simularSesion(page, "con_sesion");
    await page.goto("/reparaciones");

    await expect(page.getByText("Todavía no hay pedidos de reparación.")).toBeVisible();
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);
    await page.getByRole("button", { name: "Pedir una reparación" }).click();
    const ventana = page.getByRole("dialog", { name: "Pedir una reparación" });
    await ventana.getByLabel("Qué hay que reparar").fill(TEXTO_LARGO);
    await ventana.getByLabel("Lugar dentro de la unidad (si hace falta)").fill("Baño principal, al lado del lavarropas");
    await expect(ventana.getByRole("button", { name: "Enviar el pedido" })).toBeVisible();
    // La ventana, no el acto: el pedido y sus fotos los prueban el backend y vitest.
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);
    expect(errores).toEqual([]);
    expect(imprevistos).toEqual([]);
  });

  test("quien gestiona: la lista, el expediente y el enlace del gasto entran en 360 px", async ({ page }) => {
    const errores = vigilarErrores(page);
    await page.setViewportSize({ width: ANCHO_TELEFONO_PX, height: 740 });
    const imprevistos = await simularApi(page, ["reparaciones.ver", "reparaciones.pedir", "reparaciones.gestionar", "contactos.ver"], true);
    await simularSesion(page, "con_sesion");
    await page.goto("/reparaciones");

    const filas = page.getByRole("list", { name: "Reparaciones" });
    await expect(filas.getByRole("button", { name: /Reparación N\.º 1234/ })).toBeVisible();
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);
    await filas.getByRole("button").click();
    const ficha = page.getByRole("dialog");
    await expect(ficha.getByText("Renglón 10:", { exact: false })).toBeVisible();
    await expect(ficha.getByText(/Asamblea extraordinaria del 3 de noviembre/)).toBeVisible();
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);
    await ficha.getByRole("button", { name: "Enlazar el gasto y cerrar" }).click();
    const ventana = page.getByRole("dialog", { name: "Enlazar el gasto y cerrar" });
    await expect(ventana.getByLabel(/Plomería y destapaciones/)).toBeChecked();
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);
    expect(errores).toEqual([]);
    expect(imprevistos).toEqual([]);
  });
});
