import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { type Cargado, CargaConLectura, type LecturaCarga, MarcaDeLectura, TEXTO_ESPERA_PARA_GUARDAR, useVersionVisible } from "../CargaConLectura";
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
  mensajeDeActo,
  mensajeDeError,
  sinLaFalla,
  sinPisarLoHecho,
  sobreLoNuevo,
} from "../egresos/comun";
import { FallaDeLectura } from "../egresos/EstadoPantalla";
import { RUTA_PROVEEDORES } from "../egresos/Proveedores";
import { exigirSesion, SinSesion } from "../egresos/useConsorcio";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { leerComoQuedo, TEXTO_ENVIADA } from "./comoQuedo";
import { AVISO_MODIFICAR } from "./textos";
import type { ActoEnCurso } from "./useActo";

// Preparar una orden o guardar una versión nueva: el proveedor se elige de la lista, nunca se
// escribe a mano. Con la orden abierta (`modificar`), el formulario sigue a la versión que muestra
// la ficha: si otra persona la cambió, lo que se escribió queda sobre la versión nueva y lo que se
// confirmó se vuelve a confirmar.
//
// Arriba, la factura (0088): elegir el archivo la sube, el antivirus la revisa y la IA lee importe,
// moneda, número, concepto y el CUIT del proveedor. Lo que completa la lectura queda marcado hasta que
// la persona lo cambia o lo da por revisado; guardar manda la versión exacta del archivo que se vio
// (`factura_version_id`): otro archivo subido después al mismo documento no cambia lo aprobado.
//
// Una orden nueva se guarda y se envía a aprobar en un solo acto (decisión del propietario del
// 12-11-2026, P-10): son dos pedidos seguidos, y si el envío pide el segundo factor se pide ahí
// mismo. «Guardar como borrador» queda como salida secundaria. Si el envío no sale, la orden ya
// quedó guardada: se va a su ficha, que lo dice y tiene «Enviar a aprobar».

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
export type OrdenInicial = {
  proveedor?: string;
  importe?: string;
  moneda?: string;
  concepto?: string;
  comprobante?: string;
  /** La factura ya subida (vuelve del alta del proveedor). */
  factura?: Cargado;
};

/** Lo que lleva la pantalla de Proveedores para dar de alta el proveedor de la factura leída y volver
 * a la orden con lo que ya estaba escrito. Nunca una cuenta: la lectura no la trae. */
export type AltaDesdeLaFactura = {
  cuit: string | null;
  razon_social: string | null;
  domicilio: string | null;
  telefono: string | null;
  correo: string | null;
};

type Campo = keyof Datos;

/** Los campos que la lectura de la factura completa, con su valor en el formulario. */
function camposLeidos(l: LecturaCarga, proveedores: Proveedor[]): Partial<Datos> {
  const sugerido = proveedores.find((p) => p.id === l.proveedor_sugerido?.id && p.estado === "activo");
  return {
    ...(l.importe_centavos === null ? {} : { importe: aTexto(l.importe_centavos) }),
    ...(l.moneda !== null && (MONEDAS as readonly string[]).includes(l.moneda) ? { moneda: l.moneda } : {}),
    ...(l.numero_comprobante === null ? {} : { comprobante: l.numero_comprobante.slice(0, 200) }),
    ...(l.concepto === null ? {} : { concepto: l.concepto }),
    ...(sugerido === undefined ? {} : { proveedor: sugerido.id }),
  };
}

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
  /** `falla`: la orden quedó guardada, pero el envío a aprobar no salió. */
  alGuardar: (orden: string, texto: string, falla?: boolean) => void;
  /** La orden nueva ya existe, aunque falte enviarla: quien pide el código sabe adónde ir si se cancela. */
  alCrear?: (orden: string) => void;
  alCancelar: () => void;
  /** La orden cambió: la ficha la vuelve a leer y el formulario sigue sobre lo nuevo. */
  alCambiarLaOrden?: () => void;
};

