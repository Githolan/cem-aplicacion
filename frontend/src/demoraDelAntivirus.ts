import type { components } from "./api/tipos";

type EstadoDeMiVersion = components["schemas"]["EstadoDeMiVersion"];
type DemoraAnalisis = components["schemas"]["DemoraAnalisis"];

/** Por qué el antivirus todavía no terminó, en palabras (`0136`). */
const CAUSA: Record<DemoraAnalisis, string> = {
  firmas_viejas: "el antivirus no tiene al día su base de virus y CEM no acepta archivos hasta que se actualice",
  antivirus_no_disponible: "el antivirus no responde",
  otra_falla: "la revisión del archivo falló",
};

const HORA = new Intl.DateTimeFormat("es-AR", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

/** Ningún sistema falla en silencio (regla del propietario, 27-11-2026). Mientras el análisis de un
 * archivo falla, la pantalla dice por qué y cuándo se vuelve a probar, en vez de «se sigue esperando».
 * Nulo si no hay nada que decir: el archivo no está en cuarentena o ningún intento falló. */
export function textoDeDemora(estado: EstadoDeMiVersion): string | null {
  if (estado.estado !== "en_cuarentena" || estado.demora == null) return null;
  const causa = CAUSA[estado.demora];
  // Solo se abandona por una falla que no es del antivirus (`0136`): otro archivo tampoco pasaría.
  if (estado.se_abandono) return `CEM dejó de intentar revisar el archivo: ${causa} varias veces. Hay que avisar a soporte.`;
  // Sin próximo intento, la cola se rindió por el antivirus: lo retoma sola cuando se recupera.
  const cuando =
    estado.proximo_intento == null ? " cuando el antivirus se recupere" : ` a las ${HORA.format(new Date(estado.proximo_intento))}`;
  return `Todavía no se puede revisar el archivo: ${causa}. CEM lo vuelve a probar solo${cuando}.`;
}

/** La cola dejó de intentarlo por una falla que no es del antivirus: la pantalla deja de esperar y no
 * ofrece subir otro (con la versión en cuarentena, el servidor lo rechazaría). */
export function seAbandono(estado: EstadoDeMiVersion): boolean {
  return estado.estado === "en_cuarentena" && estado.se_abandono === true;
}
