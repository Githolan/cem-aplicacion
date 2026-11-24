import { useId, useRef, useState } from "react";

import { api } from "../api/cliente";
import { JPEG, PDF, PNG, hoyDelDispositivo, TOPE_BYTES } from "../archivos";
import { encabezadoCsrf } from "../csrf";
import { CampoImporte } from "../egresos/CampoImporte";
import { aCentavos, aDecimal, aTexto, ENCABEZADO_IDEMPOTENCIA, renovarClave } from "../egresos/comun";
import { exigirSesion, SinSesion } from "../egresos/useConsorcio";
import { ErrorDeCampo, type ErroresDeCampo, erroresDe, faltaCompletar, hayErrores, marcaDe, SIN_ERRORES, sinElDe } from "../ErrorDeCampo";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { SubirArchivo } from "../SubirArchivo";
import { type FinDeEnvio, VentanaDeFormulario } from "../VentanaDeFormulario";
import { subirComprobante } from "./archivos";
import {
  COMPLETAR,
  CORREGIR,
  INFORMAR,
  type Medio,
  leidoEnPesos,
  MEDIOS,
  mensajeDePago,
  type PagoInformado,
  type PagoPorCorreo,
  pagoInformado,
  SIN_COMPROBANTE,
  TEXTO_MEDIO,
} from "./textos";

// Informar un pago (PI-1, app 0129), corregir uno rechazado o anulado (PI-3) o completar el que
// llegó por correo (PC-4, app 0132). Unidad (solo las que la persona puede informar), importe, fecha,
// medio, número de operación y comprobante; en efectivo, los dos últimos no hacen falta. Lo que leyó
// la IA de un correo se propone y la persona lo revisa (RN21): nada se informa sin su envío.

export const TIPOS_DE_COMPROBANTE = [PDF, JPEG, PNG] as const;

export type Modo =
  | { tipo: "nuevo" }
  | { tipo: "corregir"; pago: PagoInformado }
  | { tipo: "correo"; recibido: PagoPorCorreo };

type Unidad = { id: string; designacion: string };

type Props = {
  consorcio: string;
  modo: Modo;
  /** Las unidades para las que la persona puede informar (app 0134). */
  unidades: readonly Unidad[];
  largoNumero: number;
  aEntrar: () => void;
  alCerrar: () => void;
  alTerminar: (texto: string) => void;
};

const NINGUNA = "";

function valoresIniciales(modo: Modo, unidades: readonly Unidad[]) {
  if (modo.tipo === "corregir") {
    const p = modo.pago;
    const centavos = aCentavos(p.importe);
    return { unidad: p.unidad_id, importe: centavos === null ? "" : aTexto(centavos), fecha: p.fecha_pago, medio: p.medio, numero: p.numero_operacion ?? "" };
  }
  const unaSola = unidades.length === 1 ? (unidades[0]?.id ?? NINGUNA) : NINGUNA;
  if (modo.tipo === "correo") {
    const r = modo.recibido;
    // Solo un importe leído en pesos entra en el campo «en pesos»; otra moneda, o sin moneda, no.
    const centavos = r.importe !== null && leidoEnPesos(r) ? aCentavos(r.importe) : null;
    return { unidad: unaSola, importe: centavos === null ? "" : aTexto(centavos), fecha: r.fecha_pago ?? "", medio: "transferencia" as Medio, numero: r.numero_operacion ?? "" };
  }
  return { unidad: unaSola, importe: "", fecha: "", medio: "transferencia" as Medio, numero: "" };
}

