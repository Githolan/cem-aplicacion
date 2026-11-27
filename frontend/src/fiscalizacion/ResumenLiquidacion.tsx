import { useId } from "react";

import type { components } from "../api/tipos";

// El resumen de una liquidación leída (rediseño de la confirmación, RL1-A, decidido por el
// propietario el 26-11-2026): arriba, en palabras, cuánto se gastó y cuánto pagan las unidades;
// debajo, solo lo que hay para mirar, una tarjeta por control; plegado, lo que está bien. Los
// controles los calcula el servidor (`controles`) y vienen con su texto.

type Importacion = components["schemas"]["ImportacionLiquidacion"];
type Campo = components["schemas"]["CampoPreliminar"];
export type Control = components["schemas"]["ControlLiquidacion"];

export const ENTENDIDO = "Entendido";
export const PREGUNTAR = "Preguntar al administrador";
export const verLosBien = (n: number) => (n === 1 ? "Ver el control que está bien" : `Ver los ${n} controles que están bien`);
/** Preguntar por la deuda de una unidad no tiene sentido: se explica, no es una diferencia. */
const SOLO_SE_EXPLICA = new Set(["deuda_unidad"]);
const LARGO_SOLICITUD = 2000;

const mesDe = (periodo: string) => new Intl.DateTimeFormat("es-AR", { month: "long", year: "numeric" }).format(new Date(`${periodo}T12:00:00`));
const conMayuscula = (t: string) => t.charAt(0).toUpperCase() + t.slice(1);

/** Un importe leído («230000.00») en pesos, sin centavos si no los tiene («$ 230.000»). */
export function pesosCortos(texto: string): string {
  const negativo = texto.startsWith("-");
  const [enteros = "0", decimales = "00"] = texto.replace("-", "").split(".");
  const conMiles = enteros.replace(/^0+(?=\d)/, "").replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return `${negativo ? "-" : ""}$ ${conMiles}${decimales === "00" ? "" : `,${decimales}`}`;
}

const IMPORTE = /^-?\d{1,16}\.\d{2}$/;
const importe = (v: unknown): number | null => (typeof v === "string" && IMPORTE.test(v) ? Number(v) : null);
const aTexto = (n: number) => n.toFixed(2);
const valorDe = (c: Campo) => (c.valor_corregido ?? c.valor) as Record<string, unknown>;

/** El título de la liquidación: «Noviembre de 2026 · Edificio». */
export function tituloDe(periodo: string | null, edificio: string | undefined): string {
  const mes = periodo ? conMayuscula(mesDe(periodo)) : "Liquidación del administrador";
  return edificio ? `${mes} · ${edificio}` : mes;
}

/** Las dos líneas de arriba: los gastos del mes por rubro y lo que pagan las unidades. */
export function cifrasDe(campos: readonly Campo[]): { gastos: string | null; unidades: string | null } {
  const vigentes = campos.filter((c) => c.estado !== "descartado");
  const totales = vigentes.find((c) => c.clave === "total:gastos");
  const totalGastos = totales === undefined ? null : importe(valorDe(totales).total);
  const rubros = vigentes
    .filter((c) => c.tipo === "rubro")
    .map((c) => {
      const v = valorDe(c);
      const t = importe(v.total);
      return t === null ? null : `${String(v.nombre ?? "")} ${pesosCortos(aTexto(t))}`;
    })
    .filter((x): x is string => x !== null);
  const gastos = totalGastos === null ? null : `Gastos del mes: ${pesosCortos(aTexto(totalGastos))}${rubros.length > 0 ? ` (${rubros.join(" · ")})` : ""}.`;
  const unidades = vigentes.filter((c) => c.tipo === "importe_unidad");
  const prorrateo = vigentes.find((c) => c.clave === "total:prorrateo");
  const p = prorrateo === undefined ? null : valorDe(prorrateo);
  const total = p !== null ? importe(p.total) : null;
  const sumado = unidades.map((c) => importe(valorDe(c).total));
  const totalUnidades = total ?? (sumado.length > 0 && !sumado.includes(null) ? sumado.reduce<number>((a, b) => a + (b ?? 0), 0) : null);
  const deuda = p === null ? 0 : (importe(p.deuda) ?? 0) + (importe(p.interes) ?? 0);
  if (unidades.length === 0 || totalUnidades === null) return { gastos, unidades: null };
  const cuantas = unidades.length === 1 ? "La unidad paga" : `Las ${unidades.length} unidades pagan en total`;
  const incluye = deuda > 0 ? ` (incluye ${pesosCortos(aTexto(deuda))} de deuda e interés del mes anterior)` : "";
  return { gastos, unidades: `${cuantas} ${pesosCortos(aTexto(totalUnidades))}${incluye}.` };
}

