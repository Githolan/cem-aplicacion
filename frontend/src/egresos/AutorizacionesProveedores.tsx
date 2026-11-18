import { useEffect, useId, useRef, useState } from "react";

import { CabeceraInterna } from "../CabeceraInterna";
import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { ErrorDeCampo, type ErroresDeCampo, faltaCompletar, hayErrores, marcaDe, SIN_ERRORES, sinElDe } from "../ErrorDeCampo";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { FichaDeRegistro, FilaDeRegistro, useFichas } from "../FichaDeRegistro";
import { fechaCorta } from "../pagos/textos";
import { REQUISITOS, usePuede } from "../permisos";
import { type FinDeEnvio, VentanaDeFormulario } from "../VentanaDeFormulario";
import {
  type Aviso,
  cambioLoVisto,
  ENCABEZADO_IDEMPOTENCIA,
  ESTADO,
  mensajeDeActo,
  mensajeDeError,
  renovarClave,
  RUTA_AUTORIZACIONES_PROVEEDORES,
  RUTA_CONSULTA_PROVEEDORES,
  RUTA_PROVEEDORES,
  sinLaFalla,
  sinPisarLoHecho,
  TITULO_AUTORIZACIONES_PROVEEDORES,
} from "./comun";
import { ElegirConsorcio } from "./ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "./EstadoPantalla";
import { exigirSesion, SinSesion, useConsorcio } from "./useConsorcio";

// Página /proveedores/quien-los-ve: el Consejo autoriza a un habitante a ver los proveedores y sus
// seguros, o le retira la autorización, siempre con el motivo (app 0105; respuesta del propietario
// del 15-11-2026: los habitantes autorizados son «los que autorice el Consejo»). Nada se borra:
// cada cambio queda con quién lo hizo y cuándo. Lo decide la API con `proveedores.autorizar`, y la
// base repite la regla. Quién autoriza lo fija la configuración del edificio (por defecto, el
// Consejo); los roles que ya ven los proveedores por la configuración no necesitan autorización.
//
// Informe de fricción, tanda 4 (comentario 38): cada habitante es una fila del Padrón que abre su
// ficha (AUT-01); el motivo se pide en una ventana encima, con el botón siempre activo (AUT-02,
// C8P-23); sin el párrafo fijo de arriba (AUT-03, y con eso AUT-04). Se llega desde Proveedores
// (MEN-05), y «Volver» lleva ahí.

type Autorizacion = components["schemas"]["AutorizacionVerProveedores"];

export { RUTA_AUTORIZACIONES_PROVEEDORES, TITULO_AUTORIZACIONES_PROVEEDORES };
/** Lo único del antiguo párrafo que sirve para decidir: va en la ventana de retirar (AUT-03). */
export const ACLARACION_RETIRO = "Retirar la autorización no le quita lo que ve por su rol.";
/** Lo que se dice al terminar: solo lo que cambió, la autorización. Quien además tiene un rol que
 * ve los proveedores los sigue viendo, así que no se afirma que dejó de verlos (Codex en #250). */
export const textoAutorizado = (nombre: string) => `Se autorizó a ${nombre} a ver los proveedores.`;
export const textoRetirado = (nombre: string) => `Se retiró la autorización de ${nombre}.`;
export const SIN_HABITANTES = "El edificio no tiene habitantes con un vínculo vigente.";
export const AUTORIZAR = "Autorizar";
export const RETIRAR = "Retirar la autorización";
export const CONFIRMAR_AUTORIZAR = "Confirmar la autorización";
export const CONFIRMAR_RETIRAR = "Confirmar el retiro";
export const MOTIVO = "Motivo";
export const FALTA_MOTIVO = "Falta escribir el motivo.";
export const LARGO_MOTIVO = 500;
export const AUTORIZADO = "Autorizado";
export const SIN_AUTORIZACION = "Sin autorización";
export const RETIRADA = "Autorización retirada";

