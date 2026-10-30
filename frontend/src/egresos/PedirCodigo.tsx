import { type FormEvent, useEffect, useId, useRef, useState } from "react";

import { api } from "../api/cliente";
import { encabezadoCsrf } from "../csrf";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { codigoVerificacion } from "../SegundoFactor";
import { mensajeDeError } from "./comun";

// Renovar el segundo factor (pantalla 4, aprobada el 30-10-2026): ante `segundo_factor_requerido`
// la pantalla pide el código, lo verifica en esta sesión (`POST /api/v1/auth/totp/verificar`) y
// recién entonces reintenta el mismo acto. La pantalla que la usa conserva la misma
// `Idempotency-Key`: el reintento es el mismo pedido, nunca uno nuevo.

interface Props {
  /** Qué se va a reintentar, en palabras («aprobar la orden»). */
  acto: string;
  alVerificar: () => void;
  alCancelar: () => void;
}

export function PedirCodigo({ acto, alVerificar, alCancelar }: Props) {
  const id = useId();
  const [codigo, setCodigo] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const campo = useRef<HTMLInputElement>(null);

  // Si la pantalla se desmonta (otro edificio, otra página) mientras se verifica, el pedido
  // pendiente no sigue: se descarta con ella.
  const montado = useRef(true);
  useEffect(() => {
    montado.current = true;
    campo.current?.focus();
    return () => {
      montado.current = false;
    };
  }, []);

  function verificar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    const limpio = codigoVerificacion(codigo);
    if (!limpio) {
      setError("Escribí los seis números de la aplicación o un código de recuperación.");
      return;
    }
    setEnviando(true);
    setError(null);
    void api
      .POST("/api/v1/auth/totp/verificar", { body: { codigo: limpio }, headers: encabezadoCsrf() })
      .then(({ data, error: falla, response }) => {
        if (!montado.current) return;
        if (data !== undefined) {
          setCodigo("");
          alVerificar();
        } else if (codigoDe(falla) === "segundo_factor_invalido" || codigoDe(falla) === "codigo_invalido")
          setError("El código no es válido. Probá con el que muestra ahora la aplicación.");
        else setError(mensajeDeError(response.status, codigoDe(falla)));
      })
      .catch(() => setError(SIN_CONEXION))
      .finally(() => setEnviando(false));
  }

  return (
    <form className="formulario tarjeta" onSubmit={verificar} noValidate aria-labelledby={`${id}-titulo`}>
      <h2 id={`${id}-titulo`}>Confirmá que sos vos</h2>
      <p>
        Para {acto} hace falta el código de tu aplicación de verificación, de los últimos 10 minutos.
        Después seguimos con el mismo pedido.
      </p>
      <label htmlFor={`${id}-codigo`}>Código</label>
      <input
        id={`${id}-codigo`}
        ref={campo}
        inputMode="numeric"
        autoComplete="one-time-code"
        value={codigo}
        disabled={enviando}
        aria-invalid={error !== null}
        aria-describedby={error === null ? undefined : `${id}-error`}
        onChange={(e) => setCodigo(e.target.value)}
      />
      {error !== null && (
        <p id={`${id}-error`} role="alert">
          {error}
        </p>
      )}
      <div className="acciones">
        <button type="submit" disabled={enviando}>
          {enviando ? "Verificando…" : "Verificar y seguir"}
        </button>
        <button type="button" className="secundario" disabled={enviando} onClick={alCancelar}>
          Cancelar
        </button>
      </div>
    </form>
  );
}
