import { type FormEvent, useId, useState } from "react";

import { api } from "./api/cliente";
import { DIGITOS_CODIGO } from "./EntrarConCodigo";
import { codigoDe, mensajeDeFalla, SIN_CONEXION } from "./fallas";

// Pedir la entrada por correo (documento 13, §10.3): `POST /api/v1/auth/enlace` con el correo en
// el cuerpo, nunca en la URL. El correo trae un código de 6 números, que es el camino principal
// (decisión del propietario del 12-11-2026, pregunta 16), y un enlace como alternativa. La
// respuesta deja en este navegador la cookie `nonce` a la que queda ligado el enlace (el pedido es
// del mismo origen, así que fetch la guarda y la manda después en el canje): el enlace solo sirve en
// el navegador donde se pidió; el código sirve en cualquiera (EntrarConCodigo). El 202 es igual
// exista o no la cuenta; se muestra el mensaje que manda el servidor, sin agregarle nada que
// distinga los casos. Con el pedido hecho, /entrar pasa al código con el correo ya escrito
// (`alPedir`). Diseño provisorio: el definitivo llega con G3.

const ESTADO_INVALIDO = 422;
const ESTADO_DEMASIADOS = 429;

type Estado =
  | { tipo: "listo" }
  | { tipo: "enviando" }
  | { tipo: "invalido" }
  | { tipo: "demasiados" }
  | { tipo: "sin_servicio"; mensaje: string };

interface Props {
  /** El correo es de /entrar: sigue escrito al cambiar de forma. */
  correo: string;
  alCambiarCorreo: (correo: string) => void;
  alPedir: (correo: string, mensaje: string) => void;
}

export function PedirEnlace({ correo, alCambiarCorreo, alPedir }: Props) {
  const id = useId();
  const [estado, setEstado] = useState<Estado>({ tipo: "listo" });

  function enviar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    setEstado({ tipo: "enviando" });
    const limpio = correo.trim();
    void api
      .POST("/api/v1/auth/enlace", { body: { correo: limpio } })
      .then(({ data, error, response }) => {
        if (error === undefined) alPedir(limpio, data.mensaje);
        else if (response.status === ESTADO_INVALIDO) setEstado({ tipo: "invalido" });
        else if (response.status === ESTADO_DEMASIADOS) setEstado({ tipo: "demasiados" });
        else setEstado({ tipo: "sin_servicio", mensaje: mensajeDeFalla(response.status, codigoDe(error)) });
      })
      .catch(() => setEstado({ tipo: "sin_servicio", mensaje: SIN_CONEXION }));
  }

  return (
    <section aria-labelledby={`${id}-titulo`} className="tarjeta">
      <h2 id={`${id}-titulo`}>Pedir un código para entrar</h2>
      <p>
        Al correo llega un código de {DIGITOS_CODIGO} números para entrar desde cualquier
        dispositivo. El mismo correo trae un enlace, que sirve solo en este dispositivo y en este
        navegador.
      </p>
      <form className="formulario" onSubmit={enviar}>
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
        <button type="submit" disabled={estado.tipo === "enviando"}>
          Enviar el código
        </button>
      </form>
      <div role="status" aria-live="polite">
        {estado.tipo === "enviando" && <p>Enviando el código…</p>}
        {estado.tipo === "invalido" && (
          <p id={`${id}-invalido`} className="aviso-error">
            El correo no parece una dirección válida. Revisarlo.
          </p>
        )}
        {estado.tipo === "demasiados" && (
          <p className="aviso-error">
            Hubo demasiados pedidos con este correo. Esperar un rato y probar de nuevo.
          </p>
        )}
        {estado.tipo === "sin_servicio" && <p className="aviso-error">{estado.mensaje}</p>}
      </div>
    </section>
  );
}
