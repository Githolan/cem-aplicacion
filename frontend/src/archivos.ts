// Los tipos de archivo que se suben y cómo se reconocen. Un documento (PDF, JPEG o PNG) se reconoce
// por su tipo, porque viaja como `Content-Type` y el servidor lo exige; el extracto CSV, también por
// el nombre, porque Windows con Excel lo informa como `application/vnd.ms-excel`. En los dos casos el
// servidor vuelve a controlar el contenido real.

export type TipoArchivo = {
  /** Cómo se nombra en la pantalla. */
  nombre: string;
  /** Lo que va en `accept` del campo del sistema. */
  acepta: readonly string[];
  /** Si se saca con la cámara o se elige de la galería. */
  imagen: boolean;
  admite: (archivo: File) => boolean;
};

const porTipo = (tipo: string) => (archivo: File) => archivo.type === tipo;

export const PDF: TipoArchivo = { nombre: "PDF", acepta: [".pdf", "application/pdf"], imagen: false, admite: porTipo("application/pdf") };
export const JPEG: TipoArchivo = { nombre: "JPEG", acepta: [".jpg", ".jpeg", "image/jpeg"], imagen: true, admite: porTipo("image/jpeg") };
export const PNG: TipoArchivo = { nombre: "PNG", acepta: [".png", "image/png"], imagen: true, admite: porTipo("image/png") };
export const CSV: TipoArchivo = {
  nombre: "CSV",
  acepta: [".csv", "text/csv"],
  imagen: false,
  admite: (archivo) => archivo.type === "text/csv" || /\.csv$/i.test(archivo.name),
};

/** Lo que admite un documento del edificio y un comprobante (`TIPOS_ADMITIDOS` del contrato). */
export const TIPOS_DE_DOCUMENTO: readonly TipoArchivo[] = [PDF, JPEG, PNG];
/** El tope de un documento, el mismo que el contrato (`TOPE_BYTES`). */
export const TOPE_BYTES = 20 * 1024 * 1024;
export const BYTES_POR_MB = 1024 * 1024;

const FORMATO_MB = new Intl.NumberFormat("es-AR", { maximumFractionDigits: 1 });

/** El valor de `accept` para esos tipos. */
export const aceptar = (tipos: readonly TipoArchivo[]) => tipos.flatMap((t) => t.acepta).join(",");

/** «PDF, JPEG o PNG». */
export function nombresDe(tipos: readonly TipoArchivo[]): string {
  const nombres = tipos.map((t) => t.nombre);
  return nombres.length < 2 ? nombres.join("") : `${nombres.slice(0, -1).join(", ")} o ${nombres.at(-1)}`;
}

/** «20 MB». */
export const enMegas = (bytes: number) => `${FORMATO_MB.format(bytes / BYTES_POR_MB)} MB`;

/** Por qué el archivo no sirve, antes de mandarlo; nulo si se puede mandar (el servidor decide igual). */
export function problemaDelArchivo(archivo: File, tipos: readonly TipoArchivo[], tope: number | null): string | null {
  if (!tipos.some((t) => t.admite(archivo))) return `Solo se aceptan archivos ${nombresDe(tipos)}. Hay que elegir otro archivo.`;
  if (tope !== null && archivo.size > tope) return `El archivo pasa de ${enMegas(tope)}, el máximo. Hay que elegir uno más chico.`;
  return null;
}
