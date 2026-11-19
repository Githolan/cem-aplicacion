import { useEffect, useId, useRef, useState } from "react";

import { CampoContrasena } from "./CampoContrasena";
import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { encabezadoCsrf } from "./csrf";
import { PedirCodigo } from "./egresos/PedirCodigo";
import { ErrorDeCampo, type ErroresDeCampo, faltaCompletar, hayErrores, marcaDe, sinElDe, SIN_ERRORES } from "./ErrorDeCampo";
import { codigoDe, mensajeDeFalla, SIN_CONEXION } from "./fallas";
import { type FinDeEnvio, VentanaDeFormulario } from "./VentanaDeFormulario";

// Sección "Pedidos sobre los datos de la cuenta" de /cuenta (RF37, corte 2d-2). Decisión del propietario
// del 28-10-2026: la confirmación de un caso y la copia de los datos propios van solo dentro de
// la aplicación, al titular autenticado. Lista sus casos (`GET /api/v1/privacidad/casos`); uno
// abierto se confirma con el código que llegó al correo y uno de copia confirmado se descarga.
// Cada pedido pide reautenticación reciente y el backend decide: la contraseña si la tiene, haber
// entrado hace menos de 10 minutos si no, y el segundo factor verificado si lo activó
// (403 `reautenticacion_requerida`, `detalle.falta`); el segundo factor se pide encima y el paso
// se repite con lo que ya estaba escrito. La copia se guarda como archivo en el
// dispositivo desde la respuesta: no hay ningún enlace que se pueda compartir. Si el navegador no
// la guardó, se vuelve a pedir; el caso se cierra cuando la persona avisa que la guardó
// (`…/recibida`). Sin casos pendientes, la sección no se muestra, y en la primera consulta tampoco
// (la página ya cargó: sin caso no tiene nada que decir). Si la consulta falla, sí: esconderla
// dejaría sin camino a quien tiene un caso abierto. Desde que la persona pide consultar de nuevo,
// la sección queda a la vista con su estado (consultando, sin pedidos o los pedidos) y el foco va
// a su título, para no perderlo cuando desaparece el botón (P3 77 del repositorio CEM).
//
// Informe de fricción, tanda 9: cada pedido es una fila con un botón, y el código y la contraseña
// se escriben en una ventana propia (CTA-06), que marca lo que falta sin mandar nada (C8G-14). Con
// el código del correo vencido no se ofrece confirmar: se dice hasta cuándo servía y qué hace falta
// (C8G-15). Descargada la copia queda «Copia guardada: cerrar el pedido» y, aparte, «No se guardó»
// para volver a descargarla (CTA-07).

type Caso = components["schemas"]["CasoPrivacidad"];

const ESTADO_SIN_SESION = 401;
const ESTADO_DEMASIADOS = 429;
/** Lo que se espera antes de liberar el archivo local: el navegador ya empezó a guardarlo. */
const ESPERA_LIBERAR_MS = 60_000;
const NOMBRE_DERECHO: Record<Caso["derecho"], string> = {
  acceso: "Copia de los datos",
  rectificacion_nombre: "Corregir el nombre",
  supresion: "Suprimir los datos",
};
const NOMBRE_ESTADO: Record<Caso["estado"], string> = {
  abierto: "espera confirmación",
  confirmado: "confirmado",
  resuelto: "resuelto",
  rechazado: "rechazado",
};
const FALTA_CODIGO = "Falta el código que llegó por correo.";
/** FL-CTA-06 (tanda 10): el código lo manda solo soporte, uno por pedido (`0039`); vencido, soporte abre
 * otro pedido con otro código. El medio es el que ya dice ese correo: responderlo. */
export const CODIGO_NUEVO_POR_SOPORTE =
  "Para seguir, responder al correo que trajo el código: soporte abre un pedido nuevo, con otro código.";
const FALTA_CONTRASENA = "Falta la contraseña.";
const FALTA_CONTRASENA_ACTUAL = "Falta la contraseña actual.";

type Aviso = { tipo: "ok" | "error"; texto: string };

/** Cómo terminó un pedido sobre un caso. */
type Fin = { tipo: "ok" } | { tipo: "sin_sesion" } | { tipo: "segundo_factor" } | { tipo: "falta_contrasena" } | { tipo: "error"; texto: string };

/** La ventana abierta: confirmar el caso (código y contraseña) o descargar la copia (contraseña). */
type Ventana = { tipo: "confirmar" | "descargar"; caso: Caso };

function campoDe(error: unknown, campo: string): unknown {
  return typeof error === "object" && error !== null && campo in error
    ? (error as Record<string, unknown>)[campo]
    : undefined;
}

