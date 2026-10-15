import { type FormEvent, useEffect, useId, useState } from "react";
import { Link } from "react-router";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { encabezadoCsrf } from "./csrf";
import { RUTA_ENTRAR } from "./enlace";

// Página /seguridad: la verificación en dos pasos (TOTP, documento 13, §10.3 y TB-CEM-3). La
// persona la activa con una aplicación de autenticación, guarda diez códigos de recuperación y
// puede desactivarla confirmando con un código. El secreto se muestra para escribirlo a mano y
// como enlace otpauth://, que en el teléfono abre la aplicación de autenticación. Diseño
// provisorio: el definitivo llega con G3.

export const RUTA_SEGURIDAD = "/seguridad";

type EstadoFactor = components["schemas"]["EstadoSegundoFactor"];
type Alta = components["schemas"]["AltaTotp"];

type Paso =
  | { tipo: "cargando" }
  | { tipo: "sin_sesion" }
  | { tipo: "sin_servicio" }
  | { tipo: "inactivo" }
  | { tipo: "alta"; alta: Alta }
  | { tipo: "codigos"; codigos: string[] }
  | { tipo: "activo"; estado: EstadoFactor };

const SIN_SESION = 401;

/** El secreto en grupos de cuatro, para copiarlo a mano sin perderse. */
export function agruparSecreto(secreto: string): string {
  return secreto.match(/.{1,4}/g)?.join(" ") ?? secreto;
}

function normalizar(codigo: string): string {
  return codigo.replace(/\s/g, "").toLowerCase();
}

function FormularioCodigo({
  etiqueta,
  boton,
  enviar,
  ocupado,
  aceptaRecuperacion = false,
}: {
  etiqueta: string;
  boton: string;
  enviar: (codigo: string) => void;
  ocupado: boolean;
  aceptaRecuperacion?: boolean;
}) {
  const id = useId();
  const [codigo, setCodigo] = useState("");
  function alEnviar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    enviar(normalizar(codigo));
  }
  return (
    <form className="formulario" onSubmit={alEnviar}>
      <label htmlFor={`${id}-codigo`}>{etiqueta}</label>
      <input
        id={`${id}-codigo`}
        type="text"
        inputMode={aceptaRecuperacion ? "text" : "numeric"}
        autoComplete="one-time-code"
        required
        maxLength={aceptaRecuperacion ? 13 : 7}
        value={codigo}
        onChange={(e) => setCodigo(e.target.value)}
      />
      <button type="submit" disabled={ocupado}>
        {boton}
      </button>
    </form>
  );
}

