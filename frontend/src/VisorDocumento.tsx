import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api/cliente";
import { mensajeDeError } from "./egresos/comun";
import { Reintentar } from "./egresos/EstadoPantalla";
import { exigirSesion, SinSesion } from "./egresos/useConsorcio";
import { codigoDe, SIN_CONEXION } from "./fallas";
import { Hoja } from "./Hoja";
import { abrirPdf } from "./pagos/pdf";

// Un archivo del edificio en la pantalla, no una descarga (§4 y comentario 32 de la revisión). Llega
// por el mismo origen (`…/contenido`, con el control de acceso de `descargar`) y nunca se abre como
// una página ni en un iframe: las imágenes, en un `<img>` sobre un `blob:` (la CSP lo admite); los
// PDF, dibujados en un `<canvas>`, para que se vean igual en una PWA de iOS. Lo mismo sirve dentro
// de la pantalla (el comprobante de un pago) y en una hoja más ancha que se abre con «Ver».

/** Un comprobante son una o dos páginas y un documento rara vez pasa de unas pocas; a lo sumo se dibujan estas. */
const PAGINAS_A_DIBUJAR = 5;
const FALLA_PDF = "No pudimos mostrar este PDF.";

export type LecturaDelArchivo = { tipo: "cargando" } | { tipo: "falla"; texto: string } | { tipo: "ok"; archivo: Blob };

export type CualArchivo = { consorcio: string; documento: string; version: string };

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

/** Pide el archivo de una versión disponible. `reintentar` vuelve a pedirlo después de una falla. */
export function useArchivoDeVersion({ consorcio, documento, version }: CualArchivo, aEntrar: () => void) {
  const [lectura, setLectura] = useState<LecturaDelArchivo>({ tipo: "cargando" });
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

  const reintentar = useCallback(() => setVuelta((n) => n + 1), []);
  return { lectura, reintentar };
}

function Imagen({ archivo, nombre }: { archivo: Blob; nombre: string }) {
  const url = useUrlDelArchivo(archivo);
  return url === null ? null : <img src={url} alt={nombre} />;
}

function Pdf({ archivo, nombre }: { archivo: Blob; nombre: string }) {
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
        lienzo.setAttribute("aria-label", `${nombre}, página ${numero}`);
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
  }, [archivo, nombre]);

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

type PropsContenido = {
  lectura: LecturaDelArchivo;
  reintentar: () => void;
  /** El nombre de lo que se ve, para el lector de pantalla. */
  nombre: string;
};

/** Lo que se ve de un archivo: mientras llega, la falla con «Reintentar», o la imagen o el PDF. */
export function ContenidoDelArchivo({ lectura, reintentar, nombre }: PropsContenido) {
  return (
    <div className="visor-comprobante">
      {lectura.tipo === "cargando" && <p role="status">Cargando el archivo…</p>}
      {lectura.tipo === "falla" && (
        <>
          <p className="aviso-error" role="alert">
            {lectura.texto}
          </p>
          <Reintentar alReintentar={reintentar} />
        </>
      )}
      {lectura.tipo === "ok" &&
        (lectura.archivo.type === "application/pdf" ? <Pdf archivo={lectura.archivo} nombre={nombre} /> : <Imagen archivo={lectura.archivo} nombre={nombre} />)}
    </div>
  );
}

type PropsHoja = PropsContenido & { alCerrar: () => void };

/** El archivo en una hoja ancha, con «Cerrar» y Escape. Recibe lo ya pedido: no lo vuelve a pedir. */
export function HojaDelArchivo({ lectura, reintentar, nombre, alCerrar }: PropsHoja) {
  return (
    <Hoja titulo={nombre} alEscapar={alCerrar} ancha>
      <ContenidoDelArchivo lectura={lectura} reintentar={reintentar} nombre={nombre} />
      <div className="formulario">
        <div className="acciones">
          <button type="button" className="secundario" data-foco onClick={alCerrar}>
            Cerrar
          </button>
        </div>
      </div>
    </Hoja>
  );
}

type PropsVisor = CualArchivo & { nombre: string; alCerrar: () => void; aEntrar: () => void };

/** El archivo de una versión disponible en una hoja: lo pide al abrirse. */
export function VisorDocumento({ consorcio, documento, version, nombre, alCerrar, aEntrar }: PropsVisor) {
  const { lectura, reintentar } = useArchivoDeVersion({ consorcio, documento, version }, aEntrar);
  return <HojaDelArchivo lectura={lectura} reintentar={reintentar} nombre={nombre} alCerrar={alCerrar} />;
}
