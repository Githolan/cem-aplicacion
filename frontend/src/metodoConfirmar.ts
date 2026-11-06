// El último método con el que se confirmó en este dispositivo (rediseño de pagos, §12). Se guarda
// solo el tipo: nada de la llave ni de los códigos. Si este navegador no deja guardar, no se recuerda
// y la confirmación elige sola.

const CLAVE = "cem.confirmar.ultimo-metodo";
const METODOS = ["llave", "aplicacion", "recuperacion"] as const;

export type MetodoConfirmar = (typeof METODOS)[number];

export function ultimoMetodo(): MetodoConfirmar | null {
  try {
    const guardado = window.localStorage.getItem(CLAVE);
    return METODOS.find((m) => m === guardado) ?? null;
  } catch {
    return null;
  }
}

export function recordarMetodo(metodo: MetodoConfirmar): void {
  try {
    window.localStorage.setItem(CLAVE, metodo);
  } catch {
    return;
  }
}
