import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { encabezadoCsrf } from "./csrf";
import { RUTA_ENTRAR, RUTA_INVITACION, tokenDelFragmento } from "./enlace";

// Página /invitacion: la que abre el enlace de la invitación del correo (RF03). Como en /entrar,
// el token llega en el fragmento (#token=…), que el navegador nunca manda al servidor; la página
// lo lee, lo borra de la URL y lo manda en el cuerpo de dos POST: uno para mostrar qué invitación
// es y otro, cuando la persona confirma, para aceptarla.
//
// Si el correo invitado no tenía cuenta, aceptar crea la cuenta y deja la sesión abierta. Si ya
// la tenía, hay que entrar con ese correo y volver a abrir el enlace: la invitación nunca abre la
// cuenta de otra persona. Diseño provisorio: el definitivo llega con G3.

type Recibida = components["schemas"]["InvitacionRecibida"];

type Estado =
  | "sin_enlace"
  | "consultando"
  | "lista"
  | "aceptando"
  | "aceptada"
  | "invalida"
  | "requiere_sesion"
  | "otra_persona"
  | "sin_servicio";

const TIPOS: Record<Recibida["tipo"], string> = {
  propietario: "propietario/a",
  inquilino: "inquilino/a",
  ocupante: "ocupante",
};

const ESTADO_INVALIDA = 404;
const ESTADO_CONFLICTO = 409;

function leerToken(): string | null {
  return tokenDelFragmento(window.location.hash);
}

export function Invitacion() {
  const id = useId();
  // En el inicializador: StrictMode monta dos veces y la segunda vez el fragmento ya no está.
  const [token] = useState(leerToken);
  const [estado, setEstado] = useState<Estado>(token ? "consultando" : "sin_enlace");
  const [invitacion, setInvitacion] = useState<Recibida | null>(null);
  const [nombre, setNombre] = useState("");
  const consultada = useRef(false);

  useEffect(() => {
    if (!token || consultada.current) return;
    consultada.current = true;
    window.history.replaceState(window.history.state, "", RUTA_INVITACION);
    void api
      .POST("/api/v1/auth/invitacion/consultar", { body: { token } })
      .then(({ data, response }) => {
        if (data !== undefined) {
          setInvitacion(data);
          setNombre(data.nombre);
          setEstado("lista");
        } else setEstado(response.status === ESTADO_INVALIDA ? "invalida" : "sin_servicio");
      })
      .catch(() => setEstado("sin_servicio"));
  }, [token]);

  function aceptar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    if (!token) return;
    setEstado("aceptando");
    const elegido = nombre.trim();
    void api
      .POST("/api/v1/auth/invitacion/aceptar", {
        body: { token, nombre: elegido === "" ? null : elegido },
        headers: encabezadoCsrf(),
      })
      .then(({ error, response }) => {
        if (error === undefined) setEstado("aceptada");
        else if (response.status === ESTADO_INVALIDA) setEstado("invalida");
        else if (response.status === ESTADO_CONFLICTO && "codigo" in error)
          setEstado(error.codigo === "invitacion_requiere_sesion" ? "requiere_sesion" : "otra_persona");
        else setEstado("sin_servicio");
      })
      .catch(() => setEstado("sin_servicio"));
  }

  return (
    <>
      <h1>Invitación a Consorcio en Mano</h1>
      {invitacion && (estado === "lista" || estado === "aceptando") && (
        <section aria-labelledby={`${id}-titulo`} className="tarjeta">
          <h2 id={`${id}-titulo`}>{invitacion.consorcio}</h2>
          <dl className="detalles">
            <dt>Unidad</dt>
            <dd>{invitacion.unidad}</dd>
            <dt>Como</dt>
            <dd>{TIPOS[invitacion.tipo]}</dd>
            <dt>Correo</dt>
            <dd>{invitacion.correo}</dd>
            <dt>Te invitó</dt>
            <dd>{invitacion.invitada_por ?? "El equipo de Consorcio en Mano"}</dd>
          </dl>
          <form className="formulario" onSubmit={aceptar}>
            <label htmlFor={`${id}-nombre`}>Tu nombre</label>
            <input
              id={`${id}-nombre`}
              type="text"
              autoComplete="name"
              maxLength={100}
              value={nombre}
              onChange={(e) => setNombre(e.target.value)}
            />
            <button type="submit" disabled={estado === "aceptando"}>
              Aceptar la invitación
            </button>
          </form>
        </section>
      )}
      <div role="status" aria-live="polite" className={estado === "lista" ? undefined : "tarjeta"}>
        {estado === "consultando" && <p>Buscando la invitación…</p>}
        {estado === "aceptando" && <p>Aceptando…</p>}
        {estado === "aceptada" && (
          <>
            <p className="aviso-ok">Listo: ya sos parte de {invitacion?.consorcio}.</p>
            <p>
              <Link to="/">Ir al inicio</Link>
            </p>
          </>
        )}
        {estado === "invalida" && (
          <p className="aviso-error">
            Esta invitación no sirve: puede haber vencido, haberse usado o haber sido retirada.
            Pedile una nueva a quien te invitó.
          </p>
        )}
        {estado === "requiere_sesion" && (
          <p className="aviso-error">
            Ya tenés una cuenta con este correo. <Link to={RUTA_ENTRAR}>Entrá</Link> y después
            volvé a abrir el enlace de la invitación desde el correo.
          </p>
        )}
        {estado === "otra_persona" && (
          <p className="aviso-error">
            Estás con una cuenta que no es la del correo invitado. Salí de esa cuenta y volvé a
            abrir el enlace de la invitación.
          </p>
        )}
        {estado === "sin_servicio" && (
          <p className="aviso-error">
            No pudimos completar el pedido. Revisá la conexión y volvé a abrir el enlace en un rato.
          </p>
        )}
        {estado === "sin_enlace" && (
          <p>Para aceptar una invitación, abrí el enlace que te llegó por correo.</p>
        )}
      </div>
    </>
  );
}
