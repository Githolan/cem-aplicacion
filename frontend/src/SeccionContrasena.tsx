import { useEffect, useId, useRef, useState } from "react";

import { CampoContrasena } from "./CampoContrasena";
import { api } from "./api/cliente";
import { useListoHastaOtroActo } from "./listoHastaOtroActo";
import type { components } from "./api/tipos";
import { encabezadoCsrf } from "./csrf";
import { PedirCodigo } from "./egresos/PedirCodigo";
import { ErrorDeCampo, type ErroresDeCampo, marcaDe, sinElDe, SIN_ERRORES } from "./ErrorDeCampo";
import { CODIGO_ENTRAR_DE_NUEVO, mensajeDeFalla, SIN_CONEXION } from "./fallas";
import { type FinDeEnvio, VentanaDeFormulario } from "./VentanaDeFormulario";

// Sección "Contraseña" de /cuenta: si la persona tiene una y desde cuándo, y la ventana para
// definirla o cambiarla (`PUT /api/v1/auth/contrasena`, con X-CSRF-Token). El backend decide: con
// el segundo factor activo pide haberlo verificado en esta sesión en los últimos 10 minutos (403
// `segundo_factor_requerido`), y la sección lo pide encima de la ventana y repite el pedido sin
// perder lo escrito; sin él, cambiar una existente pide la actual (403 `contrasena_actual_invalida`).
// Cambiarla cierra las sesiones de los otros dispositivos y siempre avisa por correo.
//
// Informe de fricción: los campos van en una ventana propia (CTA-01); al guardar se cierra y el
// «Listo» queda en la tarjeta, sin el botón de volver a cambiarla mientras se ve (CTA-02).

export type EstadoContrasena = components["schemas"]["EstadoContrasena"];
type Guardada = components["schemas"]["ContrasenaGuardada"];

/**
 * Largo mínimo de `NuevaContrasena.contrasena_nueva` en contratos/openapi.json
 * (`LARGO_MINIMO_CONTRASENA` del backend). Una prueba lo compara con el contrato.
 */
export const LARGO_MINIMO_CONTRASENA = 12;
/** `maxLength` de las contraseñas en el contrato. Una prueba lo compara con el contrato. */
export const LARGO_MAXIMO_CONTRASENA = 128;

const ESTADO_SIN_SESION = 401;
const ESTADO_DEMASIADOS = 429;
const CODIGO_REQUERIDO = "segundo_factor_requerido";
const CODIGO_ACTUAL_INVALIDA = "contrasena_actual_invalida";
const CODIGO_DEBIL = "contrasena_debil";
const CODIGO_CSRF = "csrf_invalido";

/**
 * Caracteres como los cuenta el backend: puntos de código, después de NFKC (que es lo que
 * compara con el mínimo) y antes (lo que valida el contrato). Vale el menor.
 */
export function largoContrasena(valor: string): number {
  return Math.min([...valor].length, [...valor.normalize("NFKC")].length);
}

/** Respuesta de error con el cuerpo uniforme (`Error`), para leer su código y su mensaje. */
function campoDe(error: unknown, campo: "codigo" | "mensaje"): string | undefined {
  return typeof error === "object" && error !== null && campo in error
    ? String((error as Record<string, unknown>)[campo])
    : undefined;
}

interface Props {
  estado: EstadoContrasena;
  correo: string;
  segundoFactor: boolean;
  /** Fecha legible, la misma que usa el resto de la página. */
  fecha: (iso: string) => string;
  alGuardar: (guardada: Guardada) => void;
  sinSesion: () => void;
}

