import { type FormEvent, useCallback, useEffect, useId, useRef, useState } from "react";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { encabezadoCsrf } from "./csrf";
import { codigoDe, mensajeDeFallaConEnlace, SIN_CONEXION_ENLACE } from "./fallas";
import { RUTA_INVITACION, tokenDelFragmento } from "./enlace";
import { FormasDeEntrar } from "./FormasDeEntrar";
import { EnlaceAlInicio, SalirDeEstaCuenta } from "./SalidaDeInvitacion";
import { useIrAlInicio } from "./sesion";

// Página /invitacion: la que abre el enlace de la invitación del correo (RF03). Como en /entrar,
// el token llega en el fragmento (#token=…), que el navegador nunca manda al servidor; la página
// lo lee, lo borra de la URL y lo manda en el cuerpo de dos POST: uno para mostrar qué invitación
// es y otro, cuando la persona confirma, para aceptarla.
//
// Si el correo invitado no tenía cuenta, aceptar crea la cuenta y deja la sesión abierta. Si ya
// la tenía, hay que entrar con ese correo: la invitación nunca abre la cuenta de otra persona. Se
// entra acá mismo y, al entrar, la aceptación se repite sola con el mismo enlace (G-35): no hace
// falta tocar «Aceptar» otra vez. Diseño provisorio: el definitivo llega con G3.
//
// C8G-11 (informe de fricción, 17-11-2026; app 0109): la consulta del enlace, con la sesión de este
// navegador (y su CSRF), dice lo que pasaría al aceptar (`paso`). La página ofrece «Aceptar» solo si
// va a servir, y pide el nombre solo si la cuenta se va a crear. Con otra cuenta abierta lo dice de
// entrada, con «Salir de esta cuenta»; si la cuenta del correo ya existe, abre «Entrar para aceptar»;
// si la invitación la creó esta cuenta, lo dice. La aceptación vuelve a decidir: sus 409 siguen
// teniendo su pantalla, por si algo cambió entre la consulta y el clic.
//
// FL-INVA-01 (decisión del propietario, 18-11-2026): para entrar con la cuenta que ya existe, el
// correo es el invitado y no se cambia (con otro, la aceptación se rechaza), y el código se pide solo
// al llegar: queda a la vista el campo del código.

type Recibida = components["schemas"]["InvitacionRecibida"];
type Paso = components["schemas"]["PasoInvitacion"];

type Estado =
  | "sin_enlace"
  | "consultando"
  | "lista"
  | "aceptando"
  | "invalida"
  | "requiere_sesion"
  | "otra_persona"
  | "propia"
  | "ya_aceptada"
  | "conflicto"
  | "sin_servicio";

export const TIPOS: Record<Recibida["tipo"], string> = {
  propietario: "propietario/a",
  inquilino: "inquilino/a",
  ocupante: "ocupante",
};

/** Con el correo de una cuenta que ya existe: se entra acá y la invitación se acepta sola (G-35). */
/** FL-INVA-02: la abre de nuevo la misma cuenta que ya la aceptó. */
export const textoYaAceptada = (consorcio: string) =>
  `Esta invitación ya está aceptada: la cuenta ya es parte de ${consorcio}.`;

export const YA_HAY_CUENTA =
  "Ya hay una cuenta con este correo. Para aceptar la invitación, entrar con esa cuenta: al entrar, la invitación se acepta sola.";

const ESTADO_INVALIDA = 404;
const ESTADO_CONFLICTO = 409;
/** Los 409 de la aceptación que tienen pantalla propia; de cualquier otro se dice lo que dice el servidor. */
const CONFLICTOS: Record<string, Estado> = {
  invitacion_requiere_sesion: "requiere_sesion",
  invitacion_de_otra_persona: "otra_persona",
  invitacion_propia: "propia",
};

