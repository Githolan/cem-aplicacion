import { useEffect, useId, useRef, useState } from "react";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { aCentavos, aDecimal, aTexto, ENCABEZADO_IDEMPOTENCIA, fechaLegible, mensajeDeActo, mensajeDeError, momento, renovarClave } from "../egresos/comun";
import { periodo } from "../egresos/Liquidacion";
import { exigirSesion, SinSesion } from "../egresos/useConsorcio";
import { ErrorDeCampo, type ErroresDeCampo, faltaCompletar, hayErrores, marcaDe, SIN_ERRORES, sinElDe } from "../ErrorDeCampo";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { enPesos } from "../fiscalizacion/LiquidacionDelAdministrador";
import { type Resultado, ResultadoDelActo } from "../ResultadoDelActo";
import { type FinDeEnvio, VentanaDeFormulario } from "../VentanaDeFormulario";
import type { Panel as DatosPanel } from "./datos";

// El presupuesto y los desvíos por rubro en el resumen del edificio (RF15 en su forma de Modo Consejo,
// RF48; app 0128, diseño P0 S9). El presupuesto no sale de la liquidación: lo aprueba la asamblea y el
// Consejo o la administración lo registran tal como se aprobó, con el importe mensual de cada rubro.
// Lo liquidado sale de las liquidaciones oficiales. Nada se estima: un rubro sin presupuesto se dice
// «Sin presupuesto» y uno que la liquidación no trae, «No figura en la liquidación»; ninguno de los dos
// tiene desvío. Un presupuesto no se cambia: se reemplaza con otra versión, con el motivo.

type Lista = components["schemas"]["ListaPresupuestos"];
type Presupuesto = components["schemas"]["Presupuesto"];
type Desvio = DatosPanel["desvios_presupuesto"][number];

export const TITULO_DESVIOS = "Desvíos del presupuesto";
export const SEGUN_EL_PRESUPUESTO = "Según el presupuesto registrado y las liquidaciones oficiales.";
export const BASE_DESVIOS =
  "Para cada rubro del período elegido: lo presupuestado por mes en el presupuesto vigente que cubre ese período, lo que informa la liquidación oficial y el desvío, que es lo liquidado menos lo presupuestado, con su porcentaje sobre lo presupuestado. Los rubros se reconocen por su número en la liquidación.";
export const SIN_PRESUPUESTO_PARA_LOS_PERIODOS = "Todavía no hay un presupuesto registrado para los períodos con liquidación oficial.";
export const REGISTRAR = "Registrar un presupuesto";
export const REEMPLAZAR = "Reemplazar";
export const TITULO_REEMPLAZAR = "Reemplazar el presupuesto";
export const SIN_PRESUPUESTO = "Sin presupuesto";
export const NO_FIGURA = "No figura en la liquidación";
export const NO_SE_CALCULA = "No se calcula";
export const VIGENTE = "Vigente";
export const REEMPLAZADO = "Reemplazado";
export const REGISTRADO = "Listo: el presupuesto quedó registrado.";
export const NO_SE_CAMBIA = "Un presupuesto no se cambia ni se borra: si hace falta, se registra otra versión que lo reemplaza, con el motivo.";
const CON_CUENTA = "La aprobación y el motivo no pueden llevar el número de una cuenta.";
const YA_REEMPLAZADO = "Ese presupuesto ya no está vigente: otra versión lo reemplazó. La lista ya está actualizada.";

/** «Vencimientos de la liquidación de septiembre de 2026: 13/10/2026 y 23/10/2026». */
export function textoVencimientos(mes: string, vencimientos: readonly string[]): string {
  const fechas = vencimientos.map(fechaLegible);
  const lista = fechas.length > 1 ? `${fechas.slice(0, -1).join(", ")} y ${fechas[fechas.length - 1]}` : (fechas[0] ?? "");
  return `${vencimientos.length === 1 ? "Vencimiento" : "Vencimientos"} de la liquidación de ${periodo(mes)}: ${lista}`;
}

