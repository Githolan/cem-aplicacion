import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { CampoImporte } from "../egresos/CampoImporte";
import {
  aCentavos,
  aTexto,
  cambioLaOrden,
  cambioLoVisto,
  ENCABEZADO_IDEMPOTENCIA,
  ESTADO,
  importe,
  mensajeDeError,
  sinLaFalla,
  sinPisarLoHecho,
  sobreLoNuevo,
} from "../egresos/comun";
import { FallaDeLectura } from "../egresos/EstadoPantalla";
import { RUTA_PROVEEDORES } from "../egresos/Proveedores";
import { exigirSesion, SinSesion } from "../egresos/useConsorcio";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { AVISO_MODIFICAR } from "./textos";
import type { ActoEnCurso } from "./useActo";

// Preparar una orden o guardar una versión nueva: el proveedor se elige de la lista, nunca se
// escribe a mano. Con la orden abierta (`modificar`), el formulario sigue a la versión que muestra
// la ficha: si otra persona la cambió, lo que se escribió queda sobre la versión nueva y lo que se
// confirmó se vuelve a confirmar.

type Proveedor = components["schemas"]["Proveedor"];
type Contrato = components["schemas"]["Contrato"];
type Version = components["schemas"]["VersionOrdenPago"];

export const MONEDAS = ["ARS", "USD"] as const;
/** Rechazos que se arreglan cambiando lo escrito: el formulario sigue abierto con sus datos. */
const SE_CORRIGE_EN_EL_FORMULARIO = new Set(["sin_cambios", "proveedor_inactivo"]);

type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; proveedores: Proveedor[]; contratos: Contrato[] };

type Datos = {
  proveedor: string;
  importe: string;
  moneda: string;
  concepto: string;
  comprobante: string;
  reserva: boolean;
  /** El contrato o abono que la respalda ("" = ninguno): solo así es rutinaria. */
  contrato: string;
};

const opcional = (v: string) => (v.trim() === "" ? null : v.trim());

/** Los contratos que puede declarar una orden: vigentes, del proveedor y de la moneda elegidos.
 * La base vuelve a verificar que lo cubra (alcance, importe máximo, fechas) al evaluarla. */
export function contratosAplicables(contratos: Contrato[], proveedor: string, moneda: string): Contrato[] {
  return contratos.filter((k) => k.estado === "vigente" && k.proveedor_id === proveedor && k.moneda === moneda);
}

const datosVacios = (proveedor: string): Datos => ({
  proveedor,
  importe: "",
  moneda: "ARS",
  concepto: "",
  comprobante: "",
  reserva: false,
  contrato: "",
});

/** Una versión en el formulario; un contrato que ya no aplica no se trae. */
function datosDe(v: Version, contratos: Contrato[]): Datos {
  const aplica = contratosAplicables(contratos, v.proveedor_id, v.moneda).some((k) => k.id === v.contrato_id);
  return {
    proveedor: v.proveedor_id,
    importe: aTexto(v.importe_centavos),
    moneda: v.moneda,
    concepto: v.concepto,
    comprobante: v.comprobante ?? "",
    reserva: v.usa_fondo_reserva,
    contrato: aplica && v.contrato_id !== null ? v.contrato_id : "",
  };
}

/** Con qué abre una orden nueva. */
export type OrdenInicial = { proveedor?: string; importe?: string; moneda?: string; concepto?: string };

type Modificar = {
  orden: string;
  version: Version;
  /** Guardar deja sin efecto aprobaciones: se confirma antes. */
  pierdeAprobaciones: boolean;
};

type Props = {
  consorcio: string;
  acto: ActoEnCurso;
  aEntrar: () => void;
  aInicio: () => void;
  /** Sin esto, prepara una orden nueva. */
  modificar?: Modificar;
  /** Con qué abre una orden nueva; el proveedor, si sigue activo. */
  inicial?: OrdenInicial;
  alGuardar: (orden: string, texto: string) => void;
  alCancelar: () => void;
  /** La orden cambió: la ficha la vuelve a leer y el formulario sigue sobre lo nuevo. */
  alCambiarLaOrden?: () => void;
};

