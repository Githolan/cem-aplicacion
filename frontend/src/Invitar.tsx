import { type FormEvent, useCallback, useEffect, useId, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { encabezadoCsrf } from "./csrf";
import { RUTA_ENTRAR } from "./enlace";
import { codigoDe, esDeConexion, mensajeDeFalla, SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "./fallas";

// Página /invitaciones (H2b 2b-4, PR 3): invitar a una persona a una unidad, ver las invitaciones
// y retirar las que no se aceptaron. El frontend nunca concede ni deduce permisos: muestra todas
// las unidades del consorcio y es la API la que responde 403 fuera del alcance de quien invita
// (política `invitaciones.crear`). El correo de una invitación solo llega en las pendientes que
// creó quien mira (P6); si la API lo manda nulo, no se muestra. Diseño provisorio: el definitivo
// llega con G3.

type Consorcio = components["schemas"]["ConsorcioResumen"];
type Unidad = components["schemas"]["Unidad"];
type Invitacion = components["schemas"]["Invitacion"];
type TipoVinculo = components["schemas"]["TipoVinculo"];

export const RUTA_INVITACIONES = "/invitaciones";

const ESTADO_SIN_SESION = 401;
const ENCABEZADO_IDEMPOTENCIA = "Idempotency-Key" as const;
const LARGO_MAXIMO_NOMBRE = 100;

const TIPOS: { valor: TipoVinculo; texto: string }[] = [
  { valor: "propietario", texto: "Propietario o propietaria" },
  { valor: "inquilino", texto: "Inquilino o inquilina" },
  { valor: "ocupante", texto: "Ocupante" },
];
const TEXTO_TIPO = Object.fromEntries(TIPOS.map((t) => [t.valor, t.texto])) as Record<TipoVinculo, string>;

const TEXTO_ESTADO: Record<Invitacion["estado"], string> = {
  pendiente: "Pendiente",
  aceptada: "Aceptada",
  vencida: "Vencida",
  revocada: "Retirada",
};

const FORMATO_FECHA = new Intl.DateTimeFormat("es-AR", {
  dateStyle: "long",
  timeStyle: "short",
  hourCycle: "h23",
  timeZone: "America/Argentina/Buenos_Aires",
});
const fecha = (iso: string) => FORMATO_FECHA.format(new Date(iso));

/** Qué decir ante el error de invitar, según el estado y el código del cuerpo uniforme. */
export function mensajeAlInvitar(estado: number, codigo: string | undefined): string {
  if (estado === 403 && codigo === "csrf_invalido") return "No pudimos verificar el pedido. Recargá la página.";
  if (estado === 403) return "No tenés permiso para invitar a esa unidad.";
  if (estado === 404) return "Esa unidad ya no está en el consorcio. Elegí otra.";
  if (estado === 409) return "La invitación se está procesando. Probá de nuevo en unos segundos.";
  if (estado === 429) return "Llegaste al límite de invitaciones por ahora. Probá más tarde.";
  if (estado === 422 && codigo === "idempotencia_reutilizada")
    return "Cambiaste los datos de un intento anterior. Volvé a tocar «Invitar».";
  if (estado === 422) return "Revisá los datos: el correo tiene que ser válido y el nombre, de hasta 100 caracteres.";
  return mensajeDeFalla(estado, codigo);
}

type Pantalla =
  | { tipo: "cargando" }
  | { tipo: "sin_servicio"; deConexion: boolean }
  | { tipo: "sin_consorcios" }
  | { tipo: "lista"; consorcios: Consorcio[] };

type Aviso = { texto: string; ok: boolean };

/** Un 401 en cualquier pedido de la página: se va a /entrar. */
class SinSesion extends Error {}

function exigirSesion(response: Response) {
  if (response.status === ESTADO_SIN_SESION) throw new SinSesion();
}

export function Invitar() {
  const id = useId();
  const navegar = useNavigate();
  const [pantalla, setPantalla] = useState<Pantalla>({ tipo: "cargando" });
  const [consorcio, setConsorcio] = useState<string>("");
  const [unidades, setUnidades] = useState<Unidad[]>([]);
  const [invitaciones, setInvitaciones] = useState<Invitacion[] | null>(null);
  // E54: si la lista no se pudo consultar, la sección deja de decir «Consultando…».
  const [listaFallida, setListaFallida] = useState(false);
  const [unidad, setUnidad] = useState("");
  const [tipo, setTipo] = useState<TipoVinculo>("inquilino");
  const [correo, setCorreo] = useState("");
  const [nombre, setNombre] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [aRetirar, setARetirar] = useState<Invitacion | null>(null);
  // Cada invitación nueva vuelve a pedir la lista: qué se ve (y con qué correo) lo decide la API.
  const [vuelta, setVuelta] = useState(0);
  const consultado = useRef(false);
  // Una clave por intento lógico: se conserva si el pedido se cae (un reintento recibe la misma
  // respuesta, sin otro correo) y se renueva al cambiar los datos o cuando el intento terminó.
  const clave = useRef<string | null>(null);
  // El campo con el foco al invitar: mientras el envío corre queda deshabilitado y el navegador
  // pierde el foco; al terminar vuelve ahí, para que el teclado no arranque de nuevo la página.
  const foco = useRef<HTMLElement | null>(null);
  // E46: al retirar, la fila cambia de lugar (pasa al historial plegado) y el botón desaparece; el
  // foco va al aviso del resultado, como en /mandatos, para que el teclado no vuelva al principio.
  const alAviso = useRef(false);
  const textoAviso = useRef<HTMLParagraphElement | null>(null);

  useEffect(() => {
    if (enviando || foco.current === null) return;
    // Solo si nadie tomó el foco en el medio (la persona pudo ir a otra parte de la página).
    const ahora = document.activeElement;
    if (ahora === null || ahora === document.body || ahora === foco.current)
      (alAviso.current ? (textoAviso.current ?? foco.current) : foco.current).focus();
    foco.current = null;
  }, [enviando]);

  const aEntrar = useCallback(() => navegar(RUTA_ENTRAR, { replace: true }), [navegar]);

  const atajar = useCallback(
    (e: unknown) => {
      if (e instanceof SinSesion) aEntrar();
      else setAviso({ texto: SIN_CONEXION, ok: false });
    },
    [aEntrar],
  );

  useEffect(() => {
    if (consultado.current) return;
    consultado.current = true;
    void api
      .GET("/api/v1/me/consorcios")
      .then(({ data, error, response }) => {
        exigirSesion(response);
        const primero = data?.[0];
        if (data === undefined)
          setPantalla({ tipo: "sin_servicio", deConexion: esDeConexion(response.status, codigoDe(error)) });
        else if (primero === undefined) setPantalla({ tipo: "sin_consorcios" });
        else {
          setPantalla({ tipo: "lista", consorcios: data });
          setConsorcio(primero.id);
        }
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion) aEntrar();
        else setPantalla({ tipo: "sin_servicio", deConexion: true });
      });
  }, [aEntrar]);

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/unidades", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        if (data === undefined) {
          setAviso({ texto: mensajeDeFalla(response.status, codigoDe(error)), ok: false });
          return;
        }
        setUnidades(data.elementos);
        setUnidad(data.elementos[0]?.id ?? "");
      })
      .catch((e: unknown) => {
        // Un rechazo del edificio anterior no pisa al que ya se eligió.
        if (vigente) atajar(e);
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, atajar]);

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/invitaciones", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        if (data === undefined) {
          // Si falla la recarga que sigue a un retiro, queda a la vista lo que sí se hizo.
          const texto = mensajeDeFalla(response.status, codigoDe(error));
          setAviso((actual) => (actual?.ok ? actual : { texto, ok: false }));
          setListaFallida(true);
          return;
        }
        setListaFallida(false);
        setInvitaciones(data.elementos);
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        // Tampoco un corte de red en la recarga pisa el aviso de un retiro hecho.
        else {
          setAviso((actual) => (actual?.ok ? actual : { texto: SIN_CONEXION, ok: false }));
          setListaFallida(true);
        }
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, vuelta, aEntrar]);

  /** Cualquier cambio en los datos es otro intento: otra clave. */
  function cambiar<T>(fijar: (v: T) => void) {
    return (v: T) => {
      clave.current = null;
      fijar(v);
    };
  }

  function invitar(evento: FormEvent) {
    evento.preventDefault();
    const activo = document.activeElement;
    foco.current = activo instanceof HTMLElement && activo !== document.body ? activo : null;
    alAviso.current = false;
    clave.current ??= crypto.randomUUID();
    setEnviando(true);
    setAviso(null);
    void api
      .POST("/api/v1/consorcios/{consorcio_id}/invitaciones", {
        params: { path: { consorcio_id: consorcio }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current } },
        headers: encabezadoCsrf(),
        body: { unidad_id: unidad, tipo, correo: correo.trim(), nombre: nombre.trim() },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data === undefined) {
          const codigo = codigoDe(error);
          // Un 5xx es ambiguo (un proxy puede responder después de que el backend guardó el
          // resultado): igual que con la red caída, el reintento lleva la misma clave para recibir
          // lo guardado. Solo una respuesta definitiva de la aplicación la renueva.
          const ambigua = response.status >= 500 || response.status === 409;
          if (codigo === "idempotencia_reutilizada" || !ambigua) clave.current = null;
          setAviso({ texto: mensajeAlInvitar(response.status, codigo), ok: false });
          return;
        }
        clave.current = null;
        setVuelta((v) => v + 1);
        setCorreo("");
        setNombre("");
        setAviso({ texto: `Listo: le mandamos la invitación a ${data.nombre}.`, ok: true });
      })
      .catch(atajar)
      .finally(() => setEnviando(false));
  }

  function retirar(invitacion: Invitacion) {
    const activo = document.activeElement;
    foco.current = activo instanceof HTMLElement && activo !== document.body ? activo : null;
    alAviso.current = true;
    setEnviando(true);
    setAviso(null);
    void api
      .POST("/api/v1/consorcios/{consorcio_id}/invitaciones/{invitacion_id}/revocar", {
        params: { path: { consorcio_id: consorcio, invitacion_id: invitacion.id } },
        headers: encabezadoCsrf(),
      })
      .then(({ error, response }) => {
        exigirSesion(response);
        setARetirar(null);
        // E35: una lista pedida antes del retiro puede llegar después y mostrar el estado viejo.
        // Cambiar `vuelta` la descarta (el efecto anterior queda no vigente) y pide la de ahora.
        if (response.status === 409 || response.status === 404 || response.ok) setVuelta((v) => v + 1);
        if (response.status === 403) {
          const codigo = codigoDe(error);
          setAviso({
            texto:
              codigo === "csrf_invalido"
                ? "No pudimos verificar el pedido. Recargá la página."
                : "No tenés permiso para retirar esa invitación.",
            ok: false,
          });
          return;
        }
        if (response.status === 409) {
          setInvitaciones((actual) =>
            (actual ?? []).map((i) => (i.id === invitacion.id ? { ...i, estado: "aceptada" as const } : i)),
          );
          setAviso({
            texto: "Esa invitación ya se aceptó. Para quitar el acceso hay que terminar el vínculo.",
            ok: false,
          });
          return;
        }
        if (response.status === 404) {
          setInvitaciones((actual) => (actual ?? []).filter((i) => i.id !== invitacion.id));
          setAviso({ texto: "No encontramos esa invitación.", ok: false });
          return;
        }
        if (!response.ok) {
          setAviso({ texto: mensajeDeFalla(response.status, codigoDe(error)), ok: false });
          return;
        }
        setInvitaciones((actual) =>
          (actual ?? []).map((i) => (i.id === invitacion.id ? { ...i, estado: "revocada" as const } : i)),
        );
        setAviso({ texto: `Listo: retiramos la invitación de ${invitacion.nombre}.`, ok: true });
      })
      .catch(atajar)
      .finally(() => setEnviando(false));
  }

  const conMensaje = aviso !== null || pantalla.tipo !== "lista";
  // Solo presentación (doc/diseno/UX_Invitaciones_y_Formularios_Movil.md del repositorio CEM): las
  // pendientes a la vista y el resto plegado. La API devuelve todas y nada se borra.
  const pendientes = (invitaciones ?? []).filter((i) => i.estado === "pendiente");
  const historial = (invitaciones ?? []).filter((i) => i.estado !== "pendiente");

  return (
    <>
      <h1>Invitar a una persona</h1>

      {pantalla.tipo === "lista" && (
        <>
          <p className="tarjeta">
            Invitar no acredita el carácter de propietario, inquilino u ocupante: la persona entra
            con el vínculo que elijas y queda sin confirmar hasta que alguien lo confirme con el
            título.
          </p>

          <section aria-labelledby={`${id}-nueva`} className="tarjeta">
            <h2 id={`${id}-nueva`}>Nueva invitación</h2>
            <form className="formulario formulario-movil" onSubmit={invitar}>
              {pantalla.consorcios.length > 1 && (
                <>
                  <label htmlFor={`${id}-consorcio`}>Edificio</label>
                  <select
                    id={`${id}-consorcio`}
                    value={consorcio}
                    disabled={enviando}
                    onChange={(e) => {
                      // Nada del edificio anterior queda elegible mientras llegan los del nuevo.
                      setInvitaciones(null);
                      setListaFallida(false);
                      setUnidades([]);
                      setUnidad("");
                      cambiar(setConsorcio)(e.target.value);
                    }}
                  >
                    {pantalla.consorcios.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.nombre}
                      </option>
                    ))}
                  </select>
                </>
              )}
              <label htmlFor={`${id}-unidad`}>Unidad</label>
              <select
                id={`${id}-unidad`}
                value={unidad}
                onChange={(e) => cambiar(setUnidad)(e.target.value)}
                disabled={enviando}
                required
              >
                {unidades.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.designacion}
                  </option>
                ))}
              </select>
              <label htmlFor={`${id}-tipo`}>Vínculo con la unidad</label>
              <select
                id={`${id}-tipo`}
                value={tipo}
                disabled={enviando}
                onChange={(e) => cambiar(setTipo)(e.target.value as TipoVinculo)}
              >
                {TIPOS.map((t) => (
                  <option key={t.valor} value={t.valor}>
                    {t.texto}
                  </option>
                ))}
              </select>
              <label htmlFor={`${id}-nombre`}>Nombre</label>
              <input
                id={`${id}-nombre`}
                type="text"
                autoComplete="off"
                maxLength={LARGO_MAXIMO_NOMBRE}
                disabled={enviando}
                required
                value={nombre}
                onChange={(e) => cambiar(setNombre)(e.target.value)}
              />
              <label htmlFor={`${id}-correo`}>Correo</label>
              <input
                id={`${id}-correo`}
                type="email"
                autoComplete="off"
                disabled={enviando}
                required
                value={correo}
                onChange={(e) => cambiar(setCorreo)(e.target.value)}
              />
              <button type="submit" disabled={enviando || unidad === ""}>
                Invitar
              </button>
            </form>
          </section>

          <section aria-labelledby={`${id}-lista`} className="tarjeta">
            <h2 id={`${id}-lista`}>Invitaciones</h2>
            {invitaciones === null && !listaFallida && <p>Consultando…</p>}
            {invitaciones === null && listaFallida && <p>No pudimos mostrar las invitaciones.</p>}
            {invitaciones?.length === 0 && <p>Todavía no hay invitaciones que puedas ver.</p>}
            {invitaciones !== null && invitaciones.length > 0 && (
              <>
                {pendientes.length === 0 ? (
                  <p>No hay invitaciones pendientes.</p>
                ) : (
                  <ul className="sesiones" aria-label="Pendientes">
                    {pendientes.map(tarjeta)}
                  </ul>
                )}
                {historial.length > 0 && (
                  <details className="historial">
                    <summary>Historial: aceptadas, retiradas y vencidas ({historial.length})</summary>
                    <ul className="sesiones compactas">{historial.map(tarjeta)}</ul>
                  </details>
                )}
              </>
            )}
          </section>
        </>
      )}

      <div role="status" aria-live="polite" className={conMensaje ? "tarjeta" : undefined}>
        {pantalla.tipo === "cargando" && <p>Consultando…</p>}
        {pantalla.tipo === "sin_servicio" && (
          <p className="aviso-error">
            {pantalla.deConexion
              ? "No pudimos consultar tus edificios. Revisá la conexión y volvé en un rato."
              : `No pudimos consultar tus edificios. ${SERVICIO_NO_DISPONIBLE}`}
          </p>
        )}
        {pantalla.tipo === "sin_consorcios" && <p>Todavía no participás de ningún edificio.</p>}
        {aviso && (
          <p ref={textoAviso} tabIndex={-1} className={aviso.ok ? "aviso-ok" : "aviso-error"}>
            {aviso.texto}
          </p>
        )}
      </div>
    </>
  );

  /** Una invitación: en la vista principal (pendiente) o en el historial (las demás). */
  function tarjeta(i: Invitacion) {
    const cierre = i.aceptada_en ?? i.revocada_en;
    return (
                  <li key={i.id}>
                    <h3 id={`${id}-${i.id}`}>
                      {i.nombre}, {i.unidad}
                    </h3>
                    <dl className="detalles">
                      <dt>Vínculo</dt>
                      <dd>{TEXTO_TIPO[i.tipo]}</dd>
                      <dt>Estado</dt>
                      <dd>{TEXTO_ESTADO[i.estado]}</dd>
                      {i.correo && (
                        <>
                          <dt>Correo</dt>
                          <dd>{i.correo}</dd>
                        </>
                      )}
                      <dt>Enviada</dt>
                      <dd>{fecha(i.creada_en)}</dd>
                      {i.estado === "pendiente" && (
                        <>
                          <dt>Vence</dt>
                          <dd>{fecha(i.vence_en)}</dd>
                        </>
                      )}
                      {i.estado === "vencida" && (
                        <>
                          <dt>Venció</dt>
                          <dd>{fecha(i.vence_en)}</dd>
                        </>
                      )}
                      {cierre && (
                        <>
                          <dt>{i.estado === "aceptada" ? "Aceptada el" : "Retirada el"}</dt>
                          <dd>{fecha(cierre)}</dd>
                        </>
                      )}
                    </dl>
                    {(i.estado === "pendiente" || i.estado === "vencida") &&
                      (aRetirar?.id === i.id ? (
                        <div role="group" aria-labelledby={`${id}-${i.id}-confirmar`}>
                          <p id={`${id}-${i.id}-confirmar`}>
                            ¿Retirar la invitación de {i.nombre} a la unidad {i.unidad}?
                          </p>
                          <button type="button" onClick={() => retirar(i)} disabled={enviando}>
                            Sí, retirarla
                          </button>{" "}
                          <button type="button" onClick={() => setARetirar(null)} disabled={enviando}>
                            No
                          </button>
                        </div>
                      ) : (
                        <button
                          type="button"
                          onClick={() => setARetirar(i)}
                          disabled={enviando}
                          aria-describedby={`${id}-${i.id}`}
                        >
                          Retirar
                        </button>
                      ))}
                  </li>
    );
  }
}
