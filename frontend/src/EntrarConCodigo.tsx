import { type FormEvent, useId, useState } from "react";

import { api } from "./api/cliente";
import { codigoDe, mensajeDeFalla, SIN_CONEXION } from "./fallas";
import { recordarFormaDeEntrar } from "./formaDeEntrar";
import { pedirCodigoPorCorreo } from "./PedirEnlace";
import { useIrTrasEntrar } from "./sesion";

// Entrar con el código de seis dígitos del correo (documento 13, §10.3): el camino principal para
// entrar (decisión del propietario del 12-11-2026, pregunta 16). Sirve en cualquier dispositivo; el
// enlace del mismo correo, solo en el navegador que lo pidió. Va en el cuerpo de un POST, nunca en
// la URL. Después de pedirlo, el aviso de qué hacer si no llega es el mismo con cuenta o sin ella:
// no dice si el correo está registrado (AC-28).
//
// Informe de fricción: «Enviar otro código» aparece junto al rechazo y detrás de «No llegó el correo», y deja la pantalla en el mismo
// paso (ACC-02); con el correo que se acaba de pedir, va como texto con «Cambiar», sin el campo
// (ACC-03); sin párrafos que repitan la etiqueta (ACC-04). Al completar los seis números (con el
// correo escrito) se envía solo, como en egresos/PedirCodigo.tsx (ACC-01): decisión del
// propietario, 18-11-2026, deja de lado WCAG 3.2.2. El botón queda por si el envío falla.
//
// Sin un pedido hecho en esta pantalla (el enlace del correo no sirvió acá, o «Ya tengo un código»),
// pedir uno nuevo también está en este paso: «Pedir un código nuevo» (FL-ACC-04).

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
  /** Lo que respondió el pedido del código. */
  aviso: string | null;
  /** Sin esto, terminar sigue al inicio. */
  alEntrar?: () => void;
  /** Volver a pedir el código con otro correo («Cambiar»). */
  alCambiar?: () => void;
  /** El correo de la sesión vencida: va como texto, sin campo y sin «Cambiar». */
  correoFijo?: boolean;
  /** Dentro de una hoja: sin tarjeta. */
  enHoja?: boolean;
}

export function EntrarConCodigo({ correo, alCambiarCorreo, aviso, alEntrar, alCambiar, correoFijo = false, enHoja = false }: Props) {
  const id = useId();
  const irTrasEntrar = useIrTrasEntrar();
  const terminarEntrada = alEntrar ?? irTrasEntrar;
  const [falla, setFalla] = useState(SIN_CONEXION);
  const [codigo, setCodigo] = useState("");
  const [estado, setEstado] = useState<Estado>("listo");
  const [formatoMal, setFormatoMal] = useState(false);
  const [avisoActual, setAvisoActual] = useState(aviso);
  const [sinCorreo, setSinCorreo] = useState(false);
  const [reenvio, setReenvio] = useState<{ enviando: boolean; falla: string | null }>({ enviando: false, falla: null });
  // El correo va como texto si se acaba de pedir el código o si es el de la sesión (ACC-03).
  const correoComoTexto = correoFijo || aviso !== null;

  function entrarCon(limpio: string) {
    setFormatoMal(false);
    setEstado("enviando");
    void api
      .POST("/api/v1/auth/codigo", { body: { correo: correo.trim(), codigo: limpio } })
      .then(({ error, response }) => {
        if (error === undefined) {
          recordarFormaDeEntrar("correo");
          terminarEntrada();
        } else if (response.status === ESTADO_RECHAZADO) {
          setCodigo("");
          setEstado("rechazado");
        } else if (response.status === ESTADO_DEMASIADOS) setEstado("demasiados");
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

  function enviar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    // El sexto número ya lo mandó (ACC-01): Enter o el botón no repiten el pedido en viaje.
    if (estado === "enviando") return;
    const limpio = normalizarCodigo(codigo);
    if (!codigoValido(limpio)) {
      setFormatoMal(true);
      return;
    }
    entrarCon(limpio);
  }

  /** Al sexto número, con el correo escrito, entra sin esperar el botón (ACC-01). */
  function escribirCodigo(valor: string) {
    setCodigo(valor);
    const limpio = normalizarCodigo(valor);
    if (estado !== "enviando" && correo.trim() !== "" && codigoValido(limpio)) entrarCon(limpio);
  }

  /** Repite el pedido con el mismo correo y deja la pantalla en este paso (ACC-02). */
  function enviarOtro() {
    setReenvio({ enviando: true, falla: null });
    void pedirCodigoPorCorreo(correo.trim()).then((pedido) => {
      if (pedido.ok) {
        setAvisoActual(pedido.mensaje);
        setEstado("listo");
        setSinCorreo(false);
        setReenvio({ enviando: false, falla: null });
      } else setReenvio({ enviando: false, falla: pedido.mensaje });
    });
  }

  const botonOtro = (
    <button type="button" className="sutil" disabled={reenvio.enviando} onClick={enviarOtro}>
      Enviar otro código
    </button>
  );

  return (
    <section aria-labelledby={`${id}-titulo`} className={enHoja ? undefined : "tarjeta"}>
      <h2 id={`${id}-titulo`}>Escribir el código del correo</h2>
      {avisoActual !== null && <p className="aviso-ok">{avisoActual}</p>}
      <form className="formulario" onSubmit={enviar}>
        {correoComoTexto ? (
          <p>
            Correo: <strong>{correo}</strong>
            {!correoFijo && alCambiar !== undefined && (
              <>
                {" "}
                <button type="button" className="sutil" onClick={alCambiar}>
                  Cambiar
                </button>
              </>
            )}
          </p>
        ) : (
          <>
            <label htmlFor={`${id}-correo`}>Correo</label>
            <input
              id={`${id}-correo`}
              type="email"
              autoComplete="email"
              required
              value={correo}
              onChange={(e) => alCambiarCorreo(e.target.value)}
            />
          </>
        )}
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
          onChange={(e) => escribirCodigo(e.target.value)}
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
            Ese código no sirve. Revisar el correo y el código; si venció, ya se usó o se escribió mal varias
            veces, pedir uno nuevo. {botonOtro}
          </p>
        )}
        {estado === "demasiados" && (
          <p className="aviso-error">
            Hubo demasiados intentos con este correo. Esperar un rato y probar de nuevo.
          </p>
        )}
        {estado === "sin_servicio" && <p className="aviso-error">{falla}</p>}
        {reenvio.enviando && <p>Enviando otro código…</p>}
        {reenvio.falla !== null && <p className="aviso-error">{reenvio.falla}</p>}
      </div>
      {avisoActual === null && estado !== "rechazado" && alCambiar !== undefined && (
        <p>
          <button type="button" className="sutil" onClick={alCambiar}>
            Pedir un código nuevo
          </button>
        </p>
      )}
      {avisoActual !== null && estado !== "rechazado" && (
        <>
          <button type="button" className="sutil" aria-expanded={sinCorreo} onClick={() => setSinCorreo((v) => !v)}>
            No llegó el correo
          </button>
          {sinCorreo && (
            <p>
              Puede tardar hasta {MINUTOS_ESPERA_CORREO} minutos; revisar también la carpeta de correo no deseado. Si
              tampoco está ahí, puede que ese correo no esté registrado: la cuenta se crea con la invitación que envía la
              administración del edificio. {botonOtro}
            </p>
          )}
        </>
      )}
    </section>
  );
}
