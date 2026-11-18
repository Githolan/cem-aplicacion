import { useState } from "react";

import { MONEDAS } from "./FormularioOrden";
import { NOMBRE_MONEDA } from "./textos";

// «Moneda que salió del banco» (FL-PAG-01, FL-ACL-02, flujos 18-11-2026): la moneda se elige por su
// nombre, como en la orden y en el cotejo, con la de la orden ya elegida. Solo si salió en una que no
// es ninguna de esas se escribe su código, en un campo propio.

/** La opción del desplegable que pide escribir el código. */
const OTRA = "otra";

/** El nombre de una moneda para leer («Pesos», «Dólares»); una que no se conoce, por su código. */
export const nombreDeMoneda = (codigo: string) => NOMBRE_MONEDA[codigo] ?? codigo;

/** Lo que dice el campo cuando falta el código de otra moneda. */
export const FALTA_CODIGO_MONEDA = "Falta escribir el código de la moneda, de tres letras, como EUR.";

/** Si lo elegido sirve: una de las conocidas o un código de tres letras. */
export const monedaValida = (codigo: string) => /^[A-Z]{3}$/.test(codigo);

type Props = {
  /** El id del desplegable, que nombra la etiqueta de quien lo usa. */
  id: string;
  valor: string;
  alCambiar: (codigo: string) => void;
  deshabilitado?: boolean;
  /** La marca del error (`marcaDe(id, …)`): va en el campo del código, el único que puede faltar. */
  marca?: { "aria-invalid"?: true; "aria-describedby"?: string };
};

export function CampoMonedaSalida({ id, valor, alCambiar, deshabilitado = false, marca }: Props) {
  const [otra, setOtra] = useState(() => !(MONEDAS as readonly string[]).includes(valor));
  const idCodigo = `${id}-codigo`;
  return (
    <>
      <select
        id={id}
        value={otra ? OTRA : valor}
        disabled={deshabilitado}
        aria-describedby={`${id}-ayuda`}
        onChange={(e) => {
          const elegida = e.target.value;
          setOtra(elegida === OTRA);
          alCambiar(elegida === OTRA ? "" : elegida);
        }}
      >
        {MONEDAS.map((m) => (
          <option key={m} value={m}>
            {nombreDeMoneda(m)}
          </option>
        ))}
        <option value={OTRA}>Otra</option>
      </select>
      <p id={`${id}-ayuda`}>Si no es la de la orden, solo se puede reclamar.</p>
      {otra && (
        <>
          <label htmlFor={idCodigo}>Código de la moneda</label>
          <input id={idCodigo} maxLength={3} autoCapitalize="characters" value={valor} disabled={deshabilitado} {...marca}
            onChange={(e) => alCambiar(e.target.value.toUpperCase())} />
        </>
      )}
    </>
  );
}
