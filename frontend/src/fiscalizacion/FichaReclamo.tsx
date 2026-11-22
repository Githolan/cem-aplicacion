import { useEffect, useId, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { ENCABEZADO_IDEMPOTENCIA, mensajeDeActo, mensajeDeError, momento, renovarClave } from "../egresos/comun";
import { exigirSesion, SinSesion } from "../egresos/useConsorcio";
import { ErrorDeCampo, type ErroresDeCampo, faltaCompletar, hayErrores, marcaDe, SIN_ERRORES, sinElDe } from "../ErrorDeCampo";
import { FichaDeRegistro, type DatoDeFicha } from "../FichaDeRegistro";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { VentanaDeFormulario, type FinDeEnvio } from "../VentanaDeFormulario";
import { RUTA_SOLICITUDES, type Propuesta } from "./Solicitudes";
import { mesDe, motivoDe, PASAR_A_SOLICITUD, RESPONDER_RECLAMO, RETIRAR_RECLAMO, TEXTO_CAMPO, TEXTO_CON_CUENTA, TEXTO_ESTADO, tituloDe } from "./textosReclamos";

// La ficha de un reclamo sobre la liquidación (RF34, app 0123): el texto, lo que cita y su historia,
// y abajo solo lo que la API dice que la persona puede hacer ahora (`acciones`): retirarlo (quien lo
// presentó), responderlo o pasarlo a una solicitud formal (quien atiende). Pasar a solicitud formal
// abre «Preparar una solicitud» con el borrador ya redactado (como el recálculo de H5-4); al
// prepararla, el reclamo queda atendido por ella en la misma transacción.

type Reclamo = components["schemas"]["Reclamo"];
type Lista = components["schemas"]["ListaReclamos"];

export const respondido = "Listo: la respuesta quedó registrada y se le avisó por correo a quien lo presentó.";
export const retirado = "Listo: el reclamo quedó retirado.";

/** La solicitud formal que propone un reclamo: lo que cita y lo que dice, para revisarlo. */
export function propuestaDe(consorcio: string, r: Reclamo, largo: number): Propuesta {
  const dato = r.campo === null ? "" : ` (${TEXTO_CAMPO[r.campo].toLowerCase()})`;
  const encabezado = `Un propietario reclamó sobre la liquidación de ${mesDe(r.periodo)}, unidad ${r.designacion}${dato}. Se pide la documentación que lo explique. El reclamo dice:`;
  const texto = `${encabezado}\n${r.texto}`;
  return {
    consorcio,
    objeto: "periodo",
    referencia: `Liquidación de ${mesDe(r.periodo)}, unidad ${r.designacion}`,
    documentacion: texto.length <= largo ? texto : `${texto.slice(0, largo - 1)}…`,
    reclamo: r.id,
  };
}

type Props = {
  consorcio: string;
  id: string;
  lista: Lista;
  /** El largo de la documentación de una solicitud formal (RF49). */
  largoSolicitud: number;
  aEntrar: () => void;
  alCerrar: () => void;
  /** Terminó un acto: la pantalla cierra la ficha, dice el resultado y relee la lista. */
  alTerminar: (texto: string) => void;
};

type LecturaFicha = { tipo: "cargando" } | { tipo: "falla"; texto: string } | { tipo: "ok"; r: Reclamo };

export function FichaReclamo({ consorcio, id, lista, largoSolicitud, aEntrar, alCerrar, alTerminar }: Props) {
  const [lectura, setLectura] = useState<LecturaFicha>({ tipo: "cargando" });
  const [accion, setAccion] = useState<"retirar" | "responder" | null>(null);
  const navegar = useNavigate();

  useEffect(() => {
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/reclamos-liquidacion/{reclamo_id}", { params: { path: { consorcio_id: consorcio, reclamo_id: id } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        if (data !== undefined) setLectura({ tipo: "ok", r: data });
        else setLectura({ tipo: "falla", texto: mensajeDeError(response.status, codigoDe(error)) });
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else setLectura({ tipo: "falla", texto: SIN_CONEXION });
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, id, aEntrar]);

  if (lectura.tipo !== "ok")
    return (
      <FichaDeRegistro titulo="Reclamo" datos={[]} alCerrar={alCerrar}>
        {lectura.tipo === "cargando" ? (
          <p role="status">Cargando el reclamo…</p>
        ) : (
          <p role="alert" className="aviso-error">
            {lectura.texto}
          </p>
        )}
      </FichaDeRegistro>
    );
  const r = lectura.r;
  const datos: DatoDeFicha[] = [{ etiqueta: "Estado", valor: TEXTO_ESTADO[r.estado] }];
  if (!r.propio) datos.push({ etiqueta: "Lo presentó", valor: r.presentado_por_nombre });
  datos.push({
    etiqueta: "Liquidación",
    valor: r.liquidacion_vigente ? `La oficial de ${mesDe(r.periodo)}` : `La de ${mesDe(r.periodo)}, que después fue reemplazada`,
  });
  if (r.campo !== null) datos.push({ etiqueta: "Dato citado", valor: TEXTO_CAMPO[r.campo] });
  datos.push({ etiqueta: "Reclamo", valor: <pre className="texto-con-renglones">{r.texto}</pre> });
  for (const paso of r.historia) {
    const cuando = `${momento(paso.registrado_en)}, ${paso.registrado_por_nombre}`;
    const etiqueta = TEXTO_ESTADO[paso.estado];
    if (paso.estado === "derivado_a_solicitud")
      datos.push({ etiqueta, valor: `${cuando}: solicitud formal N.º ${paso.solicitud_numero ?? "—"}.` });
    else if (paso.texto !== null)
      datos.push({
        etiqueta,
        valor: (
          <>
            {cuando}
            <pre className="texto-con-renglones">{paso.texto}</pre>
          </>
        ),
      });
    else datos.push({ etiqueta, valor: cuando });
  }

  const ventana =
    accion === "retirar" ? (
      <VentanaTexto
        titulo={RETIRAR_RECLAMO}
        textoEnviar="Sí, retirar"
        etiqueta="Motivo"
        largo={lista.largo_motivo}
        renglones={false}
        peligro
        enviar={(texto, clave) =>
          api.POST("/api/v1/consorcios/{consorcio_id}/reclamos-liquidacion/{reclamo_id}/retiro", {
            headers: encabezadoCsrf(),
            params: { path: { consorcio_id: consorcio, reclamo_id: r.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
            body: { motivo: texto },
          })
        }
        aEntrar={aEntrar}
        alCerrar={() => setAccion(null)}
        alTerminar={() => alTerminar(retirado)}
      />
    ) : accion === "responder" ? (
      <VentanaTexto
        titulo={RESPONDER_RECLAMO}
        textoEnviar="Responder"
        etiqueta="Respuesta"
        largo={lista.largo_texto}
        renglones
        ayuda="Quien lo presentó recibe un correo con el enlace; la respuesta se lee en la aplicación. Responder cierra el reclamo."
        enviar={(texto, clave) =>
          api.POST("/api/v1/consorcios/{consorcio_id}/reclamos-liquidacion/{reclamo_id}/respuesta", {
            headers: encabezadoCsrf(),
            params: { path: { consorcio_id: consorcio, reclamo_id: r.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
            body: { texto },
          })
        }
        aEntrar={aEntrar}
        alCerrar={() => setAccion(null)}
        alTerminar={() => alTerminar(respondido)}
      />
    ) : null;

  return (
    <FichaDeRegistro titulo={tituloDe(r)} datos={datos} alCerrar={alCerrar} ventana={ventana}>
      {r.acciones.length > 0 && (
        <div className="ficha-registro__acciones">
          {r.acciones.includes("responder") && (
            <button type="button" onClick={() => setAccion("responder")}>
              {RESPONDER_RECLAMO}
            </button>
          )}
          {r.acciones.includes("pasar_a_solicitud") && (
            <button type="button" className="secundario" onClick={() => void navegar(RUTA_SOLICITUDES, { state: { propuesta: propuestaDe(consorcio, r, largoSolicitud) } })}>
              {PASAR_A_SOLICITUD}
            </button>
          )}
          {r.acciones.includes("retirar") && (
            <button type="button" className="secundario peligro" onClick={() => setAccion("retirar")}>
              {RETIRAR_RECLAMO}
            </button>
          )}
        </div>
      )}
    </FichaDeRegistro>
  );
}

type Respuesta = { data?: unknown; error?: unknown; response: Response };

type PropsTexto = {
  titulo: string;
  textoEnviar: string;
  etiqueta: string;
  largo: number;
  renglones: boolean;
  peligro?: boolean;
  ayuda?: string;
  enviar: (texto: string, clave: string) => Promise<Respuesta>;
  aEntrar: () => void;
  alCerrar: () => void;
  alTerminar: () => void;
};

/** Un acto con un solo texto (la respuesta o el motivo del retiro), con su clave de idempotencia. */
function VentanaTexto({ titulo, textoEnviar, etiqueta, largo, renglones, peligro = false, ayuda, enviar, aEntrar, alCerrar, alTerminar }: PropsTexto) {
  const id = useId();
  const [texto, setTexto] = useState("");
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  const clave = useRef<string | null>(null);

  async function alEnviar(): Promise<FinDeEnvio> {
    const faltan = faltaCompletar({ texto: [texto, `Falta escribir ${etiqueta.toLowerCase() === "motivo" ? "el motivo" : "la respuesta"}.`] });
    if (hayErrores(faltan)) {
      setErrores(faltan);
      return "seguir";
    }
    clave.current ??= crypto.randomUUID();
    try {
      const { data, error, response } = await enviar(texto.trim(), clave.current);
      exigirSesion(response);
      if (data !== undefined) {
        clave.current = null;
        alTerminar();
        return "cerrar";
      }
      const codigo = codigoDe(error);
      if (renovarClave(response.status, codigo)) clave.current = null;
      if (codigo === "texto_con_cuenta") setErrores({ texto: TEXTO_CON_CUENTA });
      return { rechazo: motivoDe(codigo, mensajeDeActo(response.status, error)) };
    } catch (e: unknown) {
      if (!(e instanceof SinSesion)) return { rechazo: SIN_CONEXION };
      aEntrar();
      return "seguir";
    }
  }

  const idCampo = `${id}-texto`;
  const cambiar = (v: string) => {
    setTexto(v);
    setErrores((e) => sinElDe(e, "texto"));
  };
  return (
    <VentanaDeFormulario titulo={titulo} textoEnviar={textoEnviar} alEnviar={alEnviar} alCerrar={alCerrar} peligro={peligro} ancha={renglones}>
      <label htmlFor={idCampo}>{etiqueta}</label>
      {renglones ? (
        <textarea id={idCampo} data-foco value={texto} maxLength={largo} rows={6} {...marcaDe(idCampo, errores.texto)} onChange={(ev) => cambiar(ev.target.value)} />
      ) : (
        <input id={idCampo} data-foco value={texto} maxLength={largo} {...marcaDe(idCampo, errores.texto)} onChange={(ev) => cambiar(ev.target.value)} />
      )}
      <ErrorDeCampo idCampo={idCampo} mensaje={errores.texto} />
      {ayuda !== undefined && <p className="ayuda">{ayuda}</p>}
    </VentanaDeFormulario>
  );
}
