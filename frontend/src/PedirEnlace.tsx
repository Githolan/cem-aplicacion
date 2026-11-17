import { type FormEvent, useId, useState } from "react";

import { api } from "./api/cliente";
import { codigoDe, mensajeDeFalla, SIN_CONEXION } from "./fallas";

// Pedir la entrada por correo (documento 13, §10.3): `POST /api/v1/auth/enlace` con el correo en
// el cuerpo, nunca en la URL. El correo trae un código de 6 números, que es el camino principal
// (decisión del propietario del 12-11-2026, pregunta 16), y un enlace como alternativa. La
// respuesta deja en este navegador la cookie `nonce` a la que queda ligado el enlace (el pedido es
// del mismo origen, así que fetch la guarda y la manda después en el canje): el enlace solo sirve en
// el navegador donde se pidió; el código sirve en cualquiera (EntrarConCodigo). El 202 es igual
// exista o no la cuenta; se muestra el mensaje que manda el servidor, sin agregarle nada que
// distinga los casos. Con el pedido hecho, /entrar pasa al código con el correo ya escrito
// (`alPedir`). Informe de fricción, ACC-04: sin párrafo de explicación y con el botón corto. En la
// hoja de la sesión vencida el correo es el de la sesión y va como texto (ACC-05).

const ESTADO_INVALIDO = 422;
const ESTADO_DEMASIADOS = 429;

/** Cómo terminó un pedido del código: el mensaje del servidor, o qué decir si no salió. */
export type PedidoDeCodigo = { ok: true; mensaje: string } | { ok: false; tipo: "invalido" | "demasiados" | "sin_servicio"; mensaje: string };

const CORREO_INVALIDO = "El correo no parece una dirección válida. Revisarlo.";
const DEMASIADOS_PEDIDOS = "Hubo demasiados pedidos con este correo. Esperar un rato y probar de nuevo.";

/** Pide el código (y el enlace) al correo. También lo usa «Enviar otro código» (ACC-02). */
export async function pedirCodigoPorCorreo(correo: string): Promise<PedidoDeCodigo> {
  try {
    const { data, error, response } = await api.POST("/api/v1/auth/enlace", { body: { correo } });
    if (error === undefined) return { ok: true, mensaje: data.mensaje };
    if (response.status === ESTADO_INVALIDO) return { ok: false, tipo: "invalido", mensaje: CORREO_INVALIDO };
    if (response.status === ESTADO_DEMASIADOS) return { ok: false, tipo: "demasiados", mensaje: DEMASIADOS_PEDIDOS };
    return { ok: false, tipo: "sin_servicio", mensaje: mensajeDeFalla(response.status, codigoDe(error)) };
  } catch {
    return { ok: false, tipo: "sin_servicio", mensaje: SIN_CONEXION };
  }
}

type Estado = { tipo: "listo" } | { tipo: "enviando" } | Extract<PedidoDeCodigo, { ok: false }>;

interface Props {
  /** El correo es de /entrar: sigue escrito al cambiar de forma. */
  correo: string;
  alCambiarCorreo: (correo: string) => void;
  alPedir: (correo: string, mensaje: string) => void;
  /** El correo de la sesión vencida: va como texto, sin campo. */
  correoFijo?: boolean;
  /** Dentro de una hoja: sin tarjeta. */
  enHoja?: boolean;
}

export function PedirEnlace({ correo, alCambiarCorreo, alPedir, correoFijo = false, enHoja = false }: Props) {
  const id = useId();
  const [estado, setEstado] = useState<Estado>({ tipo: "listo" });

  function enviar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    setEstado({ tipo: "enviando" });
    const limpio = correo.trim();
    void pedirCodigoPorCorreo(limpio).then((pedido) => {
      if (pedido.ok) alPedir(limpio, pedido.mensaje);
      else setEstado(pedido);
    });
  }

  return (
    <section aria-labelledby={`${id}-titulo`} className={enHoja ? undefined : "tarjeta"}>
      <h2 id={`${id}-titulo`}>Pedir un código para entrar</h2>
      <form className="formulario" onSubmit={enviar}>
        {correoFijo ? (
          <p>
            Correo: <strong>{correo}</strong>
          </p>
        ) : (
          <>
            <label htmlFor={`${id}-correo`}>Correo</label>
            <input
              id={`${id}-correo`}
              type="email"
              autoComplete="email"
              required
              aria-invalid={estado.tipo === "invalido"}
              aria-describedby={estado.tipo === "invalido" ? `${id}-invalido` : undefined}
              value={correo}
              onChange={(e) => alCambiarCorreo(e.target.value)}
            />
          </>
        )}
        <button type="submit" disabled={estado.tipo === "enviando"}>
          Enviar
        </button>
      </form>
      <div role="status" aria-live="polite">
        {estado.tipo === "enviando" && <p>Enviando el código…</p>}
        {estado.tipo === "invalido" && (
          <p id={`${id}-invalido`} className="aviso-error">
            {estado.mensaje}
          </p>
        )}
        {(estado.tipo === "demasiados" || estado.tipo === "sin_servicio") && <p className="aviso-error">{estado.mensaje}</p>}
      </div>
    </section>
  );
}
