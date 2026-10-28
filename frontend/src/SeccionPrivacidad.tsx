import { type FormEvent, useEffect, useId, useState } from "react";
import { Link } from "react-router";

import { CampoContrasena } from "./CampoContrasena";
import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { encabezadoCsrf } from "./csrf";
import { RUTA_ENTRAR } from "./enlace";
import { RUTA_SEGUNDO_FACTOR } from "./SegundoFactor";

// Sección "Tus pedidos sobre tus datos" de /cuenta (RF37, corte 2d-2). Decisión del propietario
// del 28-10-2026: la confirmación de un caso y la copia de los datos propios van solo dentro de
// la aplicación, al titular autenticado. Lista sus casos (`GET /api/v1/privacidad/casos`); uno
// abierto se confirma con el código que llegó al correo y uno de copia confirmado se descarga.
// Cada pedido pide reautenticación reciente y el backend decide: la contraseña si la tiene, haber
// entrado hace menos de 10 minutos si no, y el segundo factor verificado si lo activó
// (403 `reautenticacion_requerida`, `detalle.falta`). La copia se guarda como archivo en el
// dispositivo desde la respuesta: no hay ningún enlace que se pueda compartir, y se entrega una
// sola vez. Sin casos pendientes, la sección no se muestra.

type Caso = components["schemas"]["CasoPrivacidad"];

const ESTADO_SIN_SESION = 401;
const ESTADO_DEMASIADOS = 429;
const SIN_CONEXION = "No pudimos completar el pedido. Revisá la conexión y probá de nuevo.";
const NOMBRE_DERECHO: Record<Caso["derecho"], string> = {
  acceso: "Copia de tus datos",
  rectificacion_nombre: "Corregir tu nombre",
  supresion: "Suprimir tus datos",
};
const NOMBRE_ESTADO: Record<Caso["estado"], string> = {
  abierto: "espera tu confirmación",
  confirmado: "confirmado",
  resuelto: "resuelto",
  rechazado: "rechazado",
};

type Aviso = { tipo: "ok" | "error"; texto: string } | { tipo: "falta"; falta: string };

function campoDe(error: unknown, campo: string): unknown {
  return typeof error === "object" && error !== null && campo in error
    ? (error as Record<string, unknown>)[campo]
    : undefined;
}

function avisoDeError(error: unknown, status: number): Aviso {
  const codigo = campoDe(error, "codigo");
  if (codigo === "reautenticacion_requerida") {
    const detalle = campoDe(error, "detalle");
    return { tipo: "falta", falta: String(campoDe(detalle, "falta") ?? "") };
  }
  if (codigo === "codigo_caso_invalido" || codigo === "caso_no_disponible")
    return { tipo: "error", texto: String(campoDe(error, "mensaje") ?? SIN_CONEXION) };
  if (status === ESTADO_DEMASIADOS)
    return { tipo: "error", texto: "Hubo demasiados intentos. Esperá un rato y probá de nuevo." };
  return { tipo: "error", texto: SIN_CONEXION };
}

