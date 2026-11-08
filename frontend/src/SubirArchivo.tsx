import { type ChangeEvent, type DragEvent, useId, useRef, useState } from "react";

import { aceptar, enMegas, nombresDe, problemaDelArchivo, type TipoArchivo } from "./archivos";

// La única forma de elegir un archivo para subir (comentario 32 de la revisión): en todos los anchos,
// a la vez, la zona para soltarlo, la cámara, la galería y el archivo. Elegir por cualquiera de esos
// caminos es el único paso: el archivo que sirve pasa a quien lo usa, y el que no sirve se dice acá,
// sin mandar nada. Sin imágenes entre los tipos (el extracto CSV) no se ofrecen la cámara ni la
// galería. Los campos del sistema quedan ocultos: lo que se toca y lo que lee el lector de pantalla
// son los botones, con el teclado como cualquier botón.

type Props = {
  /** Lo que dice arriba («Subir el comprobante»); sin esto, el título lo pone la sección que la contiene. */
  titulo?: string;
  /** De qué es el archivo, para nombrar los campos: «del comprobante» → «Archivo del comprobante». */
  de: string;
  tipos: readonly TipoArchivo[];
  /** En bytes; nulo mientras no se sabe (el servidor controla igual). */
  tope: number | null;
  /** Ya hay un archivo: lo que se elija lo reemplaza. */
  reemplaza?: boolean;
  deshabilitada?: boolean;
  /** Lo que se agrega a la ayuda de qué se acepta. */
  nota?: string;
  /** El archivo ya elegido, en palabras, cuando hace falta otro paso para mandarlo. */
  elegido?: string | null;
  /** Para llevar el foco al primer botón al abrir un formulario. */
  refPrimero?: (boton: HTMLButtonElement | null) => void;
  alElegir: (archivo: File) => void;
};

export function SubirArchivo({ titulo, de, tipos, tope, reemplaza = false, deshabilitada = false, nota, elegido, refPrimero, alElegir }: Props) {
  const id = useId();
  const [encima, setEncima] = useState(false);
  const [problema, setProblema] = useState<string | null>(null);
  const foto = useRef<HTMLInputElement>(null);
  const galeria = useRef<HTMLInputElement>(null);
  const archivo = useRef<HTMLInputElement>(null);
  const imagenes = tipos.filter((t) => t.imagen);
  const conFotos = imagenes.length > 0;
  const ayuda = [tope === null ? `${nombresDe(tipos)}.` :`${nombresDe(tipos)}, hasta ${enMegas(tope)}.`, nota].filter(Boolean).join(" ");

  function recibir(elegidoAhora: File | undefined) {
    if (elegidoAhora === undefined || deshabilitada) return;
    const falla = problemaDelArchivo(elegidoAhora, tipos, tope);
    setProblema(falla);
    if (falla === null) alElegir(elegidoAhora);
  }

  function cambio(evento: ChangeEvent<HTMLInputElement>) {
    const primero = evento.target.files?.[0];
    // Sin esto, elegir de nuevo el mismo archivo no avisa nada.
    evento.target.value = "";
    recibir(primero);
  }

  function soltar(evento: DragEvent) {
    evento.preventDefault();
    setEncima(false);
    recibir(evento.dataTransfer.files[0]);
  }

  return (
    <div
      role="group"
      aria-labelledby={titulo === undefined ? undefined : `${id}-titulo`}
      aria-describedby={`${id}-ayuda`}
      className={encima ? "subir-archivo subir-archivo--encima" : "subir-archivo"}
      // Sin el `preventDefault` el navegador abre el archivo soltado en lugar de la aplicación.
      onDragOver={(e) => {
        e.preventDefault();
        if (!deshabilitada) setEncima(true);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setEncima(false);
      }}
      onDrop={soltar}
    >
      {titulo !== undefined && (
        <strong id={`${id}-titulo`} className="subir-archivo__titulo">
          {titulo}
        </strong>
      )}
      <p className="subir-archivo__soltar">{reemplaza ? "Soltar otro archivo acá" : "Soltar el archivo acá"}</p>
      <p id={`${id}-ayuda`} className="subir-archivo__ayuda">
        {ayuda}
      </p>
      {conFotos && (
        <>
          <input ref={foto} type="file" hidden accept={aceptar(imagenes)} capture="environment" aria-label={`Foto ${de}`}
            disabled={deshabilitada} onChange={cambio} />
          <input ref={galeria} type="file" hidden accept={aceptar(imagenes)} aria-label={`Imagen de la galería ${de}`}
            disabled={deshabilitada} onChange={cambio} />
        </>
      )}
      <input ref={archivo} type="file" hidden accept={aceptar(tipos)} aria-label={`Archivo ${de}`} disabled={deshabilitada} onChange={cambio} />
      <div className="subir-archivo__botones">
        {conFotos && (
          <>
            <button type="button" className="secundario" ref={refPrimero} disabled={deshabilitada} onClick={() => foto.current?.click()}>
              Sacar una foto
            </button>
            <button type="button" className="secundario" disabled={deshabilitada} onClick={() => galeria.current?.click()}>
              Elegir de la galería
            </button>
          </>
        )}
        <button type="button" className="secundario" ref={conFotos ? undefined : refPrimero} disabled={deshabilitada}
          onClick={() => archivo.current?.click()}>
          Elegir un archivo
        </button>
      </div>
      {elegido != null && <p className="archivo-elegido">Elegido: {elegido}</p>}
      {problema !== null && (
        <p className="aviso-error" role="alert">
          {problema}
        </p>
      )}
    </div>
  );
}