function finDeError(error: unknown, status: number): Fin {
  if (status === ESTADO_SIN_SESION) return { tipo: "sin_sesion" };
  const codigo = campoDe(error, "codigo");
  if (codigo === "reautenticacion_requerida") {
    const falta = campoDe(campoDe(error, "detalle"), "falta");
    if (falta === "segundo_factor") return { tipo: "segundo_factor" };
    if (falta === "contrasena") return { tipo: "falta_contrasena" };
    // Otra entrada reciente: la pide la hoja de entrar encima; acá no queda un mensaje viejo.
    return { tipo: "error", texto: "" };
  }
  if (codigo === "codigo_caso_invalido" || codigo === "caso_no_disponible")
    return { tipo: "error", texto: String(campoDe(error, "mensaje") ?? SIN_CONEXION) };
  if (status === ESTADO_DEMASIADOS) return { tipo: "error", texto: "Hubo demasiados intentos. Reintentar en un rato." };
  return { tipo: "error", texto: mensajeDeFalla(status, codigoDe(error)) };
}

/**
 * Guarda la copia como archivo del dispositivo. El enlace es local (`blob:`), va dentro del
 * documento mientras se hace el clic (algunos navegadores ignoran uno suelto) y se libera más
 * tarde: liberarlo en el acto corta la descarga en algunos navegadores.
 */