/** Dónde deja la página cada paso que informa la consulta (C8G-11). */
const ESTADO_DEL_PASO: Record<Paso, Estado> = {
  crear_cuenta: "lista",
  aceptar: "lista",
  entrar: "requiere_sesion",
  otra_cuenta: "otra_persona",
  propia: "propia",
  // FL-INVA-02 (`0110`, tanda 9): la cuenta que ya la aceptó la vuelve a abrir. Se le dice y se la
  // lleva al inicio; no se la manda a pedir otra.
  ya_aceptada: "ya_aceptada",
};

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
  const [falla, setFalla] = useState(SIN_CONEXION_ENLACE);
  const [fallaAlAceptar, setFallaAlAceptar] = useState<string | null>(null);
  /** El correo con el que se entra cuando ya hay una cuenta: el invitado, fijo (FL-INVA-01). */
  const [correoParaEntrar, setCorreoParaEntrar] = useState("");
  const consultada = useRef(false);
  const irAlInicio = useIrAlInicio();

  const consultar = useCallback((elToken: string) => {
    setEstado("consultando");
    void api
      .POST("/api/v1/auth/invitacion/consultar", { body: { token: elToken }, headers: encabezadoCsrf() })
      .then(({ data, error, response }) => {
        if (data !== undefined) {
          setInvitacion(data);
          setNombre(data.nombre);
          setCorreoParaEntrar(data.correo);
          setEstado(ESTADO_DEL_PASO[data.paso]);
        } else if (response.status === ESTADO_INVALIDA) setEstado("invalida");
        else fallar(response.status, codigoDe(error));
      })
      .catch(() => fallar(0, undefined));
  }, []);

  useEffect(() => {
    if (!token || consultada.current) return;
    consultada.current = true;
    window.history.replaceState(window.history.state, "", RUTA_INVITACION);
    consultar(token);
  }, [token, consultar]);

  function aceptar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    aceptarConElEnlace();
  }

  /** Acepta con el token del enlace: al tocar «Aceptar», o sola después de entrar (G-35). */
  function aceptarConElEnlace() {
    if (!token) return;
    setEstado("aceptando");
    setFallaAlAceptar(null);
    // El nombre es solo de una cuenta nueva: a una que ya existe no se le manda (C8G-11).
    const elegido = invitacion?.paso === "crear_cuenta" ? nombre.trim() : "";
    const volverAlFormulario = (estadoHttp: number, codigo: string | undefined) => {
      setFallaAlAceptar(mensajeDeFallaConEnlace(estadoHttp, codigo));
      setEstado("lista");
    };
    void api
      .POST("/api/v1/auth/invitacion/aceptar", {
        body: { token, nombre: elegido === "" ? null : elegido },
        headers: encabezadoCsrf(),
      })
      .then(({ error, response }) => {
        if (error === undefined) irAlInicio(`Listo: la cuenta ya es parte de ${invitacion?.consorcio}.`);
        else if (response.status === ESTADO_INVALIDA) setEstado("invalida");
        else if (response.status === ESTADO_CONFLICTO && "codigo" in error) {
          setFalla(error.mensaje);
          setEstado(CONFLICTOS[error.codigo] ?? "conflicto");
        } else volverAlFormulario(response.status, codigoDe(error));
      })
      .catch(() => volverAlFormulario(0, undefined));
  }

  // E52: un 403, un 429 o un 5xx no son de la conexión (regla E34 de fallas.ts).
  function fallar(estadoHttp: number, codigo: string | undefined) {
    setFalla(mensajeDeFallaConEnlace(estadoHttp, codigo));
    setEstado("sin_servicio");
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
            <dt>Invitó</dt>
            <dd>{invitacion.invitada_por ?? "El equipo de Consorcio en Mano"}</dd>
          </dl>
          <form className="formulario" onSubmit={aceptar}>
            {invitacion.paso === "crear_cuenta" && (
              <>
                <label htmlFor={`${id}-nombre`}>Nombre</label>
                <input
                  id={`${id}-nombre`}
                  type="text"
                  autoComplete="name"
                  maxLength={100}
                  value={nombre}
                  onChange={(e) => setNombre(e.target.value)}
                />
              </>
            )}
            <button type="submit" disabled={estado === "aceptando"}>
              Aceptar la invitación
            </button>
          </form>
        </section>
      )}
      <div role="status" aria-live="polite" className={estado === "lista" ? undefined : "tarjeta"}>
        {estado === "consultando" && <p>Buscando la invitación…</p>}
        {estado === "aceptando" && <p>Aceptando…</p>}
        {estado === "lista" && fallaAlAceptar !== null && <p className="aviso-error">{fallaAlAceptar}</p>}
        {estado === "invalida" && (
          <>
            <p className="aviso-error">
              Esta invitación no sirve: puede haber vencido, haberse usado o haber sido retirada.
              Hay que pedir una nueva a quien invitó.
            </p>
            <EnlaceAlInicio />
          </>
        )}
        {estado === "requiere_sesion" && (
          <p>{YA_HAY_CUENTA}</p>
        )}
        {estado === "otra_persona" && (
          <>
            <p className="aviso-error">
              La sesión abierta es de una cuenta que no es la del correo invitado. Hay que salir de esa
              cuenta para aceptarla.
            </p>
            {token && <SalirDeEstaCuenta alSalir={() => consultar(token)} />}
          </>
        )}
        {estado === "propia" && (
          <>
            <p className="aviso-error">
              Esta invitación la creó esta cuenta: la tiene que aceptar la persona invitada, con su
              propia cuenta.
            </p>
            <EnlaceAlInicio />
          </>
        )}
        {estado === "ya_aceptada" && invitacion && (
          <>
            <p>{textoYaAceptada(invitacion.consorcio)}</p>
            <EnlaceAlInicio />
          </>
        )}
        {estado === "conflicto" && (
          <>
            <p className="aviso-error">{falla}</p>
            <EnlaceAlInicio />
          </>
        )}
        {estado === "sin_servicio" && (
          <>
            <p className="aviso-error">{falla}</p>
            {token && (
              <button className="secundario" type="button" onClick={() => consultar(token)}>
                Reintentar
              </button>
            )}
          </>
        )}
        {estado === "sin_enlace" && (
          <>
            <p>Para aceptar una invitación hay que abrir el enlace que llegó por correo.</p>
            <EnlaceAlInicio />
          </>
        )}
      </div>
      {estado === "requiere_sesion" && (
        <section aria-labelledby={`${id}-entrar`} className="tarjeta">
          <h2 id={`${id}-entrar`}>Entrar para aceptar</h2>
          <FormasDeEntrar
            correo={correoParaEntrar}
            alCambiarCorreo={setCorreoParaEntrar}
            alEntrar={aceptarConElEnlace}
            correoFijo
            pedirAlEmpezar
          />
        </section>
      )}
    </>
  );
}
