import type { ReactNode } from "react";

import type { components } from "../api/tipos";
import { type DatoDeFicha, FichaDeRegistro } from "../FichaDeRegistro";
import { estadoSiNoEsActivo } from "./comun";
import { DocumentacionProveedor } from "./DocumentacionProveedor";
import { ExigenciaLegal } from "./ExigenciaLegal";
import { SalteoDocumentacion } from "./SalteoDocumentacion";

// La ficha del proveedor, una sola para las dos pantallas (informe de fricción, tanda 4: PRV-01 y
// CPR-01): la de quien gestiona (`/egresos/proveedores`) y la de quien consulta (`/proveedores`).
// Va en una hoja sobre la lista (`FichaDeRegistro`): arriba los datos, enseguida las acciones que el
// rol permite y después lo que exige la ley, el salteo y los seguros. «Cerrar» y Escape vuelven a la
// lista. Cada carga se hace en su ventana, encima de la ficha.

type ProveedorBase = components["schemas"]["ProveedorConsulta"];
type Salteo = components["schemas"]["SalteoDocumentacion"];

/** Lo común a la ficha de gestión (`ProveedorConVersiones`) y a la de consulta (`FichaProveedorConsulta`). */
type Ficha = {
  proveedor: ProveedorBase;
  configuracion: components["schemas"]["ConfiguracionProveedores"];
  documentacion: components["schemas"]["DocumentacionProveedor"][];
  faltantes: components["schemas"]["RequisitoFaltante"][];
  requisitos: components["schemas"]["RequisitoLegal"][];
  rubros_legales: string[];
  salteos: Salteo[];
};

/**
 * Los datos de la ficha: solo lo que está cargado (PRV-04, CPR-02). El estado se dice solo si no es
 * «Activo». `sinCuit` es lo que se dice sin CUIT a quien lo puede cargar (es un pendiente con su
 * botón); a quien solo consulta no se le dice. `cuenta`, solo para quien gestiona.
 */
export function datosDelProveedor(p: ProveedorBase, { sinCuit, cuenta }: { sinCuit?: string; cuenta?: string } = {}): DatoDeFicha[] {
  const cuit = p.cuit ?? sinCuit ?? null;
  const filas: readonly (readonly [string, string | null])[] = [
    ["CUIT", cuit],
    ["Rubro", p.rubro],
    ["Contacto", p.contacto],
    ["Teléfono", p.telefono],
    ["WhatsApp", p.whatsapp],
    ["Correo", p.correo],
    ["Domicilio", p.domicilio],
    ["Emergencias", p.atiende_emergencias ? "Atiende emergencias" : null],
    ["Estado", estadoSiNoEsActivo(p.estado)],
    ["Cuenta", cuenta ?? null],
  ];
  return filas.filter((f): f is readonly [string, string] => f[1] !== null).map(([etiqueta, valor]) => ({ etiqueta, valor }));
}

type Props = {
  consorcio: string;
  ficha: Ficha;
  datos: readonly DatoDeFicha[];
  /** Asignar y quitar rubros legales: quien gestiona. */
  puedeCambiar: boolean;
  /** Cargar y reemplazar seguros: lo dice la API. */
  puedeCargar: boolean;
  /** Lo que la configuración deja saltear (solo para mostrar: decide la API). */
  puedeSaltear: boolean;
  /** Los salteos que registró esta persona en esta pantalla (comentario 40). */
  salteosPropios: ReadonlySet<number>;
  alRegistrarSalteo: (numero: number) => void;
  /** Los botones de las acciones de quien gestiona. */
  acciones?: ReactNode;
  /** La historia de versiones (solo gestión). */
  historia?: ReactNode;
  /** La ventana de la acción abierta, encima de la ficha. */
  ventana?: ReactNode;
  ocupada?: boolean;
  aEntrar: () => void;
  /** Algo cambió en una sección: volver a leer la ficha (y la lista, por las marcas). */
  alCambiar: () => void;
  alCerrar: () => void;
};

export function FichaDelProveedor({
  consorcio,
  ficha,
  datos,
  puedeCambiar,
  puedeCargar,
  puedeSaltear,
  salteosPropios,
  alRegistrarSalteo,
  acciones,
  historia,
  ventana,
  ocupada = false,
  aEntrar,
  alCambiar,
  alCerrar,
}: Props) {
  const p = ficha.proveedor;
  return (
    <FichaDeRegistro titulo={p.razon_social} datos={datos} alCerrar={alCerrar} ocupada={ocupada} ventana={ventana}>
      {acciones}
      {historia}
      <ExigenciaLegal
        consorcio={consorcio}
        proveedorId={p.id}
        rubros={ficha.rubros_legales}
        faltantes={ficha.faltantes}
        requisitos={ficha.requisitos}
        modo={ficha.configuracion.modo_exigencia}
        puedeCambiar={puedeCambiar}
        aEntrar={aEntrar}
        alCambiar={alCambiar}
      />
      <SalteoDocumentacion
        consorcio={consorcio}
        proveedorId={p.id}
        salteos={ficha.salteos}
        modo={ficha.configuracion.modo_exigencia}
        hayFaltantes={ficha.faltantes.length > 0}
        puedeSaltear={puedeSaltear}
        propios={salteosPropios}
        alRegistrar={alRegistrarSalteo}
        aEntrar={aEntrar}
        alCambiar={alCambiar}
      />
      <DocumentacionProveedor
        consorcio={consorcio}
        proveedor={p}
        documentacion={ficha.documentacion}
        puedeCargar={puedeCargar}
        requisitos={ficha.requisitos}
        admiteNoVence={ficha.configuracion.admite_no_vence}
        aEntrar={aEntrar}
        alCambiar={alCambiar}
      />
    </FichaDeRegistro>
  );
}
