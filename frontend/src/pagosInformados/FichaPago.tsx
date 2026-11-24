import { useId, useRef, useState } from "react";

import { api } from "../api/cliente";
import { encabezadoCsrf } from "../csrf";
import { ENCABEZADO_IDEMPOTENCIA, momento, renovarClave } from "../egresos/comun";
import { exigirSesion, SinSesion } from "../egresos/useConsorcio";
import { ErrorDeCampo, type ErroresDeCampo, faltaCompletar, hayErrores, marcaDe, SIN_ERRORES, sinElDe } from "../ErrorDeCampo";
import { type DatoDeFicha, FichaDeRegistro } from "../FichaDeRegistro";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { HojaDelArchivo } from "../VisorDocumento";
import { type FinDeEnvio, VentanaDeFormulario } from "../VentanaDeFormulario";
import { useArchivoDelPago } from "./archivos";
import { InformarPago } from "./InformarPago";
import {
  ANULAR,
  CONFIRMAR,
  CORREGIR,
  cuandoInformado,
  enPesos,
  fecha,
  type ListaPagos,
  mensajeDePago,
  PAGO_ANULADO,
  PAGO_CONFIRMADO,
  PAGO_RECHAZADO,
  type PagoInformado,
  RECHAZAR,
  TEXTO_COMPROBANTE,
  TEXTO_ESTADO,
  TEXTO_MEDIO,
  tituloDe,
  VER_COMPROBANTE,
} from "./textos";

// La ficha de un pago informado (PI-4, app 0130): todos sus datos, su historia y solo los actos que
// la API dice que la persona puede hacer (`acciones`, C9): confirmar, rechazar o anular con motivo
// (el Consejo) o corregir (quien lo informó, si quedó rechazado o anulado).

type Abierta = null | "confirmar" | "rechazar" | "anular" | "corregir" | "comprobante";

type Props = {
  consorcio: string;
  pago: PagoInformado;
  lista: ListaPagos;
  aEntrar: () => void;
  alCerrar: () => void;
  alTerminar: (texto: string) => void;
};

export function FichaPago({ consorcio, pago, lista, aEntrar, alCerrar, alTerminar }: Props) {
  const [abierta, setAbierta] = useState<Abierta>(null);
  const acciones = new Set(pago.acciones);
  const corrige = pago.corrige_a === null ? null : lista.elementos.find((p) => p.id === pago.corrige_a);
  const datos: DatoDeFicha[] = [
    { etiqueta: "Unidad", valor: pago.unidad_designacion },
    { etiqueta: "Importe", valor: enPesos(pago.importe) },
    { etiqueta: "Fecha del pago", valor: fecha(pago.fecha_pago) },
    { etiqueta: "Medio", valor: TEXTO_MEDIO[pago.medio] },
    ...(pago.numero_operacion === null ? [] : [{ etiqueta: "Número de operación", valor: pago.numero_operacion }]),
    { etiqueta: "Comprobante", valor: pago.comprobante_estado === null ? "Sin comprobante" : TEXTO_COMPROBANTE[pago.comprobante_estado] },
    { etiqueta: "Estado", valor: TEXTO_ESTADO[pago.estado] },
    { etiqueta: "Informado por", valor: cuandoInformado(pago) + (pago.origen === "correo" ? " (llegó por correo)" : "") },
    ...(corrige == null ? [] : [{ etiqueta: "Corrige a", valor: `${tituloDe(corrige)}, ${TEXTO_ESTADO[corrige.estado].toLowerCase()}` }]),
    ...(pago.historia.length === 0
      ? []
      : [
          {
            etiqueta: "Historia",
            valor: (
              <ul>
                {pago.historia.map((h) => (
                  <li key={`${h.a}-${h.registrado_en}`}>
                    {TEXTO_ESTADO[h.a]}: {h.registrado_por_nombre}, {momento(h.registrado_en)}
                    {h.motivo === null ? "" : `. Motivo: ${h.motivo}`}
                  </li>
                ))}
              </ul>
            ),
          },
        ]),
  ];
  const verComprobante = pago.comprobante_version_id !== null && pago.comprobante_estado === "disponible";
  const ventana =
    abierta === "confirmar" ? (
      <Decidir consorcio={consorcio} pago={pago} paso="confirmacion" aEntrar={aEntrar} alCerrar={() => setAbierta(null)} alTerminar={alTerminar} />
    ) : abierta === "rechazar" ? (
      <Decidir consorcio={consorcio} pago={pago} paso="rechazo" largoMotivo={lista.largo_motivo} aEntrar={aEntrar} alCerrar={() => setAbierta(null)} alTerminar={alTerminar} />
    ) : abierta === "anular" ? (
      <Decidir consorcio={consorcio} pago={pago} paso="anulacion" largoMotivo={lista.largo_motivo} aEntrar={aEntrar} alCerrar={() => setAbierta(null)} alTerminar={alTerminar} />
    ) : abierta === "corregir" ? (
      <InformarPago
        consorcio={consorcio}
        modo={{ tipo: "corregir", pago }}
        unidades={lista.unidades}
        largoNumero={lista.largo_numero_operacion}
        aEntrar={aEntrar}
        alCerrar={() => setAbierta(null)}
        alTerminar={alTerminar}
      />
    ) : abierta === "comprobante" ? (
      <Comprobante consorcio={consorcio} pago={pago.id} aEntrar={aEntrar} alCerrar={() => setAbierta(null)} />
    ) : null;
  return (
    <FichaDeRegistro titulo={tituloDe(pago)} datos={datos} alCerrar={alCerrar} ventana={ventana}>
      <p className="ficha-registro__acciones">
        {acciones.has("confirmar") && (
          <button type="button" onClick={() => setAbierta("confirmar")}>
            {CONFIRMAR}
          </button>
        )}
        {acciones.has("rechazar") && (
          <button type="button" className="secundario peligro" onClick={() => setAbierta("rechazar")}>
            {RECHAZAR}
          </button>
        )}
        {acciones.has("anular") && (
          <button type="button" className="secundario peligro" onClick={() => setAbierta("anular")}>
            {ANULAR}
          </button>
        )}
        {acciones.has("corregir") && (
          <button type="button" className={acciones.has("confirmar") ? "secundario" : undefined} onClick={() => setAbierta("corregir")}>
            {CORREGIR}
          </button>
        )}
        {verComprobante && (
          <button type="button" className="secundario" onClick={() => setAbierta("comprobante")}>
            {VER_COMPROBANTE}
          </button>
        )}
      </p>
    </FichaDeRegistro>
  );
}

