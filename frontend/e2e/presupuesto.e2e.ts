import { expect, type Page, test } from "@playwright/test";

import { simularInicioSinPendientes, simularPermisos, simularSesion, sinFaltasDeAccesibilidad } from "./entorno";

// El presupuesto y los desvíos en el resumen del edificio (RF15 en Modo Consejo, RF48; app 0128), en
// la página /panel servida por el nginx de la imagen, con su CSP real y la API simulada desde el
// navegador. Las cifras y quién registra los decide la base (`test_presupuesto_desvios_0128`): acá se
// ve que en un teléfono de 360 px se registra un presupuesto con la ventana, viaja el cuerpo exacto y
// el resumen vuelve con los desvíos, sin desplazar la página y con `axe`. SINTETICO.
const C1 = "11111111-1111-4111-8111-111111111111";
const P1 = "66666666-6666-4666-8666-666666666666";
const ANCHO_TELEFONO_PX = 360;
const RUBRO_LARGO = "Mantenimiento de ascensores, montacargas y escaleras mecánicas del edificio";
const LIMITES = { largo_aprobacion: 200, largo_motivo: 500, largo_nombre_rubro: 120, maximo_rubros: 60 };

const BASE = {
  ordenes_por_estado: [],
  gasto_pagado: [],
  comprobantes_faltantes: [],
  solicitudes_abiertas: 0,
  gasto_liquidado: [
    { periodo: "2026-09-01", confirmada_en: "2026-10-05T15:00:00Z", total: "123456790512.84", grupos: [] },
    { periodo: "2026-10-01", confirmada_en: "2026-11-05T15:00:00Z", total: "123456790512.84", grupos: [] },
  ],
  rubros_liquidados: [
    { periodo: "2026-09-01", numero: 1, nombre: "Servicios públicos", total: "1500.50" },
    { periodo: "2026-09-01", numero: 2, nombre: RUBRO_LARGO, total: "123456789012.34" },
    { periodo: "2026-10-01", numero: 1, nombre: "Servicios públicos", total: "1500.50" },
    { periodo: "2026-10-01", numero: 2, nombre: RUBRO_LARGO, total: "123456789012.34" },
  ],
  liquidacion_vigente: {
    id: "77777777-7777-4777-8777-777777777777", periodo: "2026-10-01", diferencias: 0, enlace_documentacion: null,
    vencimientos: ["2026-11-10", "2026-11-20"],
  },  // prettier-ignore
};
const sinCubrir = (periodo: string) => [
  { periodo, numero: 1, nombre: "Servicios públicos", presupuesto_id: null, presupuestado: null, liquidado: "1500.50", desvio: null, porcentaje: null },
  { periodo, numero: 2, nombre: RUBRO_LARGO, presupuesto_id: null, presupuestado: null, liquidado: "123456789012.34", desvio: null, porcentaje: null },
];
const ANTES = { ...BASE, desvios_presupuesto: [...sinCubrir("2026-09-01"), ...sinCubrir("2026-10-01")] };
/** Lo que la base calcula con el presupuesto registrado: octubre cubierto, septiembre no. */
const DESPUES = {
  ...BASE,
  desvios_presupuesto: [
    ...sinCubrir("2026-09-01"),
    { periodo: "2026-10-01", numero: 1, nombre: "Servicios públicos", presupuesto_id: P1, presupuestado: "1400.00", liquidado: "1500.50", desvio: "100.50", porcentaje: "7.18" },
    { periodo: "2026-10-01", numero: 2, nombre: RUBRO_LARGO, presupuesto_id: P1, presupuestado: null, liquidado: "123456789012.34", desvio: null, porcentaje: null },
  ],
};
const REGISTRADO = {
  id: P1, desde: "2026-10-01", hasta: "2026-12-01", aprobacion: "Asamblea ordinaria del 12-03-2026",
  rubros: [{ numero: 1, nombre: "Servicios públicos", importe: "1400.00" }],
  reemplaza_a: null, motivo: null, reemplazado_por: null, registrado_por_nombre: "Consejera", registrado_en: "2026-11-22T15:00:00Z",
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

async function simularApi(pagina: Page, cuerpos: unknown[]): Promise<string[]> {
  const imprevistos: string[] = [];
  await pagina.route("**/api/v1/**", async (ruta) => {
    const pedido = ruta.request();
    const camino = new URL(pedido.url()).pathname;
    if (camino === "/api/v1/auth/sesion") return ruta.fallback();
    const permisos = simularPermisos(ruta, camino);
    if (permisos !== null) return permisos;
    const registrado = cuerpos.length > 0;
    if (pedido.method() === "POST" && camino === `/api/v1/consorcios/${C1}/presupuestos`) {
      cuerpos.push(pedido.postDataJSON());
      return ruta.fulfill({ status: 201, json: REGISTRADO });
    }
    if (pedido.method() === "GET") {
      if (camino === "/api/v1/me/consorcios") return ruta.fulfill({ json: [{ id: C1, nombre: "Edificio Azul", vinculos: [], monedas: ["ARS"] }] });
      if (camino === `/api/v1/consorcios/${C1}/panel`) return ruta.fulfill({ json: registrado ? DESPUES : ANTES });
      if (camino === `/api/v1/consorcios/${C1}/presupuestos`)
        return ruta.fulfill({ json: { elementos: registrado ? [REGISTRADO] : [], puede_registrar: true, ...LIMITES } });
      if (camino === `/api/v1/consorcios/${C1}/padron`) return ruta.fulfill({ json: { elementos: [] } });
      if (camino === `/api/v1/consorcios/${C1}/pagos/bandeja`) return ruta.fulfill({ json: { elementos: [], avisos_rechazo: [], por_cotejar: 0, sin_explicar: 0 } });
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

test.describe("presupuesto y desvíos (app 0128)", () => {
  test("en un teléfono de 360 px se registra el presupuesto y el resumen muestra los desvíos y los vencimientos", async ({ page }) => {
    const errores = vigilarErrores(page);
    await page.setViewportSize({ width: ANCHO_TELEFONO_PX, height: 740 });
    const cuerpos: unknown[] = [];
    const imprevistos = await simularApi(page, cuerpos);
    await simularSesion(page, "con_sesion");
    await page.goto("/panel");

    await expect(page.getByText("Vencimientos de la liquidación de octubre de 2026: 10/11/2026 y 20/11/2026")).toBeVisible();
    const desvios = page.getByRole("region", { name: "Desvíos del presupuesto" });
    await expect(desvios.getByText("Todavía no hay un presupuesto registrado para los períodos con liquidación oficial.")).toBeVisible();
    await expect(desvios.getByRole("table")).toHaveCount(0);
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);

    await desvios.getByRole("button", { name: "Registrar un presupuesto" }).click();
    const ventana = page.getByRole("dialog", { name: "Registrar un presupuesto" });
    await expect(ventana.getByLabel(`2. ${RUBRO_LARGO}`)).toBeVisible();
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);
    await ventana.getByLabel("Primer mes (AAAA-MM)").fill("2026-10");
    await ventana.getByLabel("Último mes (AAAA-MM)").fill("2026-12");
    await ventana.getByLabel("De dónde sale").fill("Asamblea ordinaria del 12-03-2026");
    await ventana.getByLabel("1. Servicios públicos").fill("1.400");
    await ventana.getByRole("button", { name: "Registrar", exact: true }).click();
    await expect(ventana).toBeHidden();
    await expect(desvios.getByText("Listo: el presupuesto quedó registrado.")).toBeVisible();
    expect(cuerpos).toEqual([
      {
        desde: "2026-10-01", hasta: "2026-12-01", aprobacion: "Asamblea ordinaria del 12-03-2026",
        rubros: [{ numero: 1, nombre: "Servicios públicos", importe: "1400.00" }], reemplaza_a: null, motivo: null,
      },
    ]);  // prettier-ignore

    // El resumen vuelve a leerse: octubre con su desvío; el rubro sin presupuesto lo dice, sin porcentaje.
    const tabla = desvios.getByRole("table");
    await expect(tabla.getByRole("row", { name: /^Servicios públicos/ })).toContainText(/\+\$\s?100,50 \(\+7,18 %\)/);
    await expect(tabla.getByRole("row", { name: new RegExp(`^${RUBRO_LARGO}`) })).toContainText("Sin presupuesto");
    await expect(tabla.getByRole("row", { name: new RegExp(`^${RUBRO_LARGO}`) })).toContainText("No se calcula");
    await desvios.getByText("Presupuestos registrados (1)").click();
    await expect(desvios.getByText("Octubre de 2026 a diciembre de 2026", { exact: false })).toBeVisible();
    for (const resumen of await page.getByText("Cómo se calcula").all()) await resumen.click();
    expect(await sinDesplazamiento(page)).toBe(true);
    await sinFaltasDeAccesibilidad(page);
    expect(errores).toEqual([]);
    expect(imprevistos).toEqual([]);
  });
});
