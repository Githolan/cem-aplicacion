// Borradores sin conexión de los reclamos sobre la liquidación (RF38, PA16; app 0123; decisión del
// propietario del 25-10-2026, §1.6: RF38 se construye con el formulario de reclamos). Como la copia
// de los contactos (copiaContactos.ts), va en localStorage y no en la caché del service worker (que
// nunca sirve /api): se borra por consorcio y al salir de la cuenta.
//
// Un borrador se guarda mientras se escribe y queda «Borrador sin enviar» hasta que el servidor lo
// recibe. Al pedir enviarlo sin conexión queda `por_enviar`, con su clave de idempotencia: se manda
// solo al volver la conexión, y un reintento después de un envío que sí llegó no lo repite. El
// servidor revalida todo (el vínculo, la unidad, la liquidación): si lo rechaza, el borrador queda
// marcado con el motivo para que la persona lo descarte o lo corrija; nunca se borra solo.
//
// localStorage puede no existir o lanzar (Safari privado, datos del sitio bloqueados). Entonces
// guardar devuelve false y leer devuelve null, y la pantalla lo dice: nunca simula un borrador
// guardado.

import type { components } from "../api/tipos";

export type CampoCitado = components["schemas"]["CampoCitado"];

export const PREFIJO_BORRADOR = "cem-reclamo:";

/** En qué quedó el borrador. */
export type EstadoBorrador =
  /** Escrito y guardado; nadie pidió enviarlo todavía. */
  | { tipo: "escribiendo" }
  /** Se pidió enviarlo y no hubo conexión: sale al volver, con esta clave. */
  | { tipo: "por_enviar"; clave: string }
  /** El servidor lo rechazó (p. ej. el vínculo ya no está vigente): se descarta o se corrige. */
  | { tipo: "rechazado"; motivo: string }
  /** La liquidación citada fue reemplazada: se cita la vigente o se reclama sobre la reemplazada. */
  | { tipo: "reemplazada" };

export type Borrador = {
  /** Local: identifica el borrador en este navegador. */
  id: string;
  consorcio_id: string;
  liquidacion_id: string;
  periodo: string;
  unidad_id: string;
  designacion: string;
  campo: CampoCitado | null;
  texto: string;
  /** La persona eligió reclamar sobre la liquidación citada aunque haya sido reemplazada. */
  acepta_reemplazada: boolean;
  /** Cuándo se guardó por última vez, en ISO 8601. */
  guardado_en: string;
  estado: EstadoBorrador;
};

function almacen(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

const claveDe = (b: Pick<Borrador, "consorcio_id" | "id">) => `${PREFIJO_BORRADOR}${b.consorcio_id}:${b.id}`;

/** Guarda el borrador; false si este navegador no deja guardarlo (la pantalla lo dice). */
export function guardarBorrador(b: Borrador): boolean {
  const a = almacen();
  if (a === null) return false;
  try {
    a.setItem(claveDe(b), JSON.stringify(b));
    return true;
  } catch {
    return false;
  }
}

/** Borra el borrador; false si este navegador no deja tocar lo guardado (la pantalla lo dice). */
export function borrarBorrador(b: Pick<Borrador, "consorcio_id" | "id">): boolean {
  const a = almacen();
  if (a === null) return false;
  try {
    a.removeItem(claveDe(b));
    return true;
  } catch {
    return false;
  }
}

function esCampo(x: unknown): x is CampoCitado | null {
  return x === null || x === "saldo_unidad" || x === "importe_unidad";
}

function esEstado(x: unknown): x is EstadoBorrador {
  if (typeof x !== "object" || x === null || !("tipo" in x)) return false;
  switch (x.tipo) {
    case "escribiendo":
    case "reemplazada":
      return true;
    case "por_enviar":
      return "clave" in x && typeof x.clave === "string";
    case "rechazado":
      return "motivo" in x && typeof x.motivo === "string";
    default:
      return false;
  }
}

function interpretar(crudo: string | null): Borrador | null {
  if (crudo === null) return null;
  let b: unknown;
  try {
    b = JSON.parse(crudo);
  } catch {
    return null;
  }
  if (typeof b !== "object" || b === null) return null;
  const x = b as Record<string, unknown>;
  const textos = ["id", "consorcio_id", "liquidacion_id", "periodo", "unidad_id", "designacion", "texto", "guardado_en"];
  if (!textos.every((k) => typeof x[k] === "string")) return null;
  if (!esCampo(x.campo) || !esEstado(x.estado) || typeof x.acepta_reemplazada !== "boolean") return null;
  return b as Borrador;
}

/** Los borradores del consorcio, el más viejo primero; null si este navegador no deja leerlos. Una
 * entrada que no es un borrador válido (otra versión de la aplicación) se borra, no se muestra. */
export function leerBorradores(consorcioId: string): Borrador[] | null {
  const a = almacen();
  if (a === null) return null;
  const prefijo = `${PREFIJO_BORRADOR}${consorcioId}:`;
  const borradores: Borrador[] = [];
  try {
    for (let i = a.length - 1; i >= 0; i--) {
      const clave = a.key(i);
      if (clave === null || !clave.startsWith(prefijo)) continue;
      const b = interpretar(a.getItem(clave));
      if (b === null) a.removeItem(clave);
      else borradores.push(b);
    }
  } catch {
    return null;
  }
  return borradores.sort((x, y) => x.guardado_en.localeCompare(y.guardado_en));
}

/** Al salir de la cuenta: no queda ningún borrador para quien entre después en este navegador. */
export function borrarBorradores() {
  const a = almacen();
  if (a === null) return;
  try {
    for (let i = a.length - 1; i >= 0; i--) {
      const clave = a.key(i);
      if (clave !== null && clave.startsWith(PREFIJO_BORRADOR)) a.removeItem(clave);
    }
  } catch {
    // Sin acceso al almacenamiento no pudo haber borradores guardados.
    return;
  }
}
