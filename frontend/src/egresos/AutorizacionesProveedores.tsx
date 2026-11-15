import { useEffect, useId, useRef, useState } from "react";

import { CabeceraInterna } from "../CabeceraInterna";
import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { RUTA_INICIO } from "../enlace";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { fechaCorta } from "../pagos/textos";
import { REQUISITOS } from "../permisos";
import { type Aviso, ENCABEZADO_IDEMPOTENCIA, ESTADO, mensajeDeActo, mensajeDeError, renovarClave, sinLaFalla, sinPisarLoHecho } from "./comun";
import { ElegirConsorcio } from "./ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "./EstadoPantalla";
import { exigirSesion, SinSesion, useConsorcio } from "./useConsorcio";

// Página /proveedores/quien-los-ve: el Consejo autoriza a un habitante a ver los proveedores y sus
// seguros, o le retira la autorización, siempre con el motivo (app 0105; respuesta del propietario
// del 15-11-2026: los habitantes autorizados son «los que autorice el Consejo»). Nada se borra:
// cada cambio queda con quién lo hizo y cuándo. Lo decide la API con `proveedores.autorizar`, y la
// base repite la regla. Quién autoriza lo fija la configuración del edificio (por defecto, el
// Consejo); los roles que ya ven los proveedores por la configuración no necesitan autorización.

type Autorizacion = components["schemas"]["AutorizacionVerProveedores"];

export const RUTA_AUTORIZACIONES_PROVEEDORES = "/proveedores/quien-los-ve";
export const TITULO_AUTORIZACIONES_PROVEEDORES = "Quién ve los proveedores";
export const EXPLICACION =
  "El Consejo y la administración ven los proveedores y sus seguros. Además, el Consejo puede autorizar a un habitante: cada autorización y cada retiro quedan con el motivo, quién lo hizo y cuándo.";
export const SIN_HABITANTES = "El edificio no tiene habitantes con un vínculo vigente.";
export const AUTORIZAR = "Autorizar";
export const RETIRAR = "Retirar la autorización";
export const CONFIRMAR_AUTORIZAR = "Confirmar la autorización";
export const CONFIRMAR_RETIRAR = "Confirmar el retiro";
export const MOTIVO = "Motivo";
export const LARGO_MOTIVO = 500;

export function textoEstado(a: Autorizacion): string {
  if (a.registrado_en === null || a.registrado_en === undefined) return "Sin autorización.";
  const quien = a.registrado_por_nombre ?? "alguien del Consejo";
  const cuando = fechaCorta(a.registrado_en);
  return a.autorizada
    ? `Autorizado el ${cuando} por ${quien}: ${a.motivo ?? ""}`
    : `Autorización retirada el ${cuando} por ${quien}: ${a.motivo ?? ""}`;
}

type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "apagada"; texto: string } | { tipo: "ok"; elementos: Autorizacion[] };

export function AutorizacionesProveedores() {
  const elegido = useConsorcio(REQUISITOS.autorizarProveedores);
  return <DelEdificio key={elegido.consorcio} {...elegido} />;
}

function DelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar }: ReturnType<typeof useConsorcio>) {
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [aviso, setAviso] = useState<Aviso | null>(null);

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/autorizaciones-proveedores", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        const codigo = codigoDe(error);
        if (data !== undefined) setLectura({ tipo: "ok", elementos: data.elementos });
        else if (response.status === ESTADO.sinPermiso && codigo === "sin_permiso") aInicio();
        else if (codigo === "autorizacion_por_persona_apagada") setLectura({ tipo: "apagada", texto: mensajeDeError(response.status, codigo) });
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso(sinPisarLoHecho(mensajeDeError(response.status, codigo)));
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
  }, [consorcio, vuelta, aEntrar, aInicio]);

  function reintentarLectura() {
    setAviso(sinLaFalla);
    setLectura({ tipo: "cargando" });
    setVuelta((v) => v + 1);
  }

  function alCambiar(nueva: Autorizacion) {
    setLectura((l) => (l.tipo === "ok" ? { tipo: "ok", elementos: l.elementos.map((a) => (a.persona_id === nueva.persona_id ? nueva : a)) } : l));
    setAviso({ texto: nueva.autorizada ? `${nueva.nombre} ya puede ver los proveedores.` : `${nueva.nombre} ya no ve los proveedores.`, ok: true });
  }

  return (
    <>
      <CabeceraInterna titulo={TITULO_AUTORIZACIONES_PROVEEDORES} volver={RUTA_INICIO} destino="Inicio" />
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={false} elegir={setConsorcio} />
      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">Esta cuenta no tiene edificios en Consorcio en Mano.</p>}
      <ConsorcioSinServicio pantalla={pantalla} reintentar={reintentar} />
      <AvisoPantalla aviso={aviso} />

      {pantalla.tipo === "lista" && (
        <>
          <p>{EXPLICACION}</p>
          {lectura.tipo === "cargando" && <p role="status">Cargando los habitantes…</p>}
          {lectura.tipo === "sin_servicio" && <FallaDeLectura alReintentar={reintentarLectura} avisar={setAviso} />}
          {lectura.tipo === "apagada" && <p className="tarjeta">{lectura.texto}</p>}
          {lectura.tipo === "ok" &&
            (lectura.elementos.length === 0 ? (
              <p className="tarjeta">{SIN_HABITANTES}</p>
            ) : (
              <ul className="filas-registro" aria-label="Habitantes">
                {lectura.elementos.map((a) => (
                  <li key={a.persona_id}>
                    <Habitante consorcio={consorcio} autorizacion={a} aEntrar={aEntrar} alCambiar={alCambiar} />
                  </li>
                ))}
              </ul>
            ))}
        </>
      )}
    </>
  );
}

