import { type FormEvent, useId, useState } from "react";
import { Link } from "react-router";

import { api } from "./api/cliente";

// Entrar con el código de seis dígitos del correo (documento 13, §10.3): para quien pidió la
// entrada en un dispositivo y lee el correo en otro, donde el enlace no sirve (está ligado al
// navegador que lo pidió), o para quien no puede abrir enlaces. El código llega en el mismo
// correo que el enlace. Va en el cuerpo de un POST, nunca en la URL. Diseño provisorio: el
// definitivo llega con G3.

export const DIGITOS_CODIGO = 6;
const ESTADO_RECHAZADO = 401;
const ESTADO_DEMASIADOS = 429;

type Estado = "listo" | "enviando" | "adentro" | "rechazado" | "demasiados" | "sin_servicio";

/** El código como lo escribió la persona («042 917», «042-917»), sin separadores. */
export function normalizarCodigo(valor: string): string {
  return valor.replace(/[\s-]/g, "");
}

export function codigoValido(valor: string): boolean {
  return new RegExp(`^[0-9]{${DIGITOS_CODIGO}}$`).test(valor);
}

export function EntrarConCodigo() {
  const id = useId();
  const [correo, setCorreo] = useState("");
  const [codigo, setCodigo] = useState("");
  const [estado, setEstado] = useState<Estado>("listo");
  const [formatoMal, setFormatoMal] = useState(false);

  function enviar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    const limpio = normalizarCodigo(codigo);
    if (!codigoValido(limpio)) {
      setFormatoMal(true);
      return;
    }
    setFormatoMal(false);
    setEstado("enviando");
    void api
      .POST("/api/v1/auth/codigo", { body: { correo: correo.trim(), codigo: limpio } })
      .then(({ error, response }) => {
        if (error === undefined) setEstado("adentro");
        else if (response.status === ESTADO_RECHAZADO) setEstado("rechazado");
        else if (response.status === ESTADO_DEMASIADOS) setEstado("demasiados");
        else setEstado("sin_servicio");
      })
      .catch(() => setEstado("sin_servicio"));
  }

  if (estado === "adentro") {
    return (
      <section aria-labelledby={`${id}-titulo`} className="tarjeta">
        <h2 id={`${id}-titulo`}>Entrar con un código</h2>
        <div role="status" aria-live="polite">
          <p className="aviso-ok">Entraste.</p>
          <p>
            <Link to="/">Ir al inicio</Link>
          </p>
        </div>
      </section>
    );
  }

  return (
    <section aria-labelledby={`${id}-titulo`} className="tarjeta">
      <h2 id={`${id}-titulo`}>Entrar con un código</h2>
      <p>
        ¿Leés el correo en otro dispositivo, o no podés abrir el enlace? Escribí tu correo y el
        código de {DIGITOS_CODIGO} números que llegó en el mismo correo.
      </p>
      <form className="formulario" onSubmit={enviar}>
        <label htmlFor={`${id}-correo`}>Correo</label>
        <input
          id={`${id}-correo`}
          type="email"
          autoComplete="email"
          required
          value={correo}
          onChange={(e) => setCorreo(e.target.value)}
        />
        <label htmlFor={`${id}-codigo`}>Código de {DIGITOS_CODIGO} números</label>
        <input
          id={`${id}-codigo`}
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          required
          maxLength={DIGITOS_CODIGO + 2}
          aria-invalid={formatoMal}
          aria-describedby={formatoMal ? `${id}-formato` : undefined}
          value={codigo}
          onChange={(e) => setCodigo(e.target.value)}
        />
        {formatoMal && (
          <p id={`${id}-formato`} className="aviso-error">
            El código tiene {DIGITOS_CODIGO} números.
          </p>
        )}
        <button type="submit" disabled={estado === "enviando"}>
          Entrar con el código
        </button>
      </form>
      <div role="status" aria-live="polite">
        {estado === "enviando" && <p>Entrando…</p>}
        {estado === "rechazado" && (
          <p className="aviso-error">
            No pudimos hacerte entrar con ese código. Revisá el correo y el código; si venció,
            se usó o se equivocó varias veces, pedí uno nuevo.
          </p>
        )}
        {estado === "demasiados" && (
          <p className="aviso-error">
            Hubo demasiados intentos con este correo. Esperá un rato y probá de nuevo.
          </p>
        )}
        {estado === "sin_servicio" && (
          <p className="aviso-error">No pudimos completar el pedido. Revisá la conexión y probá en un rato.</p>
        )}
      </div>
    </section>
  );
}
