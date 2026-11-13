import { useEffect, useRef, useState } from "react";

// Copiar el CBU o el importe para pegarlo en el banco (§5, control 1). Si el navegador no deja, el
// texto queda seleccionado para copiarlo a mano.

type Props = {
  /** Lo que se copia, tal como lo pide el banco. */
  valor: string;
  /** Lo que se copia, en palabras: «el CBU», «el importe». */
  nombre: string;
};

export function BotonCopiar({ valor, nombre }: Props) {
  const [estado, setEstado] = useState<"sin_copiar" | "copiado" | "a_mano">("sin_copiar");
  const seleccionable = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (estado === "a_mano") seleccionable.current?.select();
  }, [estado]);

  async function copiar() {
    try {
      await navigator.clipboard.writeText(valor);
      setEstado("copiado");
    } catch {
      setEstado("a_mano");
    }
  }

  return (
    <>
      <button type="button" className="boton-copiar" aria-label={`${estado === "copiado" ? "Copiado" : "Copiar"} ${nombre}`} onClick={() => void copiar()}>
        {estado === "copiado" ? "Copiado" : "Copiar"}
      </button>
      {estado === "a_mano" && (
        <span className="copiar-a-mano">
          <input ref={seleccionable} readOnly aria-label={`Para copiar a mano: ${nombre}`} value={valor} onFocus={(e) => e.target.select()} />
          <span role="status">No se pudo copiar solo: quedó seleccionado para copiarlo a mano.</span>
        </span>
      )}
    </>
  );
}
