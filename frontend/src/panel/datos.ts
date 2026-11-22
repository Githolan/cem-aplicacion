import type { components } from "../api/tipos";
import { importe, TEXTO_ESTADO_ORDEN } from "../egresos/comun";
import { periodo } from "../egresos/Liquidacion";
import { enPesos } from "../fiscalizacion/LiquidacionDelAdministrador";

// Lo que el resumen de pagos del edificio hace con la respuesta de `GET …/panel` (app 0094 y 0120): solo agrupa y
// ordena lo que ya vino calculado de la base, para cada gráfico. Las tablas son para el lector de pantalla. No decide nada.

export type Panel = components["schemas"]["PanelFiscalizacion"];
export type Faltante = components["schemas"]["ComprobanteFaltante"];
type Gasto = Panel["gasto_pagado"][number];

/** Una barra del gráfico: lo que mide (`valor`) y lo que dice escrito (`texto`); `detalle`, lo que la
 * desglosa (los grupos de un período), escrito debajo. */
export type Barra = { clave: string; etiqueta: string; valor: number; texto: string; detalle?: string };

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

type EstadoOrden = keyof typeof TEXTO_ESTADO_ORDEN;
const ESTADOS_EN_ORDEN = Object.keys(TEXTO_ESTADO_ORDEN) as EstadoOrden[];

/** Suma por la palabra del estado, en el orden del circuito: dos estados guardados con la misma
 * palabra (`pagada` y `conciliada` se dicen «Pagada», decisión 11) son una sola barra. */
function porPalabra<T extends { estado: EstadoOrden }>(filas: readonly T[], cantidad: (f: T) => number, centavos: (f: T) => number) {
  const totales = new Map<string, { clave: string; cantidad: number; centavos: number }>();
  for (const estado of ESTADOS_EN_ORDEN)
    for (const f of filas.filter((x) => x.estado === estado)) {
      const palabra = TEXTO_ESTADO_ORDEN[estado];
      const previo = totales.get(palabra) ?? { clave: estado, cantidad: 0, centavos: 0 };
      totales.set(palabra, { clave: previo.clave, cantidad: previo.cantidad + cantidad(f), centavos: previo.centavos + centavos(f) });
    }
  return [...totales].map(([etiqueta, t]) => ({ etiqueta, ...t }));
}

/** Por estado, en el orden del circuito: la cantidad de órdenes y su importe. */
export function ordenesPorEstado(panel: Panel, moneda: string): Barra[] {
  const filas = panel.ordenes_por_estado.filter((f) => f.moneda === moneda);
  return porPalabra(filas, (f) => f.cantidad, (f) => f.importe_centavos).map((t) => ({
    clave: t.clave,
    etiqueta: t.etiqueta,
    valor: t.cantidad,
    texto: `${cantidadDeOrdenes(t.cantidad)}, ${importe(t.centavos, moneda)}`,
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

// --- Modo Consejo: las liquidaciones oficiales (app 0120) -------------------------------------------

/** Los grupos de un período, escritos debajo de su barra y en la tabla. */
const textoDeGrupos = (grupos: Panel["gasto_liquidado"][number]["grupos"]) =>
  grupos.map((g) => `${g.nombre}: ${g.total === null ? "—" : enPesos(g.total)}`).join(" · ");

/** El gasto de cada período según su liquidación oficial, del más viejo al más nuevo, con los totales
 * por grupo. Un período cuya liquidación no tiene el total no lleva barra. */
export function gastoLiquidadoPorPeriodo(panel: Panel): Barra[] {
  return panel.gasto_liquidado.flatMap((f) =>
    f.total === null
      ? []
      : [
          {
            clave: f.periodo,
            etiqueta: periodo(f.periodo),
            valor: Number(f.total),
            texto: enPesos(f.total),
            ...(f.grupos.length === 0 ? {} : { detalle: textoDeGrupos(f.grupos) }),
          },
        ],
  );
}

/** Los períodos que tienen rubros, del más nuevo al más viejo. */
export function periodosConRubros(panel: Panel): string[] {
  return [...new Set(panel.rubros_liquidados.map((f) => f.periodo))].sort().reverse();
}

/** Los rubros de un período, en el orden de la liquidación; uno sin total no lleva barra. */
export function rubrosDelPeriodo(panel: Panel, mes: string): Barra[] {
  return panel.rubros_liquidados.flatMap((f, i) =>
    f.periodo !== mes || f.total === null
      ? []
      : [{ clave: `${f.numero ?? `fila-${i}`}`, etiqueta: f.nombre ?? `Rubro ${f.numero ?? ""}`.trim(), valor: Number(f.total), texto: enPesos(f.total) }],
  );
}

const sinOrdenes = (panel: Panel) =>
  panel.ordenes_por_estado.length === 0 && panel.gasto_pagado.length === 0 && panel.comprobantes_faltantes.length === 0;

export const estaVacio = (panel: Panel) => sinOrdenes(panel) && panel.gasto_liquidado.length === 0;
