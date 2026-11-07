/** Mientras llega la lista: tres bloques grises, y el texto para quien usa lector de pantalla. */
export function CargandoPagos() {
  return (
    <div role="status" className="bloques-carga">
      <span className="solo-lector">Cargando los pagos…</span>
      <span className="bloques-carga__bloque" aria-hidden="true" />
      <span className="bloques-carga__bloque" aria-hidden="true" />
      <span className="bloques-carga__bloque" aria-hidden="true" />
    </div>
  );
}