export function SeccionContrasena({ estado, correo, segundoFactor, fecha, alGuardar, sinSesion }: Props) {
  const id = useId();
  const [actual, setActual] = useState("");
  const [nueva, setNueva] = useState("");
  const [repetida, setRepetida] = useState("");
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  const [abierta, setAbierta] = useState(false);
  const [listo, setListo] = useState<string | null>(null);
  // El «Listo» de la contraseña se va al empezar otro acto en otra tarjeta (AC-41).
  const tarjeta = useRef<HTMLElement | null>(null);
  useListoHastaOtroActo(tarjeta, () => setListo(null));
  // El «Listo» toma el foco: el botón que abrió la ventana no está mientras se ve (CTA-02).
  const textoListo = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (listo !== null) textoListo.current?.focus();
  }, [listo]);
  // Con la verificación pedida, la ventana espera la respuesta de la hoja de confirmar.
  const [confirmando, setConfirmando] = useState<((verificado: boolean) => void) | null>(null);

  // Sin segundo factor, cambiar una existente pide la actual; con él, la verificación reciente.
  const pideActual = estado.definida && !segundoFactor;

  function abrir() {
    setActual("");
    setNueva("");
    setRepetida("");
    setErrores(SIN_ERRORES);
    setListo(null);
    setAbierta(true);
  }

  function pedirVerificacion(): Promise<boolean> {
    return new Promise((resolver) => setConfirmando(() => resolver));
  }

  async function guardar(): Promise<FinDeEnvio> {
    const faltan: Record<string, string> = {};
    if (pideActual && actual === "") faltan.actual = "Falta escribir la contraseña actual.";
    if (largoContrasena(nueva) < LARGO_MINIMO_CONTRASENA)
      faltan.nueva = `Le faltan caracteres: tiene que tener al menos ${LARGO_MINIMO_CONTRASENA}.`;
    else if (nueva !== repetida) faltan.repetida = "Las dos contraseñas no coinciden.";
    if (Object.keys(faltan).length > 0) {
      setErrores(faltan);
      return "seguir";
    }
    // El pedido se repite una vez si hizo falta confirmar la identidad: lo escrito sigue en la ventana.
    for (;;) {
      let respuesta;
      try {
        respuesta = await api.PUT("/api/v1/auth/contrasena", {
          body: { contrasena_nueva: nueva, contrasena_actual: pideActual ? actual : null },
          headers: encabezadoCsrf(),
        });
      } catch {
        return { rechazo: SIN_CONEXION };
      }
      const { data, error, response } = respuesta;
      if (data !== undefined) {
        const cerradas =
          data.sesiones_cerradas === 0
            ? ""
            : data.sesiones_cerradas === 1
              ? " Se cerró 1 sesión en otro dispositivo."
              : ` Se cerraron ${data.sesiones_cerradas} sesiones en otros dispositivos.`;
        setListo(
          `${estado.definida ? "Listo: la contraseña quedó cambiada." : "Listo: la contraseña quedó definida."}${cerradas} Llega un aviso por correo.`,
        );
        alGuardar(data);
        return "cerrar";
      }
      if (response.status === ESTADO_SIN_SESION) {
        sinSesion();
        return "seguir";
      }
      const codigo = campoDe(error, "codigo");
      if (codigo === CODIGO_REQUERIDO) {
        const verificado = await pedirVerificacion();
        setConfirmando(null);
        if (verificado) continue;
        return "seguir";
      }
      if (codigo === CODIGO_ACTUAL_INVALIDA) {
        setErrores({ actual: "La contraseña actual no es correcta." });
        return "seguir";
      }
      // El backend explica la regla (largo y que no sea el correo): se muestra tal cual, junto al campo.
      if (codigo === CODIGO_DEBIL) {
        setErrores({ nueva: campoDe(error, "mensaje") ?? SIN_CONEXION });
        return "seguir";
      }
      // La primera contraseña pide una entrada reciente: lo resuelve la hoja de entrar, y lo escrito queda.
      if (codigo === CODIGO_ENTRAR_DE_NUEVO) return "seguir";
      if (codigo === CODIGO_CSRF) return { rechazo: "La página quedó abierta mucho tiempo. Hay que recargarla y repetir el paso." };
      if (response.status === ESTADO_DEMASIADOS) return { rechazo: "Hubo demasiados intentos. Reintentar en un rato." };
      return { rechazo: mensajeDeFalla(response.status, codigo) };
    }
  }

  const ayuda = `${id}-ayuda`;
  const titulo = estado.definida ? "Cambiar la contraseña" : "Definir una contraseña";

  return (
    <section ref={tarjeta} aria-labelledby={`${id}-titulo`} className="tarjeta">
      <h2 id={`${id}-titulo`}>Contraseña</h2>
      <p>
        {estado.definida
          ? estado.cambiada_en
            ? `Contraseña definida. Último cambio: ${fecha(estado.cambiada_en)}.`
            : "Contraseña definida."
          : "Sin contraseña todavía: se entra con el código o el enlace que llega por correo."}
      </p>
      {/* Mientras está el «Listo» no se ofrece cambiarla de nuevo (CTA-02). */}
      {listo === null && (
        <button type="button" className="secundario" onClick={abrir}>
          {titulo}
        </button>
      )}
      <div role="status" aria-live="polite">
        {listo !== null && (
          <p ref={textoListo} tabIndex={-1} className="aviso-ok">
            {listo}
          </p>
        )}
      </div>
      {abierta && (
        <VentanaDeFormulario
          titulo={titulo}
          textoEnviar={estado.definida ? "Guardar la contraseña nueva" : "Guardar la contraseña"}
          alEnviar={guardar}
          alCerrar={() => setAbierta(false)}
        >
          {estado.definida && <p>Al cambiarla se cierran las sesiones de los otros dispositivos.</p>}
          {/* Para que el gestor de contraseñas sepa de qué cuenta es: no se ve ni se edita. */}
          <input type="email" name="username" autoComplete="username" value={correo} readOnly hidden />
          {pideActual && (
            <>
              <label htmlFor={`${id}-actual`}>Contraseña actual</label>
              <CampoContrasena
                id={`${id}-actual`}
                data-foco
                autoComplete="current-password"
                maxLength={LARGO_MAXIMO_CONTRASENA}
                value={actual}
                {...marcaDe(`${id}-actual`, errores.actual)}
                onChange={(e) => {
                  setActual(e.target.value);
                  setErrores((anteriores) => sinElDe(anteriores, "actual"));
                }}
              />
              <ErrorDeCampo idCampo={`${id}-actual`} mensaje={errores.actual} />
            </>
          )}
          <label htmlFor={`${id}-nueva`}>{estado.definida ? "Contraseña nueva" : "Contraseña"}</label>
          <CampoContrasena
            id={`${id}-nueva`}
            data-foco={pideActual ? undefined : true}
            autoComplete="new-password"
            maxLength={LARGO_MAXIMO_CONTRASENA}
            value={nueva}
            {...marcaDe(`${id}-nueva`, errores.nueva, ayuda)}
            onChange={(e) => {
              setNueva(e.target.value);
              setErrores((anteriores) => sinElDe(anteriores, "nueva"));
            }}
          />
          <p id={ayuda}>
            Al menos {LARGO_MINIMO_CONTRASENA} caracteres, y que no sea el correo de la cuenta. Una frase de varias palabras
            sirve.
          </p>
          <ErrorDeCampo idCampo={`${id}-nueva`} mensaje={errores.nueva} />
          <label htmlFor={`${id}-repetida`}>Repetir la contraseña</label>
          <CampoContrasena
            id={`${id}-repetida`}
            autoComplete="new-password"
            maxLength={LARGO_MAXIMO_CONTRASENA}
            value={repetida}
            {...marcaDe(`${id}-repetida`, errores.repetida)}
            onChange={(e) => {
              setRepetida(e.target.value);
              setErrores((anteriores) => sinElDe(anteriores, "repetida"));
            }}
          />
          <ErrorDeCampo idCampo={`${id}-repetida`} mensaje={errores.repetida} />
        </VentanaDeFormulario>
      )}
      {confirmando !== null && (
        <PedirCodigo acto="guardar la contraseña" alVerificar={() => confirmando(true)} alCancelar={() => confirmando(false)} />
      )}
    </section>
  );
}
