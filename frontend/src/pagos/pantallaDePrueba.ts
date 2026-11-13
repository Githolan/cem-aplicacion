import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import type { components } from "../api/tipos";

// Solo para las pruebas (usa Node): el servidor simulado arma la ficha y la bandeja con la tabla real
// de la pantalla (`contratos/cem_contracts/pantallas_datos/pagos.json`), como el backend
// (`circuito_pagos.acciones_ofrecidas`): una acción se ofrece si la tabla la nombra y su `puede_*` es
// verdadero. Así una prueba de la ficha no puede pasar con una tabla distinta de la real.

type Ficha = components["schemas"]["FichaPago"];
type Elemento = components["schemas"]["ElementoBandejaPagos"];
type AccionPago = components["schemas"]["AccionPago"];
type AccionOfrecida = components["schemas"]["AccionOfrecida"];
type Situacion = components["schemas"]["SituacionPago"];
type Paso = components["schemas"]["PasoPago"];
type Grupo = components["schemas"]["GrupoPago"];
type EstadoVisible = components["schemas"]["EstadoVisible"];
type Barra = components["schemas"]["BarraPago"];

type Botones = { principal: AccionPago | null; secundarias: AccionPago[]; negativas: AccionPago[] };
export type FilaTabla = Botones & {
  situacion: Situacion;
  paso: Paso | null;
  lista: Grupo | null;
  lista_si_viejo?: Grupo | null;
  aviso_una_vez?: boolean;
  cuenta_como_pendiente: boolean;
  que_decir: string;
  despues: "listo" | "mismo_paso";
  si_el_comprobante_no_coincide?: Botones | null;
};
export type Tabla = {
  estados: Record<EstadoVisible, string>;
  situaciones: Record<Situacion, { estado: EstadoVisible; barra: Barra | null; final: boolean }>;
  listas: Record<Grupo, { pendiente: boolean; plegada: boolean; principal: AccionPago | null }>;
  filas: FilaTabla[];
};

export const TABLA: Tabla = JSON.parse(
  readFileSync(resolve(process.cwd(), "../contratos/cem_contracts/pantallas_datos/pagos.json"), "utf8"),
) as Tabla;

export function filaDe(situacion: Situacion, paso: Paso | null): FilaTabla {
  const fila = TABLA.filas.find((f) => f.situacion === situacion && f.paso === paso);
  if (fila === undefined) throw new Error(`la tabla de la pantalla no tiene ${situacion}/${String(paso)}`);
  return fila;
}

/** Lo que la ficha simulada dice que la base acepta, por acción, con los `puede_*` de la ficha. */
function puedeDe(f: Omit<Ficha, "acciones">): Record<AccionPago, boolean> {
  const fallida = f.orden.estado === "fallida";
  return {
    enviar: f.puede_enviar,
    modificar: f.puede_modificar,
    retirar: f.puede_retirar,
    aprobar: f.puede_aprobar,
    rechazar: f.puede_rechazar,
    renovar: f.puede_renovar,
    pagar: f.puede_pagar,
    ver_cuenta_de_nuevo: f.puede_reabrir_cuenta && f.situacion === "pagando",
    retomar_pago: f.puede_reabrir_cuenta && f.situacion === "no_pago_declarado",
    tomar_pago: f.puede_tomar,
    subir_comprobante: f.puede_subir_comprobante,
    corregir: f.puede_corregir,
    declarar_no_pago: f.puede_declarar,
    conciliar: f.puede_conciliar,
    observar: f.puede_conciliar,
    confirmar_no_pago: f.puede_confirmar,
    resolver_diferencia: f.puede_resolver_diferencia,
    recuperar: f.puede_recuperar,
    cerrar_reclamo: f.puede_recuperar,
    corregir_diferencia: f.puede_corregir_diferencia,
    preparar_faltante: f.puede_preparar_faltante,
    volver_a_pagar: f.puede_modificar && fallida,
    preparar_otra: f.paso === "rechazada",
    cerrar_sin_pagar: f.puede_cerrar_sin_pagar,
    cotejar: false,
  };
}

/** Las acciones que mandaría el servidor para esa ficha. */
export function accionesDe(f: Omit<Ficha, "acciones">, comprobanteNoCoincide = false): AccionOfrecida[] {
  const fila = filaDe(f.situacion, f.paso);
  const botones = comprobanteNoCoincide && fila.si_el_comprobante_no_coincide ? fila.si_el_comprobante_no_coincide : fila;
  const puede = puedeDe(f);
  return [
    ...(botones.principal !== null ? [{ codigo: botones.principal, tipo: "principal" as const }] : []),
    ...botones.secundarias.map((codigo) => ({ codigo, tipo: "secundaria" as const })),
    ...botones.negativas.map((codigo) => ({ codigo, tipo: "negativa" as const })),
  ].filter((a) => puede[a.codigo]);
}

/** Lo que la ficha simulada trae de la tabla: estado, barra, frase y después del acto. */
export function deLaTabla(situacion: Situacion, paso: Paso | null) {
  const fila = filaDe(situacion, paso);
  const s = TABLA.situaciones[situacion];
  return { estado_visible: s.estado, barra: s.barra, que_decir: fila.que_decir, despues: fila.despues };
}

/** La fila de la bandeja simulada: la principal y si cuenta, según la tabla. */
export function deLaFila(paso: Paso, grupo: Grupo): Pick<Elemento, "principal" | "pendiente" | "situacion" | "estado_visible"> {
  const fila = TABLA.filas.find((f) => f.paso === paso && (f.lista === grupo || f.lista_si_viejo === grupo));
  if (fila === undefined) throw new Error(`la tabla no pone ${paso} en ${grupo}`);
  return {
    principal: fila.principal,
    pendiente: TABLA.listas[grupo].pendiente,
    situacion: fila.situacion,
    estado_visible: TABLA.situaciones[fila.situacion].estado,
  };
}
