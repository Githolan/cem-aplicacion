import { type TextareaHTMLAttributes, useLayoutEffect, useRef } from "react";

// Un texto largo que la API guarda en una sola línea, en un `textarea` para leerlo entero en el
// celular sin desplazarse (doc/diseno/UX_Invitaciones_y_Formularios_Movil.md del repositorio CEM,
// §3). La API rechaza los caracteres de control (`FORMATO_REFERENCIA`, U+0000 a U+001F y U+007F):
// Enter no agrega una línea y cada carácter de control pegado (salto, tabulación) se vuelve un
// espacio, sin mover el cursor. Un `textarea` no admite `pattern`, así que el texto hecho solo de
// espacios (que la API rechaza al recortarlo) se marca inválido con `setCustomValidity`.

export const SOLO_ESPACIOS = "Escribí algo más que espacios.";

const ULTIMO_CONTROL = 0x1f;
const SUPRIMIR = 0x7f;

/** Cada carácter de control, un espacio: el largo no cambia y el cursor queda donde estaba. */
export function sinControles(valor: string): string {
  let salida = "";
  for (const c of valor) {
    const codigo = c.charCodeAt(0);
    salida += codigo <= ULTIMO_CONTROL || codigo === SUPRIMIR ? " " : c;
  }
  return salida;
}

type Props = Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "value" | "onChange" | "onKeyDown"> & {
  value: string;
  onValor: (valor: string) => void;
};

export function TextoDeUnaLinea({ value, onValor, ...resto }: Props) {
  const campo = useRef<HTMLTextAreaElement>(null);
  // La posición del cursor cuando el valor se corrigió: React reescribe el campo y la perdería.
  const cursor = useRef<[number, number] | null>(null);

  // La validez sale del valor, también cuando lo cambia el código (p. ej. al limpiar el formulario).
  useLayoutEffect(() => {
    const nodo = campo.current;
    if (nodo === null) return;
    nodo.setCustomValidity(value !== "" && value.trim() === "" ? SOLO_ESPACIOS : "");
    if (cursor.current !== null) {
      nodo.setSelectionRange(...cursor.current);
      cursor.current = null;
    }
  }, [value]);

  return (
    <textarea
      {...resto}
      ref={campo}
      value={value}
      onKeyDown={(e) => {
        // El Enter que confirma una composición (IME) no se toca.
        if (e.key === "Enter" && !e.nativeEvent.isComposing) e.preventDefault();
      }}
      onChange={(e) => {
        const valor = sinControles(e.target.value);
        if (valor !== e.target.value) cursor.current = [e.target.selectionStart, e.target.selectionEnd];
        onValor(valor);
      }}
    />
  );
}
