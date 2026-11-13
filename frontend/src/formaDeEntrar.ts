// La última forma con la que se entró en este dispositivo (informe de fricción, AC-30): /entrar y la
// hoja de entrar encima abren esa. Se guarda solo el tipo, nunca el correo. Las formas por correo (el
// código y el enlace) son una sola: las dos salen del mismo pedido. Si este navegador no deja guardar,
// no se recuerda y se abre la forma principal, el código por correo (decisión del propietario del
// 12-11-2026, pregunta 16).

const CLAVE = "cem.entrar.ultima-forma";
const FORMAS = ["correo", "contrasena"] as const;

export type FormaDeEntrar = (typeof FORMAS)[number];

export function ultimaFormaDeEntrar(): FormaDeEntrar | null {
  try {
    const guardada = window.localStorage.getItem(CLAVE);
    return FORMAS.find((f) => f === guardada) ?? null;
  } catch {
    return null;
  }
}

export function recordarFormaDeEntrar(forma: FormaDeEntrar): void {
  try {
    window.localStorage.setItem(CLAVE, forma);
  } catch {
    return;
  }
}
