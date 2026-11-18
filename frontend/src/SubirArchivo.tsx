import { type ChangeEvent, type DragEvent, useEffect, useId, useRef, useState } from "react";

import { aceptar, enMegas, nombresDe, problemaDelArchivo, type TipoArchivo } from "./archivos";

// La única forma de elegir un archivo para subir (comentario 32 de la revisión): en todos los anchos,
// a la vez, la zona para soltarlo, la cámara, la galería y el archivo. Elegir por cualquiera de esos
// caminos es el único paso: el archivo que sirve pasa a quien lo usa, y el que no sirve se dice acá,
// sin mandar nada. Sin imágenes entre los tipos (el extracto CSV) no se ofrecen la cámara ni la
// galería. Los campos del sistema quedan ocultos: lo que se toca y lo que lee el lector de pantalla
// son los botones, con el teclado como cualquier botón.
//
// Con un archivo ya elegido o subido (`elegido`), las formas de elegir dejan lugar a su nombre y a un
// único «Cambiar el archivo», que vuelve a abrirlas con «Dejar este archivo» para cerrarlas (DOC-16:
// sin botones que sugieran repetir lo hecho). Es la única forma en toda la aplicación: también la usa
// la carga con lectura.

export const CAMBIAR_ARCHIVO = "Cambiar el archivo";
export const DEJAR_ARCHIVO = "Dejar este archivo";

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
  /** El archivo ya elegido o subido, en palabras: con esto, solo su nombre y «Cambiar el archivo». */
  elegido?: string | null;
  /** Ya hay un archivo aunque no se sepa su nombre (uno subido antes): solo «Cambiar el archivo».
   * Sin esto, lo decide `elegido`. */
  conArchivo?: boolean;
  /** Para llevar el foco al primer botón al abrir un formulario. Con esto, el primer botón lleva
   * también `data-foco`: dentro de una `Hoja` (una `VentanaDeFormulario`), el foco entra ahí. */
  refPrimero?: (boton: HTMLButtonElement | null) => void;
  alElegir: (archivo: File) => void;
};

export function SubirArchivo({ titulo, de, tipos, tope, reemplaza = false, deshabilitada = false, nota, elegido, conArchivo: hayArchivo, refPrimero, alElegir }: Props) {
  const id = useId();
  const [encima, setEncima] = useState(false);
  const [problema, setProblema] = useState<string | null>(null);
  const foto = useRef<HTMLInputElement>(null);
  const galeria = useRef<HTMLInputElement>(null);
  const archivo = useRef<HTMLInputElement>(null);
  const grupo = useRef<HTMLDivElement>(null);
  const primeraOpcion = useRef<HTMLButtonElement | null>(null);
  const cambiar = useRef<HTMLButtonElement | null>(null);
  const [cambiando, setCambiando] = useState(false);
  /** A dónde va el foco cuando el botón tocado deja de estar (abrir o cerrar las opciones). */
  const foco = useRef<"opciones" | "cambiar" | null>(null);
  const conArchivo = hayArchivo ?? elegido != null;
  const opciones = !conArchivo || cambiando;
  const imagenes = tipos.filter((t) => t.imagen);
  const conFotos = imagenes.length > 0;
  const ayuda = [tope === null ? `${nombresDe(tipos)}.` :`${nombresDe(tipos)}, hasta ${enMegas(tope)}.`, nota].filter(Boolean).join(" ");

  useEffect(() => {
    const destino = foco.current;
    if (destino === null) return;
    foco.current = null;
    if (destino === "opciones") primeraOpcion.current?.focus();
    // Solo si el foco quedó perdido con el botón que se fue: no se lo saca a otro campo.
    else if (document.activeElement === document.body || grupo.current?.contains(document.activeElement)) cambiar.current?.focus();
  }, [opciones]);

  function recibir(elegidoAhora: File | undefined) {
    if (elegidoAhora === undefined || deshabilitada) return;
    const falla = problemaDelArchivo(elegidoAhora, tipos, tope);
    setProblema(falla);
    if (falla === null) {
      foco.current = "cambiar";
      setCambiando(false);
      alElegir(elegidoAhora);
    }
  }

  function abrirOpciones(abiertas: boolean) {
    foco.current = abiertas ? "opciones" : "cambiar";
    setProblema(null);
    setCambiando(abiertas);
  }

  /** El primer botón: el de la cámara, o el del archivo sin imágenes entre los tipos. */
  function primero(boton: HTMLButtonElement | null) {
    primeraOpcion.current = boton;
    refPrimero?.(boton);
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
    // Con las opciones cerradas no hay zona para soltar: se abre con «Cambiar el archivo».
    if (opciones) recibir(evento.dataTransfer.files[0]);
  }

  return (
    <div
      ref={grupo}
      role="group"
      aria-labelledby={titulo === undefined ? undefined : `${id}-titulo`}
      aria-describedby={opciones ? `${id}-ayuda` : undefined}
      className={encima ? "subir-archivo subir-archivo--encima" : "subir-archivo"}
      // Sin el `preventDefault` el navegador abre el archivo soltado en lugar de la aplicación.
      onDragOver={(e) => {
        e.preventDefault();
        if (!deshabilitada && opciones) setEncima(true);
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
      {opciones && <p className="subir-archivo__soltar">{reemplaza ? "Soltar otro archivo acá" : "Soltar el archivo acá"}</p>}
      {opciones && (
        <p id={`${id}-ayuda`} className="subir-archivo__ayuda">
          {ayuda}
        </p>
      )}
      {conFotos && (
        <>
          <input ref={foto} type="file" hidden accept={aceptar(imagenes)} capture="environment" aria-label={`Foto ${de}`}
            disabled={deshabilitada} onChange={cambio} />
          <input ref={galeria} type="file" hidden accept={aceptar(imagenes)} aria-label={`Imagen de la galería ${de}`}
            disabled={deshabilitada} onChange={cambio} />
        </>
      )}
      <input ref={archivo} type="file" hidden accept={aceptar(tipos)} aria-label={`Archivo ${de}`} disabled={deshabilitada} onChange={cambio} />
      {elegido != null && <p className="archivo-elegido">Elegido: {elegido}</p>}
      {opciones ? (
        <div className="subir-archivo__botones">
          {conFotos && (
            <>
              <button type="button" className="secundario" ref={primero} data-foco={refPrimero === undefined ? undefined : true}
                disabled={deshabilitada} onClick={() => foto.current?.click()}>
                Sacar una foto
              </button>
              <button type="button" className="secundario" disabled={deshabilitada} onClick={() => galeria.current?.click()}>
                Elegir de la galería
              </button>
            </>
          )}
          <button type="button" className="secundario" ref={conFotos ? undefined : primero}
            data-foco={conFotos || refPrimero === undefined ? undefined : true} disabled={deshabilitada}
            onClick={() => archivo.current?.click()}>
            Elegir un archivo
          </button>
          {conArchivo && (
            <button type="button" className="secundario" disabled={deshabilitada} onClick={() => abrirOpciones(false)}>
              {DEJAR_ARCHIVO}
            </button>
          )}
        </div>
      ) : (
        <div className="subir-archivo__botones">
          <button type="button" className="secundario" ref={(boton) => {
            cambiar.current = boton;
            refPrimero?.(boton);
          }} data-foco={refPrimero === undefined ? undefined : true} disabled={deshabilitada} onClick={() => abrirOpciones(true)}>
            {CAMBIAR_ARCHIVO}
          </button>
        </div>
      )}
      {problema !== null && (
        <p className="aviso-error" role="alert">
          {problema}
        </p>
      )}
    </div>
  );
}
