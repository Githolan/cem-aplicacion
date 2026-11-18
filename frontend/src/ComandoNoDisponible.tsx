import { Link } from "react-router";

// Un comando que no se puede usar no se muestra (criterio 8 del informe de fricción, tanda 1): ni un
// botón apagado (salvo mientras viaja un pedido) ni un botón que lleve a un error previsible. En su
// lugar va esta línea, que dice qué falta y dónde se resuelve. Modelos que ya lo hacían bien:
// `pagos/Cotejo.tsx` («Lo deshace otra persona del Consejo: …») y `panel/Panel.tsx` («La sube la
// administración…»).
//
// Uso, en el lugar exacto del botón que no se dibuja:
//
//   {bloqueado
//     ? <ComandoNoDisponible motivo="Falta el seguro vigente del proveedor." resolver={{ texto: "Abrir su ficha", a: rutaFicha }} />
//     : <button type="submit">Guardar la orden</button>}
//
// - `motivo`: una frase completa, sin voseo, que diga qué falta o quién lo hace.
// - `resolver` (opcional): el enlace a la pantalla donde se resuelve. Sin él, el motivo ya dice dónde.
//   `estado` viaja en la navegación: con él, la pantalla de destino abre lo que hay que resolver y
//   puede volver a donde se estaba (flujos 18-11, FL-ORD-04).
// - `enLinea`: dentro de un renglón (una celda, una fila) va como `span` en vez de párrafo.

type Props = {
  motivo: string;
  resolver?: { texto: string; a: string; estado?: unknown };
  enLinea?: boolean;
};

export function ComandoNoDisponible({ motivo, resolver, enLinea = false }: Props) {
  const Contenedor = enLinea ? "span" : "p";
  return (
    <Contenedor className="comando-no-disponible">
      {motivo}
      {resolver !== undefined && (
        <>
          {" "}
          <Link to={resolver.a} state={resolver.estado} className="enlace-suelto">
            {resolver.texto}
          </Link>
        </>
      )}
    </Contenedor>
  );
}
