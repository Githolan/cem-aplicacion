import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { encabezadoCsrf } from "./csrf";
import { useAlVolverLaRed } from "./egresos/useConsorcio";
import { RUTA_INICIO } from "./enlace";
import { conLaRedDeVuelta, sinElAvisoDeLaRed } from "./egresos/comun";
import { ErrorDeCampo, type ErroresDeCampo, faltaCompletar, hayErrores, marcaDe, SIN_ERRORES, sinElDe } from "./ErrorDeCampo";
import { FichaDeRegistro, FilaDeRegistro, useFichas } from "./FichaDeRegistro";
import { useAEntrar } from "./sesionEncima";
import { codigoDe, esDeConexion, LIMITE_DE_PEDIDOS, mensajeDeFalla, motivoDeSeparacion, SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "./fallas";
import { REQUISITOS, useConsorcioDeLlegada, useEdificioElegido, usePuede } from "./permisos";
import { type FinDeEnvio, VentanaDeFormulario } from "./VentanaDeFormulario";

// Página /padron (H2b 2b-5, corte 4; doc/diseno/H2b_5_Diseno.md §5 del repositorio CEM): el padrón
// mínimo de D6 (unidad, nombre, carácter, vigencia y si está confirmado; nunca correo ni teléfono).
// Cada vínculo es una fila que abre su ficha (comentario 21), y la ficha ofrece solo lo que el rol
// permite: «Confirmar» en los que la API marca (`confirmable_por_vos`: nadie confirma su propio
// vínculo ni el que registró), «Finalizar» si ya empezó y hay `padron.gestionar` sobre su unidad, y
// «Anular» si todavía no empezó, no está confirmado y hay `padron.gestionar` sobre su unidad
// (`anular` en padron.py: uno confirmado lo anula el equipo). La facultad sobre la unidad la marca
// la API en cada fila (`gestionable_por_vos`, informe de fricción C8G-13): una facultad acotada a
// otras unidades no la alcanza, y sin ella no se ofrece (el rol no lo alcanza: sin línea, C7). No hay lectura de un vínculo suelto ni forma de modificarlo: la
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

/**
 * Lo que dice cada acción (PAD-03 y PAD-04 del informe de fricción): el botón de la ficha, el título
 * de su ventana, una sola línea de ayuda y el botón que envía. Finalizar y anular no hacen una
 * pregunta aparte: la ventana ya dice «No se puede deshacer.» y su botón es el «Sí, …».
 */
const TEXTO_ACCION: Record<
  Accion,
  { boton: string; titulo: string; ayuda: string; campo: string; ejemplo?: string; falta: string; enviar: string; hecho: string }
> = {
  confirmar: {
    boton: "Confirmar",
    titulo: "Confirmar el vínculo",
    ayuda: "Confirmar acredita el carácter sobre la unidad según el título, no la identidad de la persona.",
    campo: "Documento que lo prueba",
    ejemplo: "Por ejemplo: Escritura n.º 123 del 4/5/2020",
    falta: "Falta escribir el documento que lo prueba.",
    enviar: "Confirmar",
    hecho: "quedó confirmado",
  },
  finalizar: {
    boton: "Finalizar",
    titulo: "Finalizar el vínculo",
    ayuda: "La vigencia termina hoy y la persona pierde el acceso que le daba este vínculo. No se puede deshacer.",
    campo: "Motivo",
    falta: "Falta escribir el motivo.",
    enviar: "Sí, finalizar",
    hecho: "quedó finalizado",
  },
  anular: {
    boton: "Anular",
    titulo: "Anular el vínculo",
    ayuda: "Borra un vínculo cargado por error. No se puede deshacer.",
    campo: "Motivo",
    falta: "Falta escribir el motivo.",
    enviar: "Sí, anular",
    hecho: "quedó anulado",
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
  if (estado === ESTADO.sinPermiso && codigo === "csrf_invalido") return "La página quedó abierta mucho tiempo. Hay que recargarla y repetir el paso.";
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

/** Lo que esa cuenta puede confirmar va primero; después, el resto en el orden de la API. Un
 * vínculo sin confirmar que confirma otra persona no espera a quien mira (FL-PAD-01). */
export function confirmablesPrimero(filas: Fila[]): Fila[] {
  return [...filas.filter((f) => f.confirmable_por_vos), ...filas.filter((f) => !f.confirmable_por_vos)];
}

/** En la ficha de un vínculo sin confirmar que esa cuenta no confirma: por qué no hay «Confirmar». */
export const LO_CONFIRMA_OTRA_PERSONA = "Lo confirma otra persona: nadie confirma su propio vínculo ni el que registró.";

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

/** El padrón del edificio: llegó o no se pudo consultar, con el motivo (si la API lo niega, se va al inicio). */
type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio"; motivo: string } | { tipo: "ok"; filas: Fila[] };

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
  const llegada = useConsorcioDeLlegada();
  const { edificios, consorcio, setConsorcio } = useEdificioElegido(
    pantalla.tipo === "lista" ? pantalla.consorcios : SIN_EDIFICIOS,
    REQUISITOS.padron,
    // La fila del inicio abre el edificio de la fila (FL-AVI-03).
    llegada,
  );
  const puede = usePuede(consorcio);
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const fichas = useFichas<string>();
  // La acción abierta en su ventana, encima de la ficha; nula, la ficha muestra sus datos y sus botones.
  const [accion, setAccion] = useState<Accion | null>(null);
  const [texto, setTexto] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  /** El campo de la acción que falta completar (decisión 4: el botón queda activo y marca el campo). */
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  // Cada cambio vuelve a pedir el padrón: lo que se ve lo decide la API, no la pantalla.
  const [vuelta, setVuelta] = useState(0);
  const consultado = useRef(false);
  // Una clave por intento lógico: se conserva si el pedido se cae (el reintento recibe la misma
  // respuesta, sin repetir el efecto) y se renueva al cambiar el texto o el vínculo.
  const clave = useRef<string | null>(null);
  const textoAviso = useRef<HTMLParagraphElement | null>(null);
  // Al terminar un pedido la fila puede cambiar o desaparecer: el foco va al aviso del resultado.
  const alAviso = useRef(false);

  // El rechazo de una acción lo dice su ventana; acá, el resultado que cierra la ficha.
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
        else falloLaLectura(mensajeDeFalla(response.status, codigoDe(error)));
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else falloLaLectura(SIN_CONEXION);
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, vuelta, aEntrar, navegar]);

  /** PAD-06: el motivo va una sola vez, junto a «Consultar de nuevo»; el aviso de arriba queda para los «Listo». */
  function falloLaLectura(motivo: string) {
    setLectura({ tipo: "sin_servicio", motivo });
    setAviso(sinElAvisoDeLaRed);
  }

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
    setErrores(SIN_ERRORES);
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
      setErrores(SIN_ERRORES);
      setAccion(null);
    }
    fichas.cerrar(devolverFoco);
  }

  function abrirAccion(a: Accion) {
    clave.current = null;
    setTexto("");
    setErrores(SIN_ERRORES);
    setAviso(null);
    setAccion(a);
  }

  /** El botón de la ventana: lo que falta se marca; si está completo, se manda en un solo paso (PAD-04). */
  function enviar(fila: Fila, a: Accion): FinDeEnvio | Promise<FinDeEnvio> {
    const faltan = faltaCompletar({ texto: [texto, TEXTO_ACCION[a].falta] });
    if (hayErrores(faltan)) {
      setErrores(faltan);
      return "seguir";
    }
    return pedir(fila, a);
  }

  /** Manda la acción. Un rechazo queda en la ventana; si la lista quedó vieja, se cierra la ficha. */
  async function pedir(fila: Fila, hecha: Accion): Promise<FinDeEnvio> {
    clave.current ??= crypto.randomUUID();
    const camino = { consorcio_id: consorcio, vinculo_id: fila.vinculo_id };
    const params = { path: camino, header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current } };
    const valor = texto.trim();
    setEnviando(true);
    setAviso(null);
    const opciones = { params, headers: encabezadoCsrf() };
    try {
      const { data, error, response } = await (hecha === "confirmar"
        ? api.POST("/api/v1/consorcios/{consorcio_id}/vinculos/{vinculo_id}/confirmar", { ...opciones, body: { referencia: valor } })
        : hecha === "finalizar"
          ? api.POST("/api/v1/consorcios/{consorcio_id}/vinculos/{vinculo_id}/finalizar", { ...opciones, body: { motivo: valor } })
          : api.POST("/api/v1/consorcios/{consorcio_id}/vinculos/{vinculo_id}/anular", { ...opciones, body: { motivo: valor } }));
      exigirSesion(response);
      if (data === undefined) {
        const codigo = codigoDe(error);
        if (renovarClave(response.status, codigo)) clave.current = null;
        const motivo = motivoDeSeparacion(error) ?? mensajeDeError(response.status, codigo, hecha);
        if (!cambioElPadron(response.status, codigo)) return { rechazo: motivo };
        // La lista quedó vieja: la ficha se cierra y el motivo va arriba, con la lista nueva.
        alAviso.current = true;
        setAviso({ texto: motivo, ok: false });
        cerrarFicha(false);
        setVuelta((v) => v + 1);
        return "cerrar";
      }
      alAviso.current = true;
      cerrarFicha(false);
      const quien = `el vínculo de ${fila.nombre} con la unidad ${fila.designacion}`;
      if (hecha === "confirmar") {
        setLectura((actual) =>
          actual.tipo === "ok"
            ? { tipo: "ok", filas: actual.filas.map((f) => (f.vinculo_id === fila.vinculo_id ? { ...f, confirmado: true } : f)) }
            : actual,
        );
        setAviso({ texto: `Listo: quedó confirmado ${quien}.`, ok: true });
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
      return "cerrar";
    } catch (e: unknown) {
      if (e instanceof SinSesion) {
        // La sesión se pide encima: lo escrito queda en la ventana para repetir el paso.
        aEntrar();
        return "seguir";
      }
      return { rechazo: SIN_CONEXION };
    } finally {
      setEnviando(false);
    }
  }

  const filas = lectura.tipo === "ok" ? lectura.filas : [];
  // La ficha muestra la fila de la lista vigente: si una recarga la saca, la ficha se va.
  const enFicha = filas.find((f) => f.vinculo_id === fichas.abierta);
  const conMensaje = aviso !== null || pantalla.tipo !== "lista";

  return (
    <>
      <h1>Padrón del edificio</h1>

      <div role="status" aria-live="polite" className={conMensaje ? "tarjeta" : undefined}>
        {pantalla.tipo === "cargando" && <p>Consultando…</p>}
        {pantalla.tipo === "sin_servicio" && (
          <>
            <p className="aviso-error">
              {pantalla.deConexion
                ? "No se pudieron consultar los edificios. Hay que revisar la conexión y volver en un rato."
                : `No se pudieron consultar los edificios. ${SERVICIO_NO_DISPONIBLE}`}
            </p>
            <button className="secundario"
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
        {pantalla.tipo === "sin_consorcios" && <p>Esta cuenta todavía no participa de ningún edificio.</p>}
        {aviso && (
          <p ref={textoAviso} tabIndex={-1} className={aviso.ok ? "aviso-ok" : "aviso-error"}>
            {aviso.texto}
          </p>
        )}
      </div>

      {pantalla.tipo === "lista" && (
        <>
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

          <section aria-label="Vínculos por unidad" className="tarjeta">
            {lectura.tipo === "cargando" && <p>Consultando…</p>}
            {lectura.tipo === "sin_servicio" && (
              <>
                <p className="aviso-error">No se pudo mostrar el padrón. {lectura.motivo}</p>
                <button className="secundario" type="button" onClick={reconsultar} disabled={enviando}>
                  Consultar de nuevo
                </button>
              </>
            )}
            {lectura.tipo === "ok" && filas.length === 0 && <p>El padrón no tiene vínculos vigentes ni futuros.</p>}
            {filas.length > 0 && (
              <ul className="filas-registro" aria-label="Padrón">
                {(puede("padron.confirmar_vinculo") ? confirmablesPrimero(filas) : filas).map((f) => (
                  <li key={f.vinculo_id}>
                    <FilaDeRegistro
                      titulo={titulo(f)}
                      resumen={resumen(f)}
                      alAbrir={() => abrirFicha(f)}
                      refFila={fichas.refFila(f.vinculo_id)}
                      abierta={fichas.abierta === f.vinculo_id}
                      deshabilitada={enviando}
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
    // Si ya empezó lo dice la API con la fecha del consorcio, la misma con la que decide el fin; y si
    // la facultad alcanza esta unidad, con la misma decisión que finalizar y anular (C8G-13).
    const gestiona = f.gestionable_por_vos;
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
          { etiqueta: "Tipo", valor: TEXTO_TIPO[f.tipo] },
          { etiqueta: "Vigencia", valor: `${vigencia(f)}${f.iniciado ? "" : " (todavía no empezó)"}` },
          { etiqueta: "Confirmación", valor: f.confirmado ? "Confirmado con el título" : "Sin confirmar" },
        ]}
        ventana={accion !== null && acciones.includes(accion) && ventana(f, accion)}
      >
        {!f.confirmado && !f.confirmable_por_vos && puede("padron.confirmar_vinculo") && <p>{LO_CONFIRMA_OTRA_PERSONA}</p>}
        {acciones.length > 0 && (
          <p className="ficha-registro__acciones">
            {acciones.map((a) => (
              <button key={a} type="button" className={a === "confirmar" ? undefined : "secundario peligro"} onClick={() => abrirAccion(a)}>
                {TEXTO_ACCION[a].boton}
              </button>
            ))}
          </p>
        )}
      </FichaDeRegistro>
    );
  }

  /** La acción en su ventana, encima de la ficha: el campo, el botón y «Cancelar». */
  function ventana(f: Fila, a: Accion) {
    const textos = TEXTO_ACCION[a];
    const base = `${id}-${f.vinculo_id}`;
    const ayuda = `${base}-ayuda`;
    const idCampo = `${base}-texto`;
    return (
      <VentanaDeFormulario
        titulo={textos.titulo}
        textoEnviar={textos.enviar}
        peligro={a !== "confirmar"}
        alEnviar={() => enviar(f, a)}
        alCerrar={soloLaFicha}
      >
        <p id={ayuda}>{textos.ayuda}</p>
        <label htmlFor={idCampo}>{textos.campo}</label>
        <input
          id={idCampo}
          data-foco
          type="text"
          autoComplete="off"
          maxLength={LARGO_TEXTO}
          pattern={PATRON_TEXTO}
          required
          placeholder={textos.ejemplo}
          {...marcaDe(idCampo, errores.texto, ayuda)}
          value={texto}
          onChange={(e) => {
            clave.current = null;
            setErrores((actuales) => sinElDe(actuales, "texto"));
            setTexto(e.target.value);
          }}
        />
        <ErrorDeCampo idCampo={idCampo} mensaje={errores.texto} />
      </VentanaDeFormulario>
    );
  }
}
