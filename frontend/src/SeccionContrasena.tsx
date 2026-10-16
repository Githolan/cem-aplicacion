import { type FormEvent, useId, useState } from "react";
import { Link } from "react-router";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { encabezadoCsrf } from "./csrf";
import { RUTA_SEGUNDO_FACTOR } from "./SegundoFactor";

// Sección "Contraseña" de /cuenta: si la persona tiene una y desde cuándo, y el formulario para
// definirla o cambiarla (`PUT /api/v1/auth/contrasena`, con X-CSRF-Token). El backend decide y
// esta sección solo lo explica: con el segundo factor activo pide haberlo verificado en esta
// sesión en los últimos 10 minutos (403 `segundo_factor_requerido`); sin él, cambiar una
// existente pide la actual (403 `contrasena_actual_invalida`). Cambiarla cierra las sesiones de
// los otros dispositivos y siempre avisa por correo.

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
const SIN_CONEXION = "No pudimos completar el pedido. Revisá la conexión y probá de nuevo.";

/**
 * Caracteres como los cuenta el backend: puntos de código, después de NFKC (que es lo que
 * compara con el mínimo) y antes (lo que valida el contrato). Vale el menor.
 */
export function largoContrasena(valor: string): number {
  return Math.min([...valor].length, [...valor.normalize("NFKC")].length);
}

