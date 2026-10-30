import { useId } from "react";

import type { PantallaConsorcio } from "./useConsorcio";

type Props = { pantalla: PantallaConsorcio; consorcio: string; elegir: (id: string) => void; deshabilitado: boolean };

/** El edificio sobre el que se trabaja, cuando la persona tiene más de uno. */
export function ElegirConsorcio({ pantalla, consorcio, elegir, deshabilitado }: Props) {
  const id = useId();
  if (pantalla.tipo !== "lista" || pantalla.consorcios.length < 2) return null;
  return (
    <div className="formulario">
      <label htmlFor={id}>Edificio</label>
      <select id={id} value={consorcio} disabled={deshabilitado} onChange={(e) => elegir(e.target.value)}>
        {pantalla.consorcios.map((c) => (
          <option key={c.id} value={c.id}>
            {c.nombre}
          </option>
        ))}
      </select>
    </div>
  );
}
