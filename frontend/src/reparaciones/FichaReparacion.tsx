import { type ReactNode, useEffect, useId, useRef, useState } from "react";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { TIPOS_DE_DOCUMENTO, TOPE_BYTES } from "../archivos";
import { encabezadoCsrf } from "../csrf";
import { ENCABEZADO_IDEMPOTENCIA, fechaLegible, mensajeDeActo, mensajeDeError, momento, renovarClave } from "../egresos/comun";
import { exigirSesion, SinSesion } from "../egresos/useConsorcio";
import { ErrorDeCampo, type ErroresDeCampo, faltaCompletar, hayErrores, marcaDe, SIN_ERRORES, sinElDe } from "../ErrorDeCampo";
import { type DatoDeFicha, FichaDeRegistro } from "../FichaDeRegistro";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { enPesos } from "../fiscalizacion/LiquidacionDelAdministrador";
import { SubirArchivo } from "../SubirArchivo";
import { type FinDeEnvio, VentanaDeFormulario } from "../VentanaDeFormulario";
import { subirArchivo, useArchivoDelActo } from "./archivoDelActo";
import { TIPOS_DE_FOTO } from "./NuevaGestion";
import {
  AGREGAR_FOTO,
  APROBAR,
  CAMBIAR_GESTION,
  ENLAZAR_GASTO,
  GASTO_PENDIENTE,
  lugarDe,
  mesDe,
  motivoDe,
  NO_ES_PAGO,
  NO_REALIZADA,
  PRESUPUESTAR,
  REGISTRAR_GESTION,
  RETIRAR,
  REVISANDO,
  TERMINAR,
  TEXTO_ADJUNTO,
  TEXTO_CON_CUENTA,
  TEXTO_ESTADO,
  TEXTO_PRIORIDAD,
  tituloDe,
  type Prioridad,
} from "./textos";

// El expediente de una reparación (documento 08, línea del «Expediente»; RF19, app 0124): lo pedido,
// la gestión vigente, la historia con lo que registró cada paso y los archivos. Abajo, solo lo que
// la API dice que la persona puede hacer ahora (`acciones`), cada acto en su ventana. Un archivo que
// un acto cita se sube y se espera al antivirus antes de citarlo. Sin un gasto para enlazar, la
// reparación terminada lo dice y no ofrece el cierre.

type Reparacion = components["schemas"]["Reparacion"];
type Lista = components["schemas"]["ListaReparaciones"];
type Accion = components["schemas"]["AccionReparacion"];
type Gasto = components["schemas"]["GastoOficial"];
type Respuesta = { data?: unknown; error?: unknown; response: Response };

export const LISTO: Record<Exclude<Accion, "agregar_foto">, string> = {
  gestionar: "Listo: la gestión quedó registrada.",
  presupuestar: "Listo: el presupuesto quedó registrado.",
  aprobar: "Listo: la aprobación quedó registrada.",
  terminar: "Listo: quedó registrado que se hizo. Falta el gasto para cerrarla.",
  enlazar_gasto: "Listo: el gasto quedó enlazado y la reparación, cerrada.",
  no_realizada: "Listo: quedó registrado que no se pudo hacer.",
  retirar: "Listo: el pedido quedó retirado.",
};
export const FOTO_CARGADA = "Listo: la foto se cargó. Se ve en el expediente cuando el antivirus la acepta.";

const TEXTO_ACCION: Record<Accion, string> = {
  gestionar: REGISTRAR_GESTION,
  presupuestar: PRESUPUESTAR,
  aprobar: APROBAR,
  terminar: TERMINAR,
  enlazar_gasto: ENLAZAR_GASTO,
  no_realizada: NO_REALIZADA,
  retirar: RETIRAR,
  agregar_foto: AGREGAR_FOTO,
};

const ESTADO_ARCHIVO = { disponible: null, en_cuarentena: "Revisándose con el antivirus", rechazado: "Rechazado por el antivirus" } as const;

export const textoGasto = (g: Gasto) => [`Liquidación de ${mesDe(g.periodo)}`, g.concepto, g.total == null ? null : enPesos(g.total)].filter((x) => x != null).join(" · ");

const contenido = (consorcio: string, r: Reparacion, adjunto: string) => `/api/v1/consorcios/${consorcio}/reparaciones/${r.id}/adjuntos/${adjunto}/contenido`;

