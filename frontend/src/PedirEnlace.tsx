import { type FormEvent, useId, useState } from "react";

import { api } from "./api/cliente";
import { DIGITOS_CODIGO } from "./EntrarConCodigo";

// Pedir la entrada por correo (documento 13, §10.3): `POST /api/v1/auth/enlace` con el correo en
// el cuerpo, nunca en la URL. La respuesta deja en este navegador la cookie `nonce` a la que
// queda ligado el enlace (el pedido es del mismo origen, así que fetch la guarda y la manda
// después en el canje): el enlace solo sirve en el navegador donde se pidió. Para otro
// dispositivo está el código del mismo correo (EntrarConCodigo). El 202 es igual exista o no la
// cuenta; se muestra el mensaje que manda el servidor, sin agregarle nada que distinga los casos.
// Diseño provisorio: el definitivo llega con G3.

const ESTADO_INVALIDO = 422;
const ESTADO_DEMASIADOS = 429;

type Estado =
  | { tipo: "listo" }
  | { tipo: "enviando" }
  | { tipo: "pedido"; mensaje: string }
  | { tipo: "invalido" }
  | { tipo: "demasiados" }
  | { tipo: "sin_servicio" };

export function PedirEnlace() {
  const id = useId();
  const [correo, setCorreo] = useState("");
  const [estado, setEstado] = useState<Estado>({ tipo: "listo" });

  function enviar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    setEstado({ tipo: "enviando" });
    void api
      .POST("/api/v1/auth/enlace", { body: { correo: correo.trim() } })
      .then(({ data, error, response }) => {
        if (error === undefined) setEstado({ tipo: "pedido", mensaje: data.mensaje });
        else if (response.status === ESTADO_INVALIDO) setEstado({ tipo: "invalido" });
        else if (response.status === ESTADO_DEMASIADOS) setEstado({ tipo: "demasiados" });
        else setEstado({ tipo: "sin_servicio" });
      })
      .catch(() => setEstado({ tipo: "sin_servicio" }));
  }

  return (
    <section aria-labelledby={`${id}-titulo`} className="tarjeta">
      <h2 id={`${id}-titulo`}>Pedir el enlace para entrar</h2>
      <p>
        Escribí tu correo y te mandamos un enlace para entrar. Abrilo en este mismo navegador: el
        enlace solo sirve donde lo pediste. Si leés el correo en otro dispositivo, usá el código
        de {DIGITOS_CODIGO} números que llega en el mismo correo.
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
          onChange={(e) => setCorreo(e.target.value)}
        />
        <button type="submit" disabled={estado.tipo === "enviando"}>
          Pedir el enlace
        </button>
      </form>
      <div role="status" aria-live="polite">
        {estado.tipo === "enviando" && <p>Pidiendo el enlace…</p>}
        {estado.tipo === "pedido" && <p className="aviso-ok">{estado.mensaje}</p>}
        {estado.tipo === "invalido" && (
          <p id={`${id}-invalido`} className="aviso-error">
            Revisá el correo: no parece una dirección válida.
          </p>
        )}
        {estado.tipo === "demasiados" && (
          <p className="aviso-error">
            Hubo demasiados pedidos con este correo. Esperá un rato y probá de nuevo.
          </p>
        )}
        {estado.tipo === "sin_servicio" && (
          <p className="aviso-error">No pudimos completar el pedido. Revisá la conexión y probá en un rato.</p>
        )}
      </div>
    </section>
  );
}