/** «CEM revisó 9 controles: 7 están bien, 2 para mirar.» */
export function lineaDeControles(controles: readonly Control[]): string {
  const bien = controles.filter((c) => c.estado === "bien").length;
  const mirar = controles.filter((c) => c.estado === "para_mirar").length;
  const n = bien + mirar;
  const partes = [bien === 1 ? "1 está bien" : `${bien} están bien`, ...(mirar > 0 ? [`${mirar} para mirar`] : [])];
  return `CEM revisó ${n === 1 ? "1 control" : `${n} controles`}: ${partes.join(", ")}.`;
}

export const sePuedePreguntar = (c: Control) => !SOLO_SE_EXPLICA.has(c.clave);

/** Lo que se marca como visto es la tarjeta con su contenido: si el control cambia (porque se
 * corrigió un dato), vuelve a estar abierta. */
export const vistoDe = (c: Control) => [c.clave, ...c.lineas].join("\n");

/** El texto de la solicitud formal desde una tarjeta: la cuenta y su base, sin calificarla (RN33). */
export function textoDePregunta(c: Control, periodo: string | null): string {
  const de = periodo ? ` de ${mesDe(periodo)}` : "";
  const encabezado = `Sobre la liquidación${de}, al cruzar sus números («${c.titulo}»):`;
  const cierre = "Se pide la base de cálculo, o el criterio aplicado si es distinto del porcentaje de cada unidad.";
  const partes = [encabezado];
  for (const [i, linea] of c.lineas.entries()) {
    const quedan = c.lineas.length - i;
    const mas = `- Y ${quedan === 1 ? "un caso más" : `${quedan} casos más`}, en la aplicación.`;
    if ([...partes, `- ${linea}`, mas, cierre].join("\n").length > LARGO_SOLICITUD) {
      partes.push(mas);
      break;
    }
    partes.push(`- ${linea}`);
  }
  partes.push(cierre);
  return partes.join("\n");
}

type Props = {
  importacion: Importacion;
  edificio: string | undefined;
  vistos: ReadonlySet<string>;
  alVer: (clave: string) => void;
  /** Sin la facultad de preparar solicitudes, no hay botón. */
  alPreguntar: ((c: Control) => void) | null;
};

export function ResumenDeLaLiquidacion({ importacion, edificio, vistos, alVer, alPreguntar }: Props) {
  const id = useId();
  const { gastos, unidades } = cifrasDe(importacion.campos);
  const controles = importacion.controles;
  const mirar = controles.filter((c) => c.estado === "para_mirar");
  const bien = controles.filter((c) => c.estado === "bien");
  const sinComprobar = controles.filter((c) => c.estado === "no_aplica");
  return (
    <>
      <section className="tarjeta resumen-liquidacion" aria-labelledby={`${id}-cifras`}>
        <h2 id={`${id}-cifras`}>{tituloDe(importacion.periodo, edificio)}</h2>
        {gastos !== null && <p>{gastos}</p>}
        {unidades !== null && <p>{unidades}</p>}
        {controles.length > 0 && <p className="resumen-liquidacion__controles">{lineaDeControles(controles)}</p>}
      </section>
      {mirar.length > 0 && (
        <ul className="para-mirar" aria-label="Para mirar">
          {mirar.map((c) =>
            vistos.has(vistoDe(c)) ? (
              <li key={c.clave} className="tarjeta para-mirar__vista">
                <p>{`Visto: ${c.titulo.toLowerCase()}.`}</p>
              </li>
            ) : (
              <li key={c.clave} className="tarjeta para-mirar__tarjeta">
                <h3>{c.titulo}</h3>
                <p>{c.texto}</p>
                <ul>
                  {c.lineas.map((l) => (
                    <li key={l}>{l}</li>
                  ))}
                </ul>
                <p className="ficha-registro__acciones">
                  <button type="button" className="secundario" onClick={() => alVer(vistoDe(c))}>
                    {ENTENDIDO}
                  </button>
                  {alPreguntar !== null && sePuedePreguntar(c) && (
                    <button type="button" className="secundario" onClick={() => alPreguntar(c)}>
                      {PREGUNTAR}
                    </button>
                  )}
                </p>
              </li>
            ),
          )}
        </ul>
      )}
      {bien.length + sinComprobar.length > 0 && (
        <details className="historial">
          <summary>{verLosBien(bien.length)}</summary>
          <ul aria-label="Controles que están bien">
            {bien.map((c) => (
              <li key={c.clave}>{`✓ ${c.texto}`}</li>
            ))}
            {sinComprobar.map((c) => (
              <li key={c.clave}>{`${c.titulo}: ${c.texto}`}</li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}