const conSigno = (texto: string) => (texto.startsWith("-") ? enPesos(texto) : `+${enPesos(texto)}`);
const porciento = (texto: string) => `${texto.startsWith("-") ? "" : "+"}${texto.replace(".", ",")} %`;

/** El desvío como se lee: «+$ 100,00 (+9,09 %)»; sin porcentaje si lo presupuestado es cero. */
export function textoDesvio(d: Desvio): string {
  if (d.desvio === null) return NO_SE_CALCULA;
  return d.porcentaje === null ? conSigno(d.desvio) : `${conSigno(d.desvio)} (${porciento(d.porcentaje)})`;
}

const nombreDe = (d: { numero: number | null; nombre: string | null }) => d.nombre ?? `Rubro ${d.numero ?? ""}`.trim();

/** «Septiembre de 2026 a octubre de 2026», o un solo mes. */
export const rango = (p: { desde: string; hasta: string }) => (p.desde === p.hasta ? periodo(p.desde) : `${periodo(p.desde)} a ${periodo(p.hasta)}`);

/** Los rubros que trae alguna liquidación de la ventana, por número, con el nombre del período más nuevo. */
export function rubrosConocidos(panel: DatosPanel): { numero: number; nombre: string }[] {
  const porNumero = new Map<number, string>();
  for (const r of [...panel.rubros_liquidados].sort((a, b) => a.periodo.localeCompare(b.periodo)))
    if (r.numero !== null) porNumero.set(r.numero, r.nombre ?? `Rubro ${r.numero}`);
  return [...porNumero].sort(([a], [b]) => a - b).map(([numero, nombre]) => ({ numero, nombre }));
}

type Lectura = { tipo: "cargando" } | { tipo: "falla"; texto: string } | { tipo: "ok"; lista: Lista };

type Props = {
  panel: DatosPanel;
  consorcio: string;
  aEntrar: () => void;
  /** Un presupuesto quedó registrado: el resumen se vuelve a leer, con los desvíos nuevos. */
  alCambiar: () => void;
};

