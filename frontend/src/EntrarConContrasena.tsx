import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router";

import { api } from "./api/cliente";
import { codigoVerificacion } from "./SegundoFactor";

// Entrar con correo y contraseña (documento 13, §10.3: el método de administradores, consejeros
// y operadores). Si la persona tiene segundo factor, la contraseña no abre la sesión: el backend
// deja la cookie del desafío (5 minutos, cinco intentos) y responde `falta_segundo_factor`; acá
// se pide el código de la aplicación o uno de recuperación y se completa con `/segundo-factor`
// desde el mismo navegador. Sin cuenta, sin contraseña o con una incorrecta, el backend responde
// lo mismo (401 `credenciales_invalidas`) y la página también. Diseño provisorio: el definitivo
// llega con G3.

const ESTADO_INVALIDO = 400;
const ESTADO_RECHAZADO = 401;
const ESTADO_DEMASIADOS = 429;

const FORMATO_HORA = new Intl.DateTimeFormat("es-AR", {
  timeStyle: "short",
  hourCycle: "h23",
  timeZone: "America/Argentina/Buenos_Aires",
});

type Paso =
  | { tipo: "contrasena" }
  | { tipo: "codigo"; venceEn: string }
  | { tipo: "adentro" };

type Estado =
  | "listo"
  | "enviando"
  | "rechazado"
  | "vencida"
  | "codigo_invalido"
  | "formato_codigo"
  | "demasiados"
  | "sin_servicio";

export function EntrarConContrasena() {
  const id = useId();
  const [correo, setCorreo] = useState("");
  const [contrasena, setContrasena] = useState("");
  const [codigo, setCodigo] = useState("");
  const [paso, setPaso] = useState<Paso>({ tipo: "contrasena" });
  const [estado, setEstado] = useState<Estado>("listo");
  const campoCodigo = useRef<HTMLInputElement>(null);
  const campoContrasena = useRef<HTMLInputElement>(null);

  // Al cambiar de paso, el foco va al campo que hay que completar.
  useEffect(() => {
    if (paso.tipo === "codigo") campoCodigo.current?.focus();
  }, [paso]);

  function fallo(status: number): Estado {
    return status === ESTADO_DEMASIADOS ? "demasiados" : "sin_servicio";
  }

  function entrar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    setEstado("enviando");
    void api
      .POST("/api/v1/auth/contrasena/entrar", { body: { correo: correo.trim(), contrasena } })
      .then(({ data, response }) => {
        if (data === undefined) {
          setEstado(response.status === ESTADO_RECHAZADO ? "rechazado" : fallo(response.status));
          return;
        }
        // La contraseña ya cumplió: no queda en memoria más de lo necesario.
        setContrasena("");
        setEstado("listo");
        if (data.estado === "sesion_abierta") setPaso({ tipo: "adentro" });
        else {
          setCodigo("");
          setPaso({ tipo: "codigo", venceEn: data.vence_en });
        }
      })
      .catch(() => setEstado("sin_servicio"));
  }

  function volverALaContrasena(final: Estado) {
    setCodigo("");
    setPaso({ tipo: "contrasena" });
    setEstado(final);
    // Después de pintar el formulario de la contraseña.
    window.setTimeout(() => campoContrasena.current?.focus(), 0);
  }

  function completar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    const limpio = codigoVerificacion(codigo);
    if (!limpio) {
      setEstado("formato_codigo");
      return;
    }
    setEstado("enviando");
    void api
      .POST("/api/v1/auth/contrasena/segundo-factor", { body: { codigo: limpio } })
      .then(({ data, response }) => {
        if (data !== undefined) {
          setCodigo("");
          setEstado("listo");
          setPaso({ tipo: "adentro" });
        } else if (response.status === ESTADO_RECHAZADO) volverALaContrasena("vencida");
        else if (response.status === ESTADO_INVALIDO) setEstado("codigo_invalido");
        else setEstado(fallo(response.status));
      })
      .catch(() => setEstado("sin_servicio"));
  }

  const titulo = <h2 id={`${id}-titulo`}>Entrar con contraseña</h2>;

  if (paso.tipo === "adentro") {
    return (
      <section aria-labelledby={`${id}-titulo`} className="tarjeta">
        {titulo}
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
      {titulo}
      {paso.tipo === "contrasena" ? (
        <>
          <p>Si definiste una contraseña en Mi cuenta, escribila con tu correo.</p>
          <form className="formulario" onSubmit={entrar}>
            <label htmlFor={`${id}-correo`}>Correo</label>
            <input
              id={`${id}-correo`}
              type="email"
              autoComplete="username"
              required
              value={correo}
              onChange={(e) => setCorreo(e.target.value)}
            />
            <label htmlFor={`${id}-contrasena`}>Contraseña</label>
            <input
              id={`${id}-contrasena`}
              ref={campoContrasena}
              type="password"
              autoComplete="current-password"
              required
              value={contrasena}
              onChange={(e) => setContrasena(e.target.value)}
            />
            <button type="submit" disabled={estado === "enviando"}>
              Entrar con la contraseña
            </button>
          </form>
        </>
      ) : (
        <>
          <p>
            La contraseña es correcta. Como tenés el segundo factor activado, falta el código de
            seis números de tu aplicación de autenticación, o uno de tus códigos de recuperación.
            Tenés hasta las {FORMATO_HORA.format(new Date(paso.venceEn))}.
          </p>
          <form className="formulario" onSubmit={completar} noValidate>
            <label htmlFor={`${id}-codigo`}>Código de la aplicación o de recuperación</label>
            <input
              id={`${id}-codigo`}
              ref={campoCodigo}
              type="text"
              autoComplete="one-time-code"
              autoCapitalize="none"
              spellCheck={false}
              maxLength={13}
              aria-invalid={estado === "formato_codigo"}
              aria-describedby={estado === "formato_codigo" ? `${id}-formato` : undefined}
              value={codigo}
              onChange={(e) => setCodigo(e.target.value)}
            />
            {estado === "formato_codigo" && (
              <p id={`${id}-formato`} className="aviso-error">
                Escribí los seis números de la aplicación o un código de recuperación (como
                k7m2q-x9p4t).
              </p>
            )}
            <button type="submit" disabled={estado === "enviando"}>
              Entrar con el código
            </button>
          </form>
          <button type="button" onClick={() => volverALaContrasena("listo")} disabled={estado === "enviando"}>
            Volver a la contraseña
          </button>
        </>
      )}
      <div role="status" aria-live="polite">
        {estado === "enviando" && <p>Entrando…</p>}
        {estado === "rechazado" && (
          <p className="aviso-error">
            El correo o la contraseña no son correctos. Si no tenés contraseña, entrá con el
            enlace por correo.
          </p>
        )}
        {estado === "vencida" && (
          <p className="aviso-error">
            Pasó demasiado tiempo o hubo demasiados intentos con el código. Volvé a escribir la
            contraseña.
          </p>
        )}
        {estado === "codigo_invalido" && (
          <p className="aviso-error">
            El código no sirve. Revisá que la hora del teléfono esté bien y usá el código nuevo.
          </p>
        )}
        {estado === "demasiados" && (
          <p className="aviso-error">Hubo demasiados intentos. Esperá un rato y probá de nuevo.</p>
        )}
        {estado === "sin_servicio" && (
          <p className="aviso-error">No pudimos completar el pedido. Revisá la conexión y probá en un rato.</p>
        )}
      </div>
    </section>
  );
}