export function InformarPago({ consorcio, modo, unidades, largoNumero, aEntrar, alCerrar, alTerminar }: Props) {
  const id = useId();
  const inicio = valoresIniciales(modo, unidades);
  const [unidad, setUnidad] = useState(inicio.unidad);
  const [importe, setImporte] = useState(inicio.importe);
  const [fechaPago, setFechaPago] = useState(inicio.fecha);
  const [medio, setMedio] = useState<Medio>(inicio.medio);
  const [numero, setNumero] = useState(inicio.numero);
  const [archivo, setArchivo] = useState<File | null>(null);
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  const clave = useRef<string | null>(null);
  const claveArchivo = useRef<string | null>(null);
  /** El comprobante ya subido: un reintento con el mismo archivo y la misma unidad lo vuelve a citar. */
  const subido = useRef<{ archivo: File; unidad: string; version: string } | null>(null);
  const conArchivo = modo.tipo !== "correo";
  const sinComprobante = medio === SIN_COMPROBANTE;
  const hoy = hoyDelDispositivo();

  async function enviar(): Promise<FinDeEnvio> {
    const centavos = aCentavos(importe);
    const faltan: ErroresDeCampo = {
      ...faltaCompletar({
        unidad: [unidad, "Falta elegir la unidad."],
        fecha: [fechaPago, "Falta la fecha en que se pagó."],
        ...(sinComprobante ? {} : { numero: [numero, "Falta el número de operación."] as const }),
      }),
      ...(centavos === null ? { importe: importe.trim() === "" ? "Falta el importe." : "El importe no se entiende: escribirlo como 45000 o 45.000,50." } : {}),
      ...(fechaPago !== "" && fechaPago > hoy ? { fecha: "La fecha no puede ser posterior a hoy." } : {}),
      ...(conArchivo && !sinComprobante && archivo === null ? { archivo: "Falta el comprobante." } : {}),
    };
    if (hayErrores(faltan) || centavos === null) {
      setErrores(faltan);
      return "seguir";
    }
    try {
      let version: string | null = null;
      if (conArchivo && !sinComprobante && archivo !== null) {
        if (subido.current?.archivo === archivo && subido.current.unidad === unidad) version = subido.current.version;
        else {
          claveArchivo.current ??= crypto.randomUUID();
          const r = await subirComprobante(consorcio, unidad, archivo, claveArchivo.current);
          if ("falla" in r) {
            if (r.renovar) claveArchivo.current = null;
            return { rechazo: r.falla };
          }
          claveArchivo.current = null;
          subido.current = { archivo, unidad, version: r.version };
          version = r.version;
        }
      }
      clave.current ??= crypto.randomUUID();
      const numeroLimpio = sinComprobante && numero.trim() === "" ? null : numero.trim();
      const comunes = { unidad_id: unidad, importe: aDecimal(centavos), fecha_pago: fechaPago, medio, numero_operacion: numeroLimpio };
      const { data, error, response } =
        modo.tipo === "correo"
          ? await api.POST("/api/v1/consorcios/{consorcio_id}/pagos-informados/por-correo/{recibido_id}/informe", {
              headers: encabezadoCsrf(),
              params: { path: { consorcio_id: consorcio, recibido_id: modo.recibido.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current } },
              body: comunes,
            })
          : await api.POST("/api/v1/consorcios/{consorcio_id}/pagos-informados", {
              headers: encabezadoCsrf(),
              params: { path: { consorcio_id: consorcio }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current } },
              body: { ...comunes, comprobante_version_id: version, corrige_a: modo.tipo === "corregir" ? modo.pago.id : null },
            });
      exigirSesion(response);
      if (data === undefined) {
        const codigo = codigoDe(error);
        if (renovarClave(response.status, codigo)) clave.current = null;
        if (codigo === "fecha_futura") setErrores({ fecha: mensajeDePago(response.status, error) });
        else if (codigo === "texto_con_cuenta") setErrores({ numero: mensajeDePago(response.status, error) });
        else {
          const deCampo = erroresDe(error);
          if (hayErrores(deCampo)) setErrores(deCampo);
        }
        return { rechazo: mensajeDePago(response.status, error) };
      }
      clave.current = null;
      alTerminar(pagoInformado(data.ya_informado));
      return "cerrar";
    } catch (e: unknown) {
      if (!(e instanceof SinSesion)) return { rechazo: SIN_CONEXION };
      aEntrar();
      return "seguir";
    }
  }

  const titulo = modo.tipo === "corregir" ? CORREGIR : modo.tipo === "correo" ? COMPLETAR : INFORMAR;
  const idUnidad = `${id}-unidad`;
  const idImporte = `${id}-importe`;
  const idFecha = `${id}-fecha`;
  const idMedio = `${id}-medio`;
  const idNumero = `${id}-numero`;
  const unidadFija = modo.tipo === "corregir" ? modo.pago.unidad_designacion : unidades.length === 1 ? unidades[0]?.designacion : undefined;
  return (
    <VentanaDeFormulario titulo={titulo} textoEnviar="Informar el pago" alEnviar={enviar} alCerrar={alCerrar} ancha>
      {modo.tipo === "correo" && (
        <p className="ayuda">
          Los datos que ya aparecen los leyó la aplicación en el archivo: revisarlos antes de informar. El pago se informa recién al enviar.
        </p>
      )}
      {unidadFija !== undefined ? (
        <p>Unidad {unidadFija}</p>
      ) : (
        <>
          <label htmlFor={idUnidad}>Unidad</label>
          <select
            id={idUnidad}
            value={unidad}
            {...marcaDe(idUnidad, errores.unidad)}
            onChange={(ev) => {
              setUnidad(ev.target.value);
              setErrores((e) => sinElDe(e, "unidad"));
            }}
          >
            <option value={NINGUNA}>Elegir la unidad</option>
            {unidades.map((u) => (
              <option key={u.id} value={u.id}>
                Unidad {u.designacion}
              </option>
            ))}
          </select>
          <ErrorDeCampo idCampo={idUnidad} mensaje={errores.unidad} />
        </>
      )}
      <label htmlFor={idImporte}>Importe pagado (en pesos)</label>
      <CampoImporte
        id={idImporte}
        valor={importe}
        deshabilitado={false}
        {...marcaDe(idImporte, errores.importe)}
        alCambiar={(texto) => {
          setImporte(texto);
          setErrores((e) => sinElDe(e, "importe"));
        }}
      />
      <ErrorDeCampo idCampo={idImporte} mensaje={errores.importe} />
      <label htmlFor={idFecha}>Fecha en que se pagó</label>
      <input
        id={idFecha}
        type="date"
        max={hoy}
        value={fechaPago}
        {...marcaDe(idFecha, errores.fecha)}
        onChange={(ev) => {
          setFechaPago(ev.target.value);
          setErrores((e) => sinElDe(e, "fecha"));
        }}
      />
      <ErrorDeCampo idCampo={idFecha} mensaje={errores.fecha} />
      <label htmlFor={idMedio}>Medio de pago</label>
      <select
        id={idMedio}
        data-foco
        value={medio}
        onChange={(ev) => {
          setMedio(ev.target.value as Medio);
          setErrores((e) => sinElDe(sinElDe(e, "numero"), "archivo"));
        }}
      >
        {MEDIOS.map((m) => (
          <option key={m} value={m}>
            {TEXTO_MEDIO[m]}
          </option>
        ))}
      </select>
      <label htmlFor={idNumero}>{sinComprobante ? "Número de recibo (si hay)" : "Número de operación"}</label>
      <input
        id={idNumero}
        value={numero}
        maxLength={largoNumero}
        {...marcaDe(idNumero, errores.numero)}
        onChange={(ev) => {
          setNumero(ev.target.value);
          setErrores((e) => sinElDe(e, "numero"));
        }}
      />
      <ErrorDeCampo idCampo={idNumero} mensaje={errores.numero} />
      {conArchivo && !sinComprobante && (
        <>
          <SubirArchivo
            titulo="Comprobante"
            de="del comprobante"
            tipos={TIPOS_DE_COMPROBANTE}
            tope={TOPE_BYTES}
            elegido={archivo?.name ?? null}
            alElegir={(f) => {
              setArchivo(f);
              setErrores((e) => sinElDe(e, "archivo"));
            }}
          />
          {errores.archivo !== undefined && (
            <p className="aviso-error" role="alert">
              {errores.archivo}
            </p>
          )}
        </>
      )}
      {modo.tipo === "correo" && <p className="ayuda">El comprobante es el archivo que llegó por correo.</p>}
    </VentanaDeFormulario>
  );
}
