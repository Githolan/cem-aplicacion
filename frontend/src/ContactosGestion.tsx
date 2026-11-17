import { type FormEvent, type ReactNode, useEffect, useId, useRef, useState } from "react";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { borrarCopias } from "./copiaContactos";
import { encabezadoCsrf } from "./csrf";
import {
  ErrorDeCampo,
  type ErroresDeCampo,
  erroresDe,
  faltaCompletar,
  hayErrores,
  marcaDe,
  resumenDe,
  REVISAR_FORMULARIO,
  SIN_ERRORES,
  sinElDe,
  soloDe,
} from "./ErrorDeCampo";
import { FichaDeRegistro, FilaDeRegistro, useFichas } from "./FichaDeRegistro";
import { useAEntrar } from "./sesionEncima";
import { codigoDe, LIMITE_DE_PEDIDOS, mensajeDeFalla, SIN_CONEXION } from "./fallas";
import { type FinDeEnvio, VentanaDeFormulario } from "./VentanaDeFormulario";

// La gestión de los contactos de emergencia (H2b 2b-7c; doc/diseno/H2b_7_Diseno.md §3 y §4 del
// repositorio CEM), dentro de /contactos (G-52 y G-53: una sola pantalla). `Contactos.tsx` la monta
// solo para quien alcanza `contactos.gestionar` en el edificio elegido y con la lista general en
// línea: a nadie más se le pide la lista de gestión. Trae los futuros, la observación y la
// constancia del consentimiento, y los formularios para agregar, corregir, retirar, verificar y
// señalar. Cada contacto es una fila que abre su ficha (comentario 21), con sus datos y sus actos;
// cada acto sobre un contacto se abre en su ventana, encima de la ficha (tanda 1 del informe de
// fricción, CON-05), y termina en la misma pantalla, con su «Listo». Es solo interfaz: la API decide
// cada pedido, la base vuelve a controlar y un 403 al actuar se dice en la pantalla. Esta lista nunca
// se guarda en el dispositivo (§5): la copia sin conexión es solo la de la lista general. Si la API
// niega la lista de gestión (403), queda la lista general, sin actos.
//
// Lo ya hecho no se ofrece de nuevo (G-54): un contacto ya señalado no ofrece «Señalar un error», y
// «Marcar como comprobado» se ofrece solo con un dato señalado o sin una comprobación reciente.

type Contacto = components["schemas"]["ContactoGestion"];
type Funcion = components["schemas"]["FuncionContacto"];
type Naturaleza = components["schemas"]["NaturalezaContacto"];

/**
 * Cuántos meses vale una comprobación (G-54): pasado ese plazo, la ficha vuelve a ofrecer «Marcar
 * como comprobado». El propietario fijó 12 meses el 14-11-2026. La API no expone este plazo; si
 * algún día lo hace, se lee de ahí.
 */
export const MESES_DE_VALIDEZ_DE_LA_COMPROBACION = 12;

/** Si tiene sentido ofrecer «Marcar como comprobado»: hay un dato señalado, o la última comprobación
 * falta o tiene más de `MESES_DE_VALIDEZ_DE_LA_COMPROBACION` meses (G-54). */
export function ofreceComprobar(c: Pick<Contacto, "observado" | "verificado_en">, ahora: Date): boolean {
  if (c.observado || c.verificado_en === null) return true;
  const limite = new Date(ahora);
  limite.setMonth(limite.getMonth() - MESES_DE_VALIDEZ_DE_LA_COMPROBACION);
  return new Date(c.verificado_en) < limite;
}

/** El botón de cada acto de la ficha, con el verbo de lo que hace (G-54: «Comprobado» era un estado). */
const TEXTO_ACTO = {
  corregir: "Corregir",
  verificar: "Marcar como comprobado",
  senalar: "Señalar un error",
  retirar: "Retirar",
} as const;

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
const FORMATO_FECHA = new Intl.DateTimeFormat("es-AR", { dateStyle: "long" });
const MS_POR_DIA = 86_400_000;
const dia = (iso: string, corrimiento = 0) =>
  FORMATO_DIA.format(new Date(Date.parse(`${iso}T00:00:00Z`) + corrimiento * MS_POR_DIA));
const momento = (iso: string) => FORMATO_MOMENTO.format(new Date(iso));