export function DesviosDelPresupuesto({ panel, consorcio, aEntrar, alCambiar }: Props) {
  const id = useId();
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [resultado, setResultado] = useState<Resultado | null>(null);
  /** La ventana abierta: un presupuesto nuevo (null) o el reemplazo de uno vigente. */
  const [ventana, setVentana] = useState<{ reemplaza: Presupuesto | null } | null>(null);
  const periodos = [...new Set(panel.desvios_presupuesto.map((d) => d.periodo))].sort().reverse();
  const [elegido, setElegido] = useState("");
  const mes = periodos.includes(elegido) ? elegido : (periodos[0] ?? "");
  const conocidos = rubrosConocidos(panel);

  useEffect(() => {
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/presupuestos", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        if (data !== undefined) setLectura({ tipo: "ok", lista: data });
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
  }, [consorcio, vuelta, aEntrar]);

  const lista = lectura.tipo === "ok" ? lectura.lista : null;
  // Sin rubros numerados no hay con qué reconocerlos: registrar no se ofrece (criterio 8).
  const puedeRegistrar = lista !== null && lista.puede_registrar && conocidos.length > 0;
  const filas = panel.desvios_presupuesto.filter((d) => d.periodo === mes);
  const algunoCubierto = panel.desvios_presupuesto.some((d) => d.presupuesto_id !== null);

  return (
    <section className="tarjeta grafico" aria-labelledby={`${id}-titulo`}>
      <h2 id={`${id}-titulo`}>{`${TITULO_DESVIOS}${periodos.length === 1 ? `, ${periodo(mes)}` : ""}`}</h2>
      <p className="grafico__base">{SEGUN_EL_PRESUPUESTO}</p>
      <details className="grafico__como">
        <summary>Cómo se calcula</summary>
        <p className="grafico__base">{BASE_DESVIOS}</p>
      </details>
      <ResultadoDelActo resultado={resultado} alOtroActo={() => setResultado(null)} />
      {!algunoCubierto && <p>{SIN_PRESUPUESTO_PARA_LOS_PERIODOS}</p>}
      {algunoCubierto && periodos.length > 1 && (
        <div className="formulario">
          <label htmlFor={`${id}-periodo`}>Período</label>
          <select id={`${id}-periodo`} value={mes} onChange={(e) => setElegido(e.target.value)}>
            {periodos.map((m) => (
              <option key={m} value={m}>
                {periodo(m)}
              </option>
            ))}
          </select>
        </div>
      )}
      {algunoCubierto && (
        <div className="tabla-lista tabla-tarjetas">
          <table aria-label={`${TITULO_DESVIOS}, ${periodo(mes)}`}>
            <thead>
              <tr>
                <th scope="col">Rubro</th>
                <th scope="col">Presupuestado</th>
                <th scope="col">Liquidado</th>
                <th scope="col">Desvío</th>
              </tr>
            </thead>
            <tbody>
              {filas.map((d, i) => (
                <tr key={`${d.numero ?? `fila-${i}`}`}>
                  <th scope="row">{nombreDe(d)}</th>
                  <td data-etiqueta="Presupuestado">{d.presupuestado === null ? SIN_PRESUPUESTO : enPesos(d.presupuestado)}</td>
                  <td data-etiqueta="Liquidado">{d.liquidado === null ? NO_FIGURA : enPesos(d.liquidado)}</td>
                  <td data-etiqueta="Desvío">{textoDesvio(d)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {lectura.tipo === "cargando" && <p role="status">Cargando los presupuestos…</p>}
      {lectura.tipo === "falla" && (
        <p role="alert" className="aviso-error">
          {lectura.texto}
        </p>
      )}
      {puedeRegistrar && (
        <div className="acciones">
          <button type="button" className="secundario" onClick={() => setVentana({ reemplaza: null })}>
            {REGISTRAR}
          </button>
        </div>
      )}
      {lista !== null && lista.elementos.length > 0 && (
        <details>
          <summary>{`Presupuestos registrados (${lista.elementos.length})`}</summary>
          <ul className="versiones" aria-label="Presupuestos registrados">
            {lista.elementos.map((p) => (
              <li key={p.id}>
                <p>
                  <strong>{rango(p)}</strong> · {p.reemplazado_por === null ? VIGENTE : REEMPLAZADO}
                </p>
                <p className="ayuda">
                  {p.aprobacion}. Registrado por {p.registrado_por_nombre}, {momento(p.registrado_en)}.
                </p>
                {p.motivo !== null && <p className="ayuda">Motivo del reemplazo: {p.motivo}</p>}
                <p className="ayuda">{p.rubros.map((r) => `${r.nombre}: ${enPesos(r.importe)} por mes`).join(" · ")}</p>
                {puedeRegistrar && p.reemplazado_por === null && (
                  <button type="button" className="secundario" onClick={() => setVentana({ reemplaza: p })}>
                    {REEMPLAZAR}
                  </button>
                )}
              </li>
            ))}
          </ul>
          <p className="ayuda">{NO_SE_CAMBIA}</p>
        </details>
      )}
      {ventana !== null && lista !== null && puedeRegistrar && (
        <VentanaPresupuesto
          consorcio={consorcio}
          lista={lista}
          conocidos={conocidos}
          reemplaza={ventana.reemplaza}
          aEntrar={aEntrar}
          alCerrar={() => setVentana(null)}
          alRegistrar={() => {
            setResultado({ texto: REGISTRADO, ok: true });
            setVuelta((v) => v + 1);
            alCambiar();
          }}
          alQuedarViejo={() => setVuelta((v) => v + 1)}
        />
      )}
    </section>
  );
}

/** «1500.00» a centavos, sin pasar por un número con decimales. */
const centavosDe = (decimal: string) => Number(decimal.replace(".", ""));

/** Lo escrito en un importe: «0» vale (un rubro presupuestado en cero); vacío, el rubro no va. */
function importeEscrito(texto: string): number | null {
  if (/^0+(?:[.,]0{1,2})?$/.test(texto.trim())) return 0;
  return aCentavos(texto);
}

const MES = /^\d{4}-(0[1-9]|1[0-2])$/;

type PropsVentana = {
  consorcio: string;
  lista: Lista;
  conocidos: { numero: number; nombre: string }[];
  reemplaza: Presupuesto | null;
  aEntrar: () => void;
  alCerrar: () => void;
  alRegistrar: () => void;
  /** El que se reemplazaba ya no está vigente: la lista se vuelve a leer. */
  alQuedarViejo: () => void;
};

/** Registrar el presupuesto aprobado: los meses que cubre, de dónde sale y el importe mensual de cada
 * rubro de la liquidación. Al reemplazar, empieza con lo del que reemplaza y pide el motivo. */
function VentanaPresupuesto({ consorcio, lista, conocidos, reemplaza, aEntrar, alCerrar, alRegistrar, alQuedarViejo }: PropsVentana) {
  const id = useId();
  const rubros = [
    ...conocidos,
    ...(reemplaza?.rubros ?? []).filter((r) => !conocidos.some((c) => c.numero === r.numero)).map((r) => ({ numero: r.numero, nombre: r.nombre })),
  ].sort((a, b) => a.numero - b.numero);
  const [desde, setDesde] = useState(reemplaza?.desde.slice(0, 7) ?? "");
  const [hasta, setHasta] = useState(reemplaza?.hasta.slice(0, 7) ?? "");
  const [aprobacion, setAprobacion] = useState(reemplaza?.aprobacion ?? "");
  const [motivo, setMotivo] = useState("");
  const [importes, setImportes] = useState<Record<number, string>>(() =>
    Object.fromEntries((reemplaza?.rubros ?? []).map((r) => [r.numero, aTexto(centavosDe(r.importe))])),
  );
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  /** La misma clave mientras se reintenta el mismo registro: nunca queda dos veces. */
  const clave = useRef<string | null>(null);

  function revisar(): ErroresDeCampo {
    const faltan: Record<string, string> = {
      ...faltaCompletar({ desde: [desde, "Falta el primer mes."], hasta: [hasta, "Falta el último mes."], aprobacion: [aprobacion, "Falta de dónde sale el presupuesto."] }),
      ...(reemplaza === null ? {} : faltaCompletar({ motivo: [motivo, "Falta el motivo del reemplazo."] })),
    };
    if (faltan.desde === undefined && !MES.test(desde)) faltan.desde = "El mes se escribe AAAA-MM.";
    if (faltan.hasta === undefined && !MES.test(hasta)) faltan.hasta = "El mes se escribe AAAA-MM.";
    if (faltan.desde === undefined && faltan.hasta === undefined && hasta < desde) faltan.hasta = "El último mes no puede ser anterior al primero.";
    const escritos = rubros.filter((r) => (importes[r.numero] ?? "").trim() !== "");
    for (const r of escritos) if (importeEscrito(importes[r.numero] ?? "") === null) faltan[`rubro-${r.numero}`] = "El importe se escribe como 1.234,50.";
    if (escritos.length === 0 && rubros[0] !== undefined) faltan[`rubro-${rubros[0].numero}`] = "Falta el importe de al menos un rubro.";
    return faltan;
  }

  async function enviar(): Promise<FinDeEnvio> {
    const faltan = revisar();
    if (hayErrores(faltan)) {
      setErrores(faltan);
      return "seguir";
    }
    const cuerpo = {
      desde: `${desde}-01`,
      hasta: `${hasta}-01`,
      aprobacion: aprobacion.trim(),
      rubros: rubros.flatMap((r) => {
        const centavos = importeEscrito(importes[r.numero] ?? "");
        return (importes[r.numero] ?? "").trim() === "" || centavos === null ? [] : [{ numero: r.numero, nombre: r.nombre, importe: aDecimal(centavos) }];
      }),
      reemplaza_a: reemplaza?.id ?? null,
      motivo: reemplaza === null ? null : motivo.trim(),
    };
    clave.current ??= crypto.randomUUID();
    try {
      const { data, error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/presupuestos", {
        headers: encabezadoCsrf(),
        params: { path: { consorcio_id: consorcio }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current } },
        body: cuerpo,
      });
      exigirSesion(response);
      if (data !== undefined) {
        clave.current = null;
        alRegistrar();
        return "cerrar";
      }
      const codigo = codigoDe(error);
      if (renovarClave(response.status, codigo)) clave.current = null;
      if (codigo === "texto_con_cuenta") return { rechazo: CON_CUENTA };
      if (codigo === "reemplazo_invalido") {
        alQuedarViejo();
        return { rechazo: YA_REEMPLAZADO };
      }
      return { rechazo: mensajeDeActo(response.status, error) };
    } catch (e: unknown) {
      if (!(e instanceof SinSesion)) return { rechazo: SIN_CONEXION };
      aEntrar();
      return "seguir";
    }
  }

  const campo = (nombre: string) => ({ id: `${id}-${nombre}`, ...marcaDe(`${id}-${nombre}`, errores[nombre]) });
  const escribir = (nombre: string, poner: (v: string) => void) => (v: string) => {
    poner(v);
    setErrores((e) => sinElDe(e, nombre));
  };
  const titulo = reemplaza === null ? REGISTRAR : TITULO_REEMPLAZAR;

  return (
    <VentanaDeFormulario titulo={titulo} textoEnviar="Registrar" alEnviar={enviar} alCerrar={alCerrar} ancha>
      {reemplaza !== null && <p>Reemplaza al de {rango(reemplaza)}.</p>}
      <label htmlFor={`${id}-desde`}>Primer mes (AAAA-MM)</label>
      <input {...campo("desde")} data-foco type="month" value={desde} onChange={(ev) => escribir("desde", setDesde)(ev.target.value)} />
      <ErrorDeCampo idCampo={`${id}-desde`} mensaje={errores.desde} />
      <label htmlFor={`${id}-hasta`}>Último mes (AAAA-MM)</label>
      <input {...campo("hasta")} type="month" value={hasta} onChange={(ev) => escribir("hasta", setHasta)(ev.target.value)} />
      <ErrorDeCampo idCampo={`${id}-hasta`} mensaje={errores.hasta} />
      <label htmlFor={`${id}-aprobacion`}>De dónde sale</label>
      <input
        {...campo("aprobacion")}
        value={aprobacion}
        maxLength={lista.largo_aprobacion}
        placeholder="Asamblea ordinaria del 12-03-2026"
        onChange={(ev) => escribir("aprobacion", setAprobacion)(ev.target.value)}
      />
      <ErrorDeCampo idCampo={`${id}-aprobacion`} mensaje={errores.aprobacion} />
      <fieldset>
        <legend>Importe por mes de cada rubro</legend>
        <p className="ayuda">Los rubros son los de la liquidación. Un rubro sin importe queda sin presupuesto.</p>
        {rubros.map((r) => (
          <div key={r.numero}>
            <label htmlFor={`${id}-rubro-${r.numero}`}>{`${r.numero}. ${r.nombre}`}</label>
            <input
              {...campo(`rubro-${r.numero}`)}
              inputMode="decimal"
              value={importes[r.numero] ?? ""}
              onChange={(ev) => escribir(`rubro-${r.numero}`, (v) => setImportes((x) => ({ ...x, [r.numero]: v })))(ev.target.value)}
            />
            <ErrorDeCampo idCampo={`${id}-rubro-${r.numero}`} mensaje={errores[`rubro-${r.numero}`]} />
          </div>
        ))}
      </fieldset>
      {reemplaza !== null && (
        <>
          <label htmlFor={`${id}-motivo`}>Motivo del reemplazo</label>
          <textarea {...campo("motivo")} value={motivo} maxLength={lista.largo_motivo} rows={3} onChange={(ev) => escribir("motivo", setMotivo)(ev.target.value)} />
          <ErrorDeCampo idCampo={`${id}-motivo`} mensaje={errores.motivo} />
        </>
      )}
      <p className="ayuda">{NO_SE_CAMBIA}</p>
    </VentanaDeFormulario>
  );
}