export function Seguridad() {
  const [paso, setPaso] = useState<Paso>({ tipo: "cargando" });
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function cargar() {
    void api
      .GET("/api/v1/auth/totp")
      .then(({ data, response }) => {
        if (data !== undefined) setPaso(data.activo ? { tipo: "activo", estado: data } : { tipo: "inactivo" });
        else setPaso({ tipo: response.status === SIN_SESION ? "sin_sesion" : "sin_servicio" });
      })
      .catch(() => setPaso({ tipo: "sin_servicio" }));
  }

  useEffect(cargar, []);

  function mensajeDe(status: number, codigo?: string): string {
    if (codigo === "segundo_factor_invalido")
      return "El código no sirve. Revisá que la hora del teléfono esté bien y usá el código nuevo.";
    if (status === 429) return "Hubo demasiados intentos. Esperá un rato y probá de nuevo.";
    return "No pudimos completar el pedido. Probá de nuevo en un rato.";
  }

  function empezarAlta() {
    setOcupado(true);
    setError(null);
    void api
      .POST("/api/v1/auth/totp/alta", { headers: encabezadoCsrf() })
      .then(({ data, response }) => {
        if (data !== undefined) setPaso({ tipo: "alta", alta: data });
        else setError(mensajeDe(response.status));
      })
      .catch(() => setError(mensajeDe(0)))
      .finally(() => setOcupado(false));
  }

  function confirmar(codigo: string) {
    setOcupado(true);
    setError(null);
    void api
      .POST("/api/v1/auth/totp/confirmar", { body: { codigo }, headers: encabezadoCsrf() })
      .then(({ data, error: e, response }) => {
        if (data !== undefined) setPaso({ tipo: "codigos", codigos: data.codigos });
        else setError(mensajeDe(response.status, e && "codigo" in e ? e.codigo : undefined));
      })
      .catch(() => setError(mensajeDe(0)))
      .finally(() => setOcupado(false));
  }

  async function desactivar(codigo: string) {
    setOcupado(true);
    setError(null);
    try {
      const v = await api.POST("/api/v1/auth/totp/verificar", {
        body: { codigo },
        headers: encabezadoCsrf(),
      });
      if (v.error !== undefined) {
        setError(mensajeDe(v.response.status, "codigo" in v.error ? v.error.codigo : undefined));
        return;
      }
      const b = await api.POST("/api/v1/auth/totp/baja", { headers: encabezadoCsrf() });
      if (b.error !== undefined) setError(mensajeDe(b.response.status));
      else setPaso({ tipo: "inactivo" });
    } catch {
      setError(mensajeDe(0));
    } finally {
      setOcupado(false);
    }
  }

  return (
    <>
      <h1>Verificación en dos pasos</h1>
      <p>
        Además del correo, pide un código de una aplicación de autenticación de tu teléfono (por
        ejemplo Google Authenticator, Microsoft Authenticator o Authy). Se va a pedir a quien maneje
        dinero del consorcio.
      </p>
      <section className="tarjeta" aria-live="polite">
        {paso.tipo === "cargando" && <p role="status">Consultando…</p>}
        {paso.tipo === "sin_sesion" && (
          <p>
            <Link to={RUTA_ENTRAR}>Entrá</Link> para configurar la verificación en dos pasos.
          </p>
        )}
        {paso.tipo === "sin_servicio" && (
          <p className="aviso-error">No pudimos contactar al servicio. Probá de nuevo en unos minutos.</p>
        )}
        {paso.tipo === "inactivo" && (
          <>
            <h2>Está desactivada</h2>
            <button type="button" onClick={empezarAlta} disabled={ocupado}>
              Activar la verificación en dos pasos
            </button>
          </>
        )}
        {paso.tipo === "alta" && (
          <>
            <h2>Cargá la cuenta en tu aplicación</h2>
            <ol className="pasos">
              <li>
                En el teléfono: <a href={paso.alta.uri}>abrir en la aplicación de autenticación</a>.
              </li>
              <li>
                O escribí esta clave a mano: <code className="secreto">{agruparSecreto(paso.alta.secreto)}</code>
              </li>
              <li>Escribí abajo el código de seis números que muestra la aplicación.</li>
            </ol>
            <FormularioCodigo etiqueta="Código de seis números" boton="Confirmar" enviar={confirmar} ocupado={ocupado} />
          </>
        )}
        {paso.tipo === "codigos" && (
          <>
            <h2 className="aviso-ok">Quedó activada</h2>
            <p>
              Guardá estos códigos de recuperación en un lugar seguro. Cada uno sirve una sola vez si
              perdés el teléfono. No los vamos a volver a mostrar.
            </p>
            <ul className="codigos">
              {paso.codigos.map((c) => (
                <li key={c}>
                  <code>{c}</code>
                </li>
              ))}
            </ul>
            <button type="button" onClick={cargar}>
              Ya los guardé
            </button>
          </>
        )}
        {paso.tipo === "activo" && (
          <>
            <h2 className="aviso-ok">Está activada</h2>
            <p>Te quedan {paso.estado.codigos_recuperacion_restantes} códigos de recuperación.</p>
            <FormularioCodigo
              etiqueta="Para desactivarla, escribí un código de la aplicación o de recuperación"
              boton="Desactivar"
              enviar={(c) => void desactivar(c)}
              ocupado={ocupado}
              aceptaRecuperacion
            />
          </>
        )}
        {error && (
          <p role="alert" className="aviso-error">
            {error}
          </p>
        )}
      </section>
    </>
  );
}
