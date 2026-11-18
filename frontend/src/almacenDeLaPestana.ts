// sessionStorage cuando el navegador lo deja usar. Con el almacenamiento del sitio bloqueado (un
// ajuste de privacidad, algunas ventanas privadas), leer o escribir lanza `SecurityError`; sin esto,
// /entrar no se dibujaba. Lo que va acá es una comodidad de esta pestaña (el enlace para el próximo
// intento, adónde iba la persona): sin almacenamiento no se recuerda, y la pantalla sigue, como en
// `leerGuardada()` de lecturaAmpliada.ts y `descartadoGuardado()` de Instalar.tsx.

/** Lo guardado con esa clave, o null si no hay nada o el navegador no deja leer. */
export function leerDeLaPestana(clave: string): string | null {
  try {
    return window.sessionStorage.getItem(clave);
  } catch {
    return null;
  }
}

/** Guarda si el navegador deja; si no, no queda recordado. */
export function guardarEnLaPestana(clave: string, valor: string): void {
  try {
    window.sessionStorage.setItem(clave, valor);
  } catch {
    return;
  }
}

/** Borra si el navegador deja; si no deja, tampoco pudo haber guardado nada. */
export function borrarDeLaPestana(clave: string): void {
  try {
    window.sessionStorage.removeItem(clave);
  } catch {
    return;
  }
}