function guardarArchivo(contenido: unknown, caso: string) {
  const blob = new Blob([JSON.stringify(contenido, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `mis-datos-consorcio-en-mano-${caso}.json`;
  a.hidden = true;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), ESPERA_LIBERAR_MS);
}

/** Si el código del correo todavía sirve (`codigo_vence_en` nulo: no hay o ya se usó). */
function codigoVigente(caso: Caso): boolean {
  return caso.codigo_vence_en !== null && Date.parse(caso.codigo_vence_en) > Date.now();
}

interface Props {
  contrasena: boolean;
  fecha: (iso: string) => string;
  sinSesion: () => void;
}

export function SeccionPrivacidad({ contrasena, fecha, sinSesion }: Props) {
  const id = useId();
  const [casos, setCasos] = useState<Caso[] | null>(null);
  const [fallaConsulta, setFallaConsulta] = useState<string | null>(null);
  const [consulta, setConsulta] = useState(0);
  const [reconsulta, setReconsulta] = useState(false);
  const titulo = useRef<HTMLHeadingElement>(null);
  const [ventana, setVentana] = useState<Ventana | null>(null);
  const [codigo, setCodigo] = useState("");
  const [clave, setClave] = useState("");
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  // La confirmación del segundo factor que espera un paso: la resuelve la hoja de confirmar.
  const [confirmando, setConfirmando] = useState<((verificado: boolean) => void) | null>(null);

  useEffect(() => {
    let vigente = true;
    void api
      .GET("/api/v1/privacidad/casos")
      .then(({ data, error, response }) => {
        if (!vigente) return;
        if (response.status === ESTADO_SIN_SESION) sinSesion();
        else if (data !== undefined) setCasos(data.elementos);
        else setFallaConsulta(mensajeDeFalla(response.status, codigoDe(error)));
      })
      .catch(() => vigente && setFallaConsulta(SIN_CONEXION));
    return () => {
      vigente = false;
    };
  }, [sinSesion, consulta]);

  useEffect(() => {
    if (reconsulta) titulo.current?.focus();
  }, [reconsulta, consulta]);

  const pendientes = (casos ?? []).filter(
    (c) => c.estado === "abierto" || (c.estado === "confirmado" && c.derecho === "acceso"),
  );
  const conPedidos = pendientes.length > 0 || aviso !== null;
  // Sin pendientes no se muestra, salvo para dejar a la vista el aviso del último paso, la falla
  // de la consulta o lo que pasó después de consultar de nuevo.
  if (!conPedidos && fallaConsulta === null && !reconsulta) return null;

  function actualizar(caso: string, cambio: Partial<Caso>) {
    setCasos((actuales) => (actuales ?? []).map((c) => (c.id === caso ? { ...c, ...cambio } : c)));
  }

  function pedirVerificacion(): Promise<boolean> {
    return new Promise((resolver) => setConfirmando(() => resolver));
  }

  /** Corre un pedido; si falta el segundo factor lo pide encima y lo repite con lo mismo. */
  async function conVerificacion(pedido: () => Promise<Fin>): Promise<Fin> {
    for (;;) {
      const fin = await pedido();
      if (fin.tipo !== "segundo_factor") return fin;
      const verificado = await pedirVerificacion();
      setConfirmando(null);
      if (!verificado) return { tipo: "error", texto: "" };
    }
  }

  async function pedirConfirmar(caso: Caso): Promise<Fin> {
    try {
      const { error, response } = await api.POST("/api/v1/privacidad/casos/{caso_id}/confirmar", {
        params: { path: { caso_id: caso.id } },
        body: { codigo: codigo.trim(), contrasena: contrasena ? clave : null },
        headers: encabezadoCsrf(),
      });
      if (response.status === 204) {
        actualizar(caso.id, { estado: "confirmado", codigo_vence_en: null });
        setAviso({ tipo: "ok", texto: "Listo: el pedido quedó confirmado." });
        return { tipo: "ok" };
      }
      return finDeError(error, response.status);
    } catch {
      return { tipo: "error", texto: SIN_CONEXION };
    }
  }

  async function pedirCopia(caso: Caso): Promise<Fin> {
    try {
      const { data, error, response } = await api.POST("/api/v1/privacidad/casos/{caso_id}/copia", {
        params: { path: { caso_id: caso.id } },
        body: { contrasena: contrasena ? clave : null },
        headers: encabezadoCsrf(),
      });
      if (data !== undefined) {
        guardarArchivo(data, caso.id);
        actualizar(caso.id, { copia_entregada_en: caso.copia_entregada_en ?? data.generada_en });
        setAviso({ tipo: "ok", texto: "La copia se descargó. Conviene revisar que el archivo se haya guardado y avisarlo acá." });
        return { tipo: "ok" };
      }
      return finDeError(error, response.status);
    } catch {
      return { tipo: "error", texto: SIN_CONEXION };
    }
  }

  /** El envío de la ventana: primero lo que falta, sin pedido al servidor (C8G-14). */
  async function enviarVentana(abierta: Ventana): Promise<FinDeEnvio> {
    const faltan = faltaCompletar({
      ...(abierta.tipo === "confirmar" ? { codigo: [codigo, FALTA_CODIGO] as const } : {}),
      ...(contrasena ? { clave: [clave, FALTA_CONTRASENA] as const } : {}),
    });
    if (hayErrores(faltan)) {
      setErrores(faltan);
      return "seguir";
    }
    setAviso(null);
    const fin = await conVerificacion(() => (abierta.tipo === "confirmar" ? pedirConfirmar(abierta.caso) : pedirCopia(abierta.caso)));
    if (fin.tipo === "ok") return "cerrar";
    if (fin.tipo === "sin_sesion") {
      sinSesion();
      return "seguir";
    }
    if (fin.tipo === "falta_contrasena") {
      setErrores({ clave: FALTA_CONTRASENA_ACTUAL });
      return "seguir";
    }
    if (fin.tipo === "error" && fin.texto !== "") return { rechazo: fin.texto };
    return "seguir";
  }

  function abrir(tipo: Ventana["tipo"], caso: Caso) {
    setCodigo("");
    setClave("");
    setErrores(SIN_ERRORES);
    setAviso(null);
    setVentana({ tipo, caso });
  }

  /** Un paso sin campos (descargar sin contraseña, cerrar el pedido): directo desde la fila. */
  function directo(pedido: () => Promise<Fin>) {
    setEnviando(true);
    setAviso(null);
    void conVerificacion(pedido)
      .then((fin) => {
        if (fin.tipo === "sin_sesion") sinSesion();
        else if (fin.tipo === "falta_contrasena") setAviso({ tipo: "error", texto: FALTA_CONTRASENA_ACTUAL });
        else if (fin.tipo === "error" && fin.texto !== "") setAviso({ tipo: "error", texto: fin.texto });
      })
      .finally(() => setEnviando(false));
  }

  async function pedirRecibida(caso: Caso): Promise<Fin> {
    try {
      const { error, response } = await api.POST("/api/v1/privacidad/casos/{caso_id}/recibida", {
        params: { path: { caso_id: caso.id } },
        headers: encabezadoCsrf(),
      });
      if (response.status === 204) {
        actualizar(caso.id, { estado: "resuelto" });
        setAviso({ tipo: "ok", texto: "Listo: el pedido quedó cerrado." });
        return { tipo: "ok" };
      }
      return finDeError(error, response.status);
    } catch {
      return { tipo: "error", texto: SIN_CONEXION };
    }
  }

  /** Descargar: con contraseña, en su ventana; sin ella, directo. */
  function descargar(caso: Caso) {
    if (contrasena) abrir("descargar", caso);
    else directo(() => pedirCopia(caso));
  }

  function accionesDe(caso: Caso) {
    if (caso.estado === "abierto") {
      if (codigoVigente(caso))
        return (
          <>
            <p>El código sirve hasta el {fecha(caso.codigo_vence_en ?? "")}.</p>
            <button type="button" disabled={enviando} onClick={() => abrir("confirmar", caso)}>
              Confirmar el pedido
            </button>
          </>
        );
      // C8G-15: con el código vencido o ya usado, confirmar lleva a un rechazo seguro: no se ofrece.
      return (
        <p>
          {caso.codigo_vence_en === null
            ? "El código de este pedido ya no sirve."
            : `El código de este pedido venció el ${fecha(caso.codigo_vence_en)}.`}{" "}
          {CODIGO_NUEVO_POR_SOPORTE}
        </p>
      );
    }
    if (caso.copia_entregada_en === null)
      return (
        <button type="button" disabled={enviando} onClick={() => descargar(caso)}>
          Descargar la copia de mis datos
        </button>
      );
    // CTA-07: descargada, lo que sigue es avisar que se guardó; volver a descargar queda aparte.
    return (
      <div className="acciones-pantalla">
        <button type="button" disabled={enviando} onClick={() => directo(() => pedirRecibida(caso))}>
          Copia guardada: cerrar el pedido
        </button>
        <button type="button" className="sutil" disabled={enviando} onClick={() => descargar(caso)}>
          No se guardó
        </button>
      </div>
    );
  }

  return (
    <section aria-labelledby={`${id}-titulo`} className="tarjeta">
      <h2 id={`${id}-titulo`} ref={titulo} tabIndex={-1}>
        Pedidos sobre los datos de la cuenta
      </h2>
      {fallaConsulta !== null && (
        <>
          <p className="aviso-error" role="alert">
            {fallaConsulta}
          </p>
          <button className="secundario"
            type="button"
            onClick={() => {
              setFallaConsulta(null);
              setReconsulta(true);
              setConsulta((n) => n + 1);
            }}
          >
            Consultar de nuevo
          </button>
        </>
      )}
      {fallaConsulta === null && casos === null && <p role="status">Consultando los pedidos…</p>}
      {fallaConsulta === null && casos !== null && !conPedidos && (
        <p role="status">No hay pedidos pendientes sobre los datos de la cuenta.</p>
      )}
      {pendientes.map((caso) => (
        <article key={caso.id} aria-labelledby={`${id}-${caso.id}`}>
          <h3 id={`${id}-${caso.id}`}>
            {NOMBRE_DERECHO[caso.derecho]}: {NOMBRE_ESTADO[caso.estado]}
          </h3>
          <p>Pedido del {fecha(caso.abierto_en)}.</p>
          {accionesDe(caso)}
        </article>
      ))}
      <div role="status" aria-live="polite">
        {aviso?.tipo === "ok" && <p className="aviso-ok">{aviso.texto}</p>}
        {aviso?.tipo === "error" && <p className="aviso-error">{aviso.texto}</p>}
      </div>
      {ventana !== null && (
        <VentanaDeFormulario
          titulo={ventana.tipo === "confirmar" ? "Confirmar el pedido" : "Descargar la copia de los datos"}
          textoEnviar={ventana.tipo === "confirmar" ? "Confirmar el pedido" : "Descargar la copia de mis datos"}
          alEnviar={() => enviarVentana(ventana)}
          alCerrar={() => setVentana(null)}
        >
          {ventana.tipo === "confirmar" && (
            <>
              <label htmlFor={`${id}-codigo`}>Código que llegó por correo</label>
              <input
                id={`${id}-codigo`}
                data-foco
                autoComplete="one-time-code"
                value={codigo}
                {...marcaDe(`${id}-codigo`, errores.codigo)}
                onChange={(e) => {
                  setCodigo(e.target.value);
                  setErrores((anteriores) => sinElDe(anteriores, "codigo"));
                }}
              />
              <ErrorDeCampo idCampo={`${id}-codigo`} mensaje={errores.codigo} />
            </>
          )}
          {contrasena && (
            <>
              <label htmlFor={`${id}-clave`}>Contraseña</label>
              <CampoContrasena
                id={`${id}-clave`}
                data-foco={ventana.tipo === "descargar" ? true : undefined}
                autoComplete="current-password"
                value={clave}
                {...marcaDe(`${id}-clave`, errores.clave)}
                onChange={(e) => {
                  setClave(e.target.value);
                  setErrores((anteriores) => sinElDe(anteriores, "clave"));
                }}
              />
              <ErrorDeCampo idCampo={`${id}-clave`} mensaje={errores.clave} />
            </>
          )}
        </VentanaDeFormulario>
      )}
      {confirmando !== null && (
        <PedirCodigo acto="seguir con el pedido" alVerificar={() => confirmando(true)} alCancelar={() => confirmando(false)} />
      )}
    </section>
  );
}
