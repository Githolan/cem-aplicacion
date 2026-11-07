import { useEffect, useId, useRef, useState } from "react";

import { EntrarConCodigo } from "./EntrarConCodigo";
import { EntrarConContrasena } from "./EntrarConContrasena";
import { PedirEnlace } from "./PedirEnlace";

// Las formas de entrar, de a una por vez, con un solo correo para las tres (decisión del propietario
// del 05-11-2026, §2). Las usan /entrar y la hoja de entrar encima.

type Metodo = "enlace" | "codigo" | "contrasena";

const OTRAS_FORMAS: Record<Metodo, string> = {
  enlace: "Pedir el enlace por correo",
  codigo: "Entrar con un código",
  contrasena: "Entrar con contraseña",
};

interface Props {
  correo: string;
  alCambiarCorreo: (correo: string) => void;
  /** Elegir otra forma: /entrar deja atrás el enlace que falló. */
  alElegir?: () => void;
  /** Sin esto, terminar sigue al inicio. */
  alEntrar?: (aviso: string) => void;
}

export function FormasDeEntrar({ correo, alCambiarCorreo, alElegir, alEntrar }: Props) {
  const id = useId();
  const [metodo, setMetodo] = useState<Metodo>("enlace");
  const [avisoDelPedido, setAvisoDelPedido] = useState<string | null>(null);
  const formas = useRef<HTMLDivElement>(null);
  const eligio = useRef(false);

  // Al cambiar de forma, el foco va al primer campo por completar: lo de arriba ya no es lo que se
  // eligió, y con el correo ya escrito lo que falta es lo que sigue.
  useEffect(() => {
    if (!eligio.current) return;
    const campos = Array.from<HTMLInputElement>(formas.current?.querySelectorAll("input") ?? []);
    (campos.find((campo) => campo.value === "") ?? campos[0])?.focus();
  }, [metodo]);

  function elegir(forma: Metodo) {
    eligio.current = true;
    alElegir?.();
    setMetodo(forma);
  }

  return (
    <>
      <div ref={formas}>
        {metodo === "enlace" && (
          <PedirEnlace
            correo={correo}
            alCambiarCorreo={alCambiarCorreo}
            alPedir={(pedido, mensaje) => {
              alCambiarCorreo(pedido);
              setAvisoDelPedido(mensaje);
              elegir("codigo");
            }}
          />
        )}
        {metodo === "codigo" && (
          <EntrarConCodigo correo={correo} alCambiarCorreo={alCambiarCorreo} aviso={avisoDelPedido} alEntrar={alEntrar} />
        )}
        {metodo === "contrasena" && (
          <EntrarConContrasena correo={correo} alCambiarCorreo={alCambiarCorreo} alEntrar={alEntrar} />
        )}
      </div>
      <section aria-labelledby={`${id}-otras`} className="tarjeta">
        <h2 id={`${id}-otras`}>Otras formas de entrar</h2>
        {(Object.keys(OTRAS_FORMAS) as Metodo[])
          .filter((forma) => forma !== metodo)
          .map((forma) => (
            <p key={forma}>
              <button type="button" className="secundario" onClick={() => elegir(forma)}>
                {OTRAS_FORMAS[forma]}
              </button>
            </p>
          ))}
      </section>
    </>
  );
}
