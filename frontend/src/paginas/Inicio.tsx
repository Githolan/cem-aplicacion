import { EstadoServicio } from "../componentes/EstadoServicio";

export function Inicio() {
  return (
    <main className="pagina">
      <header className="encabezado">
        <img className="encabezado__icono" src="/icono.svg" alt="" width={56} height={56} />
        <h1>Consorcio en Mano</h1>
      </header>
      <p className="bajada">
        La administración del consorcio, clara y a mano de cada vecino: expensas, pagos, reclamos y
        documentos en un solo lugar.
      </p>
      <section aria-labelledby="titulo-estado" className="tarjeta">
        <h2 id="titulo-estado">Estado del servicio</h2>
        <EstadoServicio />
      </section>
    </main>
  );
}