function Comprobante({ consorcio, pago, aEntrar, alCerrar }: { consorcio: string; pago: string; aEntrar: () => void; alCerrar: () => void }) {
  const { lectura, reintentar } = useArchivoDelPago({ tipo: "pago", consorcio, pago }, aEntrar);
  return <HojaDelArchivo lectura={lectura} reintentar={reintentar} nombre="Comprobante del pago" alCerrar={alCerrar} />;
}

type Paso = "confirmacion" | "rechazo" | "anulacion";

const DEL_PASO: Record<Paso, { titulo: string; enviar: string; listo: string; peligro: boolean }> = {
  confirmacion: { titulo: CONFIRMAR, enviar: "Sí, confirmar", listo: PAGO_CONFIRMADO, peligro: false },
  rechazo: { titulo: RECHAZAR, enviar: "Rechazar", listo: PAGO_RECHAZADO, peligro: true },
  anulacion: { titulo: ANULAR, enviar: "Anular", listo: PAGO_ANULADO, peligro: true },
};

type PropsDecidir = {
  consorcio: string;
  pago: PagoInformado;
  paso: Paso;
  largoMotivo?: number;
  aEntrar: () => void;
  alCerrar: () => void;
  alTerminar: (texto: string) => void;
};

/** Confirmar (sin motivo), rechazar o anular (con motivo): PI-4. */
function Decidir({ consorcio, pago, paso, largoMotivo, aEntrar, alCerrar, alTerminar }: PropsDecidir) {
  const id = useId();
  const [motivo, setMotivo] = useState("");
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  const clave = useRef<string | null>(null);
  const conMotivo = paso !== "confirmacion";
  const { titulo, enviar: textoEnviar, listo, peligro } = DEL_PASO[paso];

  async function enviar(): Promise<FinDeEnvio> {
    if (conMotivo) {
      const faltan = faltaCompletar({ motivo: [motivo, "Falta escribir el motivo: quien informó el pago lo va a leer."] });
      if (hayErrores(faltan)) {
        setErrores(faltan);
        return "seguir";
      }
    }
    clave.current ??= crypto.randomUUID();
    const params = { path: { consorcio_id: consorcio, pago_id: pago.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current } };
    try {
      const { data, error, response } =
        paso === "confirmacion"
          ? await api.POST("/api/v1/consorcios/{consorcio_id}/pagos-informados/{pago_id}/confirmacion", { headers: encabezadoCsrf(), params })
          : paso === "rechazo"
            ? await api.POST("/api/v1/consorcios/{consorcio_id}/pagos-informados/{pago_id}/rechazo", { headers: encabezadoCsrf(), params, body: { motivo: motivo.trim() } })
            : await api.POST("/api/v1/consorcios/{consorcio_id}/pagos-informados/{pago_id}/anulacion", { headers: encabezadoCsrf(), params, body: { motivo: motivo.trim() } });
      exigirSesion(response);
      if (data === undefined) {
        const codigo = codigoDe(error);
        if (renovarClave(response.status, codigo)) clave.current = null;
        if (codigo === "motivo_con_cuenta" || codigo === "motivo_requerido") setErrores({ motivo: mensajeDePago(response.status, error) });
        return { rechazo: mensajeDePago(response.status, error) };
      }
      clave.current = null;
      alTerminar(listo);
      return "cerrar";
    } catch (e: unknown) {
      if (!(e instanceof SinSesion)) return { rechazo: SIN_CONEXION };
      aEntrar();
      return "seguir";
    }
  }

  const idMotivo = `${id}-motivo`;
  return (
    <VentanaDeFormulario titulo={titulo} textoEnviar={textoEnviar} alEnviar={enviar} alCerrar={alCerrar} peligro={peligro}>
      <p>
        {tituloDe(pago)}, pagado el {fecha(pago.fecha_pago)}.
      </p>
      {paso === "confirmacion" && <p className="ayuda">Confirmar dice que el comprobante es creíble; no mueve dinero.</p>}
      {conMotivo && (
        <>
          <label htmlFor={idMotivo}>Motivo</label>
          <textarea
            id={idMotivo}
            data-foco
            rows={4}
            value={motivo}
            maxLength={largoMotivo}
            {...marcaDe(idMotivo, errores.motivo)}
            onChange={(ev) => {
              setMotivo(ev.target.value);
              setErrores((e) => sinElDe(e, "motivo"));
            }}
          />
          <ErrorDeCampo idCampo={idMotivo} mensaje={errores.motivo} />
        </>
      )}
    </VentanaDeFormulario>
  );
}
