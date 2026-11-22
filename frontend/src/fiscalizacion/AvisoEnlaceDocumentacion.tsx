import type { components } from "../api/tipos";
import { periodo as mesLargo } from "../egresos/Liquidacion";

// RF47 en Modo Consejo (app 0125): lo que pide el perfil normativo del edificio sobre el enlace o el
// QR a la documentación del período, frente a lo que encontró la lectura de la liquidación oficial.
// CEM no publica la liquidación en Modo Consejo: es un dato para fiscalizar, no un bloqueo, y no
// ofrece ningún botón. «Encontrado» no se avisa: CEM no sigue los enlaces, así que no afirma que
// lleven a la documentación (lo dice la base de cálculo del contrato).

type Enlace = components["schemas"]["EnlaceDocumentacion"];

/** Cómo se nombra la jurisdicción del perfil en el aviso. */
export const JURISDICCION: Record<Enlace["jurisdiccion"], string> = { caba: "la Ciudad de Buenos Aires" };
export const VER_LA_NORMA = "Ver la norma";

/** El aviso de la liquidación oficial de un período; null si no hay nada que avisar. */
export function textoAvisoEnlace(mes: string, enlace: Enlace | null): string | null {
  if (enlace === null || enlace.estado === "encontrado") return null;
  if (enlace.estado === "no_encontrado")
    return `La liquidación oficial de ${mesLargo(mes)} no trae el enlace ni el QR a la documentación del período: CEM los buscó en todo el documento y no encontró ninguno.`;
  return `No se sabe si la liquidación oficial de ${mesLargo(mes)} trae el enlace o el QR a la documentación del período: CEM no pudo buscarlos en todo el documento y no encontró ninguno. Conviene mirar el documento.`;
}

/** Quién lo pide y qué hace CEM con eso. */
export const textoQuienLoPide = (enlace: Enlace) =>
  `Lo pide la ${enlace.norma} (perfil normativo de ${JURISDICCION[enlace.jurisdiccion]}, versión ${enlace.version}). En Modo Consejo CEM no publica la liquidación: es un dato para pedírselo a la administración.`;

/** El resumen corto para una fila de la lista; null si no hay nada que avisar. */
export function resumenAvisoEnlace(enlace: Enlace | null): string | null {
  if (enlace === null || enlace.estado === "encontrado") return null;
  return enlace.estado === "no_encontrado" ? "Sin el enlace a la documentación" : "Sin saber si trae el enlace a la documentación";
}

export function AvisoEnlaceDocumentacion({ periodo, enlace }: { periodo: string; enlace: Enlace | null }) {
  const texto = textoAvisoEnlace(periodo, enlace);
  if (texto === null || enlace === null) return null;
  return (
    <div className="aviso-pendiente aviso-enlace" role="note">
      <p>{texto}</p>
      <p>
        {textoQuienLoPide(enlace)}{" "}
        <a href={enlace.fuente} target="_blank" rel="noopener noreferrer">
          {VER_LA_NORMA}
        </a>
      </p>
    </div>
  );
}
