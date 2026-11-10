import type { components } from "../api/tipos";
import { importe, TEXTO_ESTADO_ORDEN } from "../egresos/comun";
import { periodo } from "../egresos/Liquidacion";

// Lo que el panel del Consejo hace con la respuesta de `GET …/panel` (app 0094): solo agrupa y
// ordena lo que ya vino calculado de la base, para cada gráfico y su tabla. No decide nada.

export type Panel = components["schemas"]["PanelFiscalizacion"];
type Gasto = Panel["gasto_pagado"][number];

/** Una barra del gráfico: lo que mide (`valor`) y lo que dice escrito (`texto`). */
export type Barra = { clave: string; etiqueta: string; valor: number; texto: string };

export const SIN_RUBRO = "Sin rubro";

const cantidadDeOrdenes = (n: number) => (n === 1 ? "1 orden" : `${n} órdenes`);

/** Las monedas que aparecen, en orden alfabético: cada una va en su propio gráfico. */
export function monedas(panel: Panel): string[] {
  const todas = [
    ...panel.ordenes_por_estado.map((f) => f.moneda),
    ...panel.gasto_pagado.map((f) => f.moneda),
  ];
  return [...new Set(todas)].sort();
}

/** Por estado, en el orden del circuito: la cantidad de órdenes y su importe. */
export function ordenesPorEstado(panel: Panel, moneda: string): Barra[] {
  const orden = Object.keys(TEXTO_ESTADO_ORDEN);
  return panel.ordenes_por_estado
    .filter((f) => f.moneda === moneda)
    .sort((a, b) => orden.indexOf(a.estado) - orden.indexOf(b.estado))
    .map((f) => ({
      clave: f.estado,
      etiqueta: TEXTO_ESTADO_ORDEN[f.estado],
      valor: f.cantidad,
      texto: `${cantidadDeOrdenes(f.cantidad)}, ${importe(f.importe_centavos, moneda)}`,
    }));
}

function sumarPor(filas: readonly Gasto[], clave: (f: Gasto) => string): Map<string, { centavos: number; ordenes: number }> {
  const totales = new Map<string, { centavos: number; ordenes: number }>();
  for (const f of filas) {
    const k = clave(f);
    const previo = totales.get(k) ?? { centavos: 0, ordenes: 0 };
    totales.set(k, { centavos: previo.centavos + f.importe_centavos, ordenes: previo.ordenes + f.cantidad });
  }
  return totales;
}

const barraDeImporte = (clave: string, etiqueta: string, t: { centavos: number; ordenes: number }, moneda: string): Barra => ({
  clave,
  etiqueta,
  valor: t.centavos,
  texto: `${importe(t.centavos, moneda)} (${cantidadDeOrdenes(t.ordenes)})`,
});

/** Lo pagado por mes, del más viejo al más nuevo; con `soloFondo`, solo lo pagado con el fondo de reserva. */
export function gastoPorMes(panel: Panel, moneda: string, soloFondo = false): Barra[] {
  const filas = panel.gasto_pagado.filter((f) => f.moneda === moneda && (!soloFondo || f.fondo_reserva));
  return [...sumarPor(filas, (f) => f.periodo)]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([mes, t]) => barraDeImporte(mes, periodo(mes), t, moneda));
}

/** Los meses con algún pago en esa moneda, del más nuevo al más viejo. */
export function mesesConPagos(panel: Panel, moneda: string): string[] {
  return [...new Set(panel.gasto_pagado.filter((f) => f.moneda === moneda).map((f) => f.periodo))].sort().reverse();
}

/** Lo pagado en un mes por rubro, de mayor a menor; lo que no tiene rubro va último. */
export function gastoPorRubro(panel: Panel, moneda: string, mes: string): Barra[] {
  const filas = panel.gasto_pagado.filter((f) => f.moneda === moneda && f.periodo === mes);
  const totales = sumarPor(filas, (f) => f.rubro ?? "");
  return [...totales]
    .sort(([ra, a], [rb, b]) => Number(ra === "") - Number(rb === "") || b.centavos - a.centavos || ra.localeCompare(rb))
    .map(([rubro, t]) => barraDeImporte(rubro === "" ? "sin-rubro" : rubro, rubro === "" ? SIN_RUBRO : rubro, t, moneda));
}

/** Los comprobantes faltantes contados por estado de la orden, en el orden del circuito. */
export function faltantesPorEstado(panel: Panel): Barra[] {
  const cuenta = new Map<string, number>();
  for (const f of panel.comprobantes_faltantes) cuenta.set(f.estado, (cuenta.get(f.estado) ?? 0) + 1);
  return (Object.keys(TEXTO_ESTADO_ORDEN) as (keyof typeof TEXTO_ESTADO_ORDEN)[])
    .filter((e) => cuenta.has(e))
    .map((e) => {
      const n = cuenta.get(e) ?? 0;
      return { clave: e, etiqueta: TEXTO_ESTADO_ORDEN[e], valor: n, texto: cantidadDeOrdenes(n) };
    });
}

export const estaVacio = (panel: Panel) =>
  panel.ordenes_por_estado.length === 0 && panel.gasto_pagado.length === 0 && panel.comprobantes_faltantes.length === 0;