/** El estado en una palabra, para la fila (AUT-01). */
export const estadoCorto = (a: Autorizacion) => (a.autorizada ? AUTORIZADO : SIN_AUTORIZACION);

/** El estado en la ficha: si nunca se registró nada, solo eso; si no, también desde cuándo, quién y por qué. */
function datosDe(a: Autorizacion) {
  const registrado = a.registrado_en !== null && a.registrado_en !== undefined;
  return [
    { etiqueta: "Unidades", valor: a.unidades.join(", ") },
    { etiqueta: "Estado", valor: a.autorizada ? AUTORIZADO : registrado ? RETIRADA : SIN_AUTORIZACION },
    ...(registrado
      ? [
          { etiqueta: "Desde", valor: fechaCorta(a.registrado_en as string) },
          { etiqueta: "Por", valor: a.registrado_por_nombre ?? "alguien del Consejo" },
          { etiqueta: "Motivo", valor: a.motivo ?? "" },
        ]
      : []),
  ];
}

type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "apagada"; texto: string } | { tipo: "ok"; elementos: Autorizacion[] };

export function AutorizacionesProveedores() {
  const elegido = useConsorcio(REQUISITOS.autorizarProveedores);
  return <DelEdificio key={elegido.consorcio} {...elegido} />;
}

function DelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar }: ReturnType<typeof useConsorcio>) {
  const id = useId();
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const fichas = useFichas<string>();
  const [ventana, setVentana] = useState(false);
  const [motivo, setMotivo] = useState("");
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  const [enviando, setEnviando] = useState(false);
  const clave = useRef<string | null>(null);
  const puede = usePuede(consorcio);

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

  function abrirFicha(a: Autorizacion) {
    setAviso(null);
    setVentana(false);
    fichas.abrir(a.persona_id);
  }

  function abrirVentana() {
    clave.current = null;
    setMotivo("");
    setErrores(SIN_ERRORES);
    setVentana(true);
  }

  /** Terminado el acto, la ventana y la ficha se cierran y el resultado queda arriba de la lista. */
  function cerrarTodo() {
    setVentana(false);
    fichas.cerrar();
  }

  /** El botón de la ventana, siempre activo (C8P-23): sin motivo, lo marca; si no, manda el acto. */
  async function enviar(a: Autorizacion): Promise<FinDeEnvio> {
    const faltan = faltaCompletar({ motivo: [motivo, FALTA_MOTIVO] });
    if (hayErrores(faltan)) {
      setErrores(faltan);
      return "seguir";
    }
    const autorizar = !a.autorizada;
    clave.current ??= crypto.randomUUID();
    setEnviando(true);
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
        const codigo = codigoDe(error);
        // Lo visto ya no es lo registrado (otra persona del Consejo cambió la autorización, o el
        // habitante dejó de serlo): el aviso queda y la lista se vuelve a leer (Codex en #250).
        if (cambioLoVisto(response.status, codigo) || codigo === "persona_sin_vinculo") {
          setAviso({ texto: mensajeDeActo(response.status, error), ok: false });
          cerrarTodo();
          setVuelta((v) => v + 1);
          return "cerrar";
        }
        if (renovarClave(response.status, codigo)) clave.current = null;
        return { rechazo: mensajeDeActo(response.status, error) };
      }
      setLectura((l) => (l.tipo === "ok" ? { tipo: "ok", elementos: l.elementos.map((x) => (x.persona_id === data.persona_id ? data : x)) } : l));
      setAviso({ texto: data.autorizada ? textoAutorizado(data.nombre) : textoRetirado(data.nombre), ok: true });
      cerrarTodo();
      return "cerrar";
    } catch (e: unknown) {
      if (e instanceof SinSesion) {
        aEntrar();
        return "seguir";
      }
      return { rechazo: SIN_CONEXION };
    } finally {
      setEnviando(false);
    }
  }

  const elementos = lectura.tipo === "ok" ? lectura.elementos : [];
  const enFicha = elementos.find((a) => a.persona_id === fichas.abierta);
  // «Volver» lleva a la pantalla de Proveedores desde la que se llega (MEN-05).
  const volver = puede("egresos.preparar_orden") ? RUTA_PROVEEDORES : RUTA_CONSULTA_PROVEEDORES;

  return (
    <>
      <CabeceraInterna titulo={TITULO_AUTORIZACIONES_PROVEEDORES} volver={volver} destino="Proveedores" />
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={enviando} elegir={setConsorcio} />
      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">Esta cuenta no tiene edificios en Consorcio en Mano.</p>}
      <ConsorcioSinServicio pantalla={pantalla} reintentar={reintentar} />
      <AvisoPantalla aviso={aviso} />

      {pantalla.tipo === "lista" && (
        <>
          {lectura.tipo === "cargando" && <p role="status">Cargando los habitantes…</p>}
          {lectura.tipo === "sin_servicio" && <FallaDeLectura alReintentar={reintentarLectura} avisar={setAviso} />}
          {lectura.tipo === "apagada" && <p className="tarjeta">{lectura.texto}</p>}
          {lectura.tipo === "ok" &&
            (elementos.length === 0 ? (
              <p className="tarjeta">{SIN_HABITANTES}</p>
            ) : (
              <ul className="filas-registro" aria-label="Habitantes">
                {elementos.map((a) => (
                  <li key={a.persona_id}>
                    <FilaDeRegistro
                      titulo={a.nombre}
                      resumen={[...(a.unidades.length > 0 ? [a.unidades.join(", ")] : []), estadoCorto(a)].join(" · ")}
                      alAbrir={() => abrirFicha(a)}
                      refFila={fichas.refFila(a.persona_id)}
                      abierta={fichas.abierta === a.persona_id}
                      deshabilitada={enviando}
                    />
                  </li>
                ))}
              </ul>
            ))}
        </>
      )}

      {enFicha !== undefined && (
        <FichaDeRegistro
          titulo={enFicha.nombre}
          datos={datosDe(enFicha)}
          alCerrar={() => fichas.cerrar()}
          ocupada={enviando}
          ventana={ventana && ventanaDe(enFicha)}
        >
          <p className="ficha-registro__acciones">
            <button type="button" className={enFicha.autorizada ? "secundario peligro" : undefined} onClick={abrirVentana}>
              {enFicha.autorizada ? RETIRAR : AUTORIZAR}
            </button>
          </p>
        </FichaDeRegistro>
      )}
    </>
  );

  /** El motivo, en su ventana encima de la ficha (AUT-02). */
  function ventanaDe(a: Autorizacion) {
    const retirar = a.autorizada;
    const idMotivo = `${id}-motivo`;
    const ayuda = `${id}-ayuda`;
    return (
      <VentanaDeFormulario
        titulo={retirar ? `Retirar la autorización de ${a.nombre}` : `Autorizar a ${a.nombre}`}
        textoEnviar={retirar ? CONFIRMAR_RETIRAR : CONFIRMAR_AUTORIZAR}
        peligro={retirar}
        alEnviar={() => enviar(a)}
        alCerrar={() => setVentana(false)}
      >
        {retirar && <p id={ayuda}>{ACLARACION_RETIRO}</p>}
        <label htmlFor={idMotivo}>{MOTIVO}</label>
        <textarea
          id={idMotivo}
          data-foco
          value={motivo}
          maxLength={LARGO_MOTIVO}
          {...marcaDe(idMotivo, errores.motivo, retirar ? ayuda : undefined)}
          onChange={(ev) => {
            clave.current = null;
            setErrores((actuales) => sinElDe(actuales, "motivo"));
            setMotivo(ev.target.value);
          }}
        />
        <ErrorDeCampo idCampo={idMotivo} mensaje={errores.motivo} />
      </VentanaDeFormulario>
    );
  }
}
