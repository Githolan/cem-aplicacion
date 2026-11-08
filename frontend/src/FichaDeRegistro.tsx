import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import { Hoja } from "./Hoja";

// Las listas llevan a la ficha de cada registro (comentario 21 de la revisión): tocar la fila entera
// abre una hoja con todos sus datos y, abajo, solo las acciones que el rol de la persona permite. La
// fila no tiene botones propios: es un único control, con el foco visible y 44 px de alto. Al cerrar
// la ficha el foco vuelve a la fila que la abrió. Quién puede hacer qué lo decide el servidor: la
// ficha solo deja de ofrecer lo que ya se sabe que no alcanza (`usePuede` o una marca de la API).

export type DatoDeFicha = { etiqueta: string; valor: ReactNode };

type PropsFila = {
  titulo: ReactNode;
  /** Un resumen corto del estado; todo lo demás va en la ficha. */
  resumen?: ReactNode;
  alAbrir: () => void;
  refFila?: (elemento: HTMLButtonElement | null) => void;
  /** La fila de la ficha abierta (o del detalle a la vista). */
  abierta?: boolean;
  deshabilitada?: boolean;
};

/** Una fila de una lista: el botón entero abre la ficha. */
export function FilaDeRegistro({ titulo, resumen, alAbrir, refFila, abierta = false, deshabilitada = false }: PropsFila) {
  return (
    <button
      type="button"
      ref={refFila}
      className="fila-registro"
      onClick={alAbrir}
      disabled={deshabilitada}
      aria-current={abierta ? "true" : undefined}
    >
      <span className="fila-registro__titulo">{titulo}</span>
      {/* El espacio separa el título del resumen en el nombre que lee el lector de pantalla. */}
      {resumen !== undefined && (
        <>
          {" "}
          <span className="fila-registro__resumen">{resumen}</span>
        </>
      )}
    </button>
  );
}

type PropsFicha = {
  titulo: string;
  datos: readonly DatoDeFicha[];
  /** Las acciones que el rol permite y sus formularios. Sin nada, la ficha es solo de lectura. */
  children?: ReactNode;
  alCerrar: () => void;
  /** Con un pedido en curso no se cierra: ni «Cerrar» ni Escape. */
  ocupada?: boolean;
};

/** La ficha de un registro, en una hoja sobre la lista. */
export function FichaDeRegistro({ titulo, datos, children, alCerrar, ocupada = false }: PropsFicha) {
  return (
    <Hoja titulo={titulo} alEscapar={ocupada ? undefined : alCerrar}>
      <dl className="ficha-registro">
        {datos.map((d) => (
          <div key={d.etiqueta} className="ficha-registro__dato">
            <dt>{d.etiqueta}</dt>
            <dd>{d.valor}</dd>
          </div>
        ))}
      </dl>
      {children}
      <p className="ficha-registro__cerrar">
        <button type="button" className="secundario" onClick={alCerrar} disabled={ocupada}>
          Cerrar
        </button>
      </p>
    </Hoja>
  );
}

/**
 * Qué ficha está abierta, por la clave de su registro, y la fila de cada clave para devolverle el
 * foco al cerrar. `Hoja` ya devuelve el foco a lo que lo tenía al abrir, pero un navegador que no
 * enfoca el botón al tocarlo (Safari) lo dejaría en la página: por eso se vuelve a la fila. Cuando
 * la ficha se cierra porque terminó un pedido, la pantalla lleva el foco al aviso del resultado y
 * cierra con `cerrar(false)`.
 */
export function useFichas<K>() {
  const [abierta, setAbierta] = useState<K | null>(null);
  const actual = useRef<K | null>(null);
  const devolverA = useRef<K | null>(null);
  const filas = useRef(new Map<K, HTMLElement>());

  useEffect(() => {
    if (abierta !== null || devolverA.current === null) return;
    const fila = filas.current.get(devolverA.current);
    devolverA.current = null;
    fila?.focus();
  }, [abierta]);

  const abrir = useCallback((clave: K) => {
    actual.current = clave;
    setAbierta(clave);
  }, []);

  const cerrar = useCallback((devolverFoco = true) => {
    devolverA.current = devolverFoco ? actual.current : null;
    actual.current = null;
    setAbierta(null);
  }, []);

  const refFila = useCallback(
    (clave: K) => (elemento: HTMLElement | null) => {
      if (elemento === null) filas.current.delete(clave);
      else filas.current.set(clave, elemento);
    },
    [],
  );

  const filaDe = useCallback((clave: K) => filas.current.get(clave) ?? null, []);

  return { abierta, abrir, cerrar, refFila, filaDe };
}