/** Lo que dice la ficha cuando la orden quedó guardada y el envío no salió. */
export const noSeEnvio = (motivo: string) =>
  `La orden quedó guardada como borrador, pero no se envió a aprobar. ${motivo} Para enviarla, usar «Enviar a aprobar».`;

export function FormularioOrden({ consorcio, acto, aEntrar, aInicio, modificar, inicial, alGuardar, alCrear, alCancelar, alCambiarLaOrden }: Props) {
  const id = useId();
  /** Qué botón se tocó: una orden nueva se envía a aprobar salvo que se pida guardarla como borrador. */
  const destino = useRef<"enviar" | "borrador">("enviar");
  const { enviando, setAviso } = acto;
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [datos, setDatos] = useState<Datos | null>(null);
  const [confirmar, setConfirmar] = useState(false);
  /** Los datos de la versión que se abrió, para saber qué cambió la persona. */
  const vistos = useRef<Datos | null>(null);
  const versionVista = modificar?.version;
  /** La versión del archivo de la factura que se manda al guardar: la de la versión abierta, la que
   * vuelve del alta o la subida acá. */
  const [factura, setFactura] = useState<string | null>(
    modificar?.version.factura_version_id ?? inicial?.factura?.version ?? null,
  );
  const facturaTocada = useRef(false);
  const facturaDeLaVersion = useVersionVisible(
    consorcio,
    inicial?.factura === undefined ? (modificar?.version.documento_id ?? null) : null,
    inicial?.factura === undefined ? (modificar?.version.factura_version_id ?? null) : null,
    aEntrar,
  );
  /** Los campos que completó la lectura y la persona todavía no revisó. */
  const [deLaLectura, setDeLaLectura] = useState<ReadonlySet<Campo>>(new Set());
  /** La lectura con un proveedor que no está cargado: se ofrece darlo de alta. */
  const [sinProveedor, setSinProveedor] = useState<AltaDesdeLaFactura | null>(null);
  /** Un archivo de factura subiéndose, en el antivirus o leyéndose: hasta que termine no se guarda. */
  const [facturaEnEspera, setFacturaEnEspera] = useState(false);
  /** La factura subida en este formulario, ya aprobada por el antivirus. */
  const [subida, setSubida] = useState<Cargado | null>(null);

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
        const { importe: importeInicial, moneda, concepto, comprobante } = inicial ?? {};
        return {
          ...datosVacios(elegido?.id ?? ""),
          ...(importeInicial === undefined ? {} : { importe: importeInicial }),
          ...(moneda === undefined ? {} : { moneda }),
          ...(concepto === undefined ? {} : { concepto }),
          ...(comprobante === undefined ? {} : { comprobante }),
        };
      });
      return;
    }
    const nuevos = datosDe(versionVista, lectura.contratos);
    const antes = vistos.current;
    vistos.current = nuevos;
    setConfirmar(false);
    setDatos((d) => (d === null || antes === null ? nuevos : sobreLoNuevo(d, antes, nuevos)));
    // La factura sigue a la versión, salvo que la persona haya subido otra.
    if (!facturaTocada.current) setFactura(versionVista.factura_version_id ?? null);
    if (antes === null && versionVista.contrato_id !== null && nuevos.contrato === "")
      setAviso({ texto: "El contrato de esta orden ya no rige: se quitó del formulario. Si guardás, pide aprobación.", ok: false });
  }, [lectura, versionVista, inicial, setAviso]);

  function revisado(campo: Campo) {
    setDeLaLectura((marcados) => {
      if (!marcados.has(campo)) return marcados;
      const otros = new Set(marcados);
      otros.delete(campo);
      return otros;
    });
  }

  function cargarFactura(cargada: Cargado | null) {
    facturaTocada.current = true;
    setConfirmar(false);
    // Mientras el archivo nuevo no pasa el antivirus, no hay factura que mandar.
    setFactura(cargada?.version ?? null);
  }

  function leerFactura(l: LecturaCarga) {
    if (lectura.tipo !== "ok") return;
    const leidos = camposLeidos(l, lectura.proveedores);
    setConfirmar(false);
    setDatos((d) => {
      if (d === null) return d;
      const nuevo = { ...d, ...leidos };
      if (leidos.proveedor !== undefined && leidos.proveedor !== d.proveedor) nuevo.contrato = "";
      if (leidos.moneda !== undefined && leidos.moneda !== d.moneda) nuevo.contrato = "";
      return nuevo;
    });
    setDeLaLectura(new Set(Object.keys(leidos) as Campo[]));
    setSinProveedor(
      l.proveedor_sugerido === null && (l.cuit !== null || l.razon_social !== null)
        ? { cuit: l.cuit, razon_social: l.razon_social, domicilio: l.domicilio, telefono: l.telefono, correo: l.correo }
        : null,
    );
  }

  function cambiar<K extends keyof Datos>(campo: K, valor: Datos[K]) {
    revisado(campo);
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
    // Guardar ahora mandaría la orden sin el archivo que todavía no terminó (o sin la factura anterior).
    if (datos === null || facturaEnEspera) return;
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
      factura_version_id: factura,
    };
    void acto.hacer({
      quePide: ["guardar", modificar?.orden, modificar?.version.numero, datos, factura],
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
      alTerminar: (data) => {
        if (modificar !== undefined) alGuardar(data.id, "Listo: guardamos la versión nueva.");
        else if (destino.current === "borrador") alGuardar(data.id, "Listo: la orden quedó en borrador.");
        else {
          alCrear?.(data.id);
          enviarLaNueva(data);
        }
      },
      alFallar: (estado, codigo) => {
        if (cambioLaOrden(codigo)) alCambiarLaOrden?.();
        else if (cambioLoVisto(estado, codigo) && !SE_CORRIGE_EN_EL_FORMULARIO.has(codigo ?? "")) {
          alCambiarLaOrden?.();
          alCancelar();
        }
      },
    });
  }

  /** El segundo pedido de «Guardar y enviar a aprobar», sobre la versión recién guardada. */
  function enviarLaNueva(nueva: { id: string; version: number }) {
    const siSigueAbierto = (hacer: () => void) => {
      if (acto.montado.current) hacer();
    };
    void acto.hacer({
      quePide: ["enviar", nueva.id, nueva.version],
      nombre: "enviar la orden a aprobar",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/enviar-a-revision", {
          headers: encabezadoCsrf(),
          params: { path: { consorcio_id: consorcio, orden_id: nueva.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: { version_esperada: nueva.version },
        }),
      alTerminar: () => {
        // Hasta saber en qué quedó, el botón no se vuelve a tocar: la orden ya existe.
        acto.setEnviando(true);
        void leerComoQuedo(consorcio, nueva.id)
          .then((texto) => siSigueAbierto(() => alGuardar(nueva.id, texto)))
          .catch((e: unknown) =>
            siSigueAbierto(() => (e instanceof SinSesion ? aEntrar() : alGuardar(nueva.id, TEXTO_ENVIADA))),
          )
          .finally(() => acto.setEnviando(false));
      },
      alFallar: (estado, codigo) => alGuardar(nueva.id, noSeEnvio(mensajeDeActo(estado, codigo)), true),
      alNoLlegar: () => alGuardar(nueva.id, noSeEnvio(SIN_CONEXION), true),
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

  const marca = (campo: Campo) => (deLaLectura.has(campo) ? <MarcaDeLectura alRevisar={() => revisado(campo)} /> : null);
  const facturaVisible: Cargado | undefined = subida ?? inicial?.factura ?? facturaDeLaVersion ?? undefined;
  const alAlta = sinProveedor === null ? null : {
    consorcio,
    alta: sinProveedor,
    documento: facturaVisible,
    ...(modificar === undefined
      ? { paraPreparar: true, orden: { importe: datos.importe, moneda: datos.moneda, concepto: datos.concepto, comprobante: datos.comprobante, factura: facturaVisible } }
      : {}),
  };

  return (
    <form className="formulario tarjeta" onSubmit={guardar} aria-labelledby={`${id}-form`}>
      <h2 id={`${id}-form`}>{modificar === undefined ? "Preparar una orden" : "Modificar la orden"}</h2>
      <CargaConLectura consorcio={consorcio} tipo="factura" titulo="Factura" de="de la factura" textoVer="Ver la factura"
        deshabilitada={enviando} inicial={inicial?.factura ?? facturaDeLaVersion} aEntrar={aEntrar}
        alCargar={(c) => {
          setSubida(c);
          cargarFactura(c);
        }}
        alLeer={leerFactura} alEsperar={setFacturaEnEspera} />
      {alAlta !== null && (
        <div className="aviso-info" role="note">
          <p>
            El proveedor de la factura no está cargado en este edificio
            {sinProveedor?.razon_social != null && <> ({sinProveedor.razon_social})</>}.
          </p>
          <Link to={RUTA_PROVEEDORES} state={alAlta}>
            Dar de alta el proveedor
          </Link>
        </div>
      )}
      <label htmlFor={`${id}-proveedor`}>Proveedor</label>
      <select id={`${id}-proveedor`} required value={datos.proveedor} disabled={enviando}
        onChange={(e) => cambiar("proveedor", e.target.value)}>
        <option value="" disabled>
          Elegir un proveedor
        </option>
        {lectura.proveedores
          .filter((p) => p.estado === "activo" || p.id === datos.proveedor)
          .map((p) => (
            <option key={p.id} value={p.id}>
              {p.razon_social}
            </option>
          ))}
      </select>
      {marca("proveedor")}
      <label htmlFor={`${id}-importe`}>Importe</label>
      <CampoImporte id={`${id}-importe`} valor={datos.importe} deshabilitado={enviando}
        alCambiar={(texto) => cambiar("importe", texto)} />
      {marca("importe")}
      <label htmlFor={`${id}-moneda`}>Moneda</label>
      <select id={`${id}-moneda`} value={datos.moneda} disabled={enviando} onChange={(e) => cambiar("moneda", e.target.value)}>
        {MONEDAS.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
      {marca("moneda")}
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
      {marca("concepto")}
      <label htmlFor={`${id}-comprobante`}>Comprobante (opcional)</label>
      <input id={`${id}-comprobante`} maxLength={200} value={datos.comprobante} disabled={enviando}
        onChange={(e) => cambiar("comprobante", e.target.value)} />
      {marca("comprobante")}
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
      {facturaEnEspera && (
        <p id={`${id}-espera`} className="aviso-info">
          {TEXTO_ESPERA_PARA_GUARDAR}
        </p>
      )}
      <div className="acciones">
        <button type="submit" disabled={enviando || datos.proveedor === "" || facturaEnEspera}
          aria-describedby={facturaEnEspera ? `${id}-espera` : undefined} onClick={() => (destino.current = "enviar")}>
          {modificar === undefined ? "Guardar y enviar a aprobar" : confirmar ? "Sí, crear la versión nueva" : "Guardar la orden"}
        </button>
        {modificar === undefined && (
          <button type="submit" className="secundario" disabled={enviando || datos.proveedor === "" || facturaEnEspera}
            aria-describedby={facturaEnEspera ? `${id}-espera` : undefined} onClick={() => (destino.current = "borrador")}>
            Guardar como borrador
          </button>
        )}
        <button type="button" className="secundario peligro" disabled={enviando} onClick={alCancelar}>
          Cancelar
        </button>
      </div>
    </form>
  );
}
