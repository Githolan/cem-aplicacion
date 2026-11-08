import { useState } from "react";

import { ContenidoDelArchivo, HojaDelArchivo, useArchivoDeVersion } from "../VisorDocumento";

// El comprobante de un pago a la vista, al lado de los datos (§4), y «Ver más grande» para mirarlo
// en una hoja ancha con el mismo archivo ya pedido.

const NOMBRE = "Comprobante de la transferencia";

type Props = {
  consorcio: string;
  documento: string;
  version: string;
  aEntrar: () => void;
};

export function VisorComprobante({ consorcio, documento, version, aEntrar }: Props) {
  const { lectura, reintentar } = useArchivoDeVersion({ consorcio, documento, version }, aEntrar);
  const [grande, setGrande] = useState(false);
  return (
    <div>
      <ContenidoDelArchivo lectura={lectura} reintentar={reintentar} nombre={NOMBRE} />
      {lectura.tipo === "ok" && (
        <div className="formulario">
          <div className="acciones">
            <button type="button" className="secundario" onClick={() => setGrande(true)}>
              Ver más grande
            </button>
          </div>
        </div>
      )}
      {grande && <HojaDelArchivo lectura={lectura} reintentar={reintentar} nombre={NOMBRE} alCerrar={() => setGrande(false)} />}
    </div>
  );
}
