import { type FormEvent, useCallback, useEffect, useId, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { borrarCopias } from "./copiaContactos";
import { encabezadoCsrf } from "./csrf";
import { ErrorDeCampo, type ErroresDeCampo, erroresDe, marcaDe, resumenDe, REVISAR_FORMULARIO, SIN_ERRORES, sinElDe, soloDe } from "./ErrorDeCampo";
import { RUTA_INICIO } from "./enlace";
import { FichaDeRegistro, FilaDeRegistro, useFichas } from "./FichaDeRegistro";
import { useAEntrar } from "./sesionEncima";
import { codigoDe, esDeConexion, LIMITE_DE_PEDIDOS, mensajeDeFalla, SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "./fallas";
import { REQUISITOS, useEdificioElegido } from "./permisos";

// Página /contactos/gestion (H2b 2b-7c; doc/diseno/H2b_7_Diseno.md §3 y §4 del repositorio CEM):
// la lista de gestión de los contactos de emergencia, con los futuros, la observación y la
// constancia del consentimiento, y los formularios para agregar, corregir, retirar, verificar y
// señalar. Cada contacto es una fila que abre su ficha (comentario 21), con sus datos y sus acciones. Quien no alcanza `contactos.gestionar` no llega acá: la dirección y una lectura negada
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
/** El navegador aplica `pattern` entero y con la marca `v`: la misma cuenta, para decirlo al escribir. */
const TELEFONO_BIEN_ESCRITO = new RegExp(`^(?:${PATRON_TELEFONO})$`, "v");
/** El formato del teléfono, en vez del mensaje genérico del navegador (G-57). */
export const TELEFONO_MAL_ESCRITO = "El teléfono va solo con números, espacios, paréntesis o guiones.";
const telefonoMalEscrito = (telefono: string) => telefono !== "" && !TELEFONO_BIEN_ESCRITO.test(telefono);

export const TEXTO_FUNCION: Record<Funcion, string> = {
  administracion: "Administración",
  encargado: "Encargado/a",
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
  profesional: "Un profesional (con su número profesional y la constancia de que aceptó figurar)",
};

/** Cómo se nombra cada campo en el resumen del error: una frase por campo marcado (G-58). */
const TEXTO_CAMPO: Record<string, string> = {
  nombre: "el nombre",
  telefono: "el teléfono",
  indicaciones: "las indicaciones",
  consentimiento_referencia: "dónde consta que aceptó figurar",
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

/** El campo de la API de cada dato del formulario. */
const CAMPO_API: Partial<Record<keyof Datos, string>> = {
  nombre: "nombre",
  telefono: "telefono",
  consentimiento: "consentimiento_referencia",
  indicaciones: "indicaciones",
  desde: "vigente_desde",
};

/** Qué decir ante un error de una acción, según el estado, el código y los campos del 422. */
export function mensajeDeError(estado: number, codigo: string | undefined, accion: Accion, campos: string[] = []): string {
  if (estado === ESTADO.sinPermiso && codigo === "csrf_invalido") return "La página quedó abierta mucho tiempo. Hay que recargarla y repetir el paso.";
  if (estado === ESTADO.sinPermiso)
    return "Esta cuenta no tiene permiso para gestionar los contactos de este edificio: hace falta la facultad de la administración o del Consejo.";
  if (estado === ESTADO.noEncontrado) return "Ese contacto ya no está en este edificio.";
  if (estado === ESTADO.conflicto && codigo === "idempotencia_en_curso")
    return "El pedido se está procesando. Reintentar en unos segundos.";
  if (estado === ESTADO.conflicto && codigo === "contacto_terminado")
    return "Ese contacto ya no está vigente: para cambiarlo, hay que agregar uno nuevo.";
  if (estado === ESTADO.conflicto) return "Otra persona cambió este dato recién. La lista ya está actualizada.";
  if (estado === ESTADO.invalido && codigo === "idempotencia_reutilizada")
    return "Los datos cambiaron desde un intento anterior. Hay que enviar el formulario de nuevo.";
  // Cada campo marcado lleva su motivo al lado (`ErrorDeCampo`); el resumen solo los nombra (G-58).
  if (estado === ESTADO.invalido) {
    const marcados = campos.length > 0 ? campos : accion === "senalar" ? ["body.motivo"] : [];
    const errores = Object.fromEntries(marcados.map((c) => [c.split(".").pop() ?? "", ""]));
    return marcados.length > 0 ? resumenDe(errores, TEXTO_CAMPO) : REVISAR_FORMULARIO;
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

/** Lo que se tocó en /contactos para hacer acá (comentario 25): agregar, o corregir o retirar uno. */
export type PedidoDeGestion =
  | { consorcio: string; accion: "agregar" }
  | { consorcio: string; accion: "corregir" | "retirar"; contacto: string };

function pedidoDe(estado: unknown): PedidoDeGestion | null {
  if (typeof estado !== "object" || estado === null) return null;
  const { consorcio, accion, contacto } = estado as Record<string, unknown>;
  if (typeof consorcio !== "string") return null;
  if (accion === "agregar") return { consorcio, accion };
  if ((accion === "corregir" || accion === "retirar") && typeof contacto === "string") return { consorcio, accion, contacto };
  return null;
}

type Aviso = { texto: string; ok: boolean };

class SinSesion extends Error {}

function exigirSesion(response: Response) {
  if (response.status === ESTADO.sinSesion) throw new SinSesion();
}

export function ContactosGestion() {
  const id = useId();
  const navegar = useNavigate();
  const [pantalla, setPantalla] = useState<Pantalla>({ tipo: "cargando" });
  const { state } = useLocation();
  // Lo que se pidió desde /contactos. Al recargar, la historia lo conserva pero el edificio
  // elegido en memoria se pierde: el del pedido se prefiere al elegir (Codex en #220).
  const [pedidoInicial] = useState(() => pedidoDe(state));
  const { edificios, consorcio, setConsorcio } = useEdificioElegido(
    pantalla.tipo === "lista" ? pantalla.consorcios : SIN_EDIFICIOS,
    REQUISITOS.contactosGestion,
    pedidoInicial?.consorcio ?? null,
  );
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  // Se usa una sola vez, con la primera lectura de la lista.
  const pedido = useRef(pedidoInicial);
  const [abierto, setAbierto] = useState<Abierto | null>(null);
  // La ficha abierta, por el id del contacto; las acciones de ese contacto van dentro de ella.
  const { abierta: fichaAbierta, abrir: abrirFichaDe, cerrar: cerrarFichaDe, refFila } = useFichas<string>();
  const [datos, setDatos] = useState<Datos>(VACIO);
  const [motivo, setMotivo] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  /** Los campos que rechazó la API, con su motivo (G-58). */
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
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
    /** Lo pedido desde /contactos se hace solo en el edificio para el que se pidió: si quedó
     * otro (no se gestiona ese, o ya no está), no se abre nada. Agregar no depende de la lista
     * leída; corregir y retirar, sí. */
    const tomarPedido = (contactos: readonly Contacto[] | null) => {
      const p = pedido.current;
      pedido.current = null;
      if (p === null || p.consorcio !== consorcio) return;
      if (p.accion === "agregar") {
        setDatos(VACIO);
        setAbierto({ accion: "agregar", contacto: null });
        return;
      }
      // Si el contacto ya no está, queda la lista: no se abre otro en su lugar.
      const elegido = contactos?.find((c) => c.id === p.contacto);
      if (elegido === undefined) return;
      setDatos(datosDe(elegido));
      setAbierto({ accion: p.accion, contacto: elegido });
      abrirFichaDe(elegido.id);
    };
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/contactos/gestion", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        // Una lista pedida antes de un cambio, o de otro edificio, puede llegar después.
        if (!vigente) return;
        if (data !== undefined) {
          setLectura({ tipo: "ok", contactos: data.elementos });
          tomarPedido(data.elementos);
        } else if (response.status === ESTADO.sinPermiso) navegar(RUTA_INICIO, { replace: true });
        else {
          setLectura({ tipo: "sin_servicio" });
          const falla = mensajeDeFalla(response.status, codigoDe(error));
          setAviso((actual) => (actual?.ok ? actual : { texto: falla, ok: false }));
          tomarPedido(null);
        }
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso((actual) => (actual?.ok ? actual : { texto: SIN_CONEXION, ok: false }));
          tomarPedido(null);
        }
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, vuelta, aEntrar, navegar, abrirFichaDe]);

  // Si la lista nueva ya no trae el contacto de la ficha, la ficha y su formulario se van.
  const contactos = lectura.tipo === "ok" ? lectura.contactos : null;
  useEffect(() => {
    if (contactos === null || fichaAbierta === null || contactos.some((c) => c.id === fichaAbierta)) return;
    cerrarFichaDe(false);
    setAbierto((actual) => (actual?.accion === "agregar" ? actual : null));
  }, [contactos, fichaAbierta, cerrarFichaDe]);

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
    setErrores(SIN_ERRORES);
    setDatos(contacto === null ? VACIO : datosDe(contacto));
    setMotivo("");
    setAviso(null);
    setAbierto({ accion, contacto });
  }

  function cerrar() {
    clave.current = null;
    setErrores(SIN_ERRORES);
    setAbierto(null);
  }

  /** «Cancelar» en Agregar: el rechazo era del formulario y se va con él, como al corregir (G-59). */
  function cancelarAgregar() {
    cerrar();
    setAviso((actual) => (actual?.ok === false ? null : actual));
  }

  function abrirFicha(c: Contacto) {
    cerrar();
    setMotivo("");
    setAviso(null);
    abrirFichaDe(c.id);
  }

  /** De un formulario a la ficha con sus botones; el rechazo de ese formulario ya no vale. */
  function volverALaFicha() {
    cerrar();
    setAviso((actual) => (actual?.ok === false ? null : actual));
  }

  /** Cierra la ficha. Si la cierra un resultado, el foco va a su aviso y no a la fila. */
  function cerrarFicha(devolverFoco = true) {
    if (devolverFoco) volverALaFicha();
    else cerrar();
    cerrarFichaDe(devolverFoco);
  }

  function cambiar<K extends keyof Datos>(campo: K, valor: Datos[K]) {
    clave.current = null;
    const deLaApi = CAMPO_API[campo];
    if (deLaApi !== undefined) setErrores((e) => sinElDe(e, deLaApi));
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
          const marcados = response.status === ESTADO.invalido ? soloDe(erroresDe(error), TEXTO_CAMPO) : SIN_ERRORES;
          setErrores(marcados);
          setAviso({ texto: mensajeDeError(response.status, codigo, accion, Object.keys(marcados).map((c) => `body.${c}`)), ok: false });
          if (cambioLaLista(response.status, codigo)) {
            // Lo escrito no se pierde: una corrección que ya no tiene contacto sigue como uno nuevo, y el
            // motivo espera la lista nueva, que muestra el formulario solo si el contacto sigue ahí.
            if (accion === "corregir" && yaNoEsta(response.status, codigo)) {
              setAbierto({ accion: "agregar", contacto: null });
              cerrarFichaDe(false);
            } else if (accion !== "senalar" && accion !== "agregar") cerrarFicha(false);
            volverAPedir();
          }
          return;
        }
        if (accion === "agregar") cerrar();
        else cerrarFicha(false);
        const hecho: Record<Accion, string> = {
          agregar: `el contacto ${nombre} quedó agregado`,
          corregir: `el contacto ${nombre} quedó corregido`,
          retirar: `el contacto ${nombre} se retiró`,
          verificar: `quedó la constancia de que el contacto ${nombre} se comprobó`,
          senalar: `quedó señalado el dato incorrecto del contacto ${nombre}`,
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

  // La ficha muestra el contacto de la lista vigente: mientras llega una lista nueva, no se ve.
  const enFicha = contactos?.find((c) => c.id === fichaAbierta);
  // Con la ficha abierta, lo de atrás queda inerte: un rechazo va dentro de la ficha.
  const avisoEnFicha = enFicha !== undefined && aviso !== null && !aviso.ok;
  const conMensaje = (aviso !== null && !avisoEnFicha) || pantalla.tipo !== "lista";

  return (
    <>
      <h1>Gestionar los contactos de emergencia</h1>

      {pantalla.tipo === "lista" && (
        <>
          <p className="tarjeta">
            La administración y el Consejo mantienen la lista que todos ven en «Contactos de
            emergencia». Solo números de empresas, servicios u organismos; un profesional va con su
            número profesional y la constancia de que aceptó figurar. Nunca un celular personal.
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
                <p>No se pudo mostrar la lista.</p>
                <button className="secundario" type="button" onClick={reconsultar} disabled={enviando}>
                  Consultar de nuevo
                </button>
              </>
            )}
            {lectura.tipo === "ok" && (
              <>
                {lectura.contactos.length === 0 && <p>El edificio no tiene contactos de emergencia cargados.</p>}
                {lectura.contactos.length > 0 && (
                  <ul className="filas-registro" aria-label="Contactos para gestionar">
                    {lectura.contactos.map((c) => (
                      <li key={c.id}>
                        <FilaDeRegistro
                          titulo={titulo(c)}
                          resumen={resumen(c)}
                          alAbrir={() => abrirFicha(c)}
                          refFila={refFila(c.id)}
                          abierta={fichaAbierta === c.id}
                          deshabilitada={enviando}
                        />
                      </li>
                    ))}
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
        {aviso && !avisoEnFicha && (
          <p ref={textoAviso} tabIndex={-1} className={aviso.ok ? "aviso-ok" : "aviso-error"}>
            {aviso.texto}
          </p>
        )}
      </div>

      {enFicha !== undefined && ficha(enFicha)}
    </>
  );

  function titulo(c: Contacto) {
    return `${TEXTO_FUNCION[c.funcion]}: ${c.nombre}`;
  }

  /** En la fila, lo que dice si el contacto está en condiciones; todo lo demás va en la ficha. */
  function resumen(c: Contacto) {
    return [
      c.telefono ?? "Sin teléfono",
      c.verificado_en === null ? "Sin comprobar" : "Comprobado",
      ...(c.observado ? ["Dato señalado como incorrecto"] : []),
    ].join(" · ");
  }

  /** La ficha de un contacto con sus datos de gestión y sus acciones (la API decide si se puede). */
  function ficha(c: Contacto) {
    const base = `${id}-${c.id}`;
    const accion = abierto?.contacto?.id === c.id ? abierto.accion : null;
    return (
      <FichaDeRegistro
        titulo={titulo(c)}
        alCerrar={() => cerrarFicha()}
        ocupada={enviando}
        datos={[
          { etiqueta: "Función", valor: TEXTO_FUNCION[c.funcion] },
          { etiqueta: "Nombre", valor: c.nombre },
          { etiqueta: "De quién es el número", valor: TEXTO_NATURALEZA[c.naturaleza] },
          { etiqueta: "Teléfono", valor: c.telefono ?? "Sin teléfono" },
          ...(c.consentimiento_referencia === null ? [] : [{ etiqueta: "Dónde consta que aceptó figurar", valor: c.consentimiento_referencia }]),
          ...(c.indicaciones === null ? [] : [{ etiqueta: "Indicaciones", valor: c.indicaciones }]),
          { etiqueta: "Vigencia", valor: vigencia(c) },
          { etiqueta: "Última modificación", valor: momento(c.actualizado_en) },
          { etiqueta: "Última comprobación", valor: c.verificado_en === null ? "Sin comprobar" : momento(c.verificado_en) },
          ...(c.observado ? [{ etiqueta: "Dato señalado como incorrecto", valor: c.observacion ?? "Sin el motivo" }] : []),
        ]}
      >
        {avisoEnFicha && (
          <p ref={textoAviso} tabIndex={-1} role="alert" className="aviso-error">
            {aviso.texto}
          </p>
        )}
        {accion === null ? (
          <p className="ficha-registro__acciones">
            {(["corregir", "verificar", "senalar", "retirar"] as const).map((a) => (
              <button key={a} type="button" className={a === "retirar" ? "secundario peligro" : "secundario"} onClick={() => abrir(a, c)} disabled={enviando}>
                {{ corregir: "Corregir", verificar: "Comprobado", senalar: "Señalar un error", retirar: "Retirar" }[a]}
              </button>
            ))}
          </p>
        ) : accion === "corregir" ? (
          formularioDatos(c, base)
        ) : accion === "retirar" ? (
          preguntaRetirar(c, base)
        ) : (
          accion !== "agregar" && formularioAccion(accion, base)
        )}
      </FichaDeRegistro>
    );
  }

  /** Agregar (contacto nulo) o corregir. */
  function formularioDatos(c: Contacto | null, titulo: string) {
    const nuevo = c === null;
    const profesional = datos.naturaleza === "profesional";
    const ayudaTelefono = `${titulo}-ayuda-telefono`;
    const ayudaTexto = `${titulo}-ayuda-texto`;
    const errorTelefono = telefonoMalEscrito(datos.telefono) ? TELEFONO_MAL_ESCRITO : errores.telefono;
    return (
      <form className="formulario formulario-movil" onSubmit={enviar} aria-labelledby={`${titulo}-accion`}>
        <h3 id={`${titulo}-accion`}>{nuevo ? "Agregar un contacto" : "Corregir el contacto"}</h3>
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
        <label htmlFor={`${titulo}-naturaleza`}>¿De quién es el número?</label>
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
          {...marcaDe(`${titulo}-nombre`, errores.nombre, ayudaTexto)}
          value={datos.nombre}
          onChange={(e) => cambiar("nombre", e.target.value)}
        />
        <ErrorDeCampo idCampo={`${titulo}-nombre`} mensaje={errores.nombre} />
        <label htmlFor={`${titulo}-telefono`}>Teléfono{profesional ? "" : " (opcional)"}</label>
        <input
          id={`${titulo}-telefono`}
          type="tel"
          autoComplete="off"
          maxLength={30}
          pattern={PATRON_TELEFONO}
          required={profesional}
          disabled={enviando}
          {...marcaDe(`${titulo}-telefono`, errorTelefono, ayudaTelefono)}
          value={datos.telefono}
          onChange={(e) => {
            e.target.setCustomValidity(telefonoMalEscrito(e.target.value) ? TELEFONO_MAL_ESCRITO : "");
            cambiar("telefono", e.target.value);
          }}
        />
        <p id={ayudaTelefono}>Solo números de empresas, servicios u organismos; un profesional va con su constancia.</p>
        <ErrorDeCampo idCampo={`${titulo}-telefono`} mensaje={errorTelefono} />
        {profesional && (
          <>
            <label htmlFor={`${titulo}-consentimiento`}>¿Dónde consta que aceptó figurar? (por ejemplo: nota firmada el 3/5/2026)</label>
            <input
              id={`${titulo}-consentimiento`}
              type="text"
              autoComplete="off"
              maxLength={LARGO_TEXTO}
              pattern={PATRON_TEXTO}
              required
              disabled={enviando}
              {...marcaDe(`${titulo}-consentimiento`, errores.consentimiento_referencia)}
              value={datos.consentimiento}
              onChange={(e) => cambiar("consentimiento", e.target.value)}
            />
            <ErrorDeCampo idCampo={`${titulo}-consentimiento`} mensaje={errores.consentimiento_referencia} />
          </>
        )}
        <label htmlFor={`${titulo}-indicaciones`}>Indicaciones (opcional)</label>
        <textarea
          id={`${titulo}-indicaciones`}
          maxLength={LARGO_TEXTO}
          disabled={enviando}
          {...marcaDe(`${titulo}-indicaciones`, errores.indicaciones, ayudaTexto)}
          value={datos.indicaciones}
          onChange={(e) => cambiar("indicaciones", e.target.value)}
        />
        <ErrorDeCampo idCampo={`${titulo}-indicaciones`} mensaje={errores.indicaciones} />
        {nuevo && (
          <>
            <label htmlFor={`${titulo}-desde`}>Desde (opcional; si queda vacío, desde hoy)</label>
            <input
              id={`${titulo}-desde`}
              type="date"
              disabled={enviando}
              {...marcaDe(`${titulo}-desde`, errores.vigente_desde)}
              value={datos.desde}
              onChange={(e) => cambiar("desde", e.target.value)}
            />
            <ErrorDeCampo idCampo={`${titulo}-desde`} mensaje={errores.vigente_desde} />
          </>
        )}
        <p>
          <button type="submit" disabled={enviando}>
            {nuevo ? "Agregar el contacto" : "Guardar la corrección"}
          </button>{" "}
          <button type="button" className="secundario" onClick={nuevo ? cancelarAgregar : volverALaFicha} disabled={enviando}>
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
            className="peligro"
            disabled={enviando}
            ref={(e) => {
              primerCampo.current = e;
            }}
          >
            Sí, retirar
          </button>{" "}
          <button type="button" className="secundario" onClick={volverALaFicha} disabled={enviando}>
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
        ayuda: "Sirve para dejar constancia de que se llamó o se comprobó de otra forma que el contacto es correcto. Resuelve el dato señalado, si había.",
        boton: "Lo comprobé",
      },
      senalar: {
        titulo: "Señalar un dato incorrecto",
        ayuda: "Qué está mal, sin teléfonos ni correos. Lo resuelve quien corrija o compruebe el contacto.",
        boton: "Señalar el error",
      },
    }[accion];
    return (
      <form className="formulario formulario-movil" onSubmit={enviar} aria-labelledby={`${titulo}-accion`}>
        <h3 id={`${titulo}-accion`}>{texto.titulo}</h3>
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
              {...marcaDe(`${titulo}-motivo`, errores.motivo, ayuda)}
              value={motivo}
              onChange={(e) => {
                clave.current = null;
                setErrores((actuales) => sinElDe(actuales, "motivo"));
                setMotivo(e.target.value);
              }}
            />
            <ErrorDeCampo idCampo={`${titulo}-motivo`} mensaje={errores.motivo} />
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
          <button type="button" className="secundario" onClick={volverALaFicha} disabled={enviando}>
            Cancelar
          </button>
        </p>
      </form>
    );
  }
}
