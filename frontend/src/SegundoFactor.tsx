import { type FormEvent, useCallback, useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { CodigoQr } from "./CodigoQr";
import { encabezadoCsrf } from "./csrf";
import { RUTA_ENTRAR } from "./enlace";

// Página /cuenta/segundo-factor (TB-CEM-3): la persona da de alta una aplicación de
// autenticación (QR o secreto a mano), la confirma con el primer código, guarda los diez
// códigos de recuperación, que se muestran una sola vez, y puede verificar en esta sesión y
// desactivarla. Desactivar pide haber verificado en los últimos 10 minutos; el backend lo exige
// y esta página solo lo explica. Diseño provisorio: el definitivo llega con G3.

type EstadoFactor = components["schemas"]["EstadoSegundoFactor"];
type Alta = components["schemas"]["AltaTotp"];

export const RUTA_SEGUNDO_FACTOR = "/cuenta/segundo-factor";

const ESTADO_SIN_SESION = 401;
const ESTADO_DEMASIADOS = 429;
const CODIGO_INVALIDO = "segundo_factor_invalido";
const CODIGO_SIN_ALTA = "segundo_factor_sin_alta";
const CODIGO_YA_ACTIVO = "segundo_factor_ya_activo";
const CODIGO_REQUERIDO = "segundo_factor_requerido";
const SIN_CONEXION = "No pudimos completar el pedido. Revisá la conexión y probá de nuevo.";

/** Agrupa el secreto en bloques de cuatro, para copiarlo a mano sin perderse. */
export function secretoEnBloques(secreto: string): string {
  return secreto.match(/.{1,4}/g)?.join(" ") ?? "";
}

/** El código de la aplicación (seis números) como lo escribió la persona, o null. */
export function codigoAplicacion(valor: string): string | null {
  const limpio = valor.replace(/[\s-]/g, "");
  return /^[0-9]{6}$/.test(limpio) ? limpio : null;
}

/**
 * Lo que acepta la verificación: el código de la aplicación o uno de recuperación
 * (`k7m2q-x9p4t`, con o sin guion, en mayúsculas o minúsculas), en la forma del contrato.
 */
export function codigoVerificacion(valor: string): string | null {
  const aplicacion = codigoAplicacion(valor);
  if (aplicacion) return aplicacion;
  const limpio = valor.replace(/[\s-]/g, "").toLowerCase();
  return /^[a-z2-9]{10}$/.test(limpio) ? `${limpio.slice(0, 5)}-${limpio.slice(5)}` : null;
}

const FORMATO_FECHA = new Intl.DateTimeFormat("es-AR", {
  dateStyle: "long",
  timeStyle: "short",
  hourCycle: "h23",
  timeZone: "America/Argentina/Buenos_Aires",
});
const FORMATO_HORA = new Intl.DateTimeFormat("es-AR", {
  timeStyle: "short",
  hourCycle: "h23",
  timeZone: "America/Argentina/Buenos_Aires",
});

type Pantalla =
  | { tipo: "cargando" }
  | { tipo: "sin_sesion" }
  | { tipo: "sin_servicio" }
  | { tipo: "inactivo" }
  | { tipo: "alta"; alta: Alta }
  | { tipo: "codigos"; codigos: string[] }
  | { tipo: "activo"; estado: EstadoFactor }
  | { tipo: "desactivado" };

type Aviso = { texto: string; ok: boolean };

const fallo = (texto: string): Aviso => ({ texto, ok: false });

/** Respuesta de error con el cuerpo uniforme (`Error`), para leer su código. */
function codigoDe(error: unknown): string | undefined {
  return typeof error === "object" && error !== null && "codigo" in error
    ? String(error.codigo)
    : undefined;
}

export function SegundoFactor() {
  const id = useId();
  const [pantalla, setPantalla] = useState<Pantalla>({ tipo: "cargando" });
  const [codigo, setCodigo] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const consultado = useRef(false);

  const consultar = useCallback(() => {
    setAviso(null);
    void api
      .GET("/api/v1/auth/totp")
      .then(({ data, response }) => {
        if (data !== undefined)
          setPantalla(data.activo ? { tipo: "activo", estado: data } : { tipo: "inactivo" });
        else
          setPantalla({ tipo: response.status === ESTADO_SIN_SESION ? "sin_sesion" : "sin_servicio" });
      })
      .catch(() => setPantalla({ tipo: "sin_servicio" }));
  }, []);

  useEffect(() => {
    // StrictMode monta dos veces: la consulta sale una sola.
    if (consultado.current) return;
    consultado.current = true;
    consultar();
  }, [consultar]);

  /** El aviso para un código rechazado al confirmar o verificar. */
  function avisoDeRechazo(status: number, cuerpo: unknown): Aviso {
    if (status === ESTADO_DEMASIADOS)
      return fallo("Hubo demasiados intentos. Esperá un rato y probá de nuevo.");
    if (codigoDe(cuerpo) === CODIGO_INVALIDO)
      return fallo(
        "El código no sirve. Revisá que la hora del teléfono esté bien y usá el código nuevo.",
      );
    return fallo(SIN_CONEXION);
  }

  function empezarAlta() {
    setEnviando(true);
    setAviso(null);
    void api
      .POST("/api/v1/auth/totp/alta", { headers: encabezadoCsrf() })
      .then(({ data, error, response }) => {
        if (data !== undefined) {
          setCodigo("");
          setPantalla({ tipo: "alta", alta: data });
        } else if (codigoDe(error) === CODIGO_YA_ACTIVO) consultar();
        else if (response.status === ESTADO_SIN_SESION) setPantalla({ tipo: "sin_sesion" });
        else setAviso(fallo(SIN_CONEXION));
      })
      .catch(() => setAviso(fallo(SIN_CONEXION)))
      .finally(() => setEnviando(false));
  }

  function confirmar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    const limpio = codigoAplicacion(codigo);
    if (!limpio) {
      setAviso(fallo("Escribí los seis números que muestra la aplicación."));
      return;
    }
    setEnviando(true);
    setAviso(null);
    void api
      .POST("/api/v1/auth/totp/confirmar", { body: { codigo: limpio }, headers: encabezadoCsrf() })
      .then(({ data, error, response }) => {
        if (data !== undefined) {
          setCodigo("");
          setPantalla({ tipo: "codigos", codigos: data.codigos });
        } else if (codigoDe(error) === CODIGO_SIN_ALTA) {
          setPantalla({ tipo: "inactivo" });
          setAviso(fallo("El alta venció o se reemplazó desde otro dispositivo. Empezá de nuevo."));
        } else setAviso(avisoDeRechazo(response.status, error));
      })
      .catch(() => setAviso(fallo(SIN_CONEXION)))
      .finally(() => setEnviando(false));
  }

  function verificar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    const limpio = codigoVerificacion(codigo);
    if (!limpio) {
      setAviso(fallo("Escribí los seis números de la aplicación o un código de recuperación."));
      return;
    }
    setEnviando(true);
    setAviso(null);
    void api
      .POST("/api/v1/auth/totp/verificar", { body: { codigo: limpio }, headers: encabezadoCsrf() })
      .then(({ data, error, response }) => {
        if (data !== undefined) {
          setCodigo("");
          setPantalla((actual) =>
            actual.tipo === "activo"
              ? {
                  tipo: "activo",
                  estado: {
                    ...actual.estado,
                    verificado_en: data.verificado_en,
                    codigos_recuperacion_restantes: data.codigos_recuperacion_restantes,
                  },
                }
              : actual,
          );
          setAviso({
            texto: `Listo: verificado hasta las ${FORMATO_HORA.format(new Date(data.vale_hasta))}.`,
            ok: true,
          });
        } else if (codigoDe(error) === CODIGO_SIN_ALTA) consultar();
        else setAviso(avisoDeRechazo(response.status, error));
      })
      .catch(() => setAviso(fallo(SIN_CONEXION)))
      .finally(() => setEnviando(false));
  }

  function desactivar() {
    setEnviando(true);
    setAviso(null);
    void api
      .POST("/api/v1/auth/totp/baja", { headers: encabezadoCsrf() })
      .then(({ error, response }) => {
        if (error === undefined && response.ok) setPantalla({ tipo: "desactivado" });
        else if (codigoDe(error) === CODIGO_REQUERIDO)
          setAviso(
            fallo(
              "Para desactivarla, primero escribí arriba un código de la aplicación (vale 10 minutos).",
            ),
          );
        else setAviso(fallo(SIN_CONEXION));
      })
      .catch(() => setAviso(fallo(SIN_CONEXION)))
      .finally(() => setEnviando(false));
  }

  const conMensaje = aviso !== null || !["inactivo", "alta", "codigos", "activo"].includes(pantalla.tipo);

  return (
    <>
      <h1>Segundo factor</h1>
      <p>
        Con una aplicación de autenticación en el teléfono (Google Authenticator, Microsoft
        Authenticator, Aegis u otra), las acciones delicadas piden además un código de seis
        números que cambia cada 30 segundos.
      </p>

      {pantalla.tipo === "inactivo" && (
        <section aria-labelledby={`${id}-titulo`} className="tarjeta">
          <h2 id={`${id}-titulo`}>Todavía no lo activaste</h2>
          <button type="button" onClick={empezarAlta} disabled={enviando}>
            Activar el segundo factor
          </button>
        </section>
      )}

      {pantalla.tipo === "alta" && (
        <section aria-labelledby={`${id}-titulo`} className="tarjeta">
          <h2 id={`${id}-titulo`}>Cargá la cuenta en la aplicación</h2>
          <ol className="pasos">
            <li>
              Abrí la aplicación de autenticación y elegí agregar una cuenta escaneando un código
              QR.
              <CodigoQr
                texto={pantalla.alta.uri}
                descripcion="Código QR para cargar Consorcio en Mano en la aplicación de autenticación"
              />
            </li>
            <li>
              Si no podés escanearlo, <a href={pantalla.alta.uri}>abrí la aplicación desde acá</a>{" "}
              o escribí esta clave a mano:{" "}
              <code className="secreto" translate="no">
                {secretoEnBloques(pantalla.alta.secreto)}
              </code>
            </li>
            <li>Escribí el código de seis números que muestra la aplicación.</li>
          </ol>
          <form className="formulario" onSubmit={confirmar} noValidate>
            <label htmlFor={`${id}-codigo`}>Código de la aplicación</label>
            <input
              id={`${id}-codigo`}
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={9}
              value={codigo}
              onChange={(e) => setCodigo(e.target.value)}
            />
            <button type="submit" disabled={enviando}>
              Confirmar
            </button>
          </form>
        </section>
      )}

      {pantalla.tipo === "codigos" && (
        <section aria-labelledby={`${id}-titulo`} className="tarjeta">
          <h2 id={`${id}-titulo`}>Guardá tus códigos de recuperación</h2>
          <p className="aviso-ok">Listo: el segundo factor quedó activado.</p>
          <p>
            Si perdés el teléfono, cada uno de estos códigos sirve <strong>una sola vez</strong> en
            lugar del de la aplicación. Guardalos en un lugar seguro: no los vamos a volver a
            mostrar.
          </p>
          <ul className="codigos-recuperacion" aria-label="Códigos de recuperación" translate="no">
            {pantalla.codigos.map((c) => (
              <li key={c}>
                <code>{c}</code>
              </li>
            ))}
          </ul>
          <button type="button" onClick={consultar}>
            Ya los guardé
          </button>
        </section>
      )}

      {pantalla.tipo === "activo" && (
        <section aria-labelledby={`${id}-titulo`} className="tarjeta">
          <h2 id={`${id}-titulo`}>Está activado</h2>
          <dl className="detalles">
            {pantalla.estado.activado_en && (
              <>
                <dt>Desde</dt>
                <dd>{FORMATO_FECHA.format(new Date(pantalla.estado.activado_en))}</dd>
              </>
            )}
            <dt>Códigos de recuperación sin usar</dt>
            <dd>{pantalla.estado.codigos_recuperacion_restantes}</dd>
            <dt>Verificado en esta sesión</dt>
            <dd>
              {pantalla.estado.verificado_en
                ? FORMATO_FECHA.format(new Date(pantalla.estado.verificado_en))
                : "Todavía no"}
            </dd>
          </dl>
          <form className="formulario" onSubmit={verificar} noValidate>
            <label htmlFor={`${id}-codigo`}>Código de la aplicación o de recuperación</label>
            <input
              id={`${id}-codigo`}
              type="text"
              autoComplete="one-time-code"
              autoCapitalize="none"
              spellCheck={false}
              maxLength={13}
              value={codigo}
              onChange={(e) => setCodigo(e.target.value)}
            />
            <button type="submit" disabled={enviando}>
              Verificar
            </button>
          </form>
          <h3>Desactivar</h3>
          <p>
            Borra la aplicación cargada y los códigos de recuperación. Te avisamos por correo.
          </p>
          <button type="button" onClick={desactivar} disabled={enviando}>
            Desactivar el segundo factor
          </button>
        </section>
      )}

      <div role="status" aria-live="polite" className={conMensaje ? "tarjeta" : undefined}>
        {pantalla.tipo === "cargando" && <p>Consultando…</p>}
        {pantalla.tipo === "sin_sesion" && (
          <p>
            Para ver el segundo factor, primero <Link to={RUTA_ENTRAR}>entrá a tu cuenta</Link>.
          </p>
        )}
        {pantalla.tipo === "sin_servicio" && (
          <p className="aviso-error">
            No pudimos consultar el segundo factor. Revisá la conexión y volvé en un rato.
          </p>
        )}
        {pantalla.tipo === "desactivado" && (
          <>
            <p className="aviso-ok">Listo: el segundo factor quedó desactivado.</p>
            <button type="button" onClick={consultar}>
              Volver
            </button>
          </>
        )}
        {aviso && <p className={aviso.ok ? "aviso-ok" : "aviso-error"}>{aviso.texto}</p>}
      </div>
    </>
  );
}
