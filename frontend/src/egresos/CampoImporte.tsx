import { useState } from "react";

import { aCentavos, importeEnCampo } from "./comun";

interface Props {
  id: string;
  valor: string;
  deshabilitado: boolean;
  alCambiar: (texto: string) => void;
}

// Abre el teclado numérico de cualquier dispositivo y admite punto o coma. Mientras se escribe, el
// texto queda como está; al salir se ve en el formato del idioma, con centavos, y así se ve también
// cómo se leyó lo escrito. Es solo lo que se muestra: el formulario conserva lo escrito, porque
// tocarlo soltaría la clave del pedido y un reintento podría registrar el acto dos veces.
export function CampoImporte({ id, valor, deshabilitado, alCambiar }: Props) {
  const [escribiendo, setEscribiendo] = useState(false);
  const centavos = aCentavos(valor);
  return (
    <input
      id={id}
      required
      inputMode="decimal"
      value={!escribiendo && centavos !== null ? importeEnCampo(centavos) : valor}
      disabled={deshabilitado}
      onFocus={() => setEscribiendo(true)}
      onBlur={() => setEscribiendo(false)}
      onChange={(e) => alCambiar(e.target.value)}
    />
  );
}
