import { type FormEvent, useCallback, useEffect, useId, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { borrarCopias } from "./copiaContactos";
import { encabezadoCsrf } from "./csrf";
import { RUTA_INICIO } from "./enlace";
import { useAEntrar } from "./sesionEncima";
import { codigoDe, esDeConexion, LIMITE_DE_PEDIDOS, mensajeDeFalla, SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "./fallas";
import { REQUISITOS, useEdificioElegido } from "./permisos";

// Página /contactos/gestion (H2b 2b-7c; doc/diseno/H2b_7_Diseno.md §3 y §4 del repositorio CEM):
// la lista de gestión de los contactos de emergencia, con los futuros, la observación y la
// constancia del consentimiento, y los formularios para agregar, corregir, retirar, verificar y
// señalar. Quien no alcanza `contactos.gestionar` no llega acá: la dirección y una lectura negada
// llevan al inicio. Es solo interfaz: la API decide cada pedido, la base vuelve a controlar y un
// 403 al actuar se dice en la pantalla. Esta lista nunca se guarda en el dispositivo (§5): la copia
// sin conexión es solo la de /contactos.

type Consorcio = components["schemas"]["ConsorcioResumen"];
type Contacto = components["schemas"]["ContactoGestion"];
type Funcion = components["schemas"]["FuncionContacto"];
type Naturaleza = components["schemas"]["NaturalezaContacto"];

export const RUTA_CONTACTOS_GESTION = "/contactos/gestion";

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
/** Los mismos topes que el contrato (`LARGO_MAXIMO_NOMBRE`, `LARGO_MAXIMO_TEXTO`, `FORMATO_TELEFONO`). */
const LARGO_NOMBRE = 120;
const LARGO_TEXTO = 300;
// Con la marca `v` que el navegador aplica a `pattern`, los paréntesis y el guion van escapados.
export const PATRON_TELEFONO = "[+0-9][0-9 \\(\\)\\-]{2,29}";
/** Un texto con algo más que espacios. */
const PATRON_TEXTO = ".*\\S.*";

export const TEXTO_FUNCION: Record<Funcion, string> = {
  administracion: "Administración",
  encargado: "Encargado o encargada",
  emergencias: "Emergencias",
  aseguradora: "Aseguradora",
  proveedor_critico: "Proveedor crítico",
  servicio_publico: "Servicio público",
  otro: "Otro",
};

const TEXTO_NATURALEZA: Record<Naturaleza, string> = {
  empresa: "Empresa",
  servicio: "Servicio",
  organismo: "Organismo",
  profesional: "Profesional (con su número profesional y la constancia del consentimiento)",
};

const TEXTO_CAMPO: Record<string, string> = {
  nombre: "el nombre",
  telefono: "el teléfono",
  indicaciones: "las indicaciones",
  consentimiento_referencia: "la constancia del consentimiento",
  vigente_desde: "la fecha de inicio",
  motivo: "el motivo",
};

type Accion = "agregar" | "corregir" | "retirar" | "verificar" | "senalar";

const FORMATO_DIA = new Intl.DateTimeFormat("es-AR", { dateStyle: "long", timeZone: "UTC" });
const FORMATO_MOMENTO = new Intl.DateTimeFormat("es-AR", { dateStyle: "long", timeStyle: "short" });
const MS_POR_DIA = 86_400_000;
const dia = (iso: string, corrimiento = 0) =>
  FORMATO_DIA.format(new Date(Date.parse(`${iso}T00:00:00Z`) + corrimiento * MS_POR_DIA));
const momento = (iso: string) => FORMATO_MOMENTO.format(new Date(iso));

/** La vigencia en palabras. `vigente_hasta` es exclusivo: el último día es el anterior. */
function vigencia(c: Contacto): string {
  const desde = `Desde el ${dia(c.vigente_desde)}`;
  return c.vigente_hasta === null ? `${desde}, sin fecha de fin` : `${desde}; último día: ${dia(c.vigente_hasta, -1)}`;
}

/** Los campos que la API marcó en un 422 (`detalle.campos`, p. ej. `body.nombre`). */
function camposDe(error: unknown): string[] {
  const campos = (error as { detalle?: { campos?: unknown } } | undefined)?.detalle?.campos;
  return Array.isArray(campos) ? campos.filter((c): c is string => typeof c === "string") : [];
}

/** Qué decir ante un error de una acción, según el estado, el código y los campos del 422. */
export function mensajeDeError(estado: number, codigo: string | undefined, accion: Accion, campos: string[] = []): string {
  if (estado === ESTADO.sinPermiso && codigo === "csrf_invalido") return "No pudimos verificar el pedido. Recargá la página.";
  if (estado === ESTADO.sinPermiso)
    return "No tenés permiso para gestionar los contactos de este edificio: hace falta la facultad de la administración o del Consejo.";
  if (estado === ESTADO.noEncontrado) return "Ese contacto ya no está en este edificio.";
  if (estado === ESTADO.conflicto && codigo === "idempotencia_en_curso")
    return "El pedido se está procesando. Probá de nuevo en unos segundos.";
  if (estado === ESTADO.conflicto && codigo === "contacto_terminado")
    return "Ese contacto ya no está vigente: para cambiarlo, agregá uno nuevo.";
  if (estado === ESTADO.conflicto) return "El pedido choca con el estado actual de la lista. Revisá la lista actualizada.";
  if (estado === ESTADO.invalido && codigo === "idempotencia_reutilizada")
    return "Cambiaste los datos de un intento anterior. Volvé a enviar el formulario.";
  if (estado === ESTADO.invalido) {
    const nombres = [...new Set(campos.map((c) => TEXTO_CAMPO[c.split(".").pop() ?? ""]).filter((t) => t !== undefined))];
    const que = nombres.length > 0 ? nombres.join(", ") : accion === "senalar" ? "el motivo" : "los datos";
    return `Revisá ${que}. Los teléfonos y correos van solo en «Teléfono», nunca en el nombre, las indicaciones ni el motivo; un profesional va con su teléfono y la constancia del consentimiento.`;
  }
  if (estado === ESTADO.limite) return LIMITE_DE_PEDIDOS;
  return mensajeDeFalla(estado, codigo);
}

/** Si el intento terminó y el próximo lleva otra clave (igual que en /padron). */
function renovarClave(estado: number, codigo: string | undefined): boolean {
  if (codigo === "idempotencia_reutilizada") return true;
  return estado < ESTADO.errorDelServidor && codigo !== "idempotencia_en_curso";
}

/** Una respuesta que deja la lista vieja: el contacto cambió o ya no está. */
function cambioLaLista(estado: number, codigo: string | undefined): boolean {
  return estado === ESTADO.noEncontrado || (estado === ESTADO.conflicto && codigo !== "idempotencia_en_curso");
}

/** El contacto ya no se puede corregir: no está, o terminó su vigencia. */
function yaNoEsta(estado: number, codigo: string | undefined): boolean {
  return estado === ESTADO.noEncontrado || codigo === "contacto_terminado";
}

type Datos = {
  funcion: Funcion;
  naturaleza: Naturaleza;
  nombre: string;
  telefono: string;
  consentimiento: string;
  indicaciones: string;
  desde: string;
};

const VACIO: Datos = {
  funcion: "emergencias",
  naturaleza: "empresa",
  nombre: "",
  telefono: "",
  consentimiento: "",
  indicaciones: "",
  desde: "",
};

function datosDe(c: Contacto): Datos {
  return {
    funcion: c.funcion,
    naturaleza: c.naturaleza,
    nombre: c.nombre,
    telefono: c.telefono ?? "",
    consentimiento: c.consentimiento_referencia ?? "",
    indicaciones: c.indicaciones ?? "",
    desde: "",
  };
}

/** Lo que va en el cuerpo: lo opcional vacío va nulo, y la constancia solo con un profesional. */
function cuerpo(d: Datos) {
  const opcional = (v: string) => (v.trim() === "" ? null : v.trim());
  return {
    funcion: d.funcion,
    naturaleza: d.naturaleza,
    nombre: d.nombre.trim(),
    telefono: opcional(d.telefono),
    consentimiento_referencia: d.naturaleza === "profesional" ? opcional(d.consentimiento) : null,
    indicaciones: opcional(d.indicaciones),
  };
}

type Pantalla =
  | { tipo: "cargando" }
  | { tipo: "sin_servicio"; deConexion: boolean }
  | { tipo: "sin_consorcios" }
  | { tipo: "lista"; consorcios: Consorcio[] };

const SIN_EDIFICIOS: readonly Consorcio[] = [];

type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; contactos: Contacto[] };

type Abierto = { accion: Accion; contacto: Contacto | null };

type Aviso = { texto: string; ok: boolean };

class SinSesion extends Error {}

function exigirSesion(response: Response) {
  if (response.status === ESTADO.sinSesion) throw new SinSesion();
}

export function ContactosGestion() {
  const id = useId();
  const navegar = useNavigate();
  const [pantalla, setPantalla] = useState<Pantalla>({ tipo: "cargando" });
  const { edificios, consorcio, setConsorcio } = useEdificioElegido(
    pantalla.tipo === "lista" ? pantalla.consorcios : SIN_EDIFICIOS,
    REQUISITOS.contactosGestion,
  );
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [abierto, setAbierto] = useState<Abierto | null>(null);
  const [datos, setDatos] = useState<Datos>(VACIO);
  const [motivo, setMotivo] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [vuelta, setVuelta] = useState(0);
  const consultado = useRef(false);
  // Una clave por intento lógico: se conserva si el pedido se cae y se renueva al cambiar los datos.
  const clave = useRef<string | null>(null);
  const primerCampo = useRef<HTMLElement | null>(null);
  const textoAviso = useRef<HTMLParagraphElement | null>(null);
  const alAviso = useRef(false);

  useEffect(() => {
    if (abierto !== null) primerCampo.current?.focus();
  }, [abierto]);

  useEffect(() => {
    if (enviando || !alAviso.current) return;
    alAviso.current = false;
    textoAviso.current?.focus();
  }, [enviando]);

  // Sin sesión no queda ninguna copia de /contactos, igual que allá y en /cuenta.
  const aEntrar = useAEntrar(borrarCopias);

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
      .GET("/api/v1/consorcios/{consorcio_id}/contactos/gestion", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        // Una lista pedida antes de un cambio, o de otro edificio, puede llegar después.
        if (!vigente) return;
        if (data !== undefined) setLectura({ tipo: "ok", contactos: data.elementos });
        else if (response.status === ESTADO.sinPermiso) navegar(RUTA_INICIO, { replace: true });
        else {
          setLectura({ tipo: "sin_servicio" });
          const falla = mensajeDeFalla(response.status, codigoDe(error));
          setAviso((actual) => (actual?.ok ? actual : { texto: falla, ok: false }));
        }
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso((actual) => (actual?.ok ? actual : { texto: SIN_CONEXION, ok: false }));
        }
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, vuelta, aEntrar, navegar]);

  // Mientras llega la lista nueva, la vieja no queda a mano: un formulario abierto con sus datos
  // mandaría un PUT completo que deshace el cambio recién hecho.
  function volverAPedir() {
    setLectura({ tipo: "cargando" });
    setVuelta((v) => v + 1);
  }

  /** Vuelve a pedir la lista que no se pudo consultar; un «Listo» que esté a la vista queda. */
  function reconsultar() {
    setAviso((actual) => (actual?.ok ? actual : null));
    volverAPedir();
  }

  function abrir(accion: Accion, contacto: Contacto | null) {
    clave.current = null;
    setDatos(contacto === null ? VACIO : datosDe(contacto));
    setMotivo("");
    setAviso(null);
    setAbierto({ accion, contacto });
  }

  function cerrar() {
    clave.current = null;
    setAbierto(null);
  }

  function cambiar<K extends keyof Datos>(campo: K, valor: Datos[K]) {
    clave.current = null;
    setDatos((d) => ({ ...d, [campo]: valor }));
  }

  function enviar(evento: FormEvent) {
    evento.preventDefault();
    if (abierto !== null) pedir(abierto);
  }

  function pedir({ accion, contacto }: Abierto) {
    clave.current ??= crypto.randomUUID();
    const encabezado = { [ENCABEZADO_IDEMPOTENCIA]: clave.current };
    const base = { consorcio_id: consorcio };
    const deContacto = { ...base, contacto_id: contacto?.id ?? "" };
    alAviso.current = true;
    setEnviando(true);
    setAviso(null);
    const opciones = { headers: encabezadoCsrf() };
    let pedido;
    switch (accion) {
      case "agregar":
        pedido = api.POST("/api/v1/consorcios/{consorcio_id}/contactos", {
          ...opciones,
          params: { path: base, header: encabezado },
          body: { ...cuerpo(datos), vigente_desde: datos.desde === "" ? null : datos.desde },
        });
        break;
      case "corregir":
        pedido = api.PUT("/api/v1/consorcios/{consorcio_id}/contactos/{contacto_id}", {
          ...opciones,
          params: { path: deContacto, header: encabezado },
          body: cuerpo(datos),
        });
        break;
      case "retirar":
        pedido = api.POST("/api/v1/consorcios/{consorcio_id}/contactos/{contacto_id}/retirar", {
          ...opciones,
          params: { path: deContacto, header: encabezado },
        });
        break;
      case "verificar":
        pedido = api.POST("/api/v1/consorcios/{consorcio_id}/contactos/{contacto_id}/verificar", {
          ...opciones,
          params: { path: deContacto, header: encabezado },
        });
        break;
      case "senalar":
        pedido = api.POST("/api/v1/consorcios/{consorcio_id}/contactos/{contacto_id}/senalar", {
          ...opciones,
          params: { path: deContacto, header: encabezado },
          body: { motivo: motivo.trim() },
        });
        break;
    }
    const nombre = accion === "agregar" ? datos.nombre.trim() : (contacto?.nombre ?? "");
    void pedido
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data === undefined) {
          const codigo = codigoDe(error);
          if (renovarClave(response.status, codigo)) clave.current = null;
          setAviso({ texto: mensajeDeError(response.status, codigo, accion, camposDe(error)), ok: false });
          if (cambioLaLista(response.status, codigo)) {
            // Lo escrito no se pierde: una corrección que ya no tiene contacto sigue como uno nuevo, y el
            // motivo espera la lista nueva, que muestra el formulario solo si el contacto sigue ahí.
            if (accion === "corregir" && yaNoEsta(response.status, codigo)) setAbierto({ accion: "agregar", contacto: null });
            else if (accion !== "senalar" && accion !== "agregar") cerrar();
            volverAPedir();
          }
          return;
        }
        cerrar();
        const hecho: Record<Accion, string> = {
          agregar: `agregamos el contacto ${nombre}`,
          corregir: `corregimos el contacto ${nombre}`,
          retirar: `retiramos el contacto ${nombre}`,
          verificar: `quedó la constancia de que comprobaste el contacto ${nombre}`,
          senalar: `señalamos el dato incorrecto del contacto ${nombre}`,
        };
        setAviso({ texto: `Listo: ${hecho[accion]}.`, ok: true });
        // Lo que se ve lo decide la API: la lista se vuelve a pedir.
        volverAPedir();
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion) aEntrar();
        else setAviso({ texto: SIN_CONEXION, ok: false });
      })
      .finally(() => setEnviando(false));
  }

  const conMensaje = aviso !== null || pantalla.tipo !== "lista";

  return (
    <>
      <h1>Gestionar los contactos de emergencia</h1>

      {pantalla.tipo === "lista" && (
        <>
          <p className="tarjeta">
            La administración y el Consejo mantienen la lista que todos ven en «Contactos de
            emergencia». Solo números de empresas, servicios u organismos; un profesional va con su
            número profesional y la constancia del consentimiento. Nunca un celular personal.
          </p>

          {edificios.length > 1 && (
            <div className="formulario formulario-movil tarjeta">
              <label htmlFor={`${id}-consorcio`}>Edificio</label>
              <select
                id={`${id}-consorcio`}
                value={consorcio}
                disabled={enviando}
                onChange={(e) => {
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

          <section aria-labelledby={`${id}-lista`} className="tarjeta">
            <h2 id={`${id}-lista`}>Contactos vigentes y futuros</h2>
            {lectura.tipo === "cargando" && <p>Consultando…</p>}
            {lectura.tipo === "sin_servicio" && (
              <>
                <p>No pudimos mostrar la lista.</p>
                <button type="button" onClick={reconsultar} disabled={enviando}>
                  Consultar de nuevo
                </button>
              </>
            )}
            {lectura.tipo === "ok" && (
              <>
                {lectura.contactos.length === 0 && <p>El edificio no tiene contactos de emergencia cargados.</p>}
                {lectura.contactos.length > 0 && (
                  <ul className="sesiones" aria-label="Contactos para gestionar">
                    {lectura.contactos.map((c) => tarjeta(c))}
                  </ul>
                )}
              </>
            )}
            {/* Agregar no depende de la lista leída: con la lista caída sigue a la vista, y un formulario
                abierto no se va mientras llega la lista. */}
            {abierto?.accion === "agregar"
              ? formularioDatos(null, `${id}-nuevo`)
              : lectura.tipo !== "cargando" && (
                  <p>
                    <button type="button" onClick={() => abrir("agregar", null)} disabled={enviando}>
                      Agregar un contacto
                    </button>
                  </p>
                )}
          </section>
        </>
      )}

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
        {aviso && (
          <p ref={textoAviso} tabIndex={-1} className={aviso.ok ? "aviso-ok" : "aviso-error"}>
            {aviso.texto}
          </p>
        )}
      </div>
    </>
  );

  /** Un contacto con sus datos de gestión y sus acciones (la API decide si se puede). */
  function tarjeta(c: Contacto) {
    const titulo = `${id}-${c.id}`;
    const accion = abierto?.contacto?.id === c.id ? abierto.accion : null;
    return (
      <li key={c.id}>
        <h3 id={titulo}>
          {TEXTO_FUNCION[c.funcion]}: {c.nombre}
        </h3>
        <dl className="detalles">
          <dt>Naturaleza</dt>
          <dd>{TEXTO_NATURALEZA[c.naturaleza]}</dd>
          {c.telefono !== null && (
            <>
              <dt>Teléfono</dt>
              <dd>{c.telefono}</dd>
            </>
          )}
          {c.consentimiento_referencia !== null && (
            <>
              <dt>Constancia del consentimiento</dt>
              <dd>{c.consentimiento_referencia}</dd>
            </>
          )}
          {c.indicaciones !== null && (
            <>
              <dt>Indicaciones</dt>
              <dd>{c.indicaciones}</dd>
            </>
          )}
          <dt>Vigencia</dt>
          <dd>{vigencia(c)}</dd>
          <dt>Última modificación</dt>
          <dd>{momento(c.actualizado_en)}</dd>
          <dt>Última comprobación</dt>
          <dd>{c.verificado_en === null ? "Sin comprobar" : momento(c.verificado_en)}</dd>
          {c.observado && (
            <>
              <dt>Dato señalado como incorrecto</dt>
              <dd>{c.observacion ?? "Sin el motivo"}</dd>
            </>
          )}
        </dl>
        {accion === null ? (
          <p>
            {(["corregir", "verificar", "senalar", "retirar"] as const).map((a, i) => (
              <span key={a}>
                {i > 0 && " "}
                <button type="button" className="secundario" onClick={() => abrir(a, c)} disabled={enviando} aria-describedby={titulo}>
                  {{ corregir: "Corregir", verificar: "Comprobado", senalar: "Señalar un error", retirar: "Retirar" }[a]}
                </button>
              </span>
            ))}
          </p>
        ) : accion === "corregir" ? (
          formularioDatos(c, titulo)
        ) : accion === "retirar" ? (
          preguntaRetirar(c, titulo)
        ) : (
          accion !== "agregar" && formularioAccion(accion, titulo)
        )}
      </li>
    );
  }

  /** Agregar (contacto nulo) o corregir. */
  function formularioDatos(c: Contacto | null, titulo: string) {
    const nuevo = c === null;
    const profesional = datos.naturaleza === "profesional";
    const ayudaTelefono = `${titulo}-ayuda-telefono`;
    const ayudaTexto = `${titulo}-ayuda-texto`;
    // Agregar cuelga de la sección, también sin tarjetas a la vista; corregir, de la tarjeta.
    const Nivel = nuevo ? "h3" : "h4";
    return (
      <form className="formulario formulario-movil" onSubmit={enviar} aria-labelledby={`${titulo}-accion`}>
        <Nivel id={`${titulo}-accion`}>{nuevo ? "Agregar un contacto" : "Corregir el contacto"}</Nivel>
        <label htmlFor={`${titulo}-funcion`}>Función</label>
        <select
          id={`${titulo}-funcion`}
          ref={(e) => {
            primerCampo.current = e;
          }}
          value={datos.funcion}
          disabled={enviando}
          onChange={(e) => cambiar("funcion", e.target.value as Funcion)}
        >
          {(Object.keys(TEXTO_FUNCION) as Funcion[]).map((f) => (
            <option key={f} value={f}>
              {TEXTO_FUNCION[f]}
            </option>
          ))}
        </select>
        <label htmlFor={`${titulo}-naturaleza`}>Naturaleza</label>
        <select
          id={`${titulo}-naturaleza`}
          value={datos.naturaleza}
          disabled={enviando}
          onChange={(e) => cambiar("naturaleza", e.target.value as Naturaleza)}
        >
          {(Object.keys(TEXTO_NATURALEZA) as Naturaleza[]).map((n) => (
            <option key={n} value={n}>
              {TEXTO_NATURALEZA[n]}
            </option>
          ))}
        </select>
        <p id={ayudaTexto}>Sin teléfonos ni correos en el nombre ni en las indicaciones: el número va solo en «Teléfono».</p>
        <label htmlFor={`${titulo}-nombre`}>Nombre</label>
        <input
          id={`${titulo}-nombre`}
          type="text"
          autoComplete="off"
          maxLength={LARGO_NOMBRE}
          pattern={PATRON_TEXTO}
          required
          disabled={enviando}
          aria-describedby={ayudaTexto}
          value={datos.nombre}
          onChange={(e) => cambiar("nombre", e.target.value)}
        />
        <label htmlFor={`${titulo}-telefono`}>Teléfono{profesional ? "" : " (opcional)"}</label>
        <input
          id={`${titulo}-telefono`}
          type="tel"
          autoComplete="off"
          maxLength={30}
          pattern={PATRON_TELEFONO}
          required={profesional}
          disabled={enviando}
          aria-describedby={ayudaTelefono}
          value={datos.telefono}
          onChange={(e) => cambiar("telefono", e.target.value)}
        />
        <p id={ayudaTelefono}>Solo números de empresas, servicios u organismos; un profesional va con su constancia.</p>
        {profesional && (
          <>
            <label htmlFor={`${titulo}-consentimiento`}>Constancia del consentimiento</label>
            <input
              id={`${titulo}-consentimiento`}
              type="text"
              autoComplete="off"
              maxLength={LARGO_TEXTO}
              pattern={PATRON_TEXTO}
              required
              disabled={enviando}
              value={datos.consentimiento}
              onChange={(e) => cambiar("consentimiento", e.target.value)}
            />
          </>
        )}
        <label htmlFor={`${titulo}-indicaciones`}>Indicaciones (opcional)</label>
        <textarea
          id={`${titulo}-indicaciones`}
          maxLength={LARGO_TEXTO}
          disabled={enviando}
          aria-describedby={ayudaTexto}
          value={datos.indicaciones}
          onChange={(e) => cambiar("indicaciones", e.target.value)}
        />
        {nuevo && (
          <>
            <label htmlFor={`${titulo}-desde`}>Desde (opcional; si queda vacío, desde hoy)</label>
            <input
              id={`${titulo}-desde`}
              type="date"
              disabled={enviando}
              value={datos.desde}
              onChange={(e) => cambiar("desde", e.target.value)}
            />
          </>
        )}
        <p>
          <button type="submit" disabled={enviando}>
            {nuevo ? "Agregar el contacto" : "Guardar la corrección"}
          </button>{" "}
          <button type="button" className="secundario" onClick={cerrar} disabled={enviando}>
            Cancelar
          </button>
        </p>
      </form>
    );
  }

  /** Retirar no se puede deshacer: se pregunta una vez, en la tarjeta, sin un paso previo. */
  function preguntaRetirar(c: Contacto, titulo: string) {
    const pregunta = `${titulo}-pregunta`;
    return (
      <form className="formulario formulario-movil" onSubmit={enviar} aria-labelledby={pregunta}>
        <p id={pregunta}>¿Retirar el contacto {c.nombre}? No se puede deshacer.</p>
        <p>
          <button
            type="submit"
            disabled={enviando}
            ref={(e) => {
              primerCampo.current = e;
            }}
          >
            Sí, retirar
          </button>{" "}
          <button type="button" className="secundario" onClick={cerrar} disabled={enviando}>
            No
          </button>
        </p>
      </form>
    );
  }

  /** Dejar constancia de la comprobación o señalar un error. */
  function formularioAccion(accion: "verificar" | "senalar", titulo: string) {
    const ayuda = `${titulo}-ayuda`;
    const texto = {
      verificar: {
        titulo: "Dejar constancia de la comprobación",
        ayuda: "Confirmá que llamaste o comprobaste de otra forma que el contacto es correcto. Resuelve el dato señalado, si había.",
        boton: "Lo comprobé",
      },
      senalar: {
        titulo: "Señalar un dato incorrecto",
        ayuda: "Contá qué está mal, sin teléfonos ni correos. Lo resuelve quien corrija o compruebe el contacto.",
        boton: "Señalar el error",
      },
    }[accion];
    return (
      <form className="formulario formulario-movil" onSubmit={enviar} aria-labelledby={`${titulo}-accion`}>
        <h4 id={`${titulo}-accion`}>{texto.titulo}</h4>
        <p id={ayuda}>{texto.ayuda}</p>
        {accion === "senalar" && (
          <>
            <label htmlFor={`${titulo}-motivo`}>Motivo</label>
            <input
              id={`${titulo}-motivo`}
              ref={(e) => {
                primerCampo.current = e;
              }}
              type="text"
              autoComplete="off"
              maxLength={LARGO_TEXTO}
              pattern={PATRON_TEXTO}
              required
              disabled={enviando}
              aria-describedby={ayuda}
              value={motivo}
              onChange={(e) => {
                clave.current = null;
                setMotivo(e.target.value);
              }}
            />
          </>
        )}
        <p>
          <button
            type="submit"
            disabled={enviando}
            ref={
              accion === "senalar"
                ? undefined
                : (e) => {
                    primerCampo.current = e;
                  }
            }
          >
            {texto.boton}
          </button>{" "}
          <button type="button" className="secundario" onClick={cerrar} disabled={enviando}>
            Cancelar
          </button>
        </p>
      </form>
    );
  }
}
