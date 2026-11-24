import { useRef, useState } from "react";

import { api } from "../api/cliente";
import { encabezadoCsrf } from "../csrf";
import { ENCABEZADO_IDEMPOTENCIA, momento, renovarClave } from "../egresos/comun";
import { exigirSesion, SinSesion } from "../egresos/useConsorcio";
import { type DatoDeFicha, FichaDeRegistro } from "../FichaDeRegistro";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { type FinDeEnvio, VentanaDeFormulario } from "../VentanaDeFormulario";
import { HojaDelArchivo } from "../VisorDocumento";
import { useArchivoDelPago } from "./archivos";
import { InformarPago } from "./InformarPago";
import { ARCHIVO_DESCARTADO, AVISO_MONEDA, COMPLETAR, DESCARTAR, fecha, importeLeido, leidoEnPesos, type ListaPagos, mensajeDePago, type PagoPorCorreo, tituloCorreo, VER_ARCHIVO } from "./textos";

// Un comprobante que llegó por correo y espera a quien lo mandó (PC-4, app 0132). La ficha muestra
// lo que leyó la aplicación como propuesta (RN21: la IA propone, la persona decide) y ofrece
// completarlo e informarlo, o descartarlo si no era un comprobante (un logo, otro archivo).

type Abierta = null | "completar" | "descartar" | "archivo";

type Props = {
  consorcio: string;
  recibido: PagoPorCorreo;
  lista: ListaPagos;
  aEntrar: () => void;
  alCerrar: () => void;
  alTerminar: (texto: string) => void;
};

const propuesto = (valor: string | null | undefined, texto: (v: string) => string = (v) => v) => (valor == null ? "No se leyó" : texto(valor));

export function FichaCorreo({ consorcio, recibido, lista, aEntrar, alCerrar, alTerminar }: Props) {
  const [abierta, setAbierta] = useState<Abierta>(null);
  const leido = recibido.leido && recibido.legible !== false;
  const datos: DatoDeFicha[] = [
    { etiqueta: "Llegó", valor: momento(recibido.recibido_en) },
    ...(leido
      ? [
          { etiqueta: "Importe leído", valor: recibido.importe === null ? propuesto(null) : importeLeido({ importe: recibido.importe, moneda: recibido.moneda }) },
          { etiqueta: "Fecha leída", valor: propuesto(recibido.fecha_pago, fecha) },
          { etiqueta: "Número leído", valor: propuesto(recibido.numero_operacion) },
        ]
      : [{ etiqueta: "Lectura", valor: recibido.leido ? "No se pudo leer el archivo: hay que escribir los datos." : "Sin lectura automática: hay que escribir los datos al completar." }]),
  ];
  const puedeInformar = lista.unidades.length > 0;
  const ventana =
    abierta === "completar" ? (
      <InformarPago
        consorcio={consorcio}
        modo={{ tipo: "correo", recibido }}
        unidades={lista.unidades}
        largoNumero={lista.largo_numero_operacion}
        aEntrar={aEntrar}
        alCerrar={() => setAbierta(null)}
        alTerminar={alTerminar}
      />
    ) : abierta === "descartar" ? (
      <Descartar consorcio={consorcio} recibido={recibido} aEntrar={aEntrar} alCerrar={() => setAbierta(null)} alTerminar={alTerminar} />
    ) : abierta === "archivo" ? (
      <Archivo consorcio={consorcio} recibido={recibido.id} aEntrar={aEntrar} alCerrar={() => setAbierta(null)} />
    ) : null;
  return (
    <FichaDeRegistro titulo={tituloCorreo(recibido)} datos={datos} alCerrar={alCerrar} ventana={ventana}>
      {leido && <p className="ayuda">Lo leyó la aplicación en el archivo: es una propuesta para revisar al completar.</p>}
      {leido && recibido.importe !== null && !leidoEnPesos(recibido) && <p className="ayuda">{AVISO_MONEDA}</p>}
      <p className="ficha-registro__acciones">
        {puedeInformar && (
          <button type="button" onClick={() => setAbierta("completar")}>
            {COMPLETAR}
          </button>
        )}
        {recibido.comprobante_estado === "disponible" && (
          <button type="button" className="secundario" onClick={() => setAbierta("archivo")}>
            {VER_ARCHIVO}
          </button>
        )}
        <button type="button" className="secundario peligro" onClick={() => setAbierta("descartar")}>
          {DESCARTAR}
        </button>
      </p>
    </FichaDeRegistro>
  );
}

function Archivo({ consorcio, recibido, aEntrar, alCerrar }: { consorcio: string; recibido: string; aEntrar: () => void; alCerrar: () => void }) {
  const { lectura, reintentar } = useArchivoDelPago({ tipo: "correo", consorcio, recibido }, aEntrar);
  return <HojaDelArchivo lectura={lectura} reintentar={reintentar} nombre="Archivo recibido por correo" alCerrar={alCerrar} />;
}

type PropsDescartar = {
  consorcio: string;
  recibido: PagoPorCorreo;
  aEntrar: () => void;
  alCerrar: () => void;
  alTerminar: (texto: string) => void;
};

function Descartar({ consorcio, recibido, aEntrar, alCerrar, alTerminar }: PropsDescartar) {
  const clave = useRef<string | null>(null);
  async function enviar(): Promise<FinDeEnvio> {
    clave.current ??= crypto.randomUUID();
    try {
      const { data, error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/pagos-informados/por-correo/{recibido_id}/descarte", {
        headers: encabezadoCsrf(),
        params: { path: { consorcio_id: consorcio, recibido_id: recibido.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current } },
      });
      exigirSesion(response);
      if (data === undefined) {
        if (renovarClave(response.status, codigoDe(error))) clave.current = null;
        return { rechazo: mensajeDePago(response.status, error) };
      }
      clave.current = null;
      alTerminar(ARCHIVO_DESCARTADO);
      return "cerrar";
    } catch (e: unknown) {
      if (!(e instanceof SinSesion)) return { rechazo: SIN_CONEXION };
      aEntrar();
      return "seguir";
    }
  }
  return (
    <VentanaDeFormulario titulo={DESCARTAR} textoEnviar="Sí, descartar" alEnviar={enviar} alCerrar={alCerrar} peligro>
      <p>No se informa ningún pago con este archivo. Queda guardado como constancia y deja de aparecer en la lista.</p>
    </VentanaDeFormulario>
  );
}
