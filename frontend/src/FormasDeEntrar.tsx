import { useEffect, useId, useRef, useState } from "react";

import { EntrarConCodigo } from "./EntrarConCodigo";
import { EntrarConContrasena } from "./EntrarConContrasena";
import { ultimaFormaDeEntrar } from "./formaDeEntrar";
import { PedirEnlace } from "./PedirEnlace";

// Las formas de entrar, de a una por vez, con un solo correo para las tres (decisión del propietario
// del 05-11-2026, §2). Las usan /entrar y la hoja de entrar encima. La principal es el código de 6
// números del correo (decisión del 12-11-2026, pregunta 16): se pide («enlace», porque el mismo
// correo trae también el enlace) y se escribe («codigo»). Se abre la última forma con la que se
// entró en este dispositivo (AC-30), salvo que quien la usa pida otra (`inicial`).
//
// En la hoja de la sesión vencida (`enHoja`, informe de fricción ACC-05): el correo de la sesión va
// como texto, sin tarjetas adentro de la hoja, y las otras formas quedan plegadas detrás de un enlace.

export type Metodo = "enlace" | "codigo" | "contrasena";

const OTRAS_FORMAS: Record<Metodo, string> = {
  enlace: "Pedir un código por correo",
  codigo: "Ya tengo un código",
  contrasena: "Entrar con contraseña",
};

function metodoInicial(): Metodo {
  return ultimaFormaDeEntrar() === "contrasena" ? "contrasena" : "enlace";
}

interface Props {
  correo: string;
  alCambiarCorreo: (correo: string) => void;
  /** Elegir otra forma: /entrar deja atrás el enlace que falló. */
  alElegir?: () => void;
  /** Sin esto, terminar sigue al inicio. */
  alEntrar?: () => void;
  /** La forma con la que se abre; sin esto, la última usada en este dispositivo. */
  inicial?: Metodo;
  /** Dentro de la hoja de la sesión vencida. */
  enHoja?: boolean;
}

export function FormasDeEntrar({ correo, alCambiarCorreo, alElegir, alEntrar, inicial, enHoja = false }: Props) {
  const id = useId();
  const [metodo, setMetodo] = useState<Metodo>(() => inicial ?? metodoInicial());
  const [avisoDelPedido, setAvisoDelPedido] = useState<string | null>(null);
  const formas = useRef<HTMLDivElement>(null);
  const eligio = useRef(false);
  // En la hoja el correo es el de la sesión: no se vuelve a escribir.
  const correoFijo = enHoja && correo !== "";

  // Al cambiar de forma, el foco va al primer campo por completar: lo de arriba ya no es lo que se
  // eligió, y con el correo ya escrito lo que falta es lo que sigue.
  useEffect(() => {
    if (!eligio.current) return;
    const campos = Array.from<HTMLInputElement>(formas.current?.querySelectorAll("input:not([hidden])") ?? []);
    (campos.find((campo) => campo.value === "") ?? campos[0])?.focus();
  }, [metodo]);

  function elegir(forma: Metodo) {
    eligio.current = true;
    alElegir?.();
    if (forma === "enlace") setAvisoDelPedido(null);
    setMetodo(forma);
  }

  const otras = (Object.keys(OTRAS_FORMAS) as Metodo[])
    .filter((forma) => forma !== metodo)
    .map((forma) => (
      <p key={forma}>
        <button type="button" className="secundario" onClick={() => elegir(forma)}>
          {OTRAS_FORMAS[forma]}
        </button>
      </p>
    ));

  return (
    <>
      <div ref={formas}>
        {metodo === "enlace" && (
          <PedirEnlace
            correo={correo}
            alCambiarCorreo={alCambiarCorreo}
            correoFijo={correoFijo}
            enHoja={enHoja}
            alPedir={(pedido, mensaje) => {
              alCambiarCorreo(pedido);
              setAvisoDelPedido(mensaje);
              elegir("codigo");
            }}
          />
        )}
        {metodo === "codigo" && (
          <EntrarConCodigo
            // Otro pedido es otro paso: el código escrito y los avisos del anterior no siguen.
            key={avisoDelPedido ?? "sin-pedido"}
            correo={correo}
            alCambiarCorreo={alCambiarCorreo}
            aviso={avisoDelPedido}
            alEntrar={alEntrar}
            alCambiar={() => elegir("enlace")}
            correoFijo={correoFijo}
            enHoja={enHoja}
          />
        )}
        {metodo === "contrasena" && (
          <EntrarConContrasena correo={correo} alCambiarCorreo={alCambiarCorreo} alEntrar={alEntrar} correoFijo={correoFijo} enHoja={enHoja} />
        )}
      </div>
      {enHoja ? (
        <details className="otras-formas">
          <summary>Otras formas de entrar</summary>
          {otras}
        </details>
      ) : (
        <section aria-labelledby={`${id}-otras`} className="tarjeta">
          <h2 id={`${id}-otras`}>Otras formas de entrar</h2>
          {otras}
        </section>
      )}
    </>
  );
}
