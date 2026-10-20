import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { encabezadoCsrf } from "./csrf";
import { codigoDe, mensajeDeFallaConEnlace, SIN_CONEXION_ENLACE } from "./fallas";
import { RUTA_ENTRAR, RUTA_INVITACION_MANDATO, tokenDelFragmento } from "./enlace";

// Página /invitacion-mandato: la que abre el enlace con que se asocia la cuenta del administrador
// externo a su mandato (A7). Como /invitacion, el token llega en el fragmento, la página lo borra
// de la URL y lo manda en el cuerpo de dos POST: consultar y, cuando la persona confirma, aceptar.
//
// La página no decide nada: la base valida el enlace, el mandato, la cuenta y la separación con el
// Consejo, y la página solo explica la respuesta. Diseño provisorio: el definitivo llega con G3.

type Recibida = components["schemas"]["InvitacionMandatoRecibida"];
type Aceptada = components["schemas"]["InvitacionMandatoAceptada"];

type Rechazo =
  | "invitacion_requiere_sesion"
  | "invitacion_de_otra_persona"
  | "asociacion_incompatible_con_consejo"
  | "asociacion_de_si_mismo";

type Estado = "sin_enlace" | "consultando" | "lista" | "aceptando" | "aceptada" | "invalida" | "sin_servicio" | Rechazo;

const RECHAZOS: readonly Rechazo[] = [
  "invitacion_requiere_sesion",
  "invitacion_de_otra_persona",
  "asociacion_incompatible_con_consejo",
  "asociacion_de_si_mismo",
];

const ESTADO_INVALIDA = 404;
const ESTADO_CONFLICTO = 409;

function leerToken(): string | null {
  return tokenDelFragmento(window.location.hash);
}

function esRechazo(codigo: string): codigo is Rechazo {
  return (RECHAZOS as readonly string[]).includes(codigo);
}

/** "2026-11-01" → "01/11/2026", sin pasar por la zona horaria del navegador. */
function fecha(iso: string): string {
  const [anio, mes, dia] = iso.split("-");
  return `${dia}/${mes}/${anio}`;
}

