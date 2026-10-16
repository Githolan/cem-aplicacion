// Modo lectura ampliada (RF53; documento 13, §14.1): un tema que se activa con una clase en <html>.
// La clase se pone desde el módulo inicial (main.tsx), antes de que React pinte nada, porque la
// política de seguridad (script-src 'self') no admite un script en línea en index.html.

export const CLASE_LECTURA_AMPLIADA = "lectura-ampliada";
export const CLAVE_LECTURA_AMPLIADA = "cem-lectura-ampliada";
// Valores guardados: la elección explícita, también "apagado", para que valga sobre la del sistema.
const ACTIVADO = "1";
const DESACTIVADO = "0";
// Sin elección guardada, quien pidió más contraste al sistema empieza con el modo activo.
export const CONSULTA_MAS_CONTRASTE = "(prefers-contrast: more)";

// localStorage puede no existir o lanzar (Safari privado, datos del sitio bloqueados). En ese caso
// la preferencia no se recuerda, pero la página tiene que seguir funcionando: devuelve null.
function leerGuardada(): boolean | null {
  let valor: string | null;
  try {
    valor = window.localStorage.getItem(CLAVE_LECTURA_AMPLIADA);
  } catch {
    return null;
  }
  if (valor === ACTIVADO) return true;
  if (valor === DESACTIVADO) return false;
  return null;
}

export function preferenciaInicial(): boolean {
  return leerGuardada() ?? window.matchMedia(CONSULTA_MAS_CONTRASTE).matches;
}

export function lecturaAmpliadaActiva(): boolean {
  return document.documentElement.classList.contains(CLASE_LECTURA_AMPLIADA);
}

export function aplicarLecturaAmpliada(activa: boolean) {
  document.documentElement.classList.toggle(CLASE_LECTURA_AMPLIADA, activa);
}

/** Guarda la elección; devuelve false si este navegador no la deja guardar (se informa en pantalla). */
export function guardarLecturaAmpliada(activa: boolean): boolean {
  try {
    window.localStorage.setItem(CLAVE_LECTURA_AMPLIADA, activa ? ACTIVADO : DESACTIVADO);
    return true;
  } catch {
    return false;
  }
}