/** Guarda la copia como archivo del dispositivo; el enlace es local y se descarta enseguida. */
function guardarArchivo(contenido: unknown, caso: string) {
  const blob = new Blob([JSON.stringify(contenido, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `mis-datos-consorcio-en-mano-${caso}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

interface Props {
  contrasena: boolean;
  segundoFactor: boolean;
  fecha: (iso: string) => string;
  sinSesion: () => void;
}

export function SeccionPrivacidad({ contrasena, segundoFactor, fecha, sinSesion }: Props) {
  const id = useId();
  const [casos, setCasos] = useState<Caso[] | null>(null);
  const [codigos, setCodigos] = useState<Record<string, string>>({});
  const [clave, setClave] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);

  useEffect(() => {
    let vigente = true;
    void api
      .GET("/api/v1/privacidad/casos")
      .then(({ data, response }) => {
        if (!vigente) return;
        if (response.status === ESTADO_SIN_SESION) sinSesion();
        else setCasos(data?.elementos ?? []);
      })
      .catch(() => vigente && setCasos([]));
    return () => {
      vigente = false;
    };
  }, [sinSesion]);

  const pendientes = (casos ?? []).filter(
    (c) => c.estado === "abierto" || (c.estado === "confirmado" && c.derecho === "acceso"),
  );
  // Sin pendientes no se muestra, salvo para dejar a la vista el aviso del último paso.
  if (pendientes.length === 0 && aviso === null) return null;

  function actualizar(caso: string, cambio: Partial<Caso>) {
    setCasos((actuales) => (actuales ?? []).map((c) => (c.id === caso ? { ...c, ...cambio } : c)));
  }

  const reautenticacion = () => ({ contrasena: contrasena ? clave : null });

  function confirmar(evento: FormEvent<HTMLFormElement>, caso: Caso) {
    evento.preventDefault();
    setEnviando(true);
    setAviso(null);
    void api
      .POST("/api/v1/privacidad/casos/{caso_id}/confirmar", {
        params: { path: { caso_id: caso.id } },
        body: { codigo: codigos[caso.id] ?? "", ...reautenticacion() },
        headers: encabezadoCsrf(),
      })
      .then(({ error, response }) => {
        if (response.status === 204) {
          actualizar(caso.id, { estado: "confirmado", codigo_vence_en: null });
          setClave("");
          setAviso({ tipo: "ok", texto: "Listo: confirmaste tu pedido." });
        } else if (response.status === ESTADO_SIN_SESION) sinSesion();
        else setAviso(avisoDeError(error, response.status));
      })
      .catch(() => setAviso({ tipo: "error", texto: SIN_CONEXION }))
      .finally(() => setEnviando(false));
  }

  function descargar(evento: FormEvent<HTMLFormElement>, caso: Caso) {
    evento.preventDefault();
    setEnviando(true);
    setAviso(null);
    void api
      .POST("/api/v1/privacidad/casos/{caso_id}/copia", {
        params: { path: { caso_id: caso.id } },
        body: reautenticacion(),
        headers: encabezadoCsrf(),
      })
      .then(({ data, error, response }) => {
        if (data !== undefined) {
          guardarArchivo(data, caso.id);
          actualizar(caso.id, { estado: "resuelto", resuelto_en: data.generada_en });
          setClave("");
          setAviso({ tipo: "ok", texto: "Listo: guardamos la copia de tus datos en este dispositivo." });
        } else if (response.status === ESTADO_SIN_SESION) sinSesion();
        else setAviso(avisoDeError(error, response.status));
      })
      .catch(() => setAviso({ tipo: "error", texto: SIN_CONEXION }))
      .finally(() => setEnviando(false));
  }

  const campoClave = (caso: string) =>
    contrasena && (
      <>
        <label htmlFor={`${id}-${caso}-clave`}>Tu contraseña</label>
        <CampoContrasena
          id={`${id}-${caso}-clave`}
          autoComplete="current-password"
          value={clave}
          onChange={(e) => setClave(e.target.value)}
          required
        />
      </>
    );

  return (
    <section aria-labelledby={`${id}-titulo`} className="tarjeta">
      <h2 id={`${id}-titulo`}>Tus pedidos sobre tus datos</h2>
      <p>
        Por seguridad, cada paso pide volver a confirmar que sos vos
        {contrasena ? " con tu contraseña" : ": si entraste hace más de 10 minutos, salí y volvé a entrar"}
        {segundoFactor ? ", y haber verificado tu segundo factor en los últimos 10 minutos" : ""}.
      </p>
      {pendientes.map((caso) => (
        <article key={caso.id} aria-labelledby={`${id}-${caso.id}`}>
          <h3 id={`${id}-${caso.id}`}>
            {NOMBRE_DERECHO[caso.derecho]}: {NOMBRE_ESTADO[caso.estado]}
          </h3>
          <p>Pedido del {fecha(caso.abierto_en)}.</p>
          {caso.estado === "abierto" ? (
            <form className="formulario" onSubmit={(e) => confirmar(e, caso)} noValidate>
              <label htmlFor={`${id}-${caso.id}-codigo`}>Código que te llegó por correo</label>
              <input
                id={`${id}-${caso.id}-codigo`}
                autoComplete="one-time-code"
                value={codigos[caso.id] ?? ""}
                onChange={(e) => setCodigos({ ...codigos, [caso.id]: e.target.value })}
                required
              />
              {campoClave(caso.id)}
              <button type="submit" disabled={enviando}>
                Confirmar el pedido
              </button>
            </form>
          ) : (
            <form className="formulario" onSubmit={(e) => descargar(e, caso)} noValidate>
              <p>La copia se entrega una sola vez y queda guardada en este dispositivo.</p>
              {campoClave(caso.id)}
              <button type="submit" disabled={enviando}>
                Descargar la copia de mis datos
              </button>
            </form>
          )}
        </article>
      ))}
      <div role="status" aria-live="polite">
        {aviso?.tipo === "ok" && <p className="aviso-ok">{aviso.texto}</p>}
        {aviso?.tipo === "error" && <p className="aviso-error">{aviso.texto}</p>}
        {aviso?.tipo === "falta" && aviso.falta === "contrasena" && (
          <p className="aviso-error">Escribí tu contraseña actual.</p>
        )}
        {aviso?.tipo === "falta" && aviso.falta === "entrar_de_nuevo" && (
          <p className="aviso-error">
            Por seguridad, <Link to={RUTA_ENTRAR}>volvé a entrar con el enlace del correo</Link> y hacelo
            dentro de los 10 minutos.
          </p>
        )}
        {aviso?.tipo === "falta" && aviso.falta === "segundo_factor" && (
          <p className="aviso-error">
            Antes, <Link to={RUTA_SEGUNDO_FACTOR}>verificá un código en la página del segundo factor</Link>{" "}
            (vale 10 minutos).
          </p>
        )}
      </div>
    </section>
  );
}
