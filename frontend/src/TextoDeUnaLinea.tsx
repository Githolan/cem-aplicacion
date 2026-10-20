import type { TextareaHTMLAttributes } from "react";

// Un texto largo que la API guarda en una sola línea, en un `textarea` para leerlo entero en el
// celular sin desplazarse (doc/diseno/UX_Invitaciones_y_Formularios_Movil.md del repositorio CEM,
// §3). La API rechaza los caracteres de control (`FORMATO_REFERENCIA`): Enter no agrega una línea y
// un salto pegado se vuelve un espacio. Un `textarea` no admite `pattern`, así que el texto hecho
// solo de espacios (que la API rechaza al recortarlo) se marca inválido con `setCustomValidity`.

export const SOLO_ESPACIOS = "Escribí algo más que espacios.";

type Props = Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "value" | "onChange" | "onKeyDown"> & {
  value: string;
  onValor: (valor: string) => void;
};

export function TextoDeUnaLinea({ value, onValor, ...resto }: Props) {
  return (
    <textarea
      {...resto}
      value={value}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.preventDefault();
      }}
      onChange={(e) => {
        const valor = e.target.value.replace(/\r\n|\r|\n/g, " ");
        e.target.setCustomValidity(valor !== "" && valor.trim() === "" ? SOLO_ESPACIOS : "");
        onValor(valor);
      }}
    />
  );
}
