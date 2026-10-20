import { type FormEvent, type MutableRefObject, useCallback, useEffect, useId, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { encabezadoCsrf } from "./csrf";
import { RUTA_ENTRAR } from "./enlace";
import { codigoDe, esDeConexion, mensajeDeFalla, SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "./fallas";
import { TextoDeUnaLinea } from "./TextoDeUnaLinea";

// Página /mandatos (H2b 2b-4c, PR 2): los mandatos y las decisiones de un edificio, su detalle,
// registrar una decisión y dar de alta o de baja un mandato. El frontend nunca concede ni deduce
// permisos: pide las listas y muestra lo que la API devuelve; un 403 se explica y nada más. Los
// avisos de A15 (la decisión la registraste vos) solo advierten antes de enviar: la autoridad es
// la API, la política y la base. Diseño: `doc/diseno/H2b_4c_Diseno.md` del repositorio CEM, §7.
// Diseño visual provisorio: el definitivo llega con G3.

type Consorcio = components["schemas"]["ConsorcioResumen"];
type Mandato = components["schemas"]["MandatoResumen"];
type MandatoDetalle = components["schemas"]["MandatoDetalle"];
type Decision = components["schemas"]["DecisionResumen"];
type DecisionDetalle = components["schemas"]["DecisionDetalle"];
type Candidato = components["schemas"]["CandidatoMandato"];
type TipoMandato = components["schemas"]["TipoMandato"];
type EstadoMandato = components["schemas"]["EstadoMandato"];
type TipoDecision = components["schemas"]["TipoDecision"];
type CausaBaja = components["schemas"]["CausaBaja"];

export const RUTA_MANDATOS = "/mandatos";

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
const LARGO_DESCRIPCION = 500;
const LARGO_REFERENCIA = 300;
/** Filas visibles de los textos largos en el celular (doc UX de invitaciones y formularios, §3). */
const FILAS_DESCRIPCION = 4;
const FILAS_REFERENCIA = 2;
const LARGO_NOMBRE = 200;
const LARGO_MATRICULA = 100;
/** Cuánto de la descripción entra en una opción: el ancho de un `<select>` es el de su opción más larga. */
const LARGO_OPCION = 60;
const PATRON_CUIT = "[0-9]{11}";
const PATRON_SHA256 = "[0-9a-fA-F]{64}";
/** Un texto con algo más que espacios (la API rechaza el vacío después de recortar). */
const PATRON_TEXTO = ".*\\S.*";

const TEXTO_TIPO_MANDATO: Record<TipoMandato, string> = {
  miembro_consejo: "Consejo de propietarios",
  administrador: "Administración",
  administrador_externo: "Administración externa",
};
const TIPOS_MANDATO = Object.keys(TEXTO_TIPO_MANDATO) as TipoMandato[];

const TEXTO_ESTADO_MANDATO: Record<EstadoMandato, string> = {
  vigente: "Vigente",
  futuro: "Empieza más adelante",
  terminado: "Terminado por su fecha",
  dado_de_baja: "Dado de baja",
  error_carga: "Anulado por error de carga",
};

const TEXTO_TIPO_DECISION: Record<TipoDecision, string> = {
  asamblea: "Asamblea",
  consejo: "Reunión del Consejo",
  contrato_administracion: "Contrato de administración",
  inscripcion_rpa: "Inscripción en el registro de administradores",
};
const TIPOS_DECISION = Object.keys(TEXTO_TIPO_DECISION) as TipoDecision[];

const TEXTO_CAUSA: Record<CausaBaja, string> = {
  remocion: "Remoción por asamblea",
  renuncia: "Renuncia",
  fallecimiento_incapacidad: "Fallecimiento o incapacidad",
  error_carga: "Error de carga",
  otro: "Otra causa",
};
const CAUSAS = Object.keys(TEXTO_CAUSA) as CausaBaja[];
/** Un mandato que ya terminó por su fecha solo admite la baja por error de carga. */
const CAUSAS_TERMINADO: CausaBaja[] = ["error_carga"];
const ESTADOS_CON_BAJA: EstadoMandato[] = ["vigente", "futuro", "terminado"];
/** Respuestas de la baja que dicen que el detalle quedó viejo: se cierra y se vuelve a pedir la lista. */
const BAJA_SOBRE_ESTADO_VIEJO = ["mandato_ya_dado_de_baja", "mandato_terminado"];

// Las fechas del contrato son días sin hora: se muestran tal cual, sin pasar por otra zona.
const FORMATO_DIA = new Intl.DateTimeFormat("es-AR", { dateStyle: "long", timeZone: "UTC" });
const fechaDe = (iso: string) => new Date(`${iso}T00:00:00Z`);
const dia = (iso: string) => FORMATO_DIA.format(fechaDe(iso));
// Un momento (cuándo se registró la baja) sí tiene hora: se muestra en la zona del edificio.
const FORMATO_MOMENTO = new Intl.DateTimeFormat("es-AR", {
  dateStyle: "long",
  timeStyle: "short",
  hourCycle: "h23",
  timeZone: "America/Argentina/Buenos_Aires",
});
const momento = (iso: string) => FORMATO_MOMENTO.format(new Date(iso));
const recortar = (texto: string) => (texto.length > LARGO_OPCION ? `${texto.slice(0, LARGO_OPCION - 1)}…` : texto);

/** Qué se estaba haciendo cuando la API respondió con error. */
export type Accion = "decision" | "alta" | "baja";

const SIN_PERMISO: Record<Accion, string> = {
  decision: "No tenés permiso para registrar decisiones en este edificio.",
  alta:
    "No tenés permiso para dar este alta. Puede ser que tu mandato no lo incluya, que la decisión" +
    " la hayas registrado vos (quien la registra no puede usarla) o que el alta sea para vos.",
  baja:
    "No tenés permiso para dar esta baja. Puede ser que tu mandato no lo incluya, que la asamblea" +
    " la hayas registrado vos (quien la registra no puede usarla para remover) o que la baja sea tuya.",
};

const NO_ENCONTRADO: Record<Accion, string> = {
  decision: "No encontramos el edificio o ya no tenés acceso.",
  alta: "No encontramos el edificio o ya no tenés acceso.",
  baja: "No encontramos ese mandato en este edificio.",
};

/** Los motivos de dominio, por el código del cuerpo uniforme. */
const POR_CODIGO: Record<string, string> = {
  administracion_ocupada: "Ya hay un mandato de administración en esas fechas en este edificio.",
  mandato_superpuesto: "La persona ya tiene un mandato de ese tipo en esas fechas.",
  consejo_incompatible_con_administracion:
    "La persona administra este edificio como externa en esas fechas: el Consejo fiscaliza y no co-administra.",
  mandato_ya_dado_de_baja: "El mandato ya tiene una baja registrada.",
  mandato_terminado: "El mandato ya terminó por su fecha: solo admite una baja por error de carga.",
  fecha_futura: "La fecha de la decisión no puede ser posterior a hoy: se registran actos ya tomados.",
  decision_invalida: "La decisión no existe en este edificio o no corresponde (la remoción pide una asamblea).",
  persona_sin_vinculo: "La persona no tiene un vínculo confirmado, vigente o futuro, en este edificio.",
  externo_por_invitacion:
    "La administración externa se da de alta sin cuenta: la cuenta se asocia después, por invitación.",
  mandato_sin_cuenta: "Un miembro del Consejo o un administrador necesita su cuenta.",
  cuit_invalido: "El CUIT no es válido.",
  facultad_fuera_de_paquete: "Alguna facultad no corresponde a ese tipo de mandato.",
  respaldo_invalido: "La remoción se respalda con una asamblea; cualquier otra causa, con el instrumento.",
};

/** Qué decir ante el error de un envío, según el estado y el código del cuerpo uniforme. */
export function mensajeDeError(estado: number, codigo: string | undefined, accion: Accion): string {
  if (estado === ESTADO.sinPermiso && codigo === "csrf_invalido") return "No pudimos verificar el pedido. Recargá la página.";
  if (estado === ESTADO.sinPermiso) return SIN_PERMISO[accion];
  if (estado === ESTADO.noEncontrado) return NO_ENCONTRADO[accion];
  if (estado === ESTADO.conflicto && codigo === "idempotencia_en_curso")
    return "El pedido se está procesando. Probá de nuevo en unos segundos.";
  if (estado === ESTADO.limite) return "Llegaste al límite de pedidos por ahora. Probá más tarde.";
  if (estado === ESTADO.invalido && codigo === "idempotencia_reutilizada")
    return "Cambiaste los datos de un intento anterior. Volvé a enviar el formulario.";
  if ((estado === ESTADO.conflicto || estado === ESTADO.invalido) && codigo !== undefined && codigo in POR_CODIGO)
    return POR_CODIGO[codigo] as string;
  if (estado === ESTADO.conflicto) return "El pedido choca con el estado actual del edificio. Recargá la página y revisá los datos.";
  if (estado === ESTADO.invalido) return "Revisá los datos del formulario.";
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

type Pantalla =
  | { tipo: "cargando" }
  | { tipo: "sin_servicio"; deConexion: boolean }
  | { tipo: "sin_consorcios" }
  | { tipo: "lista"; consorcios: Consorcio[] };

/** Una lista del edificio: la API la manda, la niega (403) o no se pudo consultar. */
type Lectura<T> = { tipo: "cargando" } | { tipo: "sin_permiso" } | { tipo: "sin_servicio" } | { tipo: "ok"; elementos: T[] };

type Detalle = { tipo: "mandato"; datos: MandatoDetalle } | { tipo: "decision"; datos: DecisionDetalle };

type Aviso = { texto: string; ok: boolean };

type Clave = MutableRefObject<string | null>;

type Resultado<T> = { data?: T; error?: unknown; response: Response };

/** Un 401 en cualquier pedido de la página: se va a /entrar. */
class SinSesion extends Error {}

function exigirSesion(response: Response) {
  if (response.status === ESTADO.sinSesion) throw new SinSesion();
}

function leer<T>({ data, response }: Resultado<{ elementos: T[] }>): Lectura<T> {
  exigirSesion(response);
  if (response.status === ESTADO.sinPermiso) return { tipo: "sin_permiso" };
  if (data === undefined) throw new Error(String(response.status));
  return { tipo: "ok", elementos: data.elementos };
}

const CARGANDO = { tipo: "cargando" } as const;
const SIN_RESPUESTA = "sin_respuesta";
const SIN_SERVICIO = { tipo: "sin_servicio" } as const;

/** Una lista que no se pudo consultar no arrastra a las otras; un 401 sí corta todo. */
function cadaUna<T>(pedido: Promise<Lectura<T>>): Promise<Lectura<T>> {
  return pedido.catch((e: unknown) => {
    if (e instanceof SinSesion) throw e;
    return SIN_SERVICIO;
  });
}

export function Mandatos() {
  const id = useId();
  const navegar = useNavigate();
  const [pantalla, setPantalla] = useState<Pantalla>({ tipo: "cargando" });
  const [consorcio, setConsorcio] = useState("");
  const [mandatos, setMandatos] = useState<Lectura<Mandato>>(CARGANDO);
  const [decisiones, setDecisiones] = useState<Lectura<Decision>>(CARGANDO);
  const [candidatos, setCandidatos] = useState<Lectura<Candidato>>(CARGANDO);
  const [detalle, setDetalle] = useState<Detalle | null>(null);
  // A15: qué decisiones registró quien mira, según el detalle que manda la API; `sin_respuesta`
  // si la consulta falló (el aviso es de esa decisión, no de la página).
  const [propias, setPropias] = useState<Record<string, boolean | "sin_respuesta">>({});
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  // Cada envío vuelve a pedir las listas: qué se ve lo decide la API.
  const [vuelta, setVuelta] = useState(0);
  const consultado = useRef(false);
  // Una clave por intento lógico de cada formulario: se conserva si el pedido se cae y se renueva
  // al cambiar los datos o cuando el intento terminó.
  const claveDecision = useRef<string | null>(null);
  const claveAlta = useRef<string | null>(null);
  const claveBaja = useRef<string | null>(null);
  // El campo con el foco al enviar: mientras el envío corre queda deshabilitado; al terminar el
  // foco va al resultado, salvo que la persona lo haya llevado a otra parte.
  const foco = useRef<HTMLElement | null>(null);
  const textoAviso = useRef<HTMLParagraphElement | null>(null);
  const tituloDetalle = useRef<HTMLHeadingElement | null>(null);
  // Cada pedido de detalle lleva su número: una respuesta que llega después de otro pedido, de
  // cerrar el detalle o de cambiar de edificio se descarta.
  const pedidoDetalle = useRef(0);
  // El botón «Ver detalle» que abrió el detalle: al cerrarlo, el foco vuelve ahí.
  const origenDetalle = useRef<HTMLElement | null>(null);
  const formularioBaja = useRef<HTMLFormElement | null>(null);
  const confirmarSi = useRef<HTMLButtonElement | null>(null);
  const botonBaja = useRef<HTMLButtonElement | null>(null);

  // Formulario de decisión.
  const [tipoDecision, setTipoDecision] = useState<TipoDecision>("asamblea");
  const [fechaDecision, setFechaDecision] = useState("");
  const [descripcionDecision, setDescripcionDecision] = useState("");
  const [referencia, setReferencia] = useState("");
  const [sha256, setSha256] = useState("");

  // Formulario de alta.
  const [tipoMandato, setTipoMandato] = useState<TipoMandato>("miembro_consejo");
  const [persona, setPersona] = useState("");
  const [nombre, setNombre] = useState("");
  const [cuit, setCuit] = useState("");
  const [matricula, setMatricula] = useState("");
  const [decisionAlta, setDecisionAlta] = useState("");
  const [desde, setDesde] = useState("");
  const [hasta, setHasta] = useState("");

  // Formulario de baja (en el detalle del mandato).
  const [causa, setCausa] = useState<CausaBaja>("renuncia");
  const [descripcionBaja, setDescripcionBaja] = useState("");
  const [instrumento, setInstrumento] = useState("");
  const [decisionBaja, setDecisionBaja] = useState("");
  const [confirmarBaja, setConfirmarBaja] = useState(false);

  useEffect(() => {
    if (enviando || foco.current === null) return;
    // Solo si nadie tomó el foco en el medio (la persona pudo ir a otra parte de la página). El
    // campo que lo tenía pudo desmontarse (la baja cierra el detalle): el foco cae en el cuerpo.
    const ahora = document.activeElement;
    if (ahora === null || ahora === document.body || ahora === foco.current) (textoAviso.current ?? foco.current).focus();
    foco.current = null;
  }, [enviando]);

  // La confirmación de la baja: al abrirse, el foco va a «Sí»; al cerrarse con «No», vuelve al
  // botón que la abrió. Mientras se envía no se toca: al terminar, el foco va al aviso (efecto de
  // arriba, que corre antes).
  useEffect(() => {
    if (confirmarBaja) confirmarSi.current?.focus();
    else if (!enviando && document.activeElement === document.body) botonBaja.current?.focus();
  }, [confirmarBaja, enviando]);

  // Al abrir un detalle, el foco va a su título: el lector de pantalla anuncia el resultado.
  useEffect(() => {
    if (detalle !== null) tituloDetalle.current?.focus();
  }, [detalle]);

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
    const path = { consorcio_id: consorcio };
    void Promise.all([
      cadaUna(api.GET("/api/v1/consorcios/{consorcio_id}/mandatos", { params: { path } }).then(leer<Mandato>)),
      cadaUna(api.GET("/api/v1/consorcios/{consorcio_id}/decisiones", { params: { path } }).then(leer<Decision>)),
      cadaUna(api.GET("/api/v1/consorcios/{consorcio_id}/mandatos/candidatos", { params: { path } }).then(leer<Candidato>)),
    ])
      .then(([m, d, c]) => {
        // Una respuesta del edificio anterior no pisa al que ya se eligió.
        if (!vigente) return;
        setMandatos(m);
        setDecisiones(d);
        setCandidatos(c);
        if (c.tipo !== "ok") return;
        setPersona((actual) => {
          if (c.elementos.some((x) => x.persona_id === actual)) return actual;
          // Otra persona es otro intento: otra clave.
          claveAlta.current = null;
          return c.elementos[0]?.persona_id ?? "";
        });
      })
      .catch((e: unknown) => {
        if (vigente) atajar(e);
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, vuelta, atajar]);

  /** Pregunta a la API si quien mira registró la decisión (A15), para avisar antes de enviar. */
  const consultarPropia = useCallback(
    (decision: string) => {
      // Una consulta que falló se repite al volver a elegir la decisión.
      if (decision === "" || (decision in propias && propias[decision] !== SIN_RESPUESTA)) return;
      void api
        .GET("/api/v1/consorcios/{consorcio_id}/decisiones/{decision_id}", {
          params: { path: { consorcio_id: consorcio, decision_id: decision } },
        })
        .then(({ data, response }) => {
          exigirSesion(response);
          if (data === undefined) throw new Error(String(response.status));
          setPropias((actual) => ({ ...actual, [data.id]: data.registrada_por_vos }));
        })
        .catch((e: unknown) => {
          if (e instanceof SinSesion) aEntrar();
          // Queda en esa decisión (por su id): una respuesta tardía de otro edificio no se ve acá.
          else setPropias((actual) => ({ ...actual, [decision]: SIN_RESPUESTA }));
        });
    },
    [consorcio, propias, aEntrar],
  );

  /** Cualquier cambio en los datos de un formulario es otro intento: otra clave. */
  function cambiar<T>(clave: Clave, fijar: (v: T) => void) {
    return (v: T) => {
      clave.current = null;
      fijar(v);
    };
  }

  /** En la baja, además, un cambio cierra la confirmación: lo que se confirma es lo que se ve. */
  function cambiarBaja<T>(fijar: (v: T) => void) {
    return (v: T) => {
      setConfirmarBaja(false);
      cambiar(claveBaja, fijar)(v);
    };
  }

  function enviar<T>(
    clave: Clave,
    accion: Accion,
    pedido: (clave: string) => Promise<Resultado<T>>,
    listo: (datos: T) => void,
    fallo?: (estado: number | null, codigo: string | undefined) => void,
  ) {
    const activo = document.activeElement;
    foco.current = activo instanceof HTMLElement && activo !== document.body ? activo : null;
    clave.current ??= crypto.randomUUID();
    setEnviando(true);
    setAviso(null);
    void pedido(clave.current)
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data === undefined) {
          const codigo = codigoDe(error);
          if (renovarClave(response.status, codigo)) clave.current = null;
          setAviso({ texto: mensajeDeError(response.status, codigo, accion), ok: false });
          fallo?.(response.status, codigo);
          return;
        }
        clave.current = null;
        listo(data);
        setVuelta((v) => v + 1);
      })
      .catch((e: unknown) => {
        atajar(e);
        fallo?.(null, undefined);
      })
      .finally(() => setEnviando(false));
  }

  function registrarDecision(evento: FormEvent) {
    evento.preventDefault();
    enviar(
      claveDecision,
      "decision",
      (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/decisiones", {
          params: { path: { consorcio_id: consorcio }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          headers: encabezadoCsrf(),
          body: {
            tipo: tipoDecision,
            fecha: fechaDecision,
            descripcion: descripcionDecision.trim(),
            referencia: referencia.trim(),
            sha256: sha256.trim() === "" ? null : sha256.trim(),
          },
        }),
      (datos) => {
        // Quien la registró es quien mira: A15 ya lo sabe sin preguntar.
        setPropias((actual) => ({ ...actual, [datos.id]: true }));
        setDescripcionDecision("");
        setReferencia("");
        setSha256("");
        setAviso({ texto: "Listo: registramos la decisión.", ok: true });
      },
    );
  }

  const externo = tipoMandato === "administrador_externo";

  function darDeAlta(evento: FormEvent) {
    evento.preventDefault();
    enviar(
      claveAlta,
      "alta",
      (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/mandatos", {
          params: { path: { consorcio_id: consorcio }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          headers: encabezadoCsrf(),
          body: {
            tipo: tipoMandato,
            decision_id: decisionAlta,
            desde,
            hasta: hasta === "" ? null : hasta,
            persona_id: externo ? null : persona,
            nombre: externo ? nombre.trim() : null,
            cuit: externo ? cuit : null,
            matricula: tipoMandato === "miembro_consejo" || matricula.trim() === "" ? null : matricula.trim(),
          },
        }),
      () => {
        setNombre("");
        setCuit("");
        setMatricula("");
        setAviso({ texto: "Listo: el mandato quedó dado de alta.", ok: true });
      },
    );
  }

  function limpiarBaja() {
    claveBaja.current = null;
    setConfirmarBaja(false);
    setDescripcionBaja("");
    setInstrumento("");
    setDecisionBaja("");
  }

  function cerrarDetalle() {
    pedidoDetalle.current += 1;
    setDetalle(null);
    origenDetalle.current?.focus();
  }

  function darDeBaja(mandato: MandatoDetalle) {
    // «Sí» envía sin pasar por el envío del formulario: la validación del navegador va acá.
    if (formularioBaja.current !== null && !formularioBaja.current.reportValidity()) {
      setConfirmarBaja(false);
      return;
    }
    const remocion = causa === "remocion";
    enviar(
      claveBaja,
      "baja",
      (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/mandatos/{mandato_id}/baja", {
          params: {
            path: { consorcio_id: consorcio, mandato_id: mandato.id },
            header: { [ENCABEZADO_IDEMPOTENCIA]: clave },
          },
          headers: encabezadoCsrf(),
          body: {
            causa,
            descripcion: descripcionBaja.trim(),
            decision_id: remocion ? decisionBaja : null,
            instrumento: remocion ? null : instrumento.trim(),
          },
        }),
      () => {
        limpiarBaja();
        pedidoDetalle.current += 1;
        setDetalle(null);
        setAviso({ texto: `Listo: dimos de baja el mandato de ${mandato.nombre}.`, ok: true });
      },
      (estado, codigo) => {
        // Un error cierra la confirmación: el reintento se vuelve a confirmar.
        setConfirmarBaja(false);
        if (estado === ESTADO.noEncontrado || (codigo !== undefined && BAJA_SOBRE_ESTADO_VIEJO.includes(codigo))) {
          pedidoDetalle.current += 1;
          setDetalle(null);
          setVuelta((v) => v + 1);
        }
      },
    );
  }

  function verMandato(mandato: Mandato) {
    const activo = document.activeElement;
    origenDetalle.current = activo instanceof HTMLElement ? activo : null;
    const numero = ++pedidoDetalle.current;
    // El detalle anterior (y su baja) no queda operable mientras llega el que se eligió.
    setDetalle(null);
    setAviso(null);
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/mandatos/{mandato_id}", {
        params: { path: { consorcio_id: consorcio, mandato_id: mandato.id } },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (numero !== pedidoDetalle.current) return;
        if (data !== undefined) {
          // Lo escrito para otro mandato no pasa a este.
          limpiarBaja();
          setCausa(data.estado === "terminado" ? "error_carga" : "renuncia");
          setDetalle({ tipo: "mandato", datos: data });
          return;
        }
        if (response.status === ESTADO.sinPermiso) setAviso({ texto: "No tenés permiso para ver ese mandato.", ok: false });
        else if (response.status === ESTADO.noEncontrado) {
          setAviso({ texto: "Ese mandato ya no está en este edificio.", ok: false });
          setVuelta((v) => v + 1);
        } else setAviso({ texto: mensajeDeFalla(response.status, codigoDe(error)), ok: false });
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion) aEntrar();
        else if (numero === pedidoDetalle.current) atajar(e);
      });
  }

  function verDecision(decision: Decision) {
    const activo = document.activeElement;
    origenDetalle.current = activo instanceof HTMLElement ? activo : null;
    const numero = ++pedidoDetalle.current;
    // El detalle anterior (y su baja) no queda operable mientras llega el que se eligió.
    setDetalle(null);
    setAviso(null);
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/decisiones/{decision_id}", {
        params: { path: { consorcio_id: consorcio, decision_id: decision.id } },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (numero !== pedidoDetalle.current) return;
        if (data !== undefined) {
          setPropias((actual) => ({ ...actual, [data.id]: data.registrada_por_vos }));
          setDetalle({ tipo: "decision", datos: data });
          return;
        }
        if (response.status === ESTADO.sinPermiso) setAviso({ texto: "No tenés permiso para ver esa decisión.", ok: false });
        else if (response.status === ESTADO.noEncontrado) {
          setAviso({ texto: "Esa decisión ya no está en este edificio.", ok: false });
          setVuelta((v) => v + 1);
        } else setAviso({ texto: mensajeDeFalla(response.status, codigoDe(error)), ok: false });
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion) aEntrar();
        else if (numero === pedidoDetalle.current) atajar(e);
      });
  }

  function elegirEdificio(valor: string) {
    // Nada del edificio anterior queda elegible mientras llegan los datos del nuevo.
    for (const clave of [claveDecision, claveAlta, claveBaja]) clave.current = null;
    pedidoDetalle.current += 1;
    setMandatos(CARGANDO);
    setDecisiones(CARGANDO);
    setCandidatos(CARGANDO);
    setDetalle(null);
    setPropias({});
    setDecisionAlta("");
    limpiarBaja();
    setPersona("");
    setAviso(null);
    setConsorcio(valor);
  }

  const listaDecisiones = decisiones.tipo === "ok" ? decisiones.elementos : [];
  const asambleas = listaDecisiones.filter((d) => d.tipo === "asamblea");
  const textoDecision = (d: Decision) => `${TEXTO_TIPO_DECISION[d.tipo]} del ${dia(d.fecha)}: ${d.descripcion}`;
  // Siempre montado: el lector anuncia el aviso cuando llega, aunque el foco siga en el selector.
  const avisoA15 = (decision: string, clave: "alta" | "remover", uso: string) => (
    <p id={`${id}-a15-${clave}`} aria-live="polite" className={propias[decision] === undefined || propias[decision] === false ? undefined : "aviso-error"}>
      {propias[decision] === SIN_RESPUESTA &&
        "No pudimos consultar quién registró esta decisión. Si la registraste vos, la plataforma igual rechaza el envío."}
      {propias[decision] === true &&
        `Esa decisión la registraste vos: no podés usarla para ${uso}. Tiene que hacerlo otra persona del Consejo o de la administración.`}
    </p>
  );
  const conMensaje = aviso !== null || pantalla.tipo !== "lista";

  return (
    <>
      <h1>Mandatos y decisiones</h1>

      {pantalla.tipo === "lista" && (
        <>
          <p className="tarjeta">
            Acá se registran las decisiones que respaldan cada mandato y se dan de alta o de baja el
            Consejo y la administración del edificio. Lo que podés ver o hacer lo decide la
            plataforma según tu mandato.
          </p>

          {pantalla.consorcios.length > 1 && (
            <div className="tarjeta formulario">
              <label htmlFor={`${id}-consorcio`}>Edificio</label>
              <select
                id={`${id}-consorcio`}
                value={consorcio}
                disabled={enviando}
                onChange={(e) => elegirEdificio(e.target.value)}
              >
                {pantalla.consorcios.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.nombre}
                  </option>
                ))}
              </select>
            </div>
          )}

          <section aria-labelledby={`${id}-mandatos`} className="tarjeta">
            <h2 id={`${id}-mandatos`}>Mandatos</h2>
            {mandatos.tipo === "cargando" && <p>Consultando…</p>}
            {mandatos.tipo === "sin_servicio" && <p>No pudimos consultar los mandatos.</p>}
            {mandatos.tipo === "sin_permiso" && <p>No tenés permiso para ver los mandatos de este edificio.</p>}
            {mandatos.tipo === "ok" && mandatos.elementos.length === 0 && <p>Todavía no hay mandatos cargados.</p>}
            {mandatos.tipo === "ok" && mandatos.elementos.length > 0 && (
              <ul className="sesiones">
                {mandatos.elementos.map((m) => (
                  <li key={m.id}>
                    <h3 id={`${id}-${m.id}`}>
                      {m.nombre}, {TEXTO_TIPO_MANDATO[m.tipo]}
                    </h3>
                    <dl className="detalles">
                      <dt>Estado</dt>
                      <dd>{TEXTO_ESTADO_MANDATO[m.estado]}</dd>
                      <dt>Desde</dt>
                      <dd>{dia(m.vigente_desde)}</dd>
                      {m.vigente_hasta && (
                        <>
                          <dt>Hasta</dt>
                          <dd>{dia(m.vigente_hasta)} (ese día ya no rige)</dd>
                        </>
                      )}
                      {m.cuit && (
                        <>
                          <dt>CUIT</dt>
                          <dd>{m.cuit}</dd>
                        </>
                      )}
                      <dt>Cuenta asociada</dt>
                      <dd>{m.con_cuenta ? "Sí" : "No"}</dd>
                    </dl>
                    <button type="button" onClick={() => verMandato(m)} disabled={enviando} aria-describedby={`${id}-${m.id}`}>
                      Ver detalle
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section aria-labelledby={`${id}-decisiones`} className="tarjeta">
            <h2 id={`${id}-decisiones`}>Decisiones</h2>
            {decisiones.tipo === "cargando" && <p>Consultando…</p>}
            {decisiones.tipo === "sin_servicio" && <p>No pudimos consultar las decisiones.</p>}
            {decisiones.tipo === "sin_permiso" && <p>No tenés permiso para ver las decisiones de este edificio.</p>}
            {decisiones.tipo === "ok" && decisiones.elementos.length === 0 && <p>Todavía no hay decisiones registradas.</p>}
            {decisiones.tipo === "ok" && decisiones.elementos.length > 0 && (
              <ul className="sesiones">
                {decisiones.elementos.map((d) => (
                  <li key={d.id}>
                    <h3 id={`${id}-${d.id}`}>
                      {TEXTO_TIPO_DECISION[d.tipo]} del {dia(d.fecha)}
                    </h3>
                    <p>{d.descripcion}</p>
                    <button type="button" onClick={() => verDecision(d)} disabled={enviando} aria-describedby={`${id}-${d.id}`}>
                      Ver detalle
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {detalle?.tipo === "decision" && (
            <section aria-labelledby={`${id}-detalle`} className="tarjeta">
              <h2 id={`${id}-detalle`} ref={tituloDetalle} tabIndex={-1}>
                {TEXTO_TIPO_DECISION[detalle.datos.tipo]} del {dia(detalle.datos.fecha)}
              </h2>
              <dl className="detalles">
                <dt>Qué se decidió</dt>
                <dd>{detalle.datos.descripcion}</dd>
                <dt>Dónde consta</dt>
                <dd>{detalle.datos.referencia}</dd>
                {detalle.datos.sha256 && (
                  <>
                    <dt>Huella del acta</dt>
                    <dd>{detalle.datos.sha256}</dd>
                  </>
                )}
                <dt>Quién la registró</dt>
                <dd>
                  {detalle.datos.registrada_por_vos
                    ? "Vos: no podés usarla para dar de alta ni para remover."
                    : detalle.datos.del_equipo
                      ? "El equipo de la plataforma."
                      : "Otra persona del edificio."}
                </dd>
              </dl>
              <button type="button" onClick={cerrarDetalle} disabled={enviando}>
                Cerrar el detalle
              </button>
            </section>
          )}

          {detalle?.tipo === "mandato" && (
            <section aria-labelledby={`${id}-detalle`} className="tarjeta">
              <h2 id={`${id}-detalle`} ref={tituloDetalle} tabIndex={-1}>
                {detalle.datos.nombre}, {TEXTO_TIPO_MANDATO[detalle.datos.tipo]}
              </h2>
              <dl className="detalles">
                <dt>Estado</dt>
                <dd>{TEXTO_ESTADO_MANDATO[detalle.datos.estado]}</dd>
                <dt>Desde</dt>
                <dd>{dia(detalle.datos.vigente_desde)}</dd>
                {detalle.datos.vigente_hasta && (
                  <>
                    <dt>Hasta</dt>
                    <dd>{dia(detalle.datos.vigente_hasta)} (ese día ya no rige)</dd>
                  </>
                )}
                {detalle.datos.cuit && (
                  <>
                    <dt>CUIT</dt>
                    <dd>{detalle.datos.cuit}</dd>
                  </>
                )}
                {detalle.datos.matricula && (
                  <>
                    <dt>Matrícula</dt>
                    <dd>{detalle.datos.matricula}</dd>
                  </>
                )}
                <dt>Decisión que lo respalda</dt>
                <dd>
                  {(() => {
                    const d = listaDecisiones.find((x) => x.id === detalle.datos.decision_id);
                    return d ? textoDecision(d) : "No figura entre las decisiones que podés ver.";
                  })()}
                </dd>
                <dt>Facultades</dt>
                <dd>
                  <ul>
                    {detalle.datos.facultades.map((f) => (
                      <li key={f}>{f}</li>
                    ))}
                  </ul>
                </dd>
                {detalle.datos.causa_baja && (
                  <>
                    <dt>Causa de la baja</dt>
                    <dd>{TEXTO_CAUSA[detalle.datos.causa_baja]}</dd>
                  </>
                )}
                {detalle.datos.finalizado_en && (
                  <>
                    <dt>Baja registrada</dt>
                    <dd>{momento(detalle.datos.finalizado_en)}</dd>
                  </>
                )}
                {detalle.datos.motivo_fin && (
                  <>
                    <dt>Motivo</dt>
                    <dd>{detalle.datos.motivo_fin}</dd>
                  </>
                )}
                {detalle.datos.baja_decision_id && (
                  <>
                    <dt>Asamblea que lo removió</dt>
                    <dd>
                      {(() => {
                        const d = listaDecisiones.find((x) => x.id === detalle.datos.baja_decision_id);
                        return d ? textoDecision(d) : "No figura entre las decisiones que podés ver.";
                      })()}
                    </dd>
                  </>
                )}
                {detalle.datos.instrumento_referencia && (
                  <>
                    <dt>Instrumento</dt>
                    <dd>{detalle.datos.instrumento_referencia}</dd>
                  </>
                )}
              </dl>

              {ESTADOS_CON_BAJA.includes(detalle.datos.estado) && (
                <form
                  ref={formularioBaja}
                  className="formulario formulario-movil"
                  onSubmit={(e) => {
                    e.preventDefault();
                    setConfirmarBaja(true);
                  }}
                >
                  <h3>Dar de baja</h3>
                  <label htmlFor={`${id}-causa`}>Causa</label>
                  <select
                    id={`${id}-causa`}
                    value={causa}
                    disabled={enviando}
                    onChange={(e) => cambiarBaja(setCausa)(e.target.value as CausaBaja)}
                  >
                    {(detalle.datos.estado === "terminado" ? CAUSAS_TERMINADO : CAUSAS).map((c) => (
                      <option key={c} value={c}>
                        {TEXTO_CAUSA[c]}
                      </option>
                    ))}
                  </select>
                  <label htmlFor={`${id}-descripcion-baja`}>Por qué termina</label>
                  <input
                    id={`${id}-descripcion-baja`}
                    type="text"
                    autoComplete="off"
                    maxLength={LARGO_DESCRIPCION}
                    pattern={PATRON_TEXTO}
                    required
                    disabled={enviando}
                    value={descripcionBaja}
                    onChange={(e) => cambiarBaja(setDescripcionBaja)(e.target.value)}
                  />
                  {causa === "remocion" ? (
                    <>
                      <label htmlFor={`${id}-decision-baja`}>Asamblea que lo remueve</label>
                      <select
                        id={`${id}-decision-baja`}
                        value={decisionBaja}
                        required
                        disabled={enviando}
                        aria-describedby={`${id}-a15-remover`}
                        onChange={(e) => {
                          consultarPropia(e.target.value);
                          cambiarBaja(setDecisionBaja)(e.target.value);
                        }}
                      >
                        <option value="">Elegí la asamblea</option>
                        {asambleas.map((d) => (
                          <option key={d.id} value={d.id}>
                            {recortar(textoDecision(d))}
                          </option>
                        ))}
                      </select>
                      {avisoA15(decisionBaja, "remover", "remover")}
                    </>
                  ) : (
                    <>
                      <label htmlFor={`${id}-instrumento`}>Instrumento (nota, certificado u otro)</label>
                      <input
                        id={`${id}-instrumento`}
                        type="text"
                        autoComplete="off"
                        maxLength={LARGO_REFERENCIA}
                        pattern={PATRON_TEXTO}
                        required
                        disabled={enviando}
                        value={instrumento}
                        onChange={(e) => cambiarBaja(setInstrumento)(e.target.value)}
                      />
                    </>
                  )}
                  {confirmarBaja ? (
                    <div role="group" aria-labelledby={`${id}-confirmar-baja`}>
                      <p id={`${id}-confirmar-baja`}>
                        ¿Dar de baja el mandato de {detalle.datos.nombre}? La baja no se puede deshacer.
                      </p>
                      <button type="button" ref={confirmarSi} onClick={() => darDeBaja(detalle.datos)} disabled={enviando}>
                        Sí, dar de baja
                      </button>{" "}
                      <button type="button" onClick={() => setConfirmarBaja(false)} disabled={enviando}>
                        No
                      </button>
                    </div>
                  ) : (
                    <button type="submit" ref={botonBaja} disabled={enviando}>
                      Dar de baja
                    </button>
                  )}
                </form>
              )}
              <button type="button" onClick={cerrarDetalle} disabled={enviando}>
                Cerrar el detalle
              </button>
            </section>
          )}

          <section aria-labelledby={`${id}-nueva-decision`} className="tarjeta">
            <h2 id={`${id}-nueva-decision`}>Registrar una decisión</h2>
            <form className="formulario formulario-movil" onSubmit={registrarDecision}>
              <label htmlFor={`${id}-tipo-decision`}>Tipo</label>
              <select
                id={`${id}-tipo-decision`}
                value={tipoDecision}
                disabled={enviando}
                onChange={(e) => cambiar(claveDecision, setTipoDecision)(e.target.value as TipoDecision)}
              >
                {TIPOS_DECISION.map((t) => (
                  <option key={t} value={t}>
                    {TEXTO_TIPO_DECISION[t]}
                  </option>
                ))}
              </select>
              <label htmlFor={`${id}-fecha-decision`}>Fecha del acto</label>
              <input
                id={`${id}-fecha-decision`}
                type="date"
                required
                disabled={enviando}
                value={fechaDecision}
                onChange={(e) => cambiar(claveDecision, setFechaDecision)(e.target.value)}
              />
              <label htmlFor={`${id}-descripcion-decision`}>Qué se decidió</label>
              <TextoDeUnaLinea
                id={`${id}-descripcion-decision`}
                rows={FILAS_DESCRIPCION}
                autoComplete="off"
                maxLength={LARGO_DESCRIPCION}
                required
                disabled={enviando}
                value={descripcionDecision}
                onValor={cambiar(claveDecision, setDescripcionDecision)}
              />
              <label htmlFor={`${id}-referencia`}>Dónde consta (libro, acta y folio)</label>
              <TextoDeUnaLinea
                id={`${id}-referencia`}
                rows={FILAS_REFERENCIA}
                autoComplete="off"
                maxLength={LARGO_REFERENCIA}
                required
                disabled={enviando}
                value={referencia}
                onValor={cambiar(claveDecision, setReferencia)}
              />
              <label htmlFor={`${id}-sha256`}>Huella SHA-256 del acta escaneada (opcional)</label>
              <input
                id={`${id}-sha256`}
                type="text"
                autoComplete="off"
                pattern={PATRON_SHA256}
                spellCheck={false}
                autoCapitalize="none"
                disabled={enviando}
                value={sha256}
                onChange={(e) => cambiar(claveDecision, setSha256)(e.target.value)}
              />
              <button type="submit" disabled={enviando || consorcio === ""}>
                Registrar decisión
              </button>
            </form>
          </section>

          <section aria-labelledby={`${id}-alta`} className="tarjeta">
            <h2 id={`${id}-alta`}>Dar de alta un mandato</h2>
            {candidatos.tipo === "cargando" && <p>Consultando…</p>}
            {candidatos.tipo === "sin_servicio" && <p>No pudimos consultar quiénes pueden recibir un mandato.</p>}
            {candidatos.tipo === "sin_permiso" && <p>No tenés permiso para dar de alta mandatos en este edificio.</p>}
            {candidatos.tipo === "ok" && decisiones.tipo === "sin_permiso" && (
              <p>Para dar un alta hace falta elegir la decisión que la respalda, y no tenés permiso para verlas.</p>
            )}
            {candidatos.tipo === "ok" && decisiones.tipo === "sin_servicio" && (
              <p>Para dar un alta hace falta elegir la decisión que la respalda, y no pudimos consultarlas.</p>
            )}
            {candidatos.tipo === "ok" && decisiones.tipo === "ok" && (
              <form className="formulario formulario-movil" onSubmit={darDeAlta}>
                <label htmlFor={`${id}-tipo-mandato`}>Tipo de mandato</label>
                <select
                  id={`${id}-tipo-mandato`}
                  value={tipoMandato}
                  disabled={enviando}
                  onChange={(e) => cambiar(claveAlta, setTipoMandato)(e.target.value as TipoMandato)}
                >
                  {TIPOS_MANDATO.map((t) => (
                    <option key={t} value={t}>
                      {TEXTO_TIPO_MANDATO[t]}
                    </option>
                  ))}
                </select>
                {externo ? (
                  <>
                    <p>
                      La administración externa se carga por su razón social y su CUIT, sin cuenta: la
                      cuenta se asocia después, por invitación.
                    </p>
                    <label htmlFor={`${id}-nombre`}>Razón social o nombre</label>
                    <input
                      id={`${id}-nombre`}
                      type="text"
                      autoComplete="off"
                      maxLength={LARGO_NOMBRE}
                      pattern={PATRON_TEXTO}
                      required
                      disabled={enviando}
                      value={nombre}
                      onChange={(e) => cambiar(claveAlta, setNombre)(e.target.value)}
                    />
                    <label htmlFor={`${id}-cuit`}>CUIT (11 números, sin guiones)</label>
                    <input
                      id={`${id}-cuit`}
                      type="text"
                      inputMode="numeric"
                      autoComplete="off"
                      pattern={PATRON_CUIT}
                      required
                      disabled={enviando}
                      value={cuit}
                      onChange={(e) => cambiar(claveAlta, setCuit)(e.target.value)}
                    />
                  </>
                ) : candidatos.elementos.length === 0 ? (
                  <p>Nadie tiene todavía un vínculo confirmado en este edificio: sin él no se puede dar el mandato.</p>
                ) : (
                  <>
                    <label htmlFor={`${id}-persona`}>Persona (con vínculo confirmado en el edificio)</label>
                    <select
                      id={`${id}-persona`}
                      value={persona}
                      required
                      disabled={enviando}
                      onChange={(e) => cambiar(claveAlta, setPersona)(e.target.value)}
                    >
                      {candidatos.elementos.map((c) => (
                        <option key={c.persona_id} value={c.persona_id}>
                          {recortar(`${c.nombre} (${c.unidades.join(", ")})`)}
                        </option>
                      ))}
                    </select>
                  </>
                )}
                {tipoMandato !== "miembro_consejo" && (
                  <>
                    <label htmlFor={`${id}-matricula`}>Matrícula (opcional)</label>
                    <input
                      id={`${id}-matricula`}
                      type="text"
                      autoComplete="off"
                      maxLength={LARGO_MATRICULA}
                      pattern={PATRON_TEXTO}
                      disabled={enviando}
                      value={matricula}
                      onChange={(e) => cambiar(claveAlta, setMatricula)(e.target.value)}
                    />
                  </>
                )}
                <label htmlFor={`${id}-decision-alta`}>Decisión que lo respalda</label>
                <select
                  id={`${id}-decision-alta`}
                  value={decisionAlta}
                  required
                  disabled={enviando}
                  aria-describedby={`${id}-a15-alta`}
                  onChange={(e) => {
                    consultarPropia(e.target.value);
                    cambiar(claveAlta, setDecisionAlta)(e.target.value);
                  }}
                >
                  <option value="">Elegí la decisión</option>
                  {listaDecisiones.map((d) => (
                    <option key={d.id} value={d.id}>
                      {recortar(textoDecision(d))}
                    </option>
                  ))}
                </select>
                {avisoA15(decisionAlta, "alta", "dar el alta")}
                <label htmlFor={`${id}-desde`}>Desde</label>
                <input
                  id={`${id}-desde`}
                  type="date"
                  required
                  disabled={enviando}
                  value={desde}
                  onChange={(e) => cambiar(claveAlta, setDesde)(e.target.value)}
                />
                <label htmlFor={`${id}-hasta`}>Hasta (opcional; ese día ya no rige)</label>
                <input
                  id={`${id}-hasta`}
                  type="date"
                  min={desde === "" ? undefined : desde}
                  disabled={enviando}
                  aria-describedby={`${id}-vigencia-vacia`}
                  value={hasta}
                  onChange={(e) => cambiar(claveAlta, setHasta)(e.target.value)}
                />
                <p id={`${id}-vigencia-vacia`} aria-live="polite">
                  {hasta !== "" &&
                    hasta === desde &&
                    "Con «hasta» igual a «desde» el mandato nunca rige: el último día es el anterior a «hasta»."}
                </p>
                <button type="submit" disabled={enviando || (!externo && candidatos.elementos.length === 0)}>
                  Dar de alta
                </button>
              </form>
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
}
