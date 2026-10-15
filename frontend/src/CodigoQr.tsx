import { encode } from "uqr";

// Código QR dibujado en el navegador como SVG: el secreto del segundo factor nunca sale a un
// servicio externo, y la CSP (`img-src 'self' data:`, `style-src 'self'`) no necesita
// excepciones porque son elementos con atributos, sin estilos en línea ni imágenes.

/** Margen blanco de cuatro módulos alrededor, el que pide la norma para que se pueda leer. */
const MARGEN = 4;
const PIXELES_POR_MODULO = 6;

/** El trazo de los módulos oscuros, uno por cuadrado de 1×1 en coordenadas de módulo. */
export function trazoQr(texto: string): { lado: number; trazo: string } {
  const { data, size } = encode(texto, { ecc: "M", border: MARGEN });
  const partes: string[] = [];
  data.forEach((fila, y) =>
    fila.forEach((oscuro, x) => {
      if (oscuro) partes.push(`M${x} ${y}h1v1h-1z`);
    }),
  );
  return { lado: size, trazo: partes.join("") };
}

export function CodigoQr({ texto, descripcion }: { texto: string; descripcion: string }) {
  const { lado, trazo } = trazoQr(texto);
  const pixeles = lado * PIXELES_POR_MODULO;
  return (
    <svg
      role="img"
      aria-label={descripcion}
      viewBox={`0 0 ${lado} ${lado}`}
      width={pixeles}
      height={pixeles}
      shapeRendering="crispEdges"
      className="codigo-qr"
    >
      <rect width={lado} height={lado} fill="#ffffff" />
      <path d={trazo} fill="#000000" />
    </svg>
  );
}
