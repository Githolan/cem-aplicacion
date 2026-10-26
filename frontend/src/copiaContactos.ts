// Copia sin conexión de los contactos de emergencia (2b-7b; doc/diseno/H2b_7_Diseno.md §5 del
// repositorio CEM; decisión del propietario del 25-10-2026, §1.1). Guarda solo la lista general de
// cada consorcio, que no tiene datos personales; nunca la lista de gestión. Se reemplaza entera al
// sincronizar, así que un contacto retirado desaparece; se borra con un 401, 403 o 404 y al salir.
//
// Va en localStorage y no en la caché del service worker (que nunca sirve /api): así se borra por
// consorcio y al salir. localStorage puede no existir o lanzar (Safari privado, datos del sitio
// bloqueados): entonces no hay copia, y la página lo dice; nunca simula una.

import type { components } from "./api/tipos";

type ListaContactos = components["schemas"]["ListaContactos"];
export type Contacto = components["schemas"]["Contacto"];

export const PREFIJO_COPIA = "cem-contactos:";

export type Copia = {
  consorcio_id: string;
  nombre: string;
  /** Cuándo se guardó, en ISO 8601. Es la fecha visible de la copia. */
  guardada_en: string;
  lista: ListaContactos;
};

function almacen(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** Guarda la lista general recién leída; devuelve false si este navegador no deja guardarla. */
export function guardarCopia(copia: Copia): boolean {
  const a = almacen();
  if (a === null) return false;
  try {
    a.setItem(PREFIJO_COPIA + copia.consorcio_id, JSON.stringify(copia));
    return true;
  } catch {
    return false;
  }
}

export function borrarCopia(consorcioId: string) {
  const a = almacen();
  if (a === null) return;
  try {
    a.removeItem(PREFIJO_COPIA + consorcioId);
  } catch {
    // Sin acceso al almacenamiento no hay copia que borrar.
  }
}

/** Todas las copias guardadas. Una entrada que no es una copia válida se borra, no se muestra. */
export function leerCopias(): Copia[] {
  const a = almacen();
  if (a === null) return [];
  const copias: Copia[] = [];
  try {
    for (let i = a.length - 1; i >= 0; i--) {
      const clave = a.key(i);
      if (clave === null || !clave.startsWith(PREFIJO_COPIA)) continue;
      const copia = interpretar(a.getItem(clave));
      if (copia === null) a.removeItem(clave);
      else copias.push(copia);
    }
  } catch {
    return [];
  }
  return copias.sort((x, y) => x.nombre.localeCompare(y.nombre, "es"));
}

/** Al salir de la cuenta, o con la sesión vencida: no queda ninguna copia. */
export function borrarCopias() {
  for (const c of leerCopias()) borrarCopia(c.consorcio_id);
}

function interpretar(texto: string | null): Copia | null {
  if (texto === null) return null;
  try {
    const c = JSON.parse(texto) as Partial<Copia>;
    const ok =
      typeof c.consorcio_id === "string" &&
      typeof c.nombre === "string" &&
      typeof c.guardada_en === "string" &&
      Array.isArray(c.lista?.elementos) &&
      typeof c.lista?.zona_horaria === "string";
    return ok ? (c as Copia) : null;
  } catch {
    return null;
  }
}

/** Hoy (AAAA-MM-DD) en la zona del consorcio, que es el día con el que la API decide la vigencia. */
export function hoyEn(zona: string, ahora: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: zona }).format(ahora);
}

/**
 * Al mostrar una copia se vuelve a aplicar la vigencia (Codex en CEM #85): un contacto cuyo
 * `vigente_hasta` (exclusivo) ya llegó no se muestra, aunque la copia sea anterior.
 */
export function vigentes(contactos: Contacto[], hoy: string): Contacto[] {
  return contactos.filter((c) => c.vigente_hasta === null || c.vigente_hasta > hoy);
}
