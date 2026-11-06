import { useEffect, useRef, useState } from "react";

import { api } from "../api/cliente";
import { mensajeDeError } from "../egresos/comun";
import { Reintentar } from "../egresos/EstadoPantalla";
import { exigirSesion, SinSesion } from "../egresos/useConsorcio";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { abrirPdf } from "./pdf";

// El comprobante en la pantalla, no una descarga (§4). El archivo llega por el mismo origen, con el
// control de acceso de `descargar`, y nunca se abre como una página: las imágenes, en un `<img>`
// sobre un `blob:`; los PDF, dibujados en un `<canvas>`, para que se vean igual en una PWA de iOS.

/** Un comprobante son una o dos páginas; a lo sumo se dibujan estas. */
const PAGINAS_A_DIBUJAR = 5;
const FALLA_PDF = "No pudimos mostrar este PDF.";

type Lectura = { tipo: "cargando" } | { tipo: "falla"; texto: string } | { tipo: "ok"; archivo: Blob };

export function useUrlDelArchivo(archivo: Blob | null): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (archivo === null) {
      setUrl(null);
      return;
    }
    const nueva = URL.createObjectURL(archivo);
    setUrl(nueva);
    return () => URL.revokeObjectURL(nueva);
  }, [archivo]);
  return url;
}

function Imagen({ archivo }: { archivo: Blob }) {
  const url = useUrlDelArchivo(archivo);
  return url === null ? null : <img src={url} alt="Comprobante de la transferencia" />;
}

function Pdf({ archivo }: { archivo: Blob }) {
  const paginas = useRef<HTMLDivElement>(null);
  const [falla, setFalla] = useState(false);
  const [total, setTotal] = useState<number | null>(null);

  useEffect(() => {
    const destino = paginas.current;
    if (destino === null) return;
    let vigente = true;
    let cerrar = () => undefined as void;

    async function dibujar(lugar: HTMLDivElement) {
      const tarea = await abrirPdf(new Uint8Array(await archivo.arrayBuffer()));
      cerrar = () => void tarea.destroy();
      const documento = await tarea.promise;
      const cantidad = Math.min(documento.numPages, PAGINAS_A_DIBUJAR);
      for (let numero = 1; numero <= cantidad && vigente; numero += 1) {
        const pagina = await documento.getPage(numero);
        const escala = (lugar.clientWidth / pagina.getViewport({ scale: 1 }).width) * window.devicePixelRatio;
        const vista = pagina.getViewport({ scale: escala });
        const lienzo = document.createElement("canvas");
        lienzo.width = Math.floor(vista.width);
        lienzo.height = Math.floor(vista.height);
        lienzo.setAttribute("role", "img");
        lienzo.setAttribute("aria-label", `Comprobante de la transferencia, página ${numero}`);
        const contexto = lienzo.getContext("2d");
        if (contexto === null) throw new Error("el navegador no da un contexto 2d");
        await pagina.render({ canvasContext: contexto, viewport: vista }).promise;
        if (vigente) lugar.append(lienzo);
      }
      if (vigente) setTotal(documento.numPages);
    }

    dibujar(destino).catch(() => {
      if (vigente) setFalla(true);
    });
    return () => {
      vigente = false;
      cerrar();
      destino.replaceChildren();
    };
  }, [archivo]);

  return (
    <>
      <div ref={paginas} />
      {total !== null && total > PAGINAS_A_DIBUJAR && (
        <p>
          Se ven las primeras {PAGINAS_A_DIBUJAR} páginas de {total}.
        </p>
      )}
      {falla && (
        <p className="aviso-error" role="alert">
          {FALLA_PDF}
        </p>
      )}
    </>
  );
}

type Props = {
  consorcio: string;
  documento: string;
  version: string;
  aEntrar: () => void;
};

export function VisorComprobante({ consorcio, documento, version, aEntrar }: Props) {
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);

  useEffect(() => {
    let vigente = true;
    setLectura({ tipo: "cargando" });
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/documentos/{documento_id}/versiones/{version_id}/contenido", {
        params: { path: { consorcio_id: consorcio, documento_id: documento, version_id: version } },
        parseAs: "blob",
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        if (data === undefined) setLectura({ tipo: "falla", texto: mensajeDeError(response.status, codigoDe(error)) });
        else setLectura({ tipo: "ok", archivo: data });
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else setLectura({ tipo: "falla", texto: SIN_CONEXION });
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, documento, version, vuelta, aEntrar]);

  return (
    <div className="visor-comprobante">
      {lectura.tipo === "cargando" && <p role="status">Cargando el comprobante…</p>}
      {lectura.tipo === "falla" && (
        <>
          <p className="aviso-error" role="alert">
            {lectura.texto}
          </p>
          <Reintentar alReintentar={() => setVuelta((n) => n + 1)} />
        </>
      )}
      {lectura.tipo === "ok" && (lectura.archivo.type === "application/pdf" ? <Pdf archivo={lectura.archivo} /> : <Imagen archivo={lectura.archivo} />)}
    </div>
  );
}