type Props = {
  consorcio: string;
  id: string;
  lista: Lista;
  aEntrar: () => void;
  alCerrar: () => void;
  /** Terminó un acto: la pantalla cierra la ficha, dice el resultado y relee la lista. */
  alTerminar: (texto: string) => void;
};

type LecturaFicha = { tipo: "cargando" } | { tipo: "falla"; texto: string } | { tipo: "ok"; r: Reparacion };

export function FichaReparacion({ consorcio, id, lista, aEntrar, alCerrar, alTerminar }: Props) {
  const [lectura, setLectura] = useState<LecturaFicha>({ tipo: "cargando" });
  const [accion, setAccion] = useState<Accion | null>(null);

  useEffect(() => {
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/reparaciones/{reparacion_id}", { params: { path: { consorcio_id: consorcio, reparacion_id: id } } })
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
      <FichaDeRegistro titulo="Reparación" datos={[]} alCerrar={alCerrar}>
        {lectura.tipo === "cargando" ? (
          <p role="status">Cargando la reparación…</p>
        ) : (
          <p role="alert" className="aviso-error">
            {lectura.texto}
          </p>
        )}
      </FichaDeRegistro>
    );
  const r = lectura.r;
  const datos: DatoDeFicha[] = [{ etiqueta: "Estado", valor: TEXTO_ESTADO[r.estado] }];
  if (!r.propia) datos.push({ etiqueta: "La pidió", valor: r.pedida_por_nombre });
  datos.push({ etiqueta: "Dónde", valor: lugarDe(r) });
  datos.push({ etiqueta: "Qué hay que reparar", valor: <pre className="texto-con-renglones">{r.descripcion}</pre> });
  if (r.gestion != null) {
    const g = r.gestion;
    const fecha = g.fecha_comprometida == null ? "" : ` · para el ${fechaLegible(g.fecha_comprometida)}`;
    datos.push({ etiqueta: "Responsable", valor: `${g.responsable_nombre} · prioridad ${TEXTO_PRIORIDAD[g.prioridad].toLowerCase()}${fecha}` });
  }
  for (const p of r.historia) datos.push({ etiqueta: TEXTO_ESTADO[p.estado], valor: detalleDelPaso(p) });
  if (r.adjuntos.length > 0)
    datos.push({
      etiqueta: "Archivos",
      valor: (
        <ul className="lista-documentacion">
          {r.adjuntos.map((a) => (
            <li key={a.id}>
              {TEXTO_ADJUNTO[a.tipo]}
              {a.estado === "disponible" ? (
                <a href={contenido(consorcio, r, a.id)} target="_blank" rel="noopener noreferrer">
                  Ver <span className="solo-lector">{TEXTO_ADJUNTO[a.tipo].toLowerCase()}</span>
                </a>
              ) : (
                <span>{ESTADO_ARCHIVO[a.estado]}</span>
              )}
            </li>
          ))}
        </ul>
      ),
    });

  const fin = (texto: string) => alTerminar(texto);
  const cerrar = () => setAccion(null);
  const comunes = { consorcio, r, lista, aEntrar, alCerrar: cerrar };
  const ventana: ReactNode =
    accion === "gestionar" ? (
      <VentanaGestion {...comunes} alTerminar={() => fin(LISTO.gestionar)} />
    ) : accion === "presupuestar" ? (
      <VentanaPresupuesto {...comunes} alTerminar={() => fin(LISTO.presupuestar)} />
    ) : accion === "aprobar" ? (
      <VentanaAprobacion {...comunes} alTerminar={() => fin(LISTO.aprobar)} />
    ) : accion === "terminar" ? (
      <VentanaFactura {...comunes} alTerminar={() => fin(LISTO.terminar)} />
    ) : accion === "enlazar_gasto" ? (
      <VentanaGasto {...comunes} alTerminar={() => fin(LISTO.enlazar_gasto)} />
    ) : accion === "agregar_foto" ? (
      <VentanaFoto {...comunes} alTerminar={() => fin(FOTO_CARGADA)} />
    ) : accion === "no_realizada" || accion === "retirar" ? (
      <VentanaMotivo {...comunes} acto={accion} alTerminar={() => fin(LISTO[accion])} />
    ) : null;

  return (
    <FichaDeRegistro titulo={tituloDe(r)} datos={datos} alCerrar={alCerrar} ventana={ventana}>
      {r.estado === "terminada_gasto_pendiente" && !r.acciones.includes("enlazar_gasto") && <p className="tarjeta">{GASTO_PENDIENTE}</p>}
      {r.acciones.length > 0 && (
        <div className="ficha-registro__acciones">
          {r.acciones.map((a, i) => (
            <button
              key={a}
              type="button"
              className={a === "no_realizada" || a === "retirar" ? "secundario peligro" : i === 0 ? undefined : "secundario"}
              onClick={() => setAccion(a)}
            >
              {a === "gestionar" && r.estado !== "pedida" ? CAMBIAR_GESTION : TEXTO_ACCION[a]}
            </button>
          ))}
        </div>
      )}
    </FichaDeRegistro>
  );
}

function detalleDelPaso(p: Reparacion["historia"][number]): ReactNode {
  const cuando = `${momento(p.registrado_en)}, ${p.registrado_por_nombre}`;
  const extra: string[] = [];
  if (p.proveedor_nombre != null) extra.push(`Proveedor: ${p.proveedor_nombre}${p.proveedor_cuit == null ? "" : ` (CUIT ${p.proveedor_cuit})`}`);
  if (p.aprobado_por != null) {
    const respaldo = p.decision_id == null ? "un documento firmado" : `la decisión del ${fechaLegible(p.decision_fecha ?? "")}: ${p.decision_descripcion ?? ""}`;
    extra.push(`Aprobó: ${p.aprobado_por}. Respaldo: ${respaldo}. Registrado con la facultad de gestionar las reparaciones.`);
  }
  if (p.estado === "terminada_gasto_pendiente") extra.push("Con la factura.");
  if (p.gasto != null) extra.push(`Gasto: ${textoGasto(p.gasto)}`);
  if (p.motivo != null) extra.push(`Motivo: ${p.motivo}`);
  return extra.length === 0 ? (
    cuando
  ) : (
    <>
      {cuando}
      {extra.map((x) => (
        <span key={x} className="texto-con-renglones">
          <br />
          {x}
        </span>
      ))}
    </>
  );
}

type PropsVentana = {
  consorcio: string;
  r: Reparacion;
  lista: Lista;
  aEntrar: () => void;
  alCerrar: () => void;
  alTerminar: () => void;
};

const camino = (r: Reparacion, consorcio: string) => ({ consorcio_id: consorcio, reparacion_id: r.id });

/** Manda un acto con su clave de idempotencia (la misma en cada reintento hasta que sale) y dice cómo
 * terminó. `marcar` recibe el código del rechazo para marcar el campo que corresponde. */
function useActo(aEntrar: () => void, alTerminar: () => void) {
  const clave = useRef<string | null>(null);
  return async (enviar: (clave: string) => Promise<Respuesta>, marcar: (codigo: string | undefined) => void = () => undefined): Promise<FinDeEnvio> => {
    clave.current ??= crypto.randomUUID();
    try {
      const { data, error, response } = await enviar(clave.current);
      exigirSesion(response);
      if (data !== undefined) {
        clave.current = null;
        alTerminar();
        return "cerrar";
      }
      const codigo = codigoDe(error);
      if (renovarClave(response.status, codigo)) clave.current = null;
      marcar(codigo);
      return { rechazo: motivoDe(codigo, mensajeDeActo(response.status, error)) };
    } catch (e: unknown) {
      if (!(e instanceof SinSesion)) return { rechazo: SIN_CONEXION };
      aEntrar();
      return "seguir";
    }
  };
}

function VentanaGestion({ consorcio, r, aEntrar, alCerrar, alTerminar }: PropsVentana) {
  const id = useId();
  const [responsable, setResponsable] = useState(r.gestion?.responsable_id ?? r.responsables_posibles[0]?.id ?? "");
  const [prioridad, setPrioridad] = useState<Prioridad>(r.gestion?.prioridad ?? "normal");
  const [fecha, setFecha] = useState(r.gestion?.fecha_comprometida ?? "");
  const hacer = useActo(aEntrar, alTerminar);
  const titulo = r.estado === "pedida" ? REGISTRAR_GESTION : CAMBIAR_GESTION;
  return (
    <VentanaDeFormulario
      titulo={titulo}
      textoEnviar="Guardar"
      alCerrar={alCerrar}
      alEnviar={() =>
        hacer((clave) =>
          api.POST("/api/v1/consorcios/{consorcio_id}/reparaciones/{reparacion_id}/gestion", {
            headers: encabezadoCsrf(),
            params: { path: camino(r, consorcio), header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
            body: { responsable_id: responsable, prioridad, fecha_comprometida: fecha === "" ? null : fecha },
          }),
        )
      }
    >
      <label htmlFor={`${id}-responsable`}>Responsable</label>
      <select id={`${id}-responsable`} data-foco value={responsable} onChange={(ev) => setResponsable(ev.target.value)}>
        {r.responsables_posibles.map((p) => (
          <option key={p.id} value={p.id}>
            {p.nombre}
          </option>
        ))}
      </select>
      <label htmlFor={`${id}-prioridad`}>Prioridad</label>
      <select id={`${id}-prioridad`} value={prioridad} onChange={(ev) => setPrioridad(ev.target.value as Prioridad)}>
        {(Object.keys(TEXTO_PRIORIDAD) as Prioridad[]).map((p) => (
          <option key={p} value={p}>
            {TEXTO_PRIORIDAD[p]}
          </option>
        ))}
      </select>
      <label htmlFor={`${id}-fecha`}>Fecha comprometida (si hay)</label>
      <input id={`${id}-fecha`} type="date" value={fecha} onChange={(ev) => setFecha(ev.target.value)} />
    </VentanaDeFormulario>
  );
}

/** El archivo que cita el acto: elegido, subido y revisado antes de mandar el acto. */
function CampoDelArchivo({ de, archivo, alElegir, falta, revisando, id }: { de: string; archivo: File | null; alElegir: (f: File) => void; falta?: string; revisando: boolean; id: string }) {
  return (
    <>
      <SubirArchivo de={de} tipos={TIPOS_DE_DOCUMENTO} tope={TOPE_BYTES} elegido={archivo?.name ?? null} alElegir={alElegir} />
      <ErrorDeCampo idCampo={id} mensaje={falta} />
      {revisando && <p role="status">{REVISANDO}</p>}
    </>
  );
}

function VentanaPresupuesto({ consorcio, r, lista, aEntrar, alCerrar, alTerminar }: PropsVentana) {
  const id = useId();
  const [proveedor, setProveedor] = useState("");
  const [cuit, setCuit] = useState("");
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  const archivo = useArchivoDelActo(consorcio, r.id, "presupuesto", aEntrar);
  const hacer = useActo(aEntrar, alTerminar);

  async function enviar(): Promise<FinDeEnvio> {
    const faltan: Record<string, string> = { ...faltaCompletar({ proveedor: [proveedor, "Falta el nombre del proveedor."] }) };
    if (cuit.trim() !== "" && !/^\d{11}$/.test(cuit.trim())) faltan.cuit = "El CUIT va con once dígitos, sin guiones.";
    if (archivo.archivo === null) faltan.archivo = "Falta el archivo del presupuesto.";
    if (hayErrores(faltan) || archivo.archivo === null) {
      setErrores(faltan);
      return "seguir";
    }
    const listo = await archivo.preparar(archivo.archivo);
    if (listo === null) return "seguir";
    if ("rechazo" in listo) return listo;
    return hacer(
      (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/reparaciones/{reparacion_id}/presupuesto", {
          headers: encabezadoCsrf(),
          params: { path: camino(r, consorcio), header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: { adjunto_id: listo.adjunto_id, proveedor_nombre: proveedor.trim(), proveedor_cuit: cuit.trim() === "" ? null : cuit.trim() },
        }),
      (codigo) => {
        if (codigo === "cuit_invalido") setErrores({ cuit: "El CUIT no es válido: hay que revisarlo." });
        if (codigo === "texto_con_cuenta") setErrores({ proveedor: TEXTO_CON_CUENTA });
      },
    );
  }

  return (
    <VentanaDeFormulario titulo={PRESUPUESTAR} textoEnviar="Registrar" alEnviar={enviar} alCerrar={alCerrar} ancha>
      <label htmlFor={`${id}-proveedor`}>Proveedor</label>
      <input id={`${id}-proveedor`} data-foco value={proveedor} maxLength={lista.largo_proveedor} {...marcaDe(`${id}-proveedor`, errores.proveedor)}
        onChange={(ev) => { setProveedor(ev.target.value); setErrores((e) => sinElDe(e, "proveedor")); }} />
      <ErrorDeCampo idCampo={`${id}-proveedor`} mensaje={errores.proveedor} />
      <label htmlFor={`${id}-cuit`}>CUIT del proveedor (si se sabe)</label>
      <input id={`${id}-cuit`} inputMode="numeric" value={cuit} maxLength={11} {...marcaDe(`${id}-cuit`, errores.cuit)}
        onChange={(ev) => { setCuit(ev.target.value); setErrores((e) => sinElDe(e, "cuit")); }} />
      <ErrorDeCampo idCampo={`${id}-cuit`} mensaje={errores.cuit} />
      <CampoDelArchivo id={`${id}-archivo`} de="del presupuesto" archivo={archivo.archivo} falta={errores.archivo} revisando={archivo.revisando}
        alElegir={(f) => { archivo.setArchivo(f); setErrores((e) => sinElDe(e, "archivo")); }} />
    </VentanaDeFormulario>
  );
}

function VentanaAprobacion({ consorcio, r, lista, aEntrar, alCerrar, alTerminar }: PropsVentana) {
  const id = useId();
  const [aprobado, setAprobado] = useState("");
  const conDecisiones = r.decisiones_citables.length > 0;
  const [respaldo, setRespaldo] = useState<"decision" | "documento">(conDecisiones ? "decision" : "documento");
  const [decision, setDecision] = useState(r.decisiones_citables[0]?.id ?? "");
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  const archivo = useArchivoDelActo(consorcio, r.id, "aprobacion", aEntrar);
  const hacer = useActo(aEntrar, alTerminar);

  async function enviar(): Promise<FinDeEnvio> {
    const faltan: Record<string, string> = { ...faltaCompletar({ aprobado: [aprobado, "Falta decir quién aprobó el presupuesto."] }) };
    if (respaldo === "documento" && archivo.archivo === null) faltan.archivo = "Falta el documento firmado.";
    if (hayErrores(faltan)) {
      setErrores(faltan);
      return "seguir";
    }
    let adjunto: string | null = null;
    if (respaldo === "documento" && archivo.archivo !== null) {
      const listo = await archivo.preparar(archivo.archivo);
      if (listo === null) return "seguir";
      if ("rechazo" in listo) return listo;
      adjunto = listo.adjunto_id;
    }
    return hacer(
      (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/reparaciones/{reparacion_id}/aprobacion", {
          headers: encabezadoCsrf(),
          params: { path: camino(r, consorcio), header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: { aprobado_por: aprobado.trim(), decision_id: respaldo === "decision" ? decision : null, adjunto_id: adjunto },
        }),
      (codigo) => {
        if (codigo === "texto_con_cuenta") setErrores({ aprobado: TEXTO_CON_CUENTA });
      },
    );
  }

  return (
    <VentanaDeFormulario titulo={APROBAR} textoEnviar="Registrar" alEnviar={enviar} alCerrar={alCerrar} ancha>
      <p className="ayuda">{NO_ES_PAGO}</p>
      <label htmlFor={`${id}-aprobado`}>Quién lo aprobó</label>
      <input id={`${id}-aprobado`} data-foco value={aprobado} maxLength={lista.largo_aprobado_por} placeholder="Por ejemplo: asamblea del 3 de noviembre" {...marcaDe(`${id}-aprobado`, errores.aprobado)}
        onChange={(ev) => { setAprobado(ev.target.value); setErrores((e) => sinElDe(e, "aprobado")); }} />
      <ErrorDeCampo idCampo={`${id}-aprobado`} mensaje={errores.aprobado} />
      {conDecisiones && (
        <fieldset>
          <legend>Qué lo respalda</legend>
          <label><input type="radio" name={`${id}-respaldo`} checked={respaldo === "decision"} onChange={() => setRespaldo("decision")} /> Una decisión formal del edificio</label>
          <label><input type="radio" name={`${id}-respaldo`} checked={respaldo === "documento"} onChange={() => setRespaldo("documento")} /> Un documento firmado</label>
        </fieldset>
      )}
      {respaldo === "decision" ? (
        <>
          <label htmlFor={`${id}-decision`}>Decisión</label>
          <select id={`${id}-decision`} value={decision} onChange={(ev) => setDecision(ev.target.value)}>
            {r.decisiones_citables.map((d) => (
              <option key={d.id} value={d.id}>{`${fechaLegible(d.fecha)}: ${d.descripcion}`}</option>
            ))}
          </select>
        </>
      ) : (
        <CampoDelArchivo id={`${id}-archivo`} de="del documento firmado" archivo={archivo.archivo} falta={errores.archivo} revisando={archivo.revisando}
          alElegir={(f) => { archivo.setArchivo(f); setErrores((e) => sinElDe(e, "archivo")); }} />
      )}
    </VentanaDeFormulario>
  );
}

function VentanaFactura({ consorcio, r, aEntrar, alCerrar, alTerminar }: PropsVentana) {
  const id = useId();
  const [falta, setFalta] = useState<string | undefined>(undefined);
  const archivo = useArchivoDelActo(consorcio, r.id, "factura", aEntrar);
  const hacer = useActo(aEntrar, alTerminar);

  async function enviar(): Promise<FinDeEnvio> {
    if (archivo.archivo === null) {
      setFalta("Falta la factura: sin ella no se registra que se hizo.");
      return "seguir";
    }
    const listo = await archivo.preparar(archivo.archivo);
    if (listo === null) return "seguir";
    if ("rechazo" in listo) return listo;
    return hacer((clave) =>
      api.POST("/api/v1/consorcios/{consorcio_id}/reparaciones/{reparacion_id}/terminacion", {
        headers: encabezadoCsrf(),
        params: { path: camino(r, consorcio), header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
        body: { adjunto_id: listo.adjunto_id },
      }),
    );
  }

  return (
    <VentanaDeFormulario titulo={TERMINAR} textoEnviar="Registrar" alEnviar={enviar} alCerrar={alCerrar}>
      <p className="ayuda">Con la factura queda hecha; se cierra cuando se enlaza el gasto de la liquidación oficial.</p>
      <CampoDelArchivo id={`${id}-archivo`} de="de la factura" archivo={archivo.archivo} falta={falta} revisando={archivo.revisando}
        alElegir={(f) => { archivo.setArchivo(f); setFalta(undefined); }} />
    </VentanaDeFormulario>
  );
}

function VentanaGasto({ consorcio, r, aEntrar, alCerrar, alTerminar }: PropsVentana) {
  const id = useId();
  const clave = (g: Gasto) => `${g.liquidacion_id} ${g.clave}`;
  const [elegido, setElegido] = useState(r.gastos_enlazables.length === 1 && r.gastos_enlazables[0] !== undefined ? clave(r.gastos_enlazables[0]) : "");
  const [falta, setFalta] = useState<string | undefined>(undefined);
  const hacer = useActo(aEntrar, alTerminar);

  function enviar(): FinDeEnvio | Promise<FinDeEnvio> {
    const gasto = r.gastos_enlazables.find((g) => clave(g) === elegido);
    if (gasto === undefined) {
      setFalta("Falta elegir el gasto.");
      return "seguir";
    }
    return hacer((k) =>
      api.POST("/api/v1/consorcios/{consorcio_id}/reparaciones/{reparacion_id}/gasto", {
        headers: encabezadoCsrf(),
        params: { path: camino(r, consorcio), header: { [ENCABEZADO_IDEMPOTENCIA]: k } },
        body: { liquidacion_id: gasto.liquidacion_id, clave: gasto.clave },
      }),
    );
  }

  return (
    <VentanaDeFormulario titulo={ENLAZAR_GASTO} textoEnviar="Enlazar y cerrar" alEnviar={enviar} alCerrar={alCerrar} ancha>
      <fieldset aria-describedby={falta === undefined ? undefined : `${id}-gasto-error`}>
        <legend>El gasto de la liquidación oficial que paga esta reparación</legend>
        {r.gastos_enlazables.map((g, i) => (
          <label key={clave(g)}>
            <input type="radio" name={`${id}-gasto`} data-foco={i === 0 ? true : undefined} checked={elegido === clave(g)} onChange={() => { setElegido(clave(g)); setFalta(undefined); }} />{" "}
            {textoGasto(g)}
          </label>
        ))}
      </fieldset>
      <ErrorDeCampo idCampo={`${id}-gasto`} mensaje={falta} />
      <p className="ayuda">Un gasto cierra una sola reparación.</p>
    </VentanaDeFormulario>
  );
}

function VentanaFoto({ consorcio, r, lista, aEntrar, alCerrar, alTerminar }: PropsVentana) {
  const id = useId();
  const [foto, setFoto] = useState<File | null>(null);
  const [falta, setFalta] = useState<string | undefined>(undefined);
  const clave = useRef<string | null>(null);

  async function enviar(): Promise<FinDeEnvio> {
    if (foto === null) {
      setFalta("Falta elegir la foto.");
      return "seguir";
    }
    clave.current ??= crypto.randomUUID();
    try {
      const subida = await subirArchivo(consorcio, r.id, "foto", foto, clave.current);
      if ("falla" in subida) {
        if (subida.renovar) clave.current = null;
        return { rechazo: subida.falla };
      }
      clave.current = null;
      alTerminar();
      return "cerrar";
    } catch (e: unknown) {
      if (!(e instanceof SinSesion)) return { rechazo: SIN_CONEXION };
      aEntrar();
      return "seguir";
    }
  }

  return (
    <VentanaDeFormulario titulo={AGREGAR_FOTO} textoEnviar="Cargar la foto" alEnviar={enviar} alCerrar={alCerrar}>
      <SubirArchivo de="de la foto" tipos={TIPOS_DE_FOTO} tope={TOPE_BYTES} nota={`Hasta ${lista.fotos_maximo} fotos por pedido.`} elegido={foto?.name ?? null}
        alElegir={(f) => { setFoto(f); setFalta(undefined); }} />
      <ErrorDeCampo idCampo={`${id}-foto`} mensaje={falta} />
    </VentanaDeFormulario>
  );
}

function VentanaMotivo({ consorcio, r, lista, aEntrar, alCerrar, alTerminar, acto }: PropsVentana & { acto: "no_realizada" | "retirar" }) {
  const id = useId();
  const [motivo, setMotivo] = useState("");
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  const hacer = useActo(aEntrar, alTerminar);
  const retirar = acto === "retirar";

  function enviar(): FinDeEnvio | Promise<FinDeEnvio> {
    const faltan = faltaCompletar({ motivo: [motivo, retirar ? "Falta el motivo del retiro." : "Falta el motivo: sin evidencia, el cierre lleva por qué no se pudo hacer."] });
    if (hayErrores(faltan)) {
      setErrores(faltan);
      return "seguir";
    }
    const marcar = (codigo: string | undefined) => {
      if (codigo === "texto_con_cuenta") setErrores({ motivo: TEXTO_CON_CUENTA });
    };
    const cuerpo = { motivo: motivo.trim() };
    return hacer(
      (clave) =>
        retirar
          ? api.POST("/api/v1/consorcios/{consorcio_id}/reparaciones/{reparacion_id}/retiro", {
              headers: encabezadoCsrf(),
              params: { path: camino(r, consorcio), header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
              body: cuerpo,
            })
          : api.POST("/api/v1/consorcios/{consorcio_id}/reparaciones/{reparacion_id}/no-realizada", {
              headers: encabezadoCsrf(),
              params: { path: camino(r, consorcio), header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
              body: cuerpo,
            }),
      marcar,
    );
  }

  return (
    <VentanaDeFormulario titulo={retirar ? RETIRAR : NO_REALIZADA} textoEnviar={retirar ? "Sí, retirar" : "Registrar"} peligro alEnviar={enviar} alCerrar={alCerrar}>
      <label htmlFor={`${id}-motivo`}>{retirar ? "Motivo del retiro" : "Por qué no se pudo hacer"}</label>
      <input id={`${id}-motivo`} data-foco value={motivo} maxLength={lista.largo_motivo} {...marcaDe(`${id}-motivo`, errores.motivo)}
        onChange={(ev) => { setMotivo(ev.target.value); setErrores((e) => sinElDe(e, "motivo")); }} />
      <ErrorDeCampo idCampo={`${id}-motivo`} mensaje={errores.motivo} />
    </VentanaDeFormulario>
  );
}