/** El teléfono como enlace para llamar, sin separadores en el número: son números de emergencia. */
export function EnlaceTelefono({ telefono }: { telefono: string }) {
  return (
    <a className="enlace-telefono" href={`tel:${telefono.replace(/[^+0-9]/g, "")}`}>
      {telefono}
    </a>
  );
}

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

/** `sin_permiso`: la API negó la lista de gestión; queda la lista general, sin actos. */
type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "sin_permiso" } | { tipo: "ok"; contactos: Contacto[] };

type Abierto = { accion: Accion; contacto: Contacto | null };

/** Los actos sobre un contacto, en el orden de su ficha. */
const ACTOS_SOBRE_UNO = ["corregir", "verificar", "senalar", "retirar"] as const;

type Aviso = { texto: string; ok: boolean };

class SinSesion extends Error {}

function exigirSesion(response: Response) {
  if (response.status === ESTADO.sinSesion) throw new SinSesion();
}

type Props = {
  /** El edificio elegido en /contactos; la pantalla monta una gestión por edificio. */
  consorcio: string;
  /** El nombre del edificio, título de la lista. */
  nombre: string;
  /** La lista general de /contactos, para cuando la de gestión no se puede mostrar. */
  listaGeneral: ReactNode;
  /** Un acto terminó bien: /contactos vuelve a leer la lista general y renueva su copia. */
  alCambiar: () => void;
  /** Hay un pedido en curso: /contactos no deja cambiar de edificio mientras tanto. */
  alOcupar: (ocupada: boolean) => void;
};

