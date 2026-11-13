import { type FormEvent, useId, useState } from "react";

import { api } from "./api/cliente";
import { codigoDe, mensajeDeFalla, SIN_CONEXION } from "./fallas";
import { recordarFormaDeEntrar } from "./formaDeEntrar";
import { useIrTrasEntrar } from "./sesion";

// Entrar con el código de seis dígitos del correo (documento 13, §10.3): el camino principal para
// entrar (decisión del propietario del 12-11-2026, pregunta 16). Sirve en cualquier dispositivo; el
// enlace del mismo correo, solo en el navegador que lo pidió. Va en el cuerpo de un POST, nunca en
// la URL. Después de pedirlo, el aviso de qué hacer si no llega es el mismo con cuenta o sin ella:
// no dice si el correo está registrado (AC-28). Diseño provisorio: el definitivo llega con G3.

export const DIGITOS_CODIGO = 6;
/** Cuánto esperar el correo antes de buscar otra causa. */
export const MINUTOS_ESPERA_CORREO = 5;
const ESTADO_RECHAZADO = 401;
const ESTADO_DEMASIADOS = 429;

type Estado = "listo" | "enviando" | "rechazado" | "demasiados" | "sin_servicio";

/** El código como lo escribió la persona («042 917», «042-917»), sin separadores. */
export function normalizarCodigo(valor: string): string {
  return valor.replace(/[\s-]/g, "");
}

export function codigoValido(valor: string): boolean {
  return new RegExp(`^[0-9]{${DIGITOS_CODIGO}}$`).test(valor);
}

interface Props {
  /** El correo es de /entrar: sigue escrito al cambiar de forma. */
  correo: string;
  alCambiarCorreo: (correo: string) => void;
  /** Lo que respondió el pedido del enlace. */
  aviso: string | null;
  /** Sin esto, terminar sigue al inicio. */
  alEntrar?: (aviso: string) => void;
}

export function EntrarConCodigo({ correo, alCambiarCorreo, aviso, alEntrar }: Props) {
  const id = useId();
  const irTrasEntrar = useIrTrasEntrar();
  const terminarEntrada = alEntrar ?? irTrasEntrar;
  const [falla, setFalla] = useState(SIN_CONEXION);
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
        if (error === undefined) {
          recordarFormaDeEntrar("correo");
          terminarEntrada("Listo: sesión abierta.");
        }
        else if (response.status === ESTADO_RECHAZADO) setEstado("rechazado");
        else if (response.status === ESTADO_DEMASIADOS) setEstado("demasiados");
        else {
          setFalla(mensajeDeFalla(response.status, codigoDe(error)));
          setEstado("sin_servicio");
        }
      })
      .catch(() => {
        setFalla(SIN_CONEXION);
        setEstado("sin_servicio");
      });
  }

  return (
    <section aria-labelledby={`${id}-titulo`} className="tarjeta">
      <h2 id={`${id}-titulo`}>Escribir el código del correo</h2>
      {aviso !== null && (
        <>
          <p className="aviso-ok">{aviso}</p>
          <p>
            Si en {MINUTOS_ESPERA_CORREO} minutos no llegó, revisar la carpeta de correo no deseado.
            Si tampoco está ahí, puede que ese correo no esté registrado: la cuenta se crea con la
            invitación que envía la administración del edificio.
          </p>
        </>
      )}
      <p>Escribir el correo y el código de {DIGITOS_CODIGO} números que llegó.</p>
      <form className="formulario" onSubmit={enviar}>
        <label htmlFor={`${id}-correo`}>Correo</label>
        <input
          id={`${id}-correo`}
          type="email"
          autoComplete="email"
          required
          value={correo}
          onChange={(e) => alCambiarCorreo(e.target.value)}
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
            Ese código no sirve. Revisar el correo y el código; si venció, ya se usó o se escribió
            mal varias veces, pedir uno nuevo.
          </p>
        )}
        {estado === "demasiados" && (
          <p className="aviso-error">
            Hubo demasiados intentos con este correo. Esperar un rato y probar de nuevo.
          </p>
        )}
        {estado === "sin_servicio" && <p className="aviso-error">{falla}</p>}
      </div>
    </section>
  );
}