type Aviso =
  | { tipo: "ok"; texto: string }
  | { tipo: "error"; texto: string }
  | { tipo: "falta_verificacion" };

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
  const [corta, setCorta] = useState(false);
  const [distintas, setDistintas] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);

  // Sin segundo factor, cambiar una existente pide la actual; con él, la verificación reciente.
  const pideActual = estado.definida && !segundoFactor;

  function guardar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    const esCorta = largoContrasena(nueva) < LARGO_MINIMO_CONTRASENA;
    const sonDistintas = nueva !== repetida;
    setCorta(esCorta);
    setDistintas(!esCorta && sonDistintas);
    if (esCorta || sonDistintas) return;
    setEnviando(true);
    setAviso(null);
    void api
      .PUT("/api/v1/auth/contrasena", {
        body: { contrasena_nueva: nueva, contrasena_actual: pideActual ? actual : null },
        headers: encabezadoCsrf(),
      })
      .then(({ data, error, response }) => {
        if (data !== undefined) {
          setActual("");
          setNueva("");
          setRepetida("");
          const cerradas =
            data.sesiones_cerradas === 0
              ? ""
              : data.sesiones_cerradas === 1
                ? " Cerramos 1 sesión en otro dispositivo."
                : ` Cerramos ${data.sesiones_cerradas} sesiones en otros dispositivos.`;
          setAviso({
            tipo: "ok",
            texto: `${estado.definida ? "Listo: cambiaste tu contraseña." : "Listo: definiste tu contraseña."}${cerradas} Te avisamos por correo.`,
          });
          alGuardar(data);
          return;
        }
        if (response.status === ESTADO_SIN_SESION) {
          sinSesion();
          return;
        }
        const codigo = campoDe(error, "codigo");
        if (codigo === CODIGO_REQUERIDO) setAviso({ tipo: "falta_verificacion" });
        else if (codigo === CODIGO_ACTUAL_INVALIDA)
          setAviso({ tipo: "error", texto: "La contraseña actual no es correcta." });
        else if (codigo === CODIGO_DEBIL)
          // El backend explica la regla (largo y que no sea el correo): se muestra tal cual.
          setAviso({ tipo: "error", texto: campoDe(error, "mensaje") ?? SIN_CONEXION });
        else if (codigo === CODIGO_CSRF)
          setAviso({ tipo: "error", texto: "No pudimos verificar el pedido. Recargá la página y probá de nuevo." });
        else if (response.status === ESTADO_DEMASIADOS)
          setAviso({ tipo: "error", texto: "Hubo demasiados intentos. Esperá un rato y probá de nuevo." });
        else setAviso({ tipo: "error", texto: SIN_CONEXION });
      })
      .catch(() => setAviso({ tipo: "error", texto: SIN_CONEXION }))
      .finally(() => setEnviando(false));
  }

  const ayuda = `${id}-ayuda`;
  const describeNueva = [ayuda, corta ? `${id}-corta` : null].filter(Boolean).join(" ");

  return (
    <section aria-labelledby={`${id}-titulo`} className="tarjeta">
      <h2 id={`${id}-titulo`}>Contraseña</h2>
      <p>
        {estado.definida
          ? estado.cambiada_en
            ? `Tenés una contraseña. La cambiaste por última vez el ${fecha(estado.cambiada_en)}.`
            : "Tenés una contraseña."
          : "Todavía no tenés contraseña: entrás con el enlace o el código que te mandamos por correo."}
      </p>
      {segundoFactor && (
        <p>
          Como tenés el segundo factor activado, antes de {estado.definida ? "cambiarla" : "definirla"}{" "}
          <Link to={RUTA_SEGUNDO_FACTOR}>verificá un código en la página del segundo factor</Link> (vale
          10 minutos).
        </p>
      )}
      {estado.definida && (
        <p>Al cambiarla cerramos las sesiones de tus otros dispositivos.</p>
      )}
      <form className="formulario" onSubmit={guardar} noValidate>
        {/* Para que el gestor de contraseñas sepa de qué cuenta es: no se ve ni se edita. */}
        <input type="email" name="username" autoComplete="username" value={correo} readOnly hidden />
        {pideActual && (
          <>
            <label htmlFor={`${id}-actual`}>Contraseña actual</label>
            <input
              id={`${id}-actual`}
              type="password"
              autoComplete="current-password"
              required
              maxLength={LARGO_MAXIMO_CONTRASENA}
              value={actual}
              onChange={(e) => setActual(e.target.value)}
            />
          </>
        )}
        <label htmlFor={`${id}-nueva`}>{estado.definida ? "Contraseña nueva" : "Contraseña"}</label>
        <input
          id={`${id}-nueva`}
          type="password"
          autoComplete="new-password"
          required
          minLength={LARGO_MINIMO_CONTRASENA}
          maxLength={LARGO_MAXIMO_CONTRASENA}
          aria-invalid={corta}
          aria-describedby={describeNueva}
          value={nueva}
          onChange={(e) => setNueva(e.target.value)}
        />
        <p id={ayuda}>
          Al menos {LARGO_MINIMO_CONTRASENA} caracteres, y que no sea tu correo. Una frase de
          varias palabras sirve.
        </p>
        {corta && (
          <p id={`${id}-corta`} className="aviso-error">
            Le faltan caracteres: tiene que tener al menos {LARGO_MINIMO_CONTRASENA}.
          </p>
        )}
        <label htmlFor={`${id}-repetida`}>Repetí la contraseña</label>
        <input
          id={`${id}-repetida`}
          type="password"
          autoComplete="new-password"
          required
          maxLength={LARGO_MAXIMO_CONTRASENA}
          aria-invalid={distintas}
          aria-describedby={distintas ? `${id}-distintas` : undefined}
          value={repetida}
          onChange={(e) => setRepetida(e.target.value)}
        />
        {distintas && (
          <p id={`${id}-distintas`} className="aviso-error">
            Las dos contraseñas no coinciden.
          </p>
        )}
        <button type="submit" disabled={enviando}>
          {estado.definida ? "Cambiar la contraseña" : "Definir la contraseña"}
        </button>
      </form>
      <div role="status" aria-live="polite">
        {enviando && <p>Guardando…</p>}
        {aviso?.tipo === "ok" && <p className="aviso-ok">{aviso.texto}</p>}
        {aviso?.tipo === "error" && <p className="aviso-error">{aviso.texto}</p>}
        {aviso?.tipo === "falta_verificacion" && (
          <p className="aviso-error">
            Para esto tenés que confirmar con el código de tu aplicación de autenticación.{" "}
            <Link to={RUTA_SEGUNDO_FACTOR}>Verificá un código en la página del segundo factor</Link> y
            volvé antes de 10 minutos.
          </p>
        )}
      </div>
    </section>
  );
}
