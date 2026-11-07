import { type FormEvent, useCallback, useEffect, useId, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { encabezadoCsrf } from "./csrf";
import { conLaRedDeVuelta, sinPisarLoHecho } from "./egresos/comun";
import { useAlVolverLaRed } from "./egresos/useConsorcio";
import { RUTA_INICIO } from "./enlace";
import { useAEntrar } from "./sesionEncima";
import { codigoDe, esDeConexion, LIMITE_DE_PEDIDOS, mensajeDeFalla, SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "./fallas";
import { REQUISITOS, useEdificioElegido, usePuede } from "./permisos";

// Página /padron (H2b 2b-5, corte 4; doc/diseno/H2b_5_Diseno.md §5 del repositorio CEM): el padrón
// mínimo de D6 (unidad, nombre, carácter, vigencia y si está confirmado; nunca correo ni teléfono),
// confirmar un vínculo con la referencia del título y finalizarlo con el motivo. «Confirmar» se
// ofrece en las filas que la API marca (`confirmable_por_vos`: nadie confirma su propio vínculo ni
// el que registró) y «Finalizar» según si ya empezó y `padron.gestionar`; si no puede leer el padrón
// va al inicio. Es solo interfaz: la API decide cada pedido y la base vuelve a controlar, y un 403
// al actuar se dice acá.

type Consorcio = components["schemas"]["ConsorcioResumen"];
type Fila = components["schemas"]["FilaPadron"];
type TipoVinculo = components["schemas"]["TipoVinculo"];

export const RUTA_PADRON = "/padron";

const ESTADO = {
  sinSesion: 401,
  sinPermiso: 403,
  noEncontrado: 404,
  conflicto: 409,
  invalido: 422,
  limite: 429,
  errorDelServidor: 500,
} as const;
const ENCABEZADO_IDEMPOTENCIA = "Idempotency-Key" as const;
/** El mismo tope que el contrato (`LARGO_MAXIMO_REFERENCIA`) y la migración 0014. */
const LARGO_TEXTO = 300;
/** Un texto con algo más que espacios (la API rechaza el vacío y los caracteres de control). */
const PATRON_TEXTO = ".*\\S.*";

const TEXTO_TIPO: Record<TipoVinculo, string> = {
  propietario: "Propietario o propietaria",
  inquilino: "Inquilino o inquilina",
  ocupante: "Ocupante",
};

type Accion = "confirmar" | "finalizar";

const FORMATO_DIA = new Intl.DateTimeFormat("es-AR", { dateStyle: "long", timeZone: "UTC" });
const MS_POR_DIA = 86_400_000;
/** Una fecha del contrato (AAAA-MM-DD) como «1 de marzo de 2026», sin correrse por la zona. */
const dia = (iso: string, corrimiento = 0) =>
  FORMATO_DIA.format(new Date(Date.parse(`${iso}T00:00:00Z`) + corrimiento * MS_POR_DIA));

/** La vigencia en palabras. `vigente_hasta` es exclusivo: el último día es el anterior. */
function vigencia(f: Fila): string {
  const desde = `Desde el ${dia(f.vigente_desde)}`;
  return f.vigente_hasta === null ? `${desde}, sin fecha de fin` : `${desde}; último día: ${dia(f.vigente_hasta, -1)}`;
}

/** Qué decir ante un error de confirmar o finalizar, según el estado y el código del cuerpo uniforme. */
export function mensajeDeError(estado: number, codigo: string | undefined, accion: Accion): string {
  if (estado === ESTADO.sinPermiso && codigo === "csrf_invalido") return "No pudimos verificar el pedido. Recargá la página.";
  if (estado === ESTADO.sinPermiso)
    return accion === "confirmar"
      ? "No tenés permiso para confirmar este vínculo: hace falta la facultad sobre la unidad."
      : "No tenés permiso para finalizar este vínculo: hace falta la facultad sobre la unidad.";
  if (estado === ESTADO.noEncontrado) return "Ese vínculo ya no está en el padrón de este edificio.";
  if (estado === ESTADO.conflicto && codigo === "idempotencia_en_curso")
    return "El pedido se está procesando. Probá de nuevo en unos segundos.";
  if (estado === ESTADO.conflicto && codigo === "vinculo_ya_confirmado") return "Ese vínculo ya estaba confirmado.";
  if (estado === ESTADO.conflicto && codigo === "vinculo_finalizado") return "Ese vínculo ya terminó.";
  if (estado === ESTADO.conflicto && codigo === "vinculo_no_iniciado")
    return "Ese vínculo todavía no empezó: no se puede finalizar.";
  if (estado === ESTADO.conflicto) return "El pedido choca con el estado actual del padrón. Revisá la lista actualizada.";
  if (estado === ESTADO.invalido && codigo === "idempotencia_reutilizada")
    return "Cambiaste los datos de un intento anterior. Volvé a enviar el formulario.";
  if (estado === ESTADO.invalido)
    return `Revisá ${accion === "confirmar" ? "la referencia" : "el motivo"}: hasta ${LARGO_TEXTO} caracteres, con texto visible y sin saltos de línea.`;
  if (estado === ESTADO.limite) return LIMITE_DE_PEDIDOS;
  return mensajeDeFalla(estado, codigo);
}

/**
 * Si el intento terminó y el próximo lleva otra clave. Un 5xx es ambiguo (un proxy puede
 * responder después de que el backend guardó el resultado) y un pedido en curso todavía no
 * terminó: el reintento lleva la misma clave para recibir lo guardado.
 */
function renovarClave(estado: number, codigo: string | undefined): boolean {
  if (codigo === "idempotencia_reutilizada") return true;
  return estado < ESTADO.errorDelServidor && codigo !== "idempotencia_en_curso";
}

/** Lo que espera a quien puede confirmar va primero; después, el resto en el orden de la API. */
export function sinConfirmarPrimero(filas: Fila[]): Fila[] {
  return [...filas.filter((f) => !f.confirmado), ...filas.filter((f) => f.confirmado)];
}

/** Una respuesta que deja la lista vieja: el vínculo cambió o ya no está. */
function cambioElPadron(estado: number, codigo: string | undefined): boolean {
  return estado === ESTADO.noEncontrado || (estado === ESTADO.conflicto && codigo !== "idempotencia_en_curso");
}

type Pantalla =
  | { tipo: "cargando" }
  | { tipo: "sin_servicio"; deConexion: boolean }
  | { tipo: "sin_consorcios" }
  | { tipo: "lista"; consorcios: Consorcio[] };

const SIN_EDIFICIOS: readonly Consorcio[] = [];

/** El padrón del edificio: llegó o no se pudo consultar (si la API lo niega, se va al inicio). */
type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; filas: Fila[] };

type Abierto = { fila: Fila; accion: Accion };

type Aviso = { texto: string; ok: boolean };

/** Un 401 en cualquier pedido de la página: se va a /entrar. */
class SinSesion extends Error {}

function exigirSesion(response: Response) {
  if (response.status === ESTADO.sinSesion) throw new SinSesion();
}

export function Padron() {
  const id = useId();
  const navegar = useNavigate();
  const [pantalla, setPantalla] = useState<Pantalla>({ tipo: "cargando" });
  const { edificios, consorcio, setConsorcio } = useEdificioElegido(
    pantalla.tipo === "lista" ? pantalla.consorcios : SIN_EDIFICIOS,
    REQUISITOS.padron,
  );
  const puede = usePuede(consorcio);
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [abierto, setAbierto] = useState<Abierto | null>(null);
  const [texto, setTexto] = useState("");
  const [confirmarFin, setConfirmarFin] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  // Cada cambio vuelve a pedir el padrón: lo que se ve lo decide la API, no la pantalla.
  const [vuelta, setVuelta] = useState(0);
  const consultado = useRef(false);
  // Una clave por intento lógico: se conserva si el pedido se cae (el reintento recibe la misma
  // respuesta, sin repetir el efecto) y se renueva al cambiar el texto o el vínculo.
  const clave = useRef<string | null>(null);
  const campo = useRef<HTMLInputElement | null>(null);
  const confirmarSi = useRef<HTMLButtonElement | null>(null);
  const textoAviso = useRef<HTMLParagraphElement | null>(null);
  // Al terminar un pedido la fila puede cambiar o desaparecer: el foco va al aviso del resultado.
  const alAviso = useRef(false);

  useEffect(() => {
    if (abierto !== null && !confirmarFin) campo.current?.focus();
  }, [abierto, confirmarFin]);

  useEffect(() => {
    if (confirmarFin) confirmarSi.current?.focus();
  }, [confirmarFin]);

  // Va después de los dos anteriores: si la API rechaza la finalización, volver del paso de
  // confirmación reenfoca el campo en el mismo commit, y el foco tiene que quedar en el aviso.
  useEffect(() => {
    if (enviando || !alAviso.current) return;
    alAviso.current = false;
    textoAviso.current?.focus();
  }, [enviando]);

  const aEntrar = useAEntrar();

  const consultarEdificios = useCallback(() => {
    void api
      .GET("/api/v1/me/consorcios")
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data === undefined)
          setPantalla({ tipo: "sin_servicio", deConexion: esDeConexion(response.status, codigoDe(error)) });
        else if (data.length === 0) setPantalla({ tipo: "sin_consorcios" });
        else setPantalla({ tipo: "lista", consorcios: data });
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion) aEntrar();
        else setPantalla({ tipo: "sin_servicio", deConexion: true });
      });
  }, [aEntrar]);

  useEffect(() => {
    if (consultado.current) return;
    consultado.current = true;
    consultarEdificios();
  }, [consultarEdificios]);

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/padron", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        // E35: un padrón pedido antes de un cambio puede llegar después y mostrar el estado viejo.
        if (!vigente) return;
        if (data !== undefined) setLectura({ tipo: "ok", filas: data.elementos });
        else if (response.status === ESTADO.sinPermiso) navegar(RUTA_INICIO, { replace: true });
        else {
          setLectura({ tipo: "sin_servicio" });
          // Si falla la recarga que sigue a un cambio, queda a la vista lo que sí se hizo.
          setAviso(sinPisarLoHecho(mensajeDeFalla(response.status, codigoDe(error))));
        }
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso(sinPisarLoHecho(SIN_CONEXION));
        }
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, vuelta, aEntrar, navegar]);

  /** Vuelve a pedir el padrón que no se pudo consultar; un «Listo» que esté a la vista queda. */
  function reconsultar() {
    setAviso((actual) => (actual?.ok ? actual : null));
    setLectura({ tipo: "cargando" });
    setVuelta((v) => v + 1);
  }

  useAlVolverLaRed(lectura.tipo === "sin_servicio", () => {
    reconsultar();
    setAviso(conLaRedDeVuelta);
  });
  useAlVolverLaRed(pantalla.tipo === "sin_servicio", () => {
    setPantalla({ tipo: "cargando" });
    consultarEdificios();
  });

  function abrir(fila: Fila, accion: Accion) {
    clave.current = null;
    setTexto("");
    setConfirmarFin(false);
    setAviso(null);
    setAbierto({ fila, accion });
  }

  function cerrar() {
    clave.current = null;
    setTexto("");
    setConfirmarFin(false);
    setAbierto(null);
  }

  function enviar(evento: FormEvent) {
    evento.preventDefault();
    if (abierto === null) return;
    // Finalizar no se puede deshacer: el formulario primero pide la confirmación explícita.
    if (abierto.accion === "finalizar" && !confirmarFin) {
      setConfirmarFin(true);
      return;
    }
    pedir(abierto);
  }

  function pedir({ fila, accion }: Abierto) {
    clave.current ??= crypto.randomUUID();
    const camino = { consorcio_id: consorcio, vinculo_id: fila.vinculo_id };
    const params = { path: camino, header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current } };
    const valor = texto.trim();
    alAviso.current = true;
    setEnviando(true);
    setAviso(null);
    const pedido =
      accion === "confirmar"
        ? api.POST("/api/v1/consorcios/{consorcio_id}/vinculos/{vinculo_id}/confirmar", {
            params,
            headers: encabezadoCsrf(),
            body: { referencia: valor },
          })
        : api.POST("/api/v1/consorcios/{consorcio_id}/vinculos/{vinculo_id}/finalizar", {
            params,
            headers: encabezadoCsrf(),
            body: { motivo: valor },
          });
    void pedido
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data === undefined) {
          const codigo = codigoDe(error);
          if (renovarClave(response.status, codigo)) clave.current = null;
          setAviso({ texto: mensajeDeError(response.status, codigo, accion), ok: false });
          setConfirmarFin(false);
          if (cambioElPadron(response.status, codigo)) {
            cerrar();
            setVuelta((v) => v + 1);
          }
          return;
        }
        cerrar();
        if (accion === "confirmar") {
          setLectura((actual) =>
            actual.tipo === "ok"
              ? { tipo: "ok", filas: actual.filas.map((f) => (f.vinculo_id === fila.vinculo_id ? { ...f, confirmado: true } : f)) }
              : actual,
          );
          setAviso({ texto: `Listo: confirmamos el vínculo de ${fila.nombre} con la unidad ${fila.designacion}.`, ok: true });
        } else {
          // Un vínculo finalizado sale del padrón (solo lista vigentes y futuros).
          setLectura((actual) =>
            actual.tipo === "ok" ? { tipo: "ok", filas: actual.filas.filter((f) => f.vinculo_id !== fila.vinculo_id) } : actual,
          );
          const fin = "vigente_hasta" in data ? data.vigente_hasta : null;
          setAviso({
            texto: `Listo: finalizamos el vínculo de ${fila.nombre} con la unidad ${fila.designacion}.${fin === null ? "" : ` Desde el ${dia(fin)} ya no vale.`}`,
            ok: true,
          });
        }
        setVuelta((v) => v + 1);
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion) aEntrar();
        else setAviso({ texto: SIN_CONEXION, ok: false });
      })
      .finally(() => setEnviando(false));
  }

  // Un rechazo con el formulario abierto va junto a él, para corregirlo sin recorrer la página.
  const avisoEnElFormulario = abierto !== null && lectura.tipo === "ok" && aviso !== null && !aviso.ok;
  const conMensaje = (aviso !== null && !avisoEnElFormulario) || pantalla.tipo !== "lista";

  return (
    <>
      <h1>Padrón del edificio</h1>

      <div role="status" aria-live="polite" className={conMensaje ? "tarjeta" : undefined}>
        {pantalla.tipo === "cargando" && <p>Consultando…</p>}
        {pantalla.tipo === "sin_servicio" && (
          <>
            <p className="aviso-error">
              {pantalla.deConexion
                ? "No pudimos consultar tus edificios. Revisá la conexión y volvé en un rato."
                : `No pudimos consultar tus edificios. ${SERVICIO_NO_DISPONIBLE}`}
            </p>
            <button
              type="button"
              onClick={() => {
                setPantalla({ tipo: "cargando" });
                consultarEdificios();
              }}
            >
              Consultar de nuevo
            </button>
          </>
        )}
        {pantalla.tipo === "sin_consorcios" && <p>Todavía no participás de ningún edificio.</p>}
        {aviso && !avisoEnElFormulario && (
          <p ref={textoAviso} tabIndex={-1} className={aviso.ok ? "aviso-ok" : "aviso-error"}>
            {aviso.texto}
          </p>
        )}
      </div>

      {pantalla.tipo === "lista" && (
        <>
          <p className="tarjeta">
            El padrón muestra quién tiene un vínculo vigente o futuro con cada unidad. No muestra
            correos ni teléfonos.
            {puede("padron.confirmar_vinculo") &&
              " Confirmar acredita el carácter sobre la unidad según el título; no acredita la identidad."}
          </p>

          {edificios.length > 1 && (
            <div className="formulario formulario-movil tarjeta">
              <label htmlFor={`${id}-consorcio`}>Edificio</label>
              <select
                id={`${id}-consorcio`}
                value={consorcio}
                disabled={enviando}
                onChange={(e) => {
                  // Nada del edificio anterior queda a la vista mientras llega el del nuevo.
                  cerrar();
                  setAviso(null);
                  setLectura({ tipo: "cargando" });
                  setConsorcio(e.target.value);
                }}
              >
                {edificios.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.nombre}
                  </option>
                ))}
              </select>
            </div>
          )}

          <section aria-labelledby={`${id}-padron`} className="tarjeta">
            <h2 id={`${id}-padron`}>Vínculos por unidad</h2>
            {lectura.tipo === "cargando" && <p>Consultando…</p>}
            {lectura.tipo === "sin_servicio" && (
              <>
                <p>No pudimos mostrar el padrón.</p>
                <button type="button" onClick={reconsultar} disabled={enviando}>
                  Consultar de nuevo
                </button>
              </>
            )}
            {lectura.tipo === "ok" && lectura.filas.length === 0 && <p>El padrón no tiene vínculos vigentes ni futuros.</p>}
            {lectura.tipo === "ok" && lectura.filas.length > 0 && (
              <ul className="sesiones" aria-label="Padrón">
                {(puede("padron.confirmar_vinculo") ? sinConfirmarPrimero(lectura.filas) : lectura.filas).map((f) => tarjeta(f))}
              </ul>
            )}
          </section>
        </>
      )}
    </>
  );

  /** Un vínculo del padrón, con sus acciones según el estado (la API decide si se puede). */
  function tarjeta(f: Fila) {
    const titulo = `${id}-${f.vinculo_id}`;
    // Si ya empezó lo dice la API con la fecha del consorcio, la misma con la que decide el fin.
    const futuro = !f.iniciado;
    const esteAbierto = abierto?.fila.vinculo_id === f.vinculo_id ? abierto.accion : null;
    const confirmable = f.confirmable_por_vos;
    const finalizable = !futuro && puede("padron.gestionar");
    return (
      <li key={f.vinculo_id}>
        <h3 id={titulo}>
          {f.designacion}: {f.nombre}
        </h3>
        <dl className="detalles">
          <dt>Unidad</dt>
          <dd>{f.designacion}</dd>
          <dt>Carácter</dt>
          <dd>{TEXTO_TIPO[f.tipo]}</dd>
          <dt>Vigencia</dt>
          <dd>
            {vigencia(f)}
            {futuro && " (todavía no empezó)"}
          </dd>
          <dt>Confirmación</dt>
          <dd>{f.confirmado ? "Confirmado con el título" : "Sin confirmar"}</dd>
        </dl>
        {esteAbierto === null ? (
          (confirmable || finalizable) && (
          <p>
            {confirmable && (
              <button type="button" className="secundario" onClick={() => abrir(f, "confirmar")} disabled={enviando} aria-describedby={titulo}>
                Confirmar
              </button>
            )}
            {confirmable && finalizable && " "}
            {finalizable && (
              <button type="button" className="secundario" onClick={() => abrir(f, "finalizar")} disabled={enviando} aria-describedby={titulo}>
                Finalizar
              </button>
            )}
          </p>
          )
        ) : (
          formulario(f, esteAbierto, titulo)
        )}
      </li>
    );
  }

  function formulario(f: Fila, accion: Accion, titulo: string) {
    const confirmar = accion === "confirmar";
    const ayuda = `${titulo}-ayuda`;
    const pregunta = `${titulo}-pregunta`;
    return (
      <form className="formulario formulario-movil" onSubmit={enviar} aria-labelledby={`${titulo}-accion`}>
        <h4 id={`${titulo}-accion`}>{confirmar ? "Confirmar el vínculo" : "Finalizar el vínculo"}</h4>
        <p id={ayuda}>
          {confirmar
            ? "Confirmar acredita el carácter sobre la unidad según el título (escritura, contrato de locación u otro), no la identidad de la persona. La referencia queda guardada en el vínculo."
            : "Finalizar cierra la vigencia hoy y conserva el vínculo con quién, cuándo y por qué. La persona pierde el acceso que le daba este vínculo. No se puede deshacer."}
        </p>
        <label htmlFor={`${titulo}-texto`}>{confirmar ? "Referencia del título" : "Motivo"}</label>
        <input
          id={`${titulo}-texto`}
          ref={campo}
          type="text"
          autoComplete="off"
          maxLength={LARGO_TEXTO}
          pattern={PATRON_TEXTO}
          required
          disabled={enviando || confirmarFin}
          aria-describedby={ayuda}
          value={texto}
          onChange={(e) => {
            clave.current = null;
            setTexto(e.target.value);
          }}
        />
        {avisoEnElFormulario && (
          <p ref={textoAviso} tabIndex={-1} role="alert" className="aviso-error">
            {aviso.texto}
          </p>
        )}
        {confirmarFin ? (
          <div role="group" aria-labelledby={pregunta}>
            <p id={pregunta}>
              ¿Finalizar el vínculo de {f.nombre} con la unidad {f.designacion}? No se puede deshacer.
            </p>
            <button type="submit" ref={confirmarSi} disabled={enviando}>
              Sí, finalizar
            </button>{" "}
            <button type="button" className="secundario" onClick={() => setConfirmarFin(false)} disabled={enviando}>
              No
            </button>
          </div>
        ) : (
          <p>
            <button type="submit" disabled={enviando}>
              {confirmar ? "Confirmar el vínculo" : "Finalizar el vínculo"}
            </button>{" "}
            <button type="button" className="secundario" onClick={cerrar} disabled={enviando}>
              Cancelar
            </button>
          </p>
        )}
      </form>
    );
  }
}
