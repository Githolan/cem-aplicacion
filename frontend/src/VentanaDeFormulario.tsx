import { type FormEvent, type ReactNode, useEffect, useId, useRef, useState } from "react";

import { Hoja } from "./Hoja";

// La ventana para cargar datos (criterio 3 del informe de fricción, tanda 1): toda carga de datos va
// en una ventana propia, encima de la pantalla, con solo su título, sus campos, un botón principal y
// «Cancelar». Se apoya en `Hoja` (foco, Escape, lo de atrás inerte) y en `pilaDeHojas` (una ventana
// encima de otra: la de una acción, sobre la ficha del registro).
//
// Cómo se usa: la pantalla la monta cuando la persona toca la acción y la desmonta en `alCerrar`.
//
//   {abierta && (
//     <VentanaDeFormulario titulo="Señalar un dato incorrecto" textoEnviar="Señalar" alCerrar={() => setAbierta(false)}
//       alEnviar={async () => {
//         const faltan = faltaCompletar({ motivo: [motivo, "Falta escribir el motivo."] });
//         if (hayErrores(faltan)) { setErrores(faltan); return "seguir"; }
//         const r = await pedir();                       // la pantalla hace su pedido
//         return r.ok ? "cerrar" : { rechazo: r.texto };  // salió bien: se cierra sola
//       }}>
//       <label htmlFor={idMotivo}>Motivo</label>
//       <input id={idMotivo} data-foco value={motivo} {...marcaDe(idMotivo, errores.motivo)} onChange={…} />
//       <ErrorDeCampo idCampo={idMotivo} mensaje={errores.motivo} />
//     </VentanaDeFormulario>
//   )}
//
// - Al abrirse, el foco va al primer elemento con `data-foco` (el primer campo: ponérselo); si no hay
//   ninguno (una pregunta sin campos), al botón principal.
// - El botón principal está siempre activo (decisión 4): lo obligatorio lo comprueba `alEnviar`, que
//   marca el campo con `ErrorDeCampo` y devuelve "seguir". El navegador no valida (`noValidate`).
// - Mientras viaja el pedido, los campos y los dos botones se apagan (lo único apagado que admite el
//   criterio 8) y Escape no cierra.
// - El rechazo se muestra adentro, arriba de los botones, y toma el foco; si la pantalla marcó un
//   campo, el foco va al primer campo marcado.

/**
 * Cómo terminó un envío.
 * - "cerrar": salió bien (o la pantalla ya no tiene a qué volver): la ventana llama a `alCerrar`.
 * - "seguir": queda abierta sin mensaje nuevo (la pantalla marcó los campos que faltan, o cambió lo
 *   que pregunta). El foco va al primer campo marcado o, si no hay, al botón principal.
 * - `{ rechazo }`: queda abierta con el motivo adentro.
 */
export type FinDeEnvio = "cerrar" | "seguir" | { rechazo: string };

type Props = {
  titulo: string;
  /** El texto del botón principal: el verbo del acto («Guardar», «Sí, retirar»). */
  textoEnviar: string;
  /** Hace el pedido y dice cómo terminó. No debe lanzar: cada pantalla traduce sus fallas. */
  alEnviar: () => FinDeEnvio | Promise<FinDeEnvio>;
  /** «Cancelar», Escape o un envío que terminó en "cerrar": la pantalla desmonta la ventana. */
  alCerrar: () => void;
  /** Los campos (con sus `ErrorDeCampo`) y, si hace falta, una línea que diga qué se va a hacer. */
  children: ReactNode;
  /** El principal es el acto negativo (retirar, anular): va en rojo. */
  peligro?: boolean;
  /** Más ancha en PC y tablet (un formulario largo). */
  ancha?: boolean;
  /** Sin el botón principal: con lo elegido el acto no se puede hacer y una línea de la ventana dice
   * qué falta y dónde se resuelve (criterio 8). Queda «Cancelar»; al cambiar lo elegido, vuelve. */
  sinEnviar?: boolean;
};

export function VentanaDeFormulario({ titulo, textoEnviar, alEnviar, alCerrar, children, peligro = false, ancha = false, sinEnviar = false }: Props) {
  const id = useId();
  const [enviando, setEnviando] = useState(false);
  const [rechazo, setRechazo] = useState<string | null>(null);
  /** Cada envío que no cierra mueve el foco una vez, cuando ya se dibujó lo que cambió. */
  const [vuelta, setVuelta] = useState(0);
  const formulario = useRef<HTMLFormElement>(null);
  const principal = useRef<HTMLButtonElement>(null);
  const textoRechazo = useRef<HTMLParagraphElement>(null);
  const montada = useRef(true);

  useEffect(() => {
    montada.current = true;
    return () => {
      montada.current = false;
    };
  }, []);

  useEffect(() => {
    if (vuelta === 0) return;
    const marcado = formulario.current?.querySelector<HTMLElement>('[aria-invalid="true"]');
    (marcado ?? textoRechazo.current ?? principal.current)?.focus();
  }, [vuelta]);

  async function enviar(evento: FormEvent) {
    evento.preventDefault();
    if (sinEnviar) return;
    setEnviando(true);
    setRechazo(null);
    let fin: FinDeEnvio;
    try {
      fin = await alEnviar();
    } finally {
      if (montada.current) setEnviando(false);
    }
    // Si la pantalla ya la desmontó (cerró también la ficha), no queda nada que hacer acá.
    if (!montada.current) return;
    if (fin === "cerrar") {
      alCerrar();
      return;
    }
    if (fin !== "seguir") setRechazo(fin.rechazo);
    setVuelta((v) => v + 1);
  }

  return (
    <Hoja titulo={titulo} alEscapar={enviando ? undefined : alCerrar} ancha={ancha}>
      <form ref={formulario} className="formulario formulario-movil ventana-formulario" noValidate onSubmit={(e) => void enviar(e)}>
        <fieldset className="ventana-formulario__campos" disabled={enviando}>
          {children}
        </fieldset>
        {rechazo !== null && (
          <p ref={textoRechazo} id={`${id}-rechazo`} tabIndex={-1} role="alert" className="aviso-error">
            {rechazo}
          </p>
        )}
        <div className="acciones">
          {!sinEnviar && (
            <button ref={principal} type="submit" className={peligro ? "peligro" : undefined} disabled={enviando} data-foco>
              {textoEnviar}
            </button>
          )}
          <button type="button" className="secundario" onClick={alCerrar} disabled={enviando}>
            Cancelar
          </button>
        </div>
      </form>
    </Hoja>
  );
}
