import { type FormEvent, useCallback, useEffect, useId, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { encabezadoCsrf } from "./csrf";
import { conLaRedDeVuelta, sinPisarLoHecho } from "./egresos/comun";
import { useAlVolverLaRed } from "./egresos/useConsorcio";
import { RUTA_INICIO } from "./enlace";
import { FichaDeRegistro, FilaDeRegistro, useFichas } from "./FichaDeRegistro";
import { useAEntrar } from "./sesionEncima";
import { codigoDe, esDeConexion, LIMITE_DE_PEDIDOS, mensajeDeFalla, SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "./fallas";
import { REQUISITOS, useEdificioElegido, usePuede } from "./permisos";

// Página /padron (H2b 2b-5, corte 4; doc/diseno/H2b_5_Diseno.md §5 del repositorio CEM): el padrón
// mínimo de D6 (unidad, nombre, carácter, vigencia y si está confirmado; nunca correo ni teléfono).
// Cada vínculo es una fila que abre su ficha (comentario 21), y la ficha ofrece solo lo que el rol
// permite: «Confirmar» en los que la API marca (`confirmable_por_vos`: nadie confirma su propio
// vínculo ni el que registró), «Finalizar» si ya empezó y hay `padron.gestionar`, y «Anular» si
// todavía no empezó, no está confirmado y hay `padron.gestionar` (`anular` en padron.py: uno
// confirmado lo anula el equipo). No hay lectura de un vínculo suelto ni forma de modificarlo: la
// ficha muestra la fila de la lista. Si no puede leer el padrón va al inicio. Es solo interfaz: la
// API decide cada pedido y la base vuelve a controlar, y un 403 al actuar se dice acá.

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
  propietario: "Propietario/a",
  inquilino: "Inquilino/a",
  ocupante: "Ocupante",
};

type Accion = "confirmar" | "finalizar" | "anular";

/** Lo que dice cada acción: el botón de la ficha, el formulario y la pregunta antes de enviar. */
const TEXTO_ACCION: Record<Accion, { boton: string; titulo: string; ayuda: string; campo: string; si: string; hecho: string }> = {
  confirmar: {
    boton: "Confirmar",
    titulo: "Confirmar el vínculo",
    ayuda:
      "Confirmar acredita el carácter sobre la unidad según el título (escritura, contrato de locación u otro), no la identidad de la persona. La referencia queda guardada en el vínculo.",
    campo: "Referencia del título",
    si: "",
    hecho: "confirmamos",
  },
  finalizar: {
    boton: "Finalizar",
    titulo: "Finalizar el vínculo",
    ayuda:
      "Finalizar cierra la vigencia hoy y conserva el vínculo con quién, cuándo y por qué. La persona pierde el acceso que le daba este vínculo. No se puede deshacer.",
    campo: "Motivo",
    si: "Sí, finalizar",
    hecho: "finalizamos",
  },
  anular: {
    boton: "Anular",
    titulo: "Anular el vínculo",
    ayuda:
      "Anular borra un vínculo cargado por error que todavía no empezó y no está confirmado. Queda constancia de la unidad, el carácter y las fechas; el motivo no queda en la auditoría. No se puede deshacer.",
    campo: "Motivo",
    si: "Sí, anular",
    hecho: "anulamos",
  },
};

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

const TEXTO_SIN_PERMISO: Record<Accion, string> = {
  confirmar: "No hay permiso para confirmar este vínculo: hace falta la facultad sobre la unidad.",
  finalizar: "No hay permiso para finalizar este vínculo: hace falta la facultad sobre la unidad.",
  anular: "No hay permiso para anular este vínculo: hace falta la facultad sobre la unidad.",
};

/** Qué decir ante un error de una acción, según el estado y el código del cuerpo uniforme. */
export function mensajeDeError(estado: number, codigo: string | undefined, accion: Accion): string {
  if (estado === ESTADO.sinPermiso && codigo === "csrf_invalido") return "No pudimos verificar el pedido. Hace falta recargar la página.";
  if (estado === ESTADO.sinPermiso) return TEXTO_SIN_PERMISO[accion];
  if (estado === ESTADO.noEncontrado) return "Ese vínculo ya no está en el padrón de este edificio.";
  if (estado === ESTADO.conflicto && codigo === "idempotencia_en_curso")
    return "El pedido se está procesando. Se puede probar de nuevo en unos segundos.";
  if (estado === ESTADO.conflicto && codigo === "vinculo_ya_confirmado") return "Ese vínculo ya estaba confirmado.";
  if (estado === ESTADO.conflicto && codigo === "vinculo_finalizado") return "Ese vínculo ya terminó.";
  if (estado === ESTADO.conflicto && codigo === "vinculo_no_iniciado")
    return "Ese vínculo todavía no empezó: no se puede finalizar.";
  if (estado === ESTADO.conflicto && codigo === "vinculo_iniciado") return "Ese vínculo ya empezó: no se anula, se finaliza.";
  if (estado === ESTADO.conflicto && codigo === "vinculo_confirmado")
    return "Ese vínculo está confirmado: lo anula el equipo de soporte, con el respaldo.";
  if (estado === ESTADO.conflicto) return "El pedido choca con el estado actual del padrón. La lista se actualizó.";
  if (estado === ESTADO.invalido && codigo === "idempotencia_reutilizada")
    return "Los datos cambiaron desde un intento anterior. Hay que volver a enviar el formulario.";
  if (estado === ESTADO.invalido)
    return `Hay que revisar ${accion === "confirmar" ? "la referencia" : "el motivo"}: hasta ${LARGO_TEXTO} caracteres, con texto visible y sin saltos de línea.`;
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
  const fichas = useFichas<string>();
  // La acción en curso dentro de la ficha abierta; nula, la ficha muestra sus datos y sus botones.
  const [accion, setAccion] = useState<Accion | null>(null);
  const [texto, setTexto] = useState("");
  // Finalizar y anular no se deshacen: antes de enviar se pregunta.
  const [preguntando, setPreguntando] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  // Cada cambio vuelve a pedir el padrón: lo que se ve lo decide la API, no la pantalla.
  const [vuelta, setVuelta] = useState(0);
  const consultado = useRef(false);
  // Una clave por intento lógico: se conserva si el pedido se cae (el reintento recibe la misma
  // respuesta, sin repetir el efecto) y se renueva al cambiar el texto o el vínculo.
  const clave = useRef<string | null>(null);
  const campo = useRef<HTMLInputElement | null>(null);
  const preguntaSi = useRef<HTMLButtonElement | null>(null);
  const textoAviso = useRef<HTMLParagraphElement | null>(null);
  // Al terminar un pedido la fila puede cambiar o desaparecer: el foco va al aviso del resultado.
  const alAviso = useRef(false);

  useEffect(() => {
    if (accion !== null && !preguntando) campo.current?.focus();
  }, [accion, preguntando]);

  useEffect(() => {
    if (preguntando) preguntaSi.current?.focus();
  }, [preguntando]);

  // Va después de los dos anteriores: si la API rechaza la finalización, volver de la pregunta
  // reenfoca el campo en el mismo commit, y el foco tiene que quedar en el aviso.
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

  /** Deja la ficha en sus datos y sus botones, sin lo escrito ni el rechazo del formulario. */
  function soloLaFicha() {
    clave.current = null;
    setTexto("");
    setPreguntando(false);
    setAccion(null);
    setAviso((actual) => (actual?.ok === false ? null : actual));
  }

  function abrirFicha(fila: Fila) {
    soloLaFicha();
    setAviso(null);
    fichas.abrir(fila.vinculo_id);
  }

  /** Cierra la ficha. Si la cierra un resultado, el foco va a su aviso y no a la fila. */
  function cerrarFicha(devolverFoco = true) {
    if (devolverFoco) soloLaFicha();
    else {
      clave.current = null;
      setTexto("");
      setPreguntando(false);
      setAccion(null);
    }
    fichas.cerrar(devolverFoco);
  }

  function abrirAccion(a: Accion) {
    clave.current = null;
    setTexto("");
    setPreguntando(false);
    setAviso(null);
    setAccion(a);
  }

  function enviar(evento: FormEvent, fila: Fila) {
    evento.preventDefault();
    if (accion === null) return;
    // Finalizar y anular no se pueden deshacer: el formulario primero pide la confirmación explícita.
    if (accion !== "confirmar" && !preguntando) {
      setPreguntando(true);
      return;
    }
    pedir(fila, accion);
  }

  function pedir(fila: Fila, hecha: Accion) {
    clave.current ??= crypto.randomUUID();
    const camino = { consorcio_id: consorcio, vinculo_id: fila.vinculo_id };
    const params = { path: camino, header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current } };
    const valor = texto.trim();
    alAviso.current = true;
    setEnviando(true);
    setAviso(null);
    const opciones = { params, headers: encabezadoCsrf() };
    const pedido =
      hecha === "confirmar"
        ? api.POST("/api/v1/consorcios/{consorcio_id}/vinculos/{vinculo_id}/confirmar", { ...opciones, body: { referencia: valor } })
        : hecha === "finalizar"
          ? api.POST("/api/v1/consorcios/{consorcio_id}/vinculos/{vinculo_id}/finalizar", { ...opciones, body: { motivo: valor } })
          : api.POST("/api/v1/consorcios/{consorcio_id}/vinculos/{vinculo_id}/anular", { ...opciones, body: { motivo: valor } });
    void pedido
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data === undefined) {
          const codigo = codigoDe(error);
          if (renovarClave(response.status, codigo)) clave.current = null;
          setAviso({ texto: mensajeDeError(response.status, codigo, hecha), ok: false });
          setPreguntando(false);
          if (cambioElPadron(response.status, codigo)) {
            cerrarFicha(false);
            setVuelta((v) => v + 1);
          }
          return;
        }
        cerrarFicha(false);
        const quien = `el vínculo de ${fila.nombre} con la unidad ${fila.designacion}`;
        if (hecha === "confirmar") {
          setLectura((actual) =>
            actual.tipo === "ok"
              ? { tipo: "ok", filas: actual.filas.map((f) => (f.vinculo_id === fila.vinculo_id ? { ...f, confirmado: true } : f)) }
              : actual,
          );
          setAviso({ texto: `Listo: confirmamos ${quien}.`, ok: true });
        } else {
          // Un vínculo finalizado o anulado sale del padrón (solo lista vigentes y futuros).
          setLectura((actual) =>
            actual.tipo === "ok" ? { tipo: "ok", filas: actual.filas.filter((f) => f.vinculo_id !== fila.vinculo_id) } : actual,
          );
          // Solo la finalización trae el fin de la vigencia; la anulación borra el vínculo.
          const fin = "vigente_hasta" in data && typeof data.vigente_hasta === "string" ? data.vigente_hasta : null;
          setAviso({
            texto: `Listo: ${TEXTO_ACCION[hecha].hecho} ${quien}.${fin === null ? "" : ` Desde el ${dia(fin)} ya no vale.`}`,
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

  const filas = lectura.tipo === "ok" ? lectura.filas : [];
  // La ficha muestra la fila de la lista vigente: si una recarga la saca, la ficha se va.
  const enFicha = filas.find((f) => f.vinculo_id === fichas.abierta);
  // Un rechazo con un formulario abierto va junto a él, dentro de la ficha, para corregirlo ahí.
  const avisoEnLaFicha = enFicha !== undefined && accion !== null && aviso !== null && !aviso.ok;
  const conMensaje = (aviso !== null && !avisoEnLaFicha) || pantalla.tipo !== "lista";

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
        {aviso && !avisoEnLaFicha && (
          <p ref={textoAviso} tabIndex={-1} className={aviso.ok ? "aviso-ok" : "aviso-error"}>
            {aviso.texto}
          </p>
        )}
      </div>

      {pantalla.tipo === "lista" && (
        <>
          <p className="tarjeta">
            El padrón muestra quién tiene un vínculo vigente o futuro con cada unidad. No muestra
            correos ni teléfonos. Cada vínculo abre su ficha.
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
                  cerrarFicha(false);
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
            {lectura.tipo === "ok" && filas.length === 0 && <p>El padrón no tiene vínculos vigentes ni futuros.</p>}
            {filas.length > 0 && (
              <ul className="filas-registro" aria-label="Padrón">
                {(puede("padron.confirmar_vinculo") ? sinConfirmarPrimero(filas) : filas).map((f) => (
                  <li key={f.vinculo_id}>
                    <FilaDeRegistro
                      titulo={titulo(f)}
                      resumen={resumen(f)}
                      alAbrir={() => abrirFicha(f)}
                      refFila={fichas.refFila(f.vinculo_id)}
                      abierta={fichas.abierta === f.vinculo_id}
                    />
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}

      {enFicha !== undefined && ficha(enFicha)}
    </>
  );

  function titulo(f: Fila) {
    return `${f.designacion}: ${f.nombre}`;
  }

  /** El estado en una línea: el carácter, si está confirmado y si todavía no empezó. */
  function resumen(f: Fila) {
    return [TEXTO_TIPO[f.tipo], f.confirmado ? "Confirmado" : "Sin confirmar", ...(f.iniciado ? [] : ["Todavía no empezó"])].join(" · ");
  }

  /** Las acciones que el rol permite sobre este vínculo (la API decide igual cada pedido). */
  function accionesDe(f: Fila): Accion[] {
    // Si ya empezó lo dice la API con la fecha del consorcio, la misma con la que decide el fin.
    const gestiona = puede("padron.gestionar");
    return [
      ...(f.confirmable_por_vos ? (["confirmar"] as const) : []),
      ...(gestiona && f.iniciado ? (["finalizar"] as const) : []),
      ...(gestiona && !f.iniciado && !f.confirmado ? (["anular"] as const) : []),
    ];
  }

  function ficha(f: Fila) {
    const acciones = accionesDe(f);
    return (
      <FichaDeRegistro
        titulo={titulo(f)}
        alCerrar={() => cerrarFicha()}
        ocupada={enviando}
        datos={[
          { etiqueta: "Unidad", valor: f.designacion },
          { etiqueta: "Persona", valor: f.nombre },
          { etiqueta: "Carácter", valor: TEXTO_TIPO[f.tipo] },
          { etiqueta: "Vigencia", valor: `${vigencia(f)}${f.iniciado ? "" : " (todavía no empezó)"}` },
          { etiqueta: "Confirmación", valor: f.confirmado ? "Confirmado con el título" : "Sin confirmar" },
        ]}
      >
        {accion === null
          ? acciones.length > 0 && (
              <p className="ficha-registro__acciones">
                {acciones.map((a) => (
                  <button key={a} type="button" className={a === "confirmar" ? "secundario" : "secundario peligro"} onClick={() => abrirAccion(a)}>
                    {TEXTO_ACCION[a].boton}
                  </button>
                ))}
              </p>
            )
          : acciones.includes(accion) && formulario(f, accion)}
      </FichaDeRegistro>
    );
  }

  function formulario(f: Fila, a: Accion) {
    const textos = TEXTO_ACCION[a];
    const base = `${id}-${f.vinculo_id}`;
    const ayuda = `${base}-ayuda`;
    const pregunta = `${base}-pregunta`;
    const confirmar = a === "confirmar";
    return (
      <form className="formulario formulario-movil" onSubmit={(e) => enviar(e, f)} aria-labelledby={`${base}-accion`}>
        <h3 id={`${base}-accion`}>{textos.titulo}</h3>
        <p id={ayuda}>{textos.ayuda}</p>
        <label htmlFor={`${base}-texto`}>{textos.campo}</label>
        <input
          id={`${base}-texto`}
          ref={campo}
          type="text"
          autoComplete="off"
          maxLength={LARGO_TEXTO}
          pattern={PATRON_TEXTO}
          required
          disabled={enviando || preguntando}
          aria-describedby={ayuda}
          value={texto}
          onChange={(e) => {
            clave.current = null;
            setTexto(e.target.value);
          }}
        />
        {avisoEnLaFicha && (
          <p ref={textoAviso} tabIndex={-1} role="alert" className="aviso-error">
            {aviso.texto}
          </p>
        )}
        {preguntando ? (
          <div role="group" aria-labelledby={pregunta}>
            <p id={pregunta}>
              ¿{textos.boton} el vínculo de {f.nombre} con la unidad {f.designacion}? No se puede deshacer.
            </p>
            <button type="submit" className="peligro" ref={preguntaSi} disabled={enviando}>
              {textos.si}
            </button>{" "}
            <button type="button" className="secundario" onClick={() => setPreguntando(false)} disabled={enviando}>
              No
            </button>
          </div>
        ) : (
          <p>
            <button type="submit" className={confirmar ? undefined : "peligro"} disabled={enviando}>
              {textos.titulo}
            </button>{" "}
            <button type="button" className="secundario peligro" onClick={soloLaFicha} disabled={enviando}>
              Cancelar
            </button>
          </p>
        )}
      </form>
    );
  }
}