type PropsHabitante = {
  consorcio: string;
  autorizacion: Autorizacion;
  aEntrar: () => void;
  alCambiar: (nueva: Autorizacion) => void;
};

function Habitante({ consorcio, autorizacion: a, aEntrar, alCambiar }: PropsHabitante) {
  const id = useId();
  const [abierto, setAbierto] = useState(false);
  const [motivo, setMotivo] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [falla, setFalla] = useState<string | null>(null);
  const clave = useRef<string | null>(null);
  const autorizar = !a.autorizada;

  function cerrar() {
    setAbierto(false);
    setMotivo("");
    setFalla(null);
    clave.current = null;
  }

  async function enviar() {
    setEnviando(true);
    setFalla(null);
    clave.current ??= crypto.randomUUID();
    try {
      const { data, error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/autorizaciones-proveedores/{persona_id}", {
        headers: encabezadoCsrf(),
        params: {
          path: { consorcio_id: consorcio, persona_id: a.persona_id },
          header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current },
        },
        body: { autorizada: autorizar, motivo: motivo.trim() },
      });
      exigirSesion(response);
      if (data === undefined) {
        if (renovarClave(response.status, codigoDe(error))) clave.current = null;
        setFalla(mensajeDeActo(response.status, error));
        return;
      }
      cerrar();
      alCambiar(data);
    } catch (e: unknown) {
      if (e instanceof SinSesion) aEntrar();
      else setFalla(SIN_CONEXION);
    } finally {
      setEnviando(false);
    }
  }

  return (
    <article className="tarjeta" aria-labelledby={`${id}-nombre`}>
      <h2 id={`${id}-nombre`}>{a.nombre}</h2>
      <p>{a.unidades.join(", ")}</p>
      <p className={a.autorizada ? "aviso-ok" : undefined}>{textoEstado(a)}</p>
      {!abierto ? (
        <div className="acciones">
          <button type="button" className={autorizar ? "secundario" : "secundario peligro"} onClick={() => setAbierto(true)}>
            {autorizar ? AUTORIZAR : RETIRAR}
          </button>
        </div>
      ) : (
        <form
          className="formulario"
          onSubmit={(ev) => {
            ev.preventDefault();
            void enviar();
          }}
        >
          <label htmlFor={`${id}-motivo`}>{MOTIVO}</label>
          <textarea
            id={`${id}-motivo`}
            value={motivo}
            maxLength={LARGO_MOTIVO}
            required
            disabled={enviando}
            onChange={(ev) => setMotivo(ev.target.value)}
          />
          {falla !== null && (
            <p role="alert" className="aviso-error">
              {falla}
            </p>
          )}
          <div className="acciones">
            <button type="submit" className={autorizar ? undefined : "peligro"} disabled={enviando || motivo.trim() === ""}>
              {autorizar ? CONFIRMAR_AUTORIZAR : CONFIRMAR_RETIRAR}
            </button>
            <button type="button" className="secundario" disabled={enviando} onClick={cerrar}>
              Cancelar
            </button>
          </div>
        </form>
      )}
    </article>
  );
}
