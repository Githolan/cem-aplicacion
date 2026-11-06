import { type ChangeEvent, type DragEvent, useRef, useState } from "react";

import { CONSULTA_TELEFONO, useAncho } from "./useAncho";

// Elegir el archivo dispara todo: no hay un botón «Subir». En PC y tablet, zona para soltar; en el
// teléfono, la cámara o la galería. Se decide por ancho (§4 y §15).

const TIPOS = "application/pdf,image/jpeg,image/png";
const TIPOS_DE_FOTO = "image/jpeg,image/png";

type Props = {
  deshabilitada: boolean;
  /** Ya hay un comprobante: lo que se elija lo reemplaza. */
  reemplaza: boolean;
  alElegir: (archivo: File) => void;
};

export function ZonaComprobante({ deshabilitada, reemplaza, alElegir }: Props) {
  const telefono = useAncho(CONSULTA_TELEFONO);
  const [encima, setEncima] = useState(false);
  const archivo = useRef<HTMLInputElement>(null);
  const foto = useRef<HTMLInputElement>(null);

  function elegido(evento: ChangeEvent<HTMLInputElement>) {
    const primero = evento.target.files?.[0];
    // Sin esto, elegir de nuevo el mismo archivo no avisa nada.
    evento.target.value = "";
    if (primero !== undefined) alElegir(primero);
  }

  function soltar(evento: DragEvent) {
    evento.preventDefault();
    setEncima(false);
    const primero = evento.dataTransfer.files[0];
    if (primero !== undefined && !deshabilitada) alElegir(primero);
  }

  const entradaArchivo = (
    <input ref={archivo} type="file" hidden accept={TIPOS} aria-label="Archivo del comprobante" disabled={deshabilitada} onChange={elegido} />
  );

  if (telefono)
    return (
      <div className="zona-comprobante">
        <strong>{reemplaza ? "Cambiá el comprobante" : "Subí el comprobante"}</strong>
        <input ref={foto} type="file" hidden accept={TIPOS_DE_FOTO} capture="environment" aria-label="Foto del comprobante"
          disabled={deshabilitada} onChange={elegido} />
        {entradaArchivo}
        <button type="button" disabled={deshabilitada} onClick={() => foto.current?.click()}>
          Sacar una foto
        </button>
        <button type="button" className="secundario" disabled={deshabilitada} onClick={() => archivo.current?.click()}>
          Elegir de la galería
        </button>
      </div>
    );

  return (
    <div className={encima ? "zona-comprobante zona-comprobante--encima" : "zona-comprobante"}
      onDragOver={(e) => {
        e.preventDefault();
        setEncima(true);
      }}
      onDragLeave={() => setEncima(false)}
      onDrop={soltar}>
      <strong>{reemplaza ? "Soltá acá otro comprobante" : "Soltá acá el comprobante"}</strong>
      <span>PDF, JPEG o PNG, hasta 20 MB</span>
      {entradaArchivo}
      <button type="button" className="secundario" disabled={deshabilitada} onClick={() => archivo.current?.click()}>
        {reemplaza ? "Elegir otro archivo" : "Elegir el archivo"}
      </button>
    </div>
  );
}