export function GestionContactos({ consorcio, nombre, listaGeneral, alCambiar, alOcupar }: Props) {
  const id = useId();
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
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
  // La vuelta ya pedida: con StrictMode el efecto corre dos veces al montar y la lista se pide una.
  const pedida = useRef(-1);
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

  // /contactos no deja cambiar de edificio con un pedido en curso; al irse, ya no hay ninguno.
  useEffect(() => {
    alOcupar(enviando);
    return () => alOcupar(false);
  }, [alOcupar, enviando]);

  // Sin sesión no queda ninguna copia de /contactos, igual que allá y en /cuenta.
  const aEntrar = useAEntrar(borrarCopias);

  useEffect(() => {
    if (pedida.current === vuelta) return;
    pedida.current = vuelta;
    // Una lista pedida antes de un cambio puede llegar después de la siguiente: solo vale la última.
    // El edificio no cambia: /contactos monta otra gestión para otro edificio.
    const vigente = () => pedida.current === vuelta;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/contactos/gestion", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente()) return;
        if (data !== undefined) setLectura({ tipo: "ok", contactos: data.elementos });
        else if (response.status === ESTADO.sinPermiso) setLectura({ tipo: "sin_permiso" });
        else {
          setLectura({ tipo: "sin_servicio" });
          const falla = mensajeDeFalla(response.status, codigoDe(error));
          setAviso((actual) => (actual?.ok ? actual : { texto: falla, ok: false }));
        }
      })
      .catch((e: unknown) => {
        if (!vigente()) return;
        if (e instanceof SinSesion) aEntrar();
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso((actual) => (actual?.ok ? actual : { texto: SIN_CONEXION, ok: false }));
        }
      });
  }, [consorcio, vuelta, aEntrar]);

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

  /** Agregar, en la página: el rechazo va arriba, con el formulario abierto. */
  function enviar(evento: FormEvent) {
    evento.preventDefault();
    if (abierto !== null) void pedir(abierto);
  }

  /** El botón de la ventana de corregir o señalar: lo que falta se marca sin enviar (decisión 4). */
  function enviarDesdeLaFicha(a: Abierto): FinDeEnvio | Promise<FinDeEnvio> {
    const profesional = datos.naturaleza === "profesional";
    const faltan =
      a.accion === "corregir"
        ? faltaCompletar({
            nombre: [datos.nombre, "Falta escribir el nombre."],
            ...(profesional
              ? {
                  telefono: [datos.telefono, "Falta escribir el teléfono."],
                  consentimiento_referencia: [datos.consentimiento, "Falta escribir dónde consta que aceptó figurar."],
                }
              : {}),
          })
        : a.accion === "senalar"
          ? faltaCompletar({ motivo: [motivo, "Falta escribir el motivo."] })
          : SIN_ERRORES;
    if (hayErrores(faltan)) {
      setErrores(faltan);
      return "seguir";
    }
    // El teléfono mal escrito ya está marcado junto al campo.
    if (a.accion === "corregir" && telefonoMalEscrito(datos.telefono)) return "seguir";
    return pedir(a);
  }

  /** Manda el acto. Agregar dice el rechazo arriba; un acto sobre un contacto, en su ventana. */
  async function pedir({ accion, contacto }: Abierto): Promise<FinDeEnvio> {
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
    try {
      const { data, error, response } = await pedido;
      exigirSesion(response);
      if (data === undefined) {
        const codigo = codigoDe(error);
        if (renovarClave(response.status, codigo)) clave.current = null;
        const marcados = response.status === ESTADO.invalido ? soloDe(erroresDe(error), TEXTO_CAMPO) : SIN_ERRORES;
        setErrores(marcados);
        const texto = mensajeDeError(response.status, codigo, accion, Object.keys(marcados).map((c) => `body.${c}`));
        if (!cambioLaLista(response.status, codigo)) {
          if (accion === "agregar") setAviso({ texto, ok: false });
          return { rechazo: texto };
        }
        // La lista quedó vieja: el motivo va arriba, con la lista nueva. Lo escrito no se pierde: una
        // corrección que ya no tiene contacto sigue como uno nuevo, y el motivo espera la lista nueva,
        // que vuelve a abrir su ventana solo si el contacto sigue ahí.
        setAviso({ texto, ok: false });
        if (accion === "corregir" && yaNoEsta(response.status, codigo)) {
          setAbierto({ accion: "agregar", contacto: null });
          cerrarFichaDe(false);
        } else if (accion !== "senalar" && accion !== "agregar") cerrarFicha(false);
        volverAPedir();
        return accion === "senalar" ? "seguir" : "cerrar";
      }
      const hecho: Record<Accion, string> = {
        agregar: `el contacto ${nombre} quedó agregado`,
        corregir: `el contacto ${nombre} quedó corregido`,
        retirar: `el contacto ${nombre} se retiró`,
        verificar: `quedó la constancia de que el contacto ${nombre} se comprobó`,
        senalar: `quedó señalado el dato incorrecto del contacto ${nombre}`,
      };
      if (accion === "agregar") cerrar();
      else cerrarFicha(false);
      setAviso({ texto: `Listo: ${hecho[accion]}.`, ok: true });
      // Lo que se ve lo decide la API: las dos listas se vuelven a pedir, y la copia sin conexión
      // queda con el cambio.
      volverAPedir();
      alCambiar();
      return "cerrar";
    } catch (e: unknown) {
      if (e instanceof SinSesion) {
        // La sesión se pide encima: lo escrito queda para repetir el paso.
        aEntrar();
        return "seguir";
      }
      if (accion === "agregar") setAviso({ texto: SIN_CONEXION, ok: false });
      return { rechazo: SIN_CONEXION };
    } finally {
      setEnviando(false);
    }
  }

  // La ficha muestra el contacto de la lista vigente: mientras llega una lista nueva, no se ve.
  const enFicha = contactos?.find((c) => c.id === fichaAbierta);
  const conMensaje = aviso !== null;

  // La API negó la lista de gestión: la persona ve la lista general, como cualquiera del edificio.
  if (lectura.tipo === "sin_permiso") return <>{listaGeneral}</>;

  return (
    <>
      <div role="status" aria-live="polite" className={conMensaje ? "tarjeta" : undefined}>
        {aviso && (
          <p ref={textoAviso} tabIndex={-1} className={aviso.ok ? "aviso-ok" : "aviso-error"}>
            {aviso.texto}
          </p>
        )}
      </div>

      {/* Arriba, antes de la lista (G-60). Agregar no depende de la lista leída: con la lista caída
          sigue a la vista, y un formulario abierto no se va mientras llega la lista. */}
      {abierto?.accion === "agregar" ? (
        <div className="tarjeta">{formularioAgregar(`${id}-nuevo`)}</div>
      ) : (
        lectura.tipo !== "cargando" && (
          <p className="acciones-pantalla">
            <button type="button" onClick={() => abrir("agregar", null)} disabled={enviando}>
              Agregar un contacto
            </button>
          </p>
        )
      )}

      {lectura.tipo === "sin_servicio" ? (
        <>
          {/* Son teléfonos de emergencia: sin la lista de gestión, queda la general a la vista. */}
          <div className="tarjeta">
            <p>No se pudo consultar la lista para gestionar los contactos.</p>
            <button className="secundario" type="button" onClick={reconsultar} disabled={enviando}>
              Consultar de nuevo
            </button>
          </div>
          {listaGeneral}
        </>
      ) : (
        <section aria-labelledby={`${id}-lista`} className="tarjeta">
          <h2 id={`${id}-lista`}>{nombre}</h2>
          {lectura.tipo === "cargando" && <p>Consultando…</p>}
          {lectura.tipo === "ok" && lectura.contactos.length === 0 && <p>El edificio no tiene contactos de emergencia cargados.</p>}
          {lectura.tipo === "ok" && lectura.contactos.length > 0 && (
            <ul className="filas-registro" aria-label="Contactos de emergencia">
              {lectura.contactos.map((c) => (
                <li key={c.id} className="fila-con-atajo">
                  <FilaDeRegistro
                    titulo={titulo(c)}
                    resumen={resumen(c)}
                    alAbrir={() => abrirFicha(c)}
                    refFila={refFila(c.id)}
                    abierta={fichaAbierta === c.id}
                    deshabilitada={enviando}
                  />
                  {c.telefono !== null && <EnlaceTelefono telefono={c.telefono} />}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {enFicha !== undefined && ficha(enFicha)}
    </>
  );

  function titulo(c: Contacto) {
    return `${TEXTO_FUNCION[c.funcion]}: ${c.nombre}`;
  }

  /** En la fila, lo que dice si el contacto está en condiciones; el teléfono va al lado, a un toque, y
   * todo lo demás en la ficha. */
  function resumen(c: Contacto) {
    return [
      ...(c.telefono === null ? ["Sin teléfono"] : []),
      c.verificado_en === null ? "Sin comprobar" : "Comprobado",
      ...(c.observado ? ["Dato señalado como incorrecto"] : []),
    ].join(" · ");
  }

  /** La ficha de un contacto con sus datos de gestión y sus actos (la API decide si se puede). Lo ya
   * hecho no se ofrece de nuevo (G-54). */
  function ficha(c: Contacto) {
    const base = `${id}-${c.id}`;
    const accion = abierto?.contacto?.id === c.id ? abierto.accion : null;
    const comprobar = ofreceComprobar(c, new Date());
    const actos = ACTOS_SOBRE_UNO.filter((a) => (a !== "senalar" || !c.observado) && (a !== "verificar" || comprobar));
    return (
      <FichaDeRegistro
        titulo={titulo(c)}
        alCerrar={() => cerrarFicha()}
        ocupada={enviando}
        datos={[
          { etiqueta: "Función", valor: TEXTO_FUNCION[c.funcion] },
          { etiqueta: "Nombre", valor: c.nombre },
          { etiqueta: "De quién es el número", valor: TEXTO_NATURALEZA[c.naturaleza] },
          { etiqueta: "Teléfono", valor: c.telefono === null ? "Sin teléfono" : <EnlaceTelefono telefono={c.telefono} /> },
          ...(c.consentimiento_referencia === null ? [] : [{ etiqueta: "Dónde consta que aceptó figurar", valor: c.consentimiento_referencia }]),
          ...(c.indicaciones === null ? [] : [{ etiqueta: "Indicaciones", valor: c.indicaciones }]),
          { etiqueta: "Vigencia", valor: vigencia(c) },
          { etiqueta: "Última modificación", valor: momento(c.actualizado_en) },
          { etiqueta: "Última comprobación", valor: c.verificado_en === null ? "Sin comprobar" : momento(c.verificado_en) },
          ...(c.observado ? [{ etiqueta: "Dato señalado como incorrecto", valor: c.observacion ?? "Sin el motivo" }] : []),
        ]}
        ventana={accion !== null && accion !== "agregar" && ventana(c, accion, base)}
      >
        {/* Sin «Marcar como comprobado», lo que dice por qué: la comprobación reciente. */}
        {!comprobar && c.verificado_en !== null && <p>Comprobado el {FORMATO_FECHA.format(new Date(c.verificado_en))}.</p>}
        <p className="ficha-registro__acciones">
          {actos.map((a) => (
            <button key={a} type="button" className={a === "retirar" ? "secundario peligro" : "secundario"} onClick={() => abrir(a, c)} disabled={enviando}>
              {TEXTO_ACTO[a]}
            </button>
          ))}
        </p>
      </FichaDeRegistro>
    );
  }

  /** Agregar, en la página, debajo del título (CON-04 lo pasa a una ventana en la tanda 7). */
  function formularioAgregar(titulo: string) {
    return (
      <form className="formulario formulario-movil" onSubmit={enviar} aria-labelledby={`${titulo}-accion`}>
        <h2 id={`${titulo}-accion`}>Agregar un contacto</h2>
        {camposDatos(true, titulo)}
        <p>
          <button type="submit" disabled={enviando}>
            Agregar el contacto
          </button>{" "}
          <button type="button" className="secundario" onClick={cancelarAgregar} disabled={enviando}>
            Cancelar
          </button>
        </p>
      </form>
    );
  }

  /** Los campos de un contacto: los de agregar (con «Desde») o los de corregir. */
  function camposDatos(nuevo: boolean, titulo: string) {
    const profesional = datos.naturaleza === "profesional";
    const ayudaTelefono = `${titulo}-ayuda-telefono`;
    const ayudaTexto = `${titulo}-ayuda-texto`;
    const errorTelefono = telefonoMalEscrito(datos.telefono) ? TELEFONO_MAL_ESCRITO : errores.telefono;
    return (
      <>
        <label htmlFor={`${titulo}-funcion`}>Función</label>
        <select
          id={`${titulo}-funcion`}
          data-foco
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
      </>
    );
  }

  /** El acto sobre un contacto, en su ventana encima de la ficha (CON-05). */
  function ventana(c: Contacto, accion: Exclude<Accion, "agregar">, titulo: string) {
    const alEnviar = () => enviarDesdeLaFicha({ accion, contacto: c });
    switch (accion) {
      case "corregir":
        return (
          <VentanaDeFormulario titulo="Corregir el contacto" textoEnviar="Guardar la corrección" alEnviar={alEnviar} alCerrar={volverALaFicha}>
            {camposDatos(false, titulo)}
          </VentanaDeFormulario>
        );
      case "retirar":
        // Retirar no se puede deshacer: se pregunta una vez, en la ventana, sin un paso previo.
        return (
          <VentanaDeFormulario titulo="Retirar el contacto" textoEnviar="Sí, retirar" peligro alEnviar={alEnviar} alCerrar={volverALaFicha}>
            <p>¿Retirar el contacto {c.nombre}? No se puede deshacer.</p>
          </VentanaDeFormulario>
        );
      case "verificar":
        return (
          <VentanaDeFormulario titulo="Dejar constancia de la comprobación" textoEnviar="Lo comprobé" alEnviar={alEnviar} alCerrar={volverALaFicha}>
            <p>
              Sirve para dejar constancia de que se llamó o se comprobó de otra forma que el contacto es correcto. Resuelve el dato señalado, si había.
            </p>
          </VentanaDeFormulario>
        );
      case "senalar": {
        const ayuda = `${titulo}-ayuda`;
        return (
          <VentanaDeFormulario titulo="Señalar un dato incorrecto" textoEnviar="Señalar el error" alEnviar={alEnviar} alCerrar={volverALaFicha}>
            <p id={ayuda}>Qué está mal, sin teléfonos ni correos. Lo resuelve quien corrija o compruebe el contacto.</p>
            <label htmlFor={`${titulo}-motivo`}>Motivo</label>
            <input
              id={`${titulo}-motivo`}
              data-foco
              type="text"
              autoComplete="off"
              maxLength={LARGO_TEXTO}
              pattern={PATRON_TEXTO}
              required
              {...marcaDe(`${titulo}-motivo`, errores.motivo, ayuda)}
              value={motivo}
              onChange={(e) => {
                clave.current = null;
                setErrores((actuales) => sinElDe(actuales, "motivo"));
                setMotivo(e.target.value);
              }}
            />
            <ErrorDeCampo idCampo={`${titulo}-motivo`} mensaje={errores.motivo} />
          </VentanaDeFormulario>
        );
      }
    }
  }
}