export function FormularioOrden({ consorcio, acto, aEntrar, aInicio, modificar, inicial, alGuardar, alCancelar, alCambiarLaOrden }: Props) {
  const id = useId();
  const { enviando, setAviso } = acto;
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [datos, setDatos] = useState<Datos | null>(null);
  const [confirmar, setConfirmar] = useState(false);
  /** Los datos de la versión que se abrió, para saber qué cambió la persona. */
  const vistos = useRef<Datos | null>(null);
  const versionVista = modificar?.version;

  useEffect(() => {
    let vigente = true;
    const ruta = { params: { path: { consorcio_id: consorcio } } };
    void Promise.all([
      api.GET("/api/v1/consorcios/{consorcio_id}/proveedores", ruta),
      api.GET("/api/v1/consorcios/{consorcio_id}/contratos", ruta),
    ])
      .then(([proveedores, contratos]) => {
        exigirSesion(proveedores.response);
        exigirSesion(contratos.response);
        if (!vigente) return;
        if (proveedores.data !== undefined && contratos.data !== undefined) {
          setLectura({ tipo: "ok", proveedores: proveedores.data.elementos, contratos: contratos.data.elementos });
          return;
        }
        const falla = proveedores.data === undefined ? proveedores : contratos;
        const codigo = codigoDe(falla.error);
        if (falla.response.status === ESTADO.sinPermiso && codigo === "sin_permiso") aInicio();
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso(sinPisarLoHecho(mensajeDeError(falla.response.status, codigo)));
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
  }, [consorcio, vuelta, aEntrar, aInicio, setAviso]);

  // Al llegar los datos, o una versión nueva con el formulario abierto.
  useEffect(() => {
    if (lectura.tipo !== "ok") return;
    if (versionVista === undefined) {
      setDatos((d) => {
        if (d !== null) return d;
        const elegido = lectura.proveedores.find((p) => p.id === inicial?.proveedor && p.estado === "activo");
        const { importe: importeInicial, moneda, concepto } = inicial ?? {};
        return {
          ...datosVacios((elegido ?? lectura.proveedores.find((p) => p.estado === "activo"))?.id ?? ""),
          ...(importeInicial === undefined ? {} : { importe: importeInicial }),
          ...(moneda === undefined ? {} : { moneda }),
          ...(concepto === undefined ? {} : { concepto }),
        };
      });
      return;
    }
    const nuevos = datosDe(versionVista, lectura.contratos);
    const antes = vistos.current;
    vistos.current = nuevos;
    setConfirmar(false);
    setDatos((d) => (d === null || antes === null ? nuevos : sobreLoNuevo(d, antes, nuevos)));
    if (antes === null && versionVista.contrato_id !== null && nuevos.contrato === "")
      setAviso({ texto: "El contrato de esta orden ya no rige: se quitó del formulario. Si guardás, pide aprobación.", ok: false });
  }, [lectura, versionVista, inicial, setAviso]);

  function cambiar<K extends keyof Datos>(campo: K, valor: Datos[K]) {
    setConfirmar(false);
    setDatos((d) => {
      if (d === null) return d;
      const nuevo = { ...d, [campo]: valor };
      if (campo === "proveedor" || campo === "moneda") nuevo.contrato = "";
      return nuevo;
    });
  }

  function guardar(evento: FormEvent) {
    evento.preventDefault();
    if (datos === null) return;
    const centavos = aCentavos(datos.importe);
    if (centavos === null) {
      setAviso({ texto: "Escribí el importe en pesos, por ejemplo 12345,50.", ok: false });
      return;
    }
    if (modificar?.pierdeAprobaciones === true && !confirmar) {
      setConfirmar(true);
      return;
    }
    const esencial = {
      importe_centavos: centavos,
      moneda: datos.moneda,
      concepto: datos.concepto.trim(),
      proveedor_id: datos.proveedor,
      comprobante: opcional(datos.comprobante),
      usa_fondo_reserva: datos.reserva,
      contrato_id: datos.contrato === "" ? null : datos.contrato,
    };
    void acto.hacer({
      quePide: ["guardar", modificar?.orden, modificar?.version.numero, datos],
      nombre: "guardar la orden",
      pedir: (clave) => {
        const header = { [ENCABEZADO_IDEMPOTENCIA]: clave };
        return modificar === undefined
          ? api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago", {
              headers: encabezadoCsrf(),
              params: { path: { consorcio_id: consorcio }, header },
              body: esencial,
            })
          : api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/versiones", {
              headers: encabezadoCsrf(),
              params: { path: { consorcio_id: consorcio, orden_id: modificar.orden }, header },
              body: { ...esencial, version_esperada: modificar.version.numero },
            });
      },
      alTerminar: (data) =>
        alGuardar(data.id, modificar === undefined ? "Listo: la orden quedó en borrador." : "Listo: guardamos la versión nueva."),
      alFallar: (estado, codigo) => {
        if (cambioLaOrden(codigo)) alCambiarLaOrden?.();
        else if (cambioLoVisto(estado, codigo) && !SE_CORRIGE_EN_EL_FORMULARIO.has(codigo ?? "")) {
          alCambiarLaOrden?.();
          alCancelar();
        }
      },
    });
  }

  if (lectura.tipo === "cargando") return <p role="status">Cargando…</p>;
  if (lectura.tipo === "sin_servicio")
    return (
      <FallaDeLectura
        alReintentar={() => {
          setAviso(sinLaFalla);
          setLectura({ tipo: "cargando" });
          setVuelta((v) => v + 1);
        }}
        avisar={setAviso}
      />
    );
  if (modificar === undefined && !lectura.proveedores.some((p) => p.estado === "activo"))
    return (
      <p className="tarjeta">
        Para preparar una orden hace falta un proveedor activo:{" "}
        <Link to={RUTA_PROVEEDORES} state={{ consorcio, paraPreparar: true }}>
          cargalo en Proveedores
        </Link>
        .
      </p>
    );
  if (datos === null) return null;

  return (
    <form className="formulario tarjeta" onSubmit={guardar} aria-labelledby={`${id}-form`}>
      <h2 id={`${id}-form`}>{modificar === undefined ? "Preparar una orden" : "Modificar la orden"}</h2>
      <label htmlFor={`${id}-proveedor`}>Proveedor</label>
      <select id={`${id}-proveedor`} required value={datos.proveedor} disabled={enviando}
        onChange={(e) => cambiar("proveedor", e.target.value)}>
        <option value="" disabled>
          Elegí un proveedor
        </option>
        {lectura.proveedores
          .filter((p) => p.estado === "activo" || p.id === datos.proveedor)
          .map((p) => (
            <option key={p.id} value={p.id}>
              {p.razon_social}
            </option>
          ))}
      </select>
      <label htmlFor={`${id}-importe`}>Importe</label>
      <CampoImporte id={`${id}-importe`} valor={datos.importe} deshabilitado={enviando}
        alCambiar={(texto) => cambiar("importe", texto)} />
      <label htmlFor={`${id}-moneda`}>Moneda</label>
      <select id={`${id}-moneda`} value={datos.moneda} disabled={enviando} onChange={(e) => cambiar("moneda", e.target.value)}>
        {MONEDAS.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
      <label htmlFor={`${id}-contrato`}>Contrato o abono (opcional)</label>
      <select id={`${id}-contrato`} value={datos.contrato} disabled={enviando}
        onChange={(e) => cambiar("contrato", e.target.value)}>
        <option value="">Ninguno: no es un gasto rutinario</option>
        {contratosAplicables(lectura.contratos, datos.proveedor, datos.moneda).map((k) => (
          <option key={k.id} value={k.id}>
            {k.alcance} (hasta {importe(k.importe_maximo_centavos, k.moneda)})
          </option>
        ))}
      </select>
      <label htmlFor={`${id}-concepto`}>Concepto</label>
      <input id={`${id}-concepto`} required maxLength={300} value={datos.concepto} disabled={enviando}
        onChange={(e) => cambiar("concepto", e.target.value)} />
      <label htmlFor={`${id}-comprobante`}>Comprobante (opcional)</label>
      <input id={`${id}-comprobante`} maxLength={200} value={datos.comprobante} disabled={enviando}
        onChange={(e) => cambiar("comprobante", e.target.value)} />
      <label>
        <input type="checkbox" checked={datos.reserva} disabled={enviando}
          onChange={(e) => cambiar("reserva", e.target.checked)} />{" "}
        Usa el fondo de reserva (siempre lo aprueba el Consejo)
      </label>
      {confirmar && (
        <p className="aviso-error" role="alert">
          {AVISO_MODIFICAR}
        </p>
      )}
      <div className="acciones">
        <button type="submit" disabled={enviando || datos.proveedor === ""}>
          {confirmar ? "Sí, crear la versión nueva" : "Guardar"}
        </button>
        <button type="button" className="secundario" disabled={enviando} onClick={alCancelar}>
          Cancelar
        </button>
      </div>
    </form>
  );
}