export function InvitacionMandato() {
  const id = useId();
  // En el inicializador: StrictMode monta dos veces y la segunda vez el fragmento ya no está.
  const [token] = useState(leerToken);
  const [estado, setEstado] = useState<Estado>(token ? "consultando" : "sin_enlace");
  const [invitacion, setInvitacion] = useState<Recibida | null>(null);
  const [aceptada, setAceptada] = useState<Aceptada | null>(null);
  const [nombre, setNombre] = useState("");
  const [falla, setFalla] = useState(SIN_CONEXION_ENLACE);
  const consultada = useRef(false);

  useEffect(() => {
    if (!token || consultada.current) return;
    consultada.current = true;
    window.history.replaceState(window.history.state, "", RUTA_INVITACION_MANDATO);
    void api
      .POST("/api/v1/auth/invitacion-mandato/consultar", { body: { token } })
      .then(({ data, error, response }) => {
        if (data !== undefined) {
          setInvitacion(data);
          setNombre(data.mandatario ?? "");
          setEstado("lista");
        } else if (response.status === ESTADO_INVALIDA) setEstado("invalida");
        else fallar(response.status, codigoDe(error));
      })
      .catch(() => fallar(0, undefined));
  }, [token]);

  function aceptar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    if (!token) return;
    setEstado("aceptando");
    const elegido = nombre.trim();
    void api
      .POST("/api/v1/auth/invitacion-mandato/aceptar", {
        body: { token, nombre: elegido === "" ? null : elegido },
        headers: encabezadoCsrf(),
      })
      .then(({ data, error, response }) => {
        if (data !== undefined) {
          setAceptada(data);
          setEstado("aceptada");
        } else if (response.status === ESTADO_INVALIDA) setEstado("invalida");
        else if (response.status === ESTADO_CONFLICTO && error && "codigo" in error && esRechazo(error.codigo))
          setEstado(error.codigo);
        else fallar(response.status, codigoDe(error));
      })
      .catch(() => fallar(0, undefined));
  }

  // E52: un 403, un 429 o un 5xx no son de la conexión (regla E34 de fallas.ts).
  function fallar(estadoHttp: number, codigo: string | undefined) {
    setFalla(mensajeDeFallaConEnlace(estadoHttp, codigo));
    setEstado("sin_servicio");
  }

  // La fecha local, no la UTC: entre las 21 y las 24 de Argentina la UTC ya es mañana.
  const ahora = new Date();
  const hoy = [ahora.getFullYear(), ahora.getMonth() + 1, ahora.getDate()]
    .map((n, i) => String(n).padStart(i === 0 ? 4 : 2, "0"))
    .join("-");

  return (
    <>
      <h1>Asociar tu cuenta a la administración</h1>
      {invitacion && (estado === "lista" || estado === "aceptando") && (
        <section aria-labelledby={`${id}-titulo`} className="tarjeta">
          <h2 id={`${id}-titulo`}>{invitacion.consorcio}</h2>
          <dl className="detalles">
            <dt>Administración</dt>
            <dd>{invitacion.mandatario ?? "Sin nombre en el mandato"}</dd>
            <dt>Te invitó</dt>
            <dd>{invitacion.invitada_por ?? "El equipo de Consorcio en Mano"}</dd>
          </dl>
          <p>
            Al aceptar, tu cuenta queda asociada al mandato de administración de este consorcio. El
            mandato, su CUIT y su vigencia no cambian.
          </p>
          <form className="formulario" onSubmit={aceptar}>
            <label htmlFor={`${id}-nombre`}>Tu nombre (solo si la cuenta es nueva)</label>
            <input
              id={`${id}-nombre`}
              type="text"
              autoComplete="name"
              maxLength={100}
              value={nombre}
              onChange={(e) => setNombre(e.target.value)}
            />
            <button type="submit" disabled={estado === "aceptando"}>
              Aceptar y asociar mi cuenta
            </button>
          </form>
        </section>
      )}
      <div role="status" aria-live="polite" className={estado === "lista" ? undefined : "tarjeta"}>
        {estado === "consultando" && <p>Buscando la invitación…</p>}
        {estado === "aceptando" && <p>Aceptando…</p>}
        {estado === "aceptada" && aceptada && (
          <>
            <p className="aviso-ok">Listo: tu cuenta quedó asociada a la administración de {invitacion?.consorcio}.</p>
            {aceptada.vigente_desde > hoy && (
              <p>El mandato empieza el {fecha(aceptada.vigente_desde)}: desde ese día vas a ver el consorcio.</p>
            )}
            <p>
              <Link to="/">Ir al inicio</Link>
            </p>
          </>
        )}
        {estado === "invalida" && (
          <p className="aviso-error">
            Esta invitación no sirve: puede haber vencido, haberse usado o haber sido retirada. Pedile una nueva al
            consorcio.
          </p>
        )}
        {estado === "invitacion_requiere_sesion" && (
          <p className="aviso-error">
            Ya tenés una cuenta con este correo. <Link to={RUTA_ENTRAR}>Entrá</Link> y después volvé a abrir el enlace
            desde el correo.
          </p>
        )}
        {estado === "invitacion_de_otra_persona" && (
          <p className="aviso-error">
            Estás con una cuenta que no es la del correo invitado. Salí de esa cuenta y volvé a abrir el enlace.
          </p>
        )}
        {estado === "asociacion_incompatible_con_consejo" && (
          <p className="aviso-error">
            Tenés un mandato del Consejo en este consorcio que se superpone con el de administración: el Consejo
            fiscaliza y no administra. Hablalo con el consorcio.
          </p>
        )}
        {estado === "asociacion_de_si_mismo" && (
          <p className="aviso-error">Quien emitió o aprobó la invitación no puede aceptarla para sí.</p>
        )}
        {estado === "sin_servicio" && <p className="aviso-error">{falla}</p>}
        {estado === "sin_enlace" && <p>Para asociar tu cuenta, abrí el enlace que te llegó por correo.</p>}
      </div>
    </>
  );
}
