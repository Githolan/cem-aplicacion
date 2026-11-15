import { useId, useRef, useState } from "react";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { ENCABEZADO_IDEMPOTENCIA, mensajeDeActo, renovarClave } from "./comun";
import { TEXTO_TIPO, type RequisitoLegal } from "./DocumentacionProveedor";
import { exigirSesion, SinSesion } from "./useConsorcio";

// Lo que exige la ley al proveedor (RF50 configurable; app 0104; sí del propietario del
// 15-11-2026). Quien prepara órdenes le asigna los rubros legales («Ascensores», «Gas»); la base
// dice qué requisito de esos rubros le falta o tiene vencido. Qué hace la falta lo decide la
// configuración del edificio: «bloquear» no deja prepararle órdenes nuevas; «avisar» solo marca.
// Quien solo consulta ve lo mismo, sin los botones.

type Faltante = components["schemas"]["RequisitoFaltante"];
type Modo = components["schemas"]["ModoExigencia"];

export const TITULO_EXIGENCIA = "Lo que exige la ley";
export const SIN_RUBROS = "No tiene rubros legales asignados: no se le exige nada en particular.";
export const AVISO_FALTA_BLOQUEA =
  "Le falta documentación que exige la ley, o la tiene vencida: no se le pueden preparar órdenes nuevas hasta cargarla. Las órdenes que ya existen siguen su curso.";
export const AVISO_FALTA_AVISA = "Le falta documentación que exige la ley, o la tiene vencida. Se le puede seguir contratando; queda marcado.";
export const AGREGAR_RUBRO = "Agregar el rubro";

/** Los rubros de la matriz legal en palabras; uno nuevo que la pantalla no conoce va por su código. */
export const TEXTO_RUBRO: Record<string, string> = {
  general: "Todo proveedor (Ley 941)",
  con_personal: "Con personal en relación de dependencia",
  ascensores: "Ascensores y montacargas",
  incendio: "Instalaciones fijas contra incendio",
  matafuegos: "Matafuegos",
  gas: "Gas",
  electricidad: "Electricidad",
  desratizacion: "Desratización",
  fachadas: "Fachadas y trabajos en altura",
  calderas: "Calderas y termotanques",
};
export const textoRubro = (rubro: string) => TEXTO_RUBRO[rubro] ?? rubro;

export function avisoDeFalta(modo: Modo): string {
  return modo === "bloquear" ? AVISO_FALTA_BLOQUEA : AVISO_FALTA_AVISA;
}

type Props = {
  consorcio: string;
  proveedorId: string;
  rubros: readonly string[];
  faltantes: readonly Faltante[];
  requisitos: readonly RequisitoLegal[];
  modo: Modo;
  /** Quien prepara órdenes asigna y quita rubros; quien consulta, no. */
  puedeCambiar: boolean;
  aEntrar: () => void;
  alCambiar: () => void;
};

export function ExigenciaLegal({ consorcio, proveedorId, rubros, faltantes, requisitos, modo, puedeCambiar, aEntrar, alCambiar }: Props) {
  const id = useId();
  const [nuevo, setNuevo] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [falla, setFalla] = useState<string | null>(null);
  const clave = useRef<string | null>(null);
  const disponibles = [...new Set(requisitos.map((r) => r.rubro_legal))].filter((r) => !rubros.includes(r));

  async function cambiar(rubro: string, asignar: boolean) {
    setEnviando(true);
    setFalla(null);
    clave.current ??= crypto.randomUUID();
    const pedido = {
      headers: encabezadoCsrf(),
      params: {
        path: { consorcio_id: consorcio, proveedor_id: proveedorId, rubro },
        header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current },
      },
    };
    const ruta = "/api/v1/consorcios/{consorcio_id}/proveedores/{proveedor_id}/rubros-legales/{rubro}";
    try {
      const { data, error, response } = asignar ? await api.POST(ruta, pedido) : await api.DELETE(ruta, pedido);
      exigirSesion(response);
      if (data === undefined) {
        if (renovarClave(response.status, codigoDe(error))) clave.current = null;
        setFalla(mensajeDeActo(response.status, error));
        return;
      }
      clave.current = null;
      setNuevo("");
      alCambiar();
    } catch (e: unknown) {
      if (e instanceof SinSesion) aEntrar();
      else setFalla(SIN_CONEXION);
    } finally {
      setEnviando(false);
    }
  }

  return (
    <section aria-labelledby={`${id}-titulo`}>
      <h3 id={`${id}-titulo`}>{TITULO_EXIGENCIA}</h3>
      {rubros.length === 0 ? (
        <p>{SIN_RUBROS}</p>
      ) : (
        <ul className="lista-documentacion" aria-label="Rubros legales">
          {rubros.map((r) => (
            <li key={r}>
              <strong>{textoRubro(r)}</strong>
              {puedeCambiar && (
                <button type="button" className="secundario peligro" disabled={enviando} onClick={() => void cambiar(r, false)}>
                  Quitar {textoRubro(r)}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {faltantes.length > 0 && (
        <>
          <p className={modo === "bloquear" ? "aviso-error" : "aviso-pendiente"}>{avisoDeFalta(modo)}</p>
          <ul className="versiones" aria-label="Lo que falta">
            {faltantes.map((f) => (
              <li key={f.codigo}>
                {TEXTO_TIPO[f.tipo]}: {f.descripcion} ({textoRubro(f.rubro_legal)})
              </li>
            ))}
          </ul>
        </>
      )}
      {puedeCambiar && disponibles.length > 0 && (
        <div className="formulario">
          <label htmlFor={`${id}-rubro`}>Rubro legal</label>
          <select id={`${id}-rubro`} value={nuevo} disabled={enviando} onChange={(e) => setNuevo(e.target.value)}>
            <option value="">Elegir un rubro</option>
            {disponibles.map((r) => (
              <option key={r} value={r}>
                {textoRubro(r)}
              </option>
            ))}
          </select>
          <div className="acciones">
            <button type="button" className="secundario" disabled={enviando || nuevo === ""} onClick={() => void cambiar(nuevo, true)}>
              {AGREGAR_RUBRO}
            </button>
          </div>
        </div>
      )}
      {falla !== null && (
        <p role="alert" className="aviso-error">
          {falla}
        </p>
      )}
    </section>
  );
}
